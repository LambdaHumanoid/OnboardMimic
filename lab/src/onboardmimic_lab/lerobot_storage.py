"""LeRobot v2.1 layout and atomic episode publication; only numeric/video data."""
import json
from pathlib import Path
import os
from .dataset_io import write_json, digest

VIDEO_KEY='observation.images.ego_view'
DATA_PATH='data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet'
VIDEO_PATH='videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4'


def entries(root):
    path=Path(root)/'meta/episodes.jsonl'
    if not path.exists():return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def find(path):
    path=Path(path)
    if path.parent.name!='episodes':return None
    for entry in entries(path.parent.parent):
        if entry.get('om',{}).get('id')==path.name:return path.parent.parent,entry
    return None


def jsonlines(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        for row in data:stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
        stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def features(table, names):
    import pyarrow as pa
    result={}
    for field in table.schema:
        kind=field.type
        shape=[1]
        if pa.types.is_list(kind) or pa.types.is_fixed_size_list(kind):
            shape=[len(table[field.name][0].as_py())];kind=kind.value_type
        dtype='bool' if pa.types.is_boolean(kind) else 'int64' if pa.types.is_integer(kind) else 'float32'
        result[field.name]=dict(dtype=dtype,shape=shape,names=None)
    result['observation.state']['names']=names+['gravity_x','gravity_y','gravity_z']
    result['action']['names']=['motion_token_'+str(i) for i in range(64)]
    result[VIDEO_KEY]=dict(dtype='video',shape=[480,640,3],names=['height','width','channels'],
        info={'video.fps':50.0,'video.codec':'h264','video.pix_fmt':'yuv420p',
              'video.is_depth_map':False,'has_audio':False})
    return result


def stats(table, image_stats):
    import numpy as np
    result={}
    for name in table.column_names:
        value=np.asarray(table[name].to_pylist(),dtype=np.float64)
        if value.ndim==1:value=value[:,None]
        result[name]=dict(min=value.min(axis=0).tolist(),max=value.max(axis=0).tolist(),
            mean=value.mean(axis=0).tolist(),std=value.std(axis=0).tolist(),count=[len(value)])
    result[VIDEO_KEY]=image_stats
    return result


def publish(root, staging, metadata, image_stats):
    """Commit episodes.jsonl last. Existing immutable episode files are never replaced."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    root=Path(root);staging=Path(staging);previous=entries(root)
    index=len(previous);offset=sum(e['length'] for e in previous)
    tasks=[]
    for e in previous:
        for task in e['tasks']:
            if task not in tasks:tasks.append(task)
    task=metadata['task']
    if task not in tasks:tasks.append(task)
    task_index=tasks.index(task)
    data=pq.read_table(staging/'data.parquet').to_pylist()
    for i,row in enumerate(data):
        row.pop('task',None)
        row.update(timestamp=np.float32(i/50).item(),frame_index=i,episode_index=index,index=offset+i,task_index=task_index)
    table=pa.Table.from_pylist(data)
    spec=features(table,metadata['body_joint_order'])
    # Explicit float32 schema, rather than JSON-list inferred double precision.
    arrays=[]
    for field in table.schema:
        ft=spec[field.name];typ={'float32':pa.float32(),'int64':pa.int64(),'bool':pa.bool_()}[ft['dtype']]
        if isinstance(data[0][field.name],list):typ=pa.list_(typ)
        arrays.append(pa.array([r[field.name] for r in data],type=typ))
    table=pa.Table.from_arrays(arrays,names=table.column_names)
    pq.write_table(table,staging/'data.parquet',compression='zstd')
    if previous and previous[0]['om']['features']!=spec:raise ValueError('LeRobot dataset feature layout changed; use a new dataset root')
    paths={'data.parquet':DATA_PATH.format(episode_chunk=index//1000,episode_index=index),
           'ego_view.mp4':VIDEO_PATH.format(episode_chunk=index//1000,episode_index=index,video_key=VIDEO_KEY)}
    episode_stats=stats(table,image_stats)
    metadata=dict(metadata,paths=paths,features=spec,stats=episode_stats,
        files={name:digest(staging/name) for name in paths})
    row=dict(episode_index=index,tasks=[task],length=len(data),om=metadata)
    for name,relative in paths.items():
        target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        # An uncommitted file from an interrupted save may exist at this next index.
        with (staging/name).open('rb') as stream:os.fsync(stream.fileno())
        (staging/name).replace(target)
        fd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    all_entries=previous+[row]
    try:
        write_metadata(root,all_entries)
    except BaseException:
        # A commit-record rename may have succeeded even if its fsync failed.
        if not any(e.get('om',{}).get('id')==metadata['id'] for e in entries(root)):
            for relative in paths.values():(root/relative).unlink(missing_ok=True)
            write_metadata(root,previous)
        raise
    return metadata


def write_metadata(root, all_entries):
    root=Path(root);(root/'meta').mkdir(exist_ok=True)
    tasks=[]
    for entry in all_entries:
        for task in entry['tasks']:
            if task not in tasks:tasks.append(task)
    if not all_entries:
        # No committed episodes: remove only uncommitted generated metadata.
        for name in ('info.json','tasks.jsonl','episodes_stats.jsonl'):
            (root/'meta'/name).unlink(missing_ok=True)
        return
    spec=all_entries[0]['om']['features']
    info=dict(codebase_version='v2.1',robot_type='unitree_g1',total_episodes=len(all_entries),
        total_frames=sum(e['length'] for e in all_entries),total_tasks=len(tasks),total_videos=len(all_entries),
        total_chunks=(len(all_entries)+999)//1000,chunks_size=1000,fps=50,
        splits={'train':'0:'+str(len(all_entries))},data_path=DATA_PATH,video_path=VIDEO_PATH,features=spec)
    write_json(root/'meta/info.json',info)
    jsonlines(root/'meta/tasks.jsonl',[dict(task_index=i,task=t) for i,t in enumerate(tasks)])
    jsonlines(root/'meta/episodes_stats.jsonl',[dict(episode_index=e['episode_index'],stats=e['om']['stats']) for e in all_entries])
    # This is the commit record, published after data/video and all derived metadata.
    jsonlines(root/'meta/episodes.jsonl',all_entries)
    for directory in (root/'meta',root):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def recover(root):
    import fcntl
    root=Path(root)
    if not (root/'meta').exists():return
    with (root/'.export.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        committed=entries(root)
        expected={relative for entry in committed for relative in entry['om']['paths'].values()}
        # Interrupted publication can leave one next-index file before metadata.
        for pattern in ('data/chunk-*/episode_*.parquet','videos/chunk-*/observation.images.ego_view/episode_*.mp4'):
            for path in root.glob(pattern):
                if str(path.relative_to(root)) not in expected and not path.is_symlink():
                    path.unlink()
        write_metadata(root,committed)
