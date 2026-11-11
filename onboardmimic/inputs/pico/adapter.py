"""Read-only PICO tracking preview; no controller messages or retargeting."""
import json
import math
import time
from pathlib import Path
from onboardmimic.core.contracts import TrackingInput


class PicoInput(TrackingInput):
    id = 'pico'
    label = 'PICO'

    def __init__(self):
        self.runs = Path(__file__).resolve().parent/'runs'

    def preview_script(self):
        return Path(__file__).resolve().parent/"static/pico-skeleton.js"

    def status(self):
        result = dict(connected=False, tracker_count=0, calibrated=False,
                      ready=False, reason='全身 Pose 就绪状态以机载网关反馈为准')
        try:
            path = max(self.runs.glob('*/status.json'), key=lambda p: p.stat().st_mtime)
            data = json.loads(path.read_text())
            age = time.time() - float(data.get('updated_at_unix', 0))
            live = data.get('data_source') == 'sdk' and data.get('receiver_running') is True and 0 <= age <= 2
            streams = data.get('streams', {})
            result['connected'] = bool(live and streams.get('head', {}).get('fresh'))
            result['tracker_count'] = len(data.get('visible_tracker_serials', [])) if live else 0
            result['receiver_running'] = bool(live)
            result['body_ready'] = bool(live and streams.get('body',{}).get('fresh'))
            result['body_hz'] = streams.get('body',{}).get('unique_frame_hz',0) if result['body_ready'] else 0
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return result

    def skeleton(self):
        """Read-only diagnostic data; never calls the control backend."""
        result=dict(available=False,receiver_running=False,joints=[],head=None,
                    frame='PICO Unity / meters / xyzw',robot_output_enabled=False,
                    reason='等待 PICO 全身姿态',body_hz=0,head_hz=0)
        try:
            path=max(self.runs.glob('*/status.json'),key=lambda p:p.stat().st_mtime)
            data=json.loads(path.read_text());age=time.time()-float(data.get('updated_at_unix',0))
            live=data.get('data_source')=='sdk' and data.get('receiver_running') is True and 0<=age<=2
            result['receiver_running']=live
            if not live:
                result['reason']='PICO 接收未运行或状态已过期';return result
            def fresh(stream):
                a=stream.get('age_ms')
                return (stream.get('fresh') is True and type(a) in (int,float)
                        and math.isfinite(a) and a>=0 and age+a/1000<=.35)
            def pose(p):
                return (isinstance(p,list) and len(p)==7 and
                        all(type(v) in (int,float) and math.isfinite(v) for v in p) and
                        max(map(abs,p[:3]))<=100 and .64<=sum(v*v for v in p[3:])<=1.44)
            streams=data.get('streams',{});body=streams.get('body',{});head=streams.get('head',{})
            for name,stream in [('body',body),('head',head)]:
                hz=stream.get('unique_frame_hz',0)
                if fresh(stream) and type(hz) in (int,float) and math.isfinite(hz):result[name+'_hz']=max(0,hz)
            if fresh(head) and pose(head.get('payload')):result['head']=head['payload']
            joints=body.get('payload')
            if fresh(body) and isinstance(joints,list) and len(joints)==24 and all(pose(p) for p in joints):
                result.update(available=True,joints=joints,reason='全身姿态实时接收',body_age_ms=round(age*1000+body['age_ms']),timestamp_ns=str(body.get('source_timestamp_ns','')))
            else:result['reason']='仅收到头部，等待全身姿态' if result['head'] else '姿态已断流，等待 PICO 发送'
        except (OSError,ValueError,TypeError,AttributeError,KeyError):pass
        return result
