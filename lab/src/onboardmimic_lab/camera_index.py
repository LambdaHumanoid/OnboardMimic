"""Shared source-video PTS indexing for dataset exporters."""
import json
import os
import subprocess
from fractions import Fraction
from .dataset_io import rows
from .raw_storage import raw_path


def camera_index(root):
    """Decode metadata once, retaining actual source PTS; no JPEG encoding/writes."""
    result=subprocess.run(['ffprobe','-v','error','-threads','1','-select_streams','v:0',
        '-show_frames','-show_streams','-show_entries','frame=pts,pkt_pts:stream=time_base',
        '-of','json',str(root/'camera.ts')],check=True,capture_output=True,text=True,
        timeout=None if os.environ.get('OM_EXPORT_WORKER')=='1' else 600)
    info=json.loads(result.stdout);base=Fraction(info['streams'][0]['time_base']);indices={}
    for index,frame in enumerate(info['frames']):
        pts=frame.get('pts',frame.get('pkt_pts'))
        if pts is None:raise ValueError('missing source frame PTS')
        pts=round(int(pts)*base*90000)%(1<<33)
        if pts in indices:raise ValueError('ambiguous decoded frame PTS/count')
        indices[pts]=index
    result=[];seen=set()
    for frame in rows(raw_path(root,'camera.jsonl')):
        pts=frame['pts_90khz']
        if pts in seen:raise ValueError('repeated camera PTS (restart/wrap); split the episode')
        seen.add(pts)
        if pts in indices:
            result.append(dict(frame,image=dict(kind='video_frame',path='camera.ts',
                frame_index=indices[pts],pts_90khz=pts)))
    return result

