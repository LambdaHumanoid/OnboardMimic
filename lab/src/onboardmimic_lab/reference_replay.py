"""Controller-neutral, consumed-reference plans. Physical execution is OM-owned."""
from pathlib import Path
from .contracts import validate_spec, fingerprint, next_segment, integer, sha256
from .reference_dataset import verify_raw, read_aligned, samples

SCHEMA = 'onboardmimic.lab.reference-plan'


def validate_plan(plan):
    if plan.get('sample_profile') == 'lerobot_body_v21':
        from .lerobot_replay import validate_plan as validate
        return validate(plan)
    if plan.get('schema') != SCHEMA or type(plan.get('schema_version')) is not int or plan['schema_version'] not in (1,2):
        raise ValueError('unsupported reference plan')
    spec = validate_spec(plan['controller_spec'])
    if spec['schema_version'] != 1 or spec.get('physical_source') is True:
        raise ValueError('Legacy native recordings are not supported; use LeRobot v2.1')
    if plan['schema_version'] != spec['schema_version']: raise ValueError('plan/spec version mismatch')
    if plan['contract_sha256'] != fingerprint(spec): raise ValueError('plan contract mismatch')
    sha256(plan['source_sha256']); integer(plan['segment_index'], 'segment')
    frames = plan['frames']
    if not isinstance(frames, list) or not 2 <= len(frames) <= 100000:
        raise ValueError('plan needs 2..100000 frames')
    previous, first = None, frames[0]['sample']['timestamp_monotonic_ns']
    for frame in frames:
        s = frame['sample']
        if next_segment(previous, s, 0, spec) != 0: raise ValueError('replay cannot cross a discontinuity')
        if s['reference_evidence'] != 'consumed': raise ValueError('replay requires consumed references')
        if type(frame['offset_ns']) is not int or frame['offset_ns'] != s['timestamp_monotonic_ns']-first:
            raise ValueError('invalid reference offset')
        previous = s
    if frames[-1]['offset_ns']+spec['period_ns'] > 120_000_000_000:
        raise ValueError('replay exceeds 120 seconds')
    return plan


def replay_segments(episode):
    root = Path(episode).resolve(); m = verify_raw(root); read_aligned(root)
    # Missing/unconsumed frames split plans, never silently compress time.
    segments = {}; current = -1; previous_raw_segment = None
    for row in samples(root, m['controller_spec']):
        s = row['sample']
        if s['reference_evidence'] != 'consumed': previous_raw_segment = None; continue
        if previous_raw_segment != row['segment_index']: current += 1
        previous_raw_segment = row['segment_index']; segments.setdefault(current, []).append(s)
    return root, m, segments


def segment_options(episode):
    from .lerobot_storage import find
    if find(episode) is not None:
        from .lerobot_replay import segment_options as options
        return options(episode)
    _, m, segments = replay_segments(episode)
    if not segments: raise ValueError('No consumed references available')
    first = next(iter(segments.values()))[0]['timestamp_monotonic_ns']
    return [dict(index=i, frames=len(rows), start_s=(rows[0]['timestamp_monotonic_ns']-first)/1e9,
                 end_s=(rows[-1]['timestamp_monotonic_ns']-first+m['controller_spec']['period_ns'])/1e9)
            for i, rows in segments.items()]


def prepare_plan(episode, segment_index=None):
    root, m, segments = replay_segments(episode)
    if m['controller_spec']['schema_version'] != 1 or m['controller_spec'].get('physical_source') is True:
        raise ValueError('Legacy native recordings are not supported; use LeRobot v2.1')
    if segment_index is None:
        if len(segments) != 1: raise ValueError('select one --segment from: '+str(sorted(segments)))
        segment_index = next(iter(segments))
    if segment_index not in segments: raise ValueError('selected segment is empty')
    selected = segments[segment_index]; first = selected[0]['timestamp_monotonic_ns']
    return validate_plan(dict(schema=SCHEMA, schema_version=m['controller_spec']['schema_version'], episode=str(root), segment_index=segment_index,
        controller_spec=m['controller_spec'], contract_sha256=m['contract_sha256'],
        source_sha256=m['files']['controller.jsonl']['sha256'],
        frames=[dict(offset_ns=s['timestamp_monotonic_ns']-first, sample=s) for s in selected]))


def summary(plan):
    validate_plan(plan)
    return dict(plan_sha256=fingerprint(plan), episode=plan['episode'], segment_index=plan['segment_index'],
                frames=len(plan['frames']), contract_sha256=plan['contract_sha256'],
                reference_space=plan['controller_spec']['reference_space'],
                duration_s=(plan['frames'][-1]['offset_ns']+plan['controller_spec']['period_ns'])/1e9)
