"""Controller-neutral multimodal episodes; reference consumption anchors alignment."""
import json
import time
from bisect import bisect_right
from copy import deepcopy
from pathlib import Path

from .contracts import validate_spec, fingerprint, next_segment
from .dataset_io import digest, rows, write_json
from .raw_storage import raw_path, verify_streams

FORMAT = 'onboardmimic_lab_multimodal_v2'
FILES = ('controller.jsonl', 'camera.jsonl', 'runtime.jsonl', 'camera.ts')


class RawEpisode:
    def __init__(self, directory, metadata):
        self.spec = validate_spec(metadata['controller_spec'])
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=False)
        self.manifest = dict(metadata, format=FORMAT, status='recording',
                             controller_spec=self.spec, contract_sha256=fingerprint(self.spec),
                             camera_clock='local UDP ingress, not exposure', counts={}, files={})
        write_json(self.root/'manifest.json', self.manifest)
        self.streams = {name: (self.root/name).open('xb') for name in FILES}
        self.previous, self.segment = None, 0
        self.incidents = None
        self.diagnostic_context = {}

    def append(self, name, value):
        if name == "camera.jsonl" or (name == "runtime.jsonl" and "state" in value):
            self.diagnostic_context[name] = value
        line = (json.dumps(value, allow_nan=False, separators=(',', ':'))+'\n').encode()
        self.streams[name].write(line)
        self.manifest['counts'][name] = self.manifest['counts'].get(name, 0)+1

    def controller(self, sample, received_ns, *, take_ownership=False):
        """Record one sample; transferred JSON trees must not be mutated by callers.

        Keep the full previous sample for incident evidence, without copying an
        exclusively owned decoder result. Other callers retain copy isolation.
        """
        segment = next_segment(self.previous, sample, self.segment, self.spec)
        if self.previous and segment != self.segment:
            if self.incidents is not None:
                self.incidents.submit(self.previous, sample, received_ns,context=dict(self.diagnostic_context))
            self.append('runtime.jsonl',dict(event='controller_gap',received_monotonic_ns=received_ns,
                previous_index=self.previous['index'],index=sample['index'],
                source_gap_ns=sample['timestamp_monotonic_ns']-self.previous['timestamp_monotonic_ns'],
                previous_diagnostics=self.previous.get('capture_diagnostics'),diagnostics=sample.get('capture_diagnostics')))

        if type(received_ns) is not int or not 0 <= received_ns-sample['timestamp_monotonic_ns'] <= 250_000_000:
            raise ValueError('controller timestamp is stale or not local')
        self.append('controller.jsonl', dict(sample=sample, received_monotonic_ns=received_ns, segment_index=segment))
        self.append('runtime.jsonl',dict(event='controller_write_returned',index=sample['index'],
            received_monotonic_ns=received_ns,write_returned_monotonic_ns=time.monotonic_ns(),
            sample_ref=dict(file='controller.jsonl',session_id=sample['session_id'],index=sample['index']),
            boundary='buffered_write_not_fsync'))
        self.previous, self.segment = sample if take_ownership else deepcopy(sample), segment

    def video(self, payload): self.streams['camera.ts'].write(payload)

    def finish(self, error=None):
        import os
        for stream in self.streams.values():
            if not stream.closed:
                stream.flush(); os.fsync(stream.fileno()); stream.close()
        self.manifest.update(files={n: digest(self.root/n) for n in FILES},
                             status='failed' if error else 'review', error=error)
        write_json(self.root/'manifest.json', self.manifest)


def verify_raw(root):
    root = Path(root)
    m = json.loads((root/'manifest.json').read_text())
    spec = validate_spec(m['controller_spec'])
    if m.get('format') != FORMAT or m.get('status') not in ('review', 'raw_saved', 'saved'):
        raise ValueError('episode is not complete and reviewable')
    if m['contract_sha256'] != fingerprint(spec): raise ValueError('contract fingerprint mismatch')
    verify_streams(root, m, FILES)
    return m


def samples(root, spec):
    previous, segment = None, 0
    for row in rows(raw_path(root, 'controller.jsonl')):
        segment = next_segment(previous, row['sample'], segment, spec)
        if type(row['segment_index']) is not int or row['segment_index'] != segment:
            raise ValueError('invalid source segment')
        if not 0 <= row['received_monotonic_ns']-row['sample']['timestamp_monotonic_ns'] <= 250_000_000:
            raise ValueError('invalid ingress timestamp')
        previous = row['sample']
        yield row


def iter_aligned(root, camera, spec, stats, max_age_ns=75_000_000):
    stamps = [r['monotonic_ns'] for r in camera]
    if not stamps or any(b < a for a, b in zip(stamps, stamps[1:])):
        raise ValueError('invalid camera ingress timestamps')
    segment, previous_source = -1, None
    stats.update(rows=0, rejected_rows=0, segments=0)
    for row in samples(root, spec):
        s = row['sample']; timestamp = s['timestamp_monotonic_ns']
        selected = bisect_right(stamps, timestamp)-1
        if s['reference_evidence'] != 'consumed' or selected < 0 or timestamp-stamps[selected] > max_age_ns:
            stats["rejected_rows"] += 1; previous_source = None; continue
        if previous_source != row['segment_index']: segment += 1
        previous_source = row['segment_index']
        image = camera[selected]
        stats["rows"] += 1; stats["segments"] = segment+1
        yield dict(sample=s, segment_index=segment, image=image['image'],
                           camera_sequence=image['sequence'], camera_pts_90khz=image['pts_90khz'],
                           camera_age_ns=timestamp-stamps[selected])
    if not stats['rows']: raise ValueError('no consumed reference/image pairs within 75 ms')


def align(root, camera, spec, max_age_ns=75_000_000):
    stats = {}
    data = list(iter_aligned(root, camera, spec, stats, max_age_ns))
    return data, stats['rejected_rows']


def export_episode(root):
    from .compact_dataset import export_episode as compact_export
    root = Path(root); manifest = verify_raw(root)
    if (root/'aligned').exists(): raise ValueError('aligned export already exists')
    return compact_export(root, manifest)


def read_aligned(root):
    root = Path(root); m = verify_raw(root); aligned = root/'aligned'
    if m['status'] != 'saved' or digest(aligned/'episode.json') != m.get('aligned_manifest'):
        raise ValueError('aligned dataset has not been saved or changed')
    summary = json.loads((aligned/'episode.json').read_text())
    if (summary['format'] != 'onboardmimic_lab_aligned_index_v3' or summary['controller_spec'] != m['controller_spec']
            or summary['contract_sha256'] != m['contract_sha256']):
        raise ValueError('aligned contract mismatch')
    for name, descriptor in summary['files'].items():
        path = aligned/name
        if aligned.resolve() not in path.resolve().parents or digest(path) != descriptor:
            raise ValueError('aligned integrity failed: '+name)
    from .compact_dataset import read
    return read(root, m, summary)
