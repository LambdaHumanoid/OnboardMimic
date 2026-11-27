"""LeRobot v2.1 body-only export; temporary telemetry is discarded after commit."""
import json
import os
from pathlib import Path
import shutil
from fractions import Fraction
import uuid
import time

from .dataset_io import digest, write_json

FORMAT = 'onboardmimic_lerobot_v21'
PROFILE = 'sonic_body_only_v1'


def metadata(root):
    from .lerobot_storage import find
    found=find(root)
    if found is None:raise ValueError('LeRobot v2.1 episode is not saved')
    return found[1]['om']


def manifest(root):
    return manifest_from_metadata(metadata(root))


def manifest_from_metadata(value):
    """Convert a saved catalog entry without reopening the dataset index."""
    return dict(value['source_manifest'], status='saved',
                export=dict(state='complete'), frames=value['frames'], duration_s=value.get('duration_s'))


def verify(root):
    from .lerobot_storage import find
    found=find(root)
    if found is None:raise ValueError('LeRobot v2.1 episode is not saved')
    return verify_artifacts(found[0],found[1]['om'])


def verify_artifacts(root, m):
    root=Path(root)
    if m.get('format') != FORMAT or m.get('profile') != PROFILE:
        raise ValueError('Unsupported OpenLambda recording profile')
    if m.get('reference_evidence') != 'consumed_at_capture':
        raise ValueError('Missing consumed-reference provenance')
    from .contracts import validate_spec, fingerprint
    source=m['source_manifest']
    spec=validate_spec(source['controller_spec'])
    if fingerprint(spec)!=source['contract_sha256']:raise ValueError('Controller contract fingerprint mismatch')
    if set(m['files']) != {'data.parquet', 'ego_view.mp4'}:
        raise ValueError('Invalid episode files')
    for name, expected in m['files'].items():
        path=root/m.get('paths',{}).get(name,name)
        if root.resolve() not in path.resolve().parents or path.is_symlink() or digest(path) != expected:
            raise ValueError('Episode integrity failed: '+name)
    import pyarrow.parquet as pq
    if pq.ParquetFile(root/m.get('paths',{}).get('data.parquet','data.parquet')).metadata.num_rows != m['frames']:
        raise ValueError('Episode row count mismatch')
    return m


def records(root):
    import pyarrow.parquet as pq
    from .lerobot_storage import find
    found=find(root)
    if found is None:raise ValueError('LeRobot v2.1 episode is not saved')
    directory,entry=found
    for batch in pq.ParquetFile(directory/entry['om']['paths']['data.parquet']).iter_batches(batch_size=256):
        yield from batch.to_pylist()


def body_fields(sample, spec):
    import numpy as np
    def latest(prefix):
        key = next(k for k in spec['state'] if k.startswith(prefix))
        return sample['state'][key][-1]
    mapping = spec['loaded_parameters']['mujoco_to_isaaclab']
    default = spec['loaded_parameters']['default_angles']
    if sorted(mapping) != list(range(29)) or len(default) != 29:
        raise ValueError('Missing absolute joint position mapping')
    q = [0.]*29; dq = [0.]*29
    for i, hw in enumerate(mapping):
        q[hw] = latest('his_body_joint_positions_')[i]+default[hw]
        dq[hw] = latest('his_body_joint_velocities_')[i]
    gravity = latest('his_gravity_dir_')
    token = sample['reference']['token_state']
    return {
        'observation.state': np.asarray(q+gravity, dtype=np.float32).tolist(),
        'observation.valid_mask': [True]*32,
        'action': np.asarray(token,dtype=np.float32).tolist(),
        'action.valid_mask': [True]*64,
        'observation.body_joint_position': np.asarray(q,dtype=np.float32).tolist(),
        'observation.body_joint_velocity': np.asarray(dq,dtype=np.float32).tolist(),
        'observation.projected_gravity': np.asarray(gravity,dtype=np.float32).tolist(),
        'action.motion_token': np.asarray(token,dtype=np.float32).tolist(),
    }


