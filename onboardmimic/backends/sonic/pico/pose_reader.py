"""Read-only PICO -> SONIC SMPL mapping worker. Publication belongs to gateway."""
from collections import deque
import json
from pathlib import Path
import threading
import time
import sys
import numpy as np
from onboardmimic.backends.sonic.pico.pose_mapping import map_body,mix
from onboardmimic.backends.sonic.pico.pose_wire import pack_pose_message
from onboardmimic.backends.sonic.pico.pose_consistency import ArmAdmission,compare_arms
from onboardmimic.core.runtime_timing import wait_event

class PoseReader:
    def __init__(self,path,clock=time.monotonic):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock();self.stop=threading.Event()
        self.prev=None;self.next_stamp=None;self.frames=deque(maxlen=5);self.index=0
        self.frozen=None
        self.mapping_enabled=lambda:True
        self.suspended=False
        self.admission=ArmAdmission()
        self.preview=None;self.sent_preview=None;self.sent_at=None
        self.updated=None;self.packet=None;self.reason='等待 PICO 全身数据';self.regressed=False
        self.thread=threading.Thread(target=self.run,daemon=True)
    def invalidate(self,reason):
        with self.lock:
            self.admission=ArmAdmission()
            self.preview=None;self.sent_preview=None;self.sent_at=None
            self.packet=None;self.frames.clear();self.prev=None;self.next_stamp=None;self.reason=reason
    def feed(self,data,wall=None):
        wall=time.time() if wall is None else wall;now=self.clock()
        with self.lock:
            b=data.get('streams',{}).get('body',{});age=wall-float(data.get('updated_at_unix',0))
            stamp=b.get('source_timestamp_ns');poses=b.get('payload')
            if (data.get('data_source')!='sdk' or data.get('receiver_running') is not True or
                b.get('fresh') is not True or not 0<=age<=.25 or
                type(b.get('age_ms')) not in (int,float) or not 0<=b['age_ms']+age*1000<=250 or
                type(stamp) is not int or stamp<=0):
                self.invalidate('PICO 身体数据已断流');return
            if self.regressed:return
            if self.prev and stamp<self.prev[0]:
                self.regressed=True;self.invalidate('PICO 时间戳回退，请重启接收适配器');return
            if self.prev and stamp==self.prev[0]:return
            mapped=map_body(poses)
            if self.prev and stamp-self.prev[0]>250000000:self.invalidate('PICO 数据间隔过大')
            self.admission.update(compare_arms(poses,mapped),now)
            if self.prev is None:
                self.prev=(stamp,mapped);self.next_stamp=stamp;return
            old_stamp,old=self.prev
            while self.next_stamp<=stamp:
                if self.next_stamp>=old_stamp:
                    a=(self.next_stamp-old_stamp)/(stamp-old_stamp)
                    row=mix(old,mapped,a);row['frame_index']=np.int64(self.index);self.index+=1;self.frames.append(row)
                self.next_stamp+=20000000
            self.prev=(stamp,mapped);self.updated=now
            if len(self.frames)==5:
                fields={k:np.stack([r[k] for r in self.frames]) for k in self.frames[0]}
                self.preview=dict(available=True,source='sonic_smpl',coordinate_frame='root_local_z_up',
                    joints=fields['smpl_joints'][-1].tolist(),body_quat_w=fields['body_quat_w'][-1].tolist(),
                    frame_index=int(fields['frame_index'][-1]),window_frame_indices=fields['frame_index'].tolist(),
                    body_hz=50,reason='SONIC SMPL',robot_output_enabled=False)
                self.packet=pack_pose_message(fields,version=3);self.reason='SONIC SMPL · 50 Hz · 5 帧窗口'
    def freeze_reference(self):
        with self.lock:
            if not self.snapshot()['pose_admission_ready']: raise ValueError('Pose reference not ready')
            self.frozen={k:v.copy() for k,v in self.frames[-1].items()}
    def release_reference(self):
        with self.lock:self.frozen=None
    def snapshot(self):
        with self.lock:
            fresh=self.updated is not None and 0<=self.clock()-self.updated<=.25
            packet=self.packet;preview=self.preview
            if fresh and packet and self.frozen is not None:
                fields={k:np.stack([self.frozen[k]]*5) for k in self.frozen}
                fields['frame_index']=np.asarray([r['frame_index'] for r in self.frames],dtype=np.int64)
                packet=pack_pose_message(fields,version=3)
                preview=dict(preview,joints=fields['smpl_joints'][-1].tolist(),body_quat_w=fields['body_quat_w'][-1].tolist())
            return dict(pose_admission_ready=bool(fresh and self.packet and self.admission.result['ready']),
                        consistency=dict(self.admission.result),ready=bool(fresh and self.packet),reason=self.reason if fresh else '等待新鲜 PICO 身体数据',packet=packet if fresh else None,preview=preview if fresh and packet else None)
    def mark_published(self,snapshot):
        # Capture the same immutable snapshot whose bytes were handed to ZMQ.
        with self.lock:
            self.sent_preview=snapshot.get('preview');self.sent_at=self.clock()
    def skeleton(self,published=False):
        with self.lock:
            if published:
                fresh=self.sent_at is not None and 0<=self.clock()-self.sent_at<=.25
                preview=self.sent_preview if fresh else None
            else:
                preview=self.snapshot().get('preview')
            if not preview:
                return dict(available=False,joints=[],reason='等待新鲜 SONIC SMPL',source='sonic_smpl')
            return dict(preview,consistency=dict(self.admission.result),delivery='published' if published else 'preview',
                        reason='最近发送的 SONIC SMPL' if published else '待发送的 SONIC SMPL')
    def read_once(self):
        if not self.mapping_enabled():
            if not self.suspended:
                self.invalidate("回放期间暂停 PICO 姿态映射")
                self.release_reference()
                self.suspended=True
            return
        self.suspended=False
        diagnostics=getattr(self,"runtime_diagnostics",None)
        if diagnostics is not None:diagnostics.mark("pose","read_decode")
        data=json.loads(self.path.read_text())
        if diagnostics is not None:diagnostics.mark("pose","map")
        self.feed(data)

    def run(self):
        diagnostics=getattr(self,'runtime_diagnostics',None)
        while not self.stop.is_set():
            try:
                self.read_once()
            except (OSError,ValueError,TypeError,KeyError,AttributeError):self.invalidate('PICO 数据读取或映射失败')
            if diagnostics is not None:diagnostics.mark('pose','wait')
            wait_event(self.stop, .01)
