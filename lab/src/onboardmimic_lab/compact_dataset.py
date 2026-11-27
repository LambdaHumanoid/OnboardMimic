"""Video-backed alignment index; samples are reconstructed from verified raw data."""
import json
import os
from pathlib import Path
import subprocess
from itertools import zip_longest
import uuid

from .dataset_io import digest, rows, write_json
from .raw_storage import raw_path

from .camera_index import camera_index

FORMAT = 'onboardmimic_lab_aligned_index_v3'



def indexed(row):
    return dict(source_index=row['sample']['index'],segment_index=row['segment_index'],
        camera_sequence=row['camera_sequence'],camera_pts_90khz=row['camera_pts_90khz'],
        camera_age_ns=row['camera_age_ns'])


def export_episode(root, manifest):
    from .reference_dataset import iter_aligned
    root=Path(root);staging=root/('.export-'+uuid.uuid4().hex);staging.mkdir()
    camera=camera_index(root);stats={}
    with (staging/'camera-index.jsonl').open('x') as f:
        for row in camera:f.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
        f.flush(); os.fsync(f.fileno())
    with (staging/'frames.jsonl').open('x') as f:
        for row in iter_aligned(root,camera,manifest['controller_spec'],stats):
            f.write(json.dumps(indexed(row),separators=(',',':'),allow_nan=False)+'\n')
        f.flush(); os.fsync(f.fileno())
    summary=dict(format=FORMAT,task=manifest['task'],controller_spec=manifest['controller_spec'],
        contract_sha256=manifest['contract_sha256'],raw_files=manifest['files'],
        reference_evidence='consumed',rows=stats['rows'],rejected_rows=stats['rejected_rows'],
        segments=stats['segments'],images=dict(kind='video_frame',path='camera.ts',clock='original PTS + local ingress'),
        files={str(p.relative_to(staging)):digest(p) for p in staging.iterdir()})
    summary.update({key:manifest[key] for key in ('task_id','task_name','task_description','collector_name','episode_index') if key in manifest})
    write_json(staging/'episode.json',summary);staging.rename(root/'aligned')
    return summary


def read(root, manifest, summary):
    from .reference_dataset import iter_aligned
    if summary.get('raw_files') != manifest['files']:raise ValueError('alignment raw source identity changed')
    if set(summary['files']) != {'frames.jsonl','camera-index.jsonl'}:raise ValueError('invalid alignment index files')
    camera=list(rows(root/'aligned/camera-index.jsonl'))
    raw_camera={r['sequence']:r for r in rows(raw_path(root,'camera.jsonl',manifest))}
    previous=-1
    for row in camera:
        image=row.get('image',{});frame=image.get('frame_index')
        original={k:v for k,v in row.items() if k!='image'}
        if (original!=raw_camera.get(row.get('sequence')) or image.get('kind')!='video_frame'
            or image.get('path')!='camera.ts' or image.get('pts_90khz')!=row['pts_90khz']
            or type(frame) is not int or frame<=previous):raise ValueError('camera index does not match raw source')
        previous=frame
    stats={};data=[]
    for row,entry in zip_longest(iter_aligned(root,camera,manifest['controller_spec'],stats),rows(root/'aligned/frames.jsonl')):
        if row is None or entry!=indexed(row):raise ValueError('alignment index does not match consumed source/image')
        data.append(row)
    if any(summary[k]!=stats[k] for k in ('rows','rejected_rows','segments')):raise ValueError('aligned row count mismatch')
    return summary,data


def image_bytes(root, row):
    """Read a verified aligned frame on demand; no persistent JPEG cache."""
    from .reference_dataset import read_aligned
    root=Path(root);_,data=read_aligned(root)
    if not any(item==row for item in data):raise ValueError('image row is not verified')
    image=row['image']
    index=image['frame_index']
    result = subprocess.run(['ffmpeg','-v','error','-nostdin','-threads','1','-i',str(root/'camera.ts'),
        '-vf','select=eq(n\\,%d)'%index,'-vsync','0','-frames:v','1','-threads','1',
        '-f','image2pipe','-c:v','mjpeg','pipe:1'],check=True,capture_output=True,timeout=120).stdout

    if not result: raise ValueError('video frame could not be decoded')
    return result
