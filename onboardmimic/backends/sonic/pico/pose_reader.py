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
from onboardmimic.core.runtime_timing import wait_event

class PoseReader:
    def __init__(self,path,clock=time.monotonic):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock();self.stop=threading.Event()
        self.prev=None;self.next_stamp=None;self.frames=deque(maxlen=5);self.index=0
        self.updated=None;self.packet=None;self.reason='等待 PICO 全身数据';self.regressed=False
        self.thread=threading.Thread(target=self.run,daemon=True)
    def invalidate(self,reason):
        with self.lock:
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
                self.packet=pack_pose_message(fields,version=3);self.reason='SONIC SMPL · 50 Hz · 5 帧窗口'
    def snapshot(self):
        with self.lock:
            fresh=self.updated is not None and 0<=self.clock()-self.updated<=.25
            return dict(ready=bool(fresh and self.packet),reason=self.reason if fresh else '等待新鲜 PICO 身体数据',packet=self.packet if fresh else None)
    def run(self):
        while not self.stop.is_set():
            try:self.feed(json.loads(self.path.read_text()))
            except (OSError,ValueError,TypeError,KeyError,AttributeError):self.invalidate('PICO 数据读取或映射失败')
            wait_event(self.stop, .01)