def export(root):
    import fcntl
    root=Path(root)
    if root.parent.name!='episodes':raise ValueError('Capture must be under dataset episodes directory')
    with (root.parent.parent/'.export.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        from .lerobot_storage import find
        if find(root) is not None:
            cleanup(root)
            return metadata(root)
        return _export(root)


def _export(root):
    import av
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from .reference_dataset import verify_raw
    from .video_alignment import aligned_video
    started = time.monotonic()
    cpu_started = time.process_time()
    timings = {}
    from .export_progress import ExportProgress
    progress = ExportProgress(root)
    progress.update("verify_raw")
    def timed_frames(frames):
        iterator = iter(frames)
        decoded = 0
        while True:
            before = time.monotonic()
            frame = next(iterator, None)
            timings['source_decode_s'] = timings.get('source_decode_s', 0.)+time.monotonic()-before
            if frame is None:
                return
            decoded += 1
            progress.update("video", decoded, original.get("counts", {}).get("camera.jsonl"))
            yield frame
    stage = time.monotonic()
    root = Path(root)
    if root.parent.name!='episodes':raise ValueError('Capture must be under dataset episodes directory')
    original = verify_raw(root)
    timings['verify_raw_s'] = time.monotonic()-stage
    spec = original['controller_spec']
    if spec.get('schema_version') != 2 or spec.get('backend_id') != 'sonic':
        raise ValueError('OpenLambda body export requires SONIC native data')
    for partial in root.glob('.export-*'):
        if len(partial.name)==40 and all(c in '0123456789abcdef' for c in partial.name[8:]) and partial.is_dir() and not partial.is_symlink():
            shutil.rmtree(partial)
    staging = root/('.export-'+uuid.uuid4().hex); staging.mkdir()
    writer = None
    stats = {}; segments = []; count = 0; start = None
    image_sum=np.zeros(3);image_square=np.zeros(3);image_min=np.ones(3);image_max=np.zeros(3);image_pixels=0
    try:
        stage = time.monotonic()
        progress.update("video", 0, original.get("counts", {}).get("camera.jsonl"))
        with av.open(str(root/'camera.ts')) as source, av.open(str(staging/'ego_view.mp4'),'w') as video:
            source.streams.video[0].codec_context.thread_count = 1
            current = -1
            image_moments = None
            stream = video.add_stream('libx264', rate=50)
            stream.width=640;stream.height=480;stream.pix_fmt='yuv420p'
            stream.time_base=Fraction(1,90000);stream.codec_context.time_base=Fraction(1,90000)
            stream.codec_context.thread_count=1
            stream.options={'crf':'18','preset':'fast','bf':'0'}
            batch=[]
            for aligned in aligned_video(root, timed_frames(source.decode(video=0)), spec, stats):
                s=aligned['sample'];wanted=aligned['image']['frame_index']
                if wanted != current:
                    current = wanted
                    image=aligned['image']['frame'].to_ndarray(format='rgb24');h,w=image.shape[:2]
                    if w/h>4/3:
                        width=round(h*4/3);left=(w-width)//2;image=image[:,left:left+width]
                    else:
                        height=round(w*3/4);top=(h-height)//2;image=image[top:top+height,:]
                    rgb=av.VideoFrame.from_ndarray(np.ascontiguousarray(image),format='rgb24').reformat(width=640,height=480)
                    # Reused camera frames carry identical pixels at multiple 50Hz samples.
                    image_array=rgb.to_ndarray(format='rgb24')
                    pixels=image_array[::4,::4].reshape(-1,3).astype(np.float64)/255
                    image_moments=(pixels.sum(axis=0),(pixels*pixels).sum(axis=0),
                                   pixels.min(axis=0),pixels.max(axis=0),len(pixels))
                at=s['timestamp_monotonic_ns']
                if start is None:start=at
                row=body_fields(s,spec)
                row.update(timestamp=(at-start)/1e9,frame_index=count,segment_index=aligned['segment_index'],
                    task=original.get('task',''),**{
                    'source.robot_index':s['index'],
                    'source.consumption_sequence':s.get('consumption',{}).get('sequence',s['index']),
                    'source.consumption_segment':s.get('consumption',{}).get('segment',aligned['segment_index']),
                    'quality.superseded_attempts':s.get('consumption',{}).get('skipped_attempts',0),
                    'source.robot_realtime_ns':s['timestamp_realtime_ns'],
                    'source.robot_monotonic_ns':at,
                    'source.robot_received_monotonic_ns':aligned['received_monotonic_ns'],
                    'source.camera_sequence':aligned['camera_sequence'],
                    'quality.alignment_delta_ms':[aligned['camera_age_ns']/1e6]})
                # Preserve measured variable timestamps; repeated 30Hz images at robot samples.
                frame=av.VideoFrame.from_ndarray(image_array,format='rgb24')
                frame.pts=count*1800;frame.time_base=Fraction(1,90000)
                sums,squares,minimum,maximum,size=image_moments
                image_sum+=sums;image_square+=squares
                image_min=np.minimum(image_min,minimum);image_max=np.maximum(image_max,maximum);image_pixels+=size
                before_encode = time.monotonic()
                for packet in stream.encode(frame):video.mux(packet)
                timings['encode_s'] = timings.get('encode_s', 0.)+time.monotonic()-before_encode
                if not segments or segments[-1]['segment_index']!=row['segment_index']:
                    if segments:segments[-1]['end_frame_exclusive']=count
                    segments.append(dict(segment_index=row['segment_index'],start_frame=count))
                batch.append(row);count+=1
                if len(batch)>=256:
                    table=pa.Table.from_pylist(batch)
                    if writer is None:writer=pq.ParquetWriter(staging/'data.parquet',table.schema,compression='zstd')
                    writer.write_table(table);batch.clear()
            if batch:
                table=pa.Table.from_pylist(batch)
                if writer is None:writer=pq.ParquetWriter(staging/'data.parquet',table.schema,compression='zstd')
                writer.write_table(table)
            before_encode = time.monotonic()
            for packet in stream.encode():video.mux(packet)
            timings['encode_s'] = timings.get('encode_s', 0.)+time.monotonic()-before_encode
        writer.close();writer=None
        timings['decode_align_encode_s'] = time.monotonic()-stage
        stage = time.monotonic()
        progress.update('verify_output', 0, count)
        segments[-1]['end_frame_exclusive']=count
        # Same metadata role as OpenLambda source_manifest; no raw tensors/log payloads.
        retained={k:v for k,v in original.items() if k not in ('storage','export','error','aligned_manifest')}
        retained.update(status='saved')
        names=next(v['joint_names'] for k,v in spec['state'].items() if k.startswith('his_body_joint_positions_'))
        ordered=[None]*29
        for i,hw in enumerate(spec['loaded_parameters']['mujoco_to_isaaclab']):ordered[hw]=names[i]
        result=dict(format=FORMAT,id=root.name,profile=PROFILE,reference_evidence='consumed_at_capture',task=original.get('task',''),frames=count,
            rejected_frames=stats['rejected_rows'],nominal_fps=50,duration_s=(at-start+spec['period_ns'])/1e9,
            timing='50Hz sample-order dataset/video timeline; actual clocks retained in source.robot_*_ns',segments=segments,
            images={'observation.images.ego_view':'ego_view.mp4'},state_dim=32,action_dim=64,
            body_joint_order=ordered,state_layout=['body_q[29]','gravity[3]'],
            action_layout=['sonic_consumed_motion_token[64]'],
            missing_sources=['hands','root_orientation'],
            state_semantics='SONIC consumed observation: joint velocity after observation guard',
            source_manifest=retained,
            files={name:digest(staging/name) for name in ('data.parquet','ego_view.mp4')})
        for name in result['files']:
            with (staging/name).open('rb') as f:os.fsync(f.fileno())
        verify_artifacts(staging,result)
        with av.open(str(staging/'ego_view.mp4')) as video:
            verified = 0
            for _ in video.decode(video=0):
                verified += 1
                progress.update('verify_output', verified, count)
            if verified!=count:raise ValueError('Export video count mismatch')
        timings['verify_output_s'] = time.monotonic()-stage
        def channels(value):return value.reshape(3,1,1).tolist()
        image_mean=image_sum/image_pixels
        image_stats=dict(min=channels(image_min),max=channels(image_max),mean=channels(image_mean),
            std=channels(np.sqrt(np.maximum(0,image_square/image_pixels-image_mean*image_mean))),count=[count])
        from .lerobot_storage import publish
        result['processing'] = dict(schema=1, stages=dict(timings),
            elapsed_before_publish_s=time.monotonic()-started,
            cpu_before_publish_s=time.process_time()-cpu_started)
        stage = time.monotonic()
        progress.update('saving')
        result=publish(root.parent.parent,staging,result,image_stats)
        cleanup(root)
        timings['publish_cleanup_s'] = time.monotonic()-stage
        print(json.dumps(dict(event='data_processing_complete', episode_id=root.name,
            stages=timings, elapsed_s=time.monotonic()-started,
            cpu_s=time.process_time()-cpu_started)), flush=True)
        return result
    finally:
        if writer is not None:writer.close()
        if staging.exists():shutil.rmtree(staging)


def cleanup(root):
    """Only after durable publication and integrity verification, never on old episodes."""
    root=Path(root);verify(root)
    if root.parent.name!='episodes' or root.is_symlink():raise ValueError('Invalid capture cleanup path')
    if root.exists():shutil.rmtree(root)
    fd=os.open(root.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def verify_raw(root):
    verify(root)
    return manifest(root)


def read_aligned(root):
    return verify(root), list(records(root))


def export_episode(root):
    raise ValueError('Episode is already exported')
