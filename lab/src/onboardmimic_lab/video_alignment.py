"""Bounded-memory, single-decode join using real decoded PTS and ingress clocks."""
from .dataset_io import rows
from .raw_storage import raw_path
from .reference_dataset import samples


def decoded_camera(root, frames):
    receipts = list(rows(raw_path(root, 'camera.jsonl')))
    stamps = [r['monotonic_ns'] for r in receipts]
    if not stamps or any(b < a for a, b in zip(stamps, stamps[1:])):
        raise ValueError('invalid camera ingress timestamps')
    by_pts = {}
    for receipt in receipts:
        pts = receipt['pts_90khz']
        if pts in by_pts:
            raise ValueError('repeated camera PTS (restart/wrap); split the episode')
        by_pts[pts] = receipt
    seen = set()
    previous_stamp = None
    for index, frame in enumerate(frames):
        if frame.pts is None or frame.time_base is None:
            raise ValueError('missing source frame PTS')
        pts = round(frame.pts * frame.time_base * 90000) % (1 << 33)
        if pts in seen:
            raise ValueError('ambiguous decoded frame PTS/count')
        seen.add(pts)
        receipt = by_pts.get(pts)
        if receipt is None:
            continue
        stamp = receipt['monotonic_ns']
        if previous_stamp is not None and stamp < previous_stamp:
            raise ValueError('Camera source order regressed')
        previous_stamp = stamp
        yield dict(receipt, frame_index=index, frame=frame)


def aligned_video(root, frames, spec, stats, max_age_ns=75_000_000):
    camera = iter(decoded_camera(root, frames))
    upcoming = next(camera, None)
    current = None
    segment, previous_source = -1, None
    stats.update(rows=0, rejected_rows=0, segments=0)
    for row in samples(root, spec):
        sample = row['sample']
        at = sample['timestamp_monotonic_ns']
        while upcoming is not None and upcoming['monotonic_ns'] <= at:
            current, upcoming = upcoming, next(camera, None)
        if (sample['reference_evidence'] != 'consumed' or current is None
                or at-current['monotonic_ns'] > max_age_ns):
            stats['rejected_rows'] += 1
            previous_source = None
            continue
        if previous_source != row['segment_index']:
            segment += 1
        previous_source = row['segment_index']
        stats['rows'] += 1
        stats['segments'] = segment+1
        yield dict(sample=sample, received_monotonic_ns=row['received_monotonic_ns'],
                   segment_index=segment, image=current,
                   camera_sequence=current['sequence'], camera_age_ns=at-current['monotonic_ns'])
    # Validate the tail too, including duplicate/missing timestamps after the last sample.
    for _ in camera:
        pass
    if not stats['rows']:
        raise ValueError('no consumed reference/image pairs within 75 ms')
