"""Receive/record XR tracking only. No robot SDK, control client, publisher, or actuator output."""
import argparse
from collections import deque
import importlib
import json
import math
import os
from pathlib import Path
import time

ROLES=('left_wrist','right_wrist','waist','left_ankle','right_ankle')

def pose(value):
    if not isinstance(value,(list,tuple)) or len(value)!=7:raise ValueError('pose must contain xyz + xyzw')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):raise ValueError('non-finite or invalid pose')
    result=list(map(float,value));norm=math.sqrt(sum(x*x for x in result[3:]))
    if not .8<=norm<=1.2:raise ValueError('invalid quaternion norm')
    if max(map(abs,result[:3]))>100:raise ValueError('position outside diagnostic bounds')
    return result[:3]+[q/norm for q in result[3:]]

class Stream:
    def __init__(self):
        self.stamp=None;self.at=None;self.times=deque(maxlen=121);self.payload=None;self.error='no sample';self.regressions=0;self.clock_fault=False
    def update(self, stamp, payload, now):
        if type(stamp) is not int or stamp<=0:raise ValueError('missing source timestamp')
        if self.stamp is not None and stamp<self.stamp:
            self.clock_fault=True;self.regressions+=1;self.error='source clock regressed; restart receiver after reconnect';self.payload=None
            return
        if self.clock_fault:return
        if stamp==self.stamp:return  # Polling a cached SDK value is NOT a new tracking frame.
        self.stamp=stamp;self.at=now;self.payload=payload;self.error='';self.times.append(now)
    def status(self,now,max_age):
        age=None if self.at is None else max(0.,now-self.at)
        fresh=age is not None and age<=max_age and not self.error and not self.clock_fault
        times=[t for t in self.times if now-t<=2]
        fps=(len(times)-1)/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] else 0.
        gaps=[(b-a)*1000 for a,b in zip(times,times[1:])]
        return dict(fresh=fresh,age_ms=None if age is None else round(age*1000,2),
                    unique_frame_hz=round(fps,2),max_observed_gap_ms=round(max(gaps,default=0),2),
                    source_timestamp_ns=self.stamp,error=self.error or ('' if fresh else 'stale'),
                    payload=self.payload if fresh else None)

class Monitor:
    def __init__(self,config):
        self.roles=config.get('roles',{})
        if set(self.roles)!=set(ROLES) or any(not isinstance(v,str) for v in self.roles.values()):raise ValueError('five explicit tracker roles required')
        assigned=[v for v in self.roles.values() if v]
        if len(set(assigned))!=len(assigned):raise ValueError('one serial cannot fill two roles')
        self.max_age=float(config.get('max_age_ms',250))/1000
        if not .02<=self.max_age<=1:raise ValueError('invalid freshness threshold')
        self.streams={name:Stream() for name in ('head','body','motion')}
    def ingest(self,sample,now):
        for name,stream in self.streams.items():
            try:
                x=sample.get(name)
                if not isinstance(x,dict):raise ValueError('stream unavailable')
                if name=='head':payload=pose(x['pose'])
                elif name=='body':
                    if len(x['poses'])!=24:raise ValueError('expected 24 body joints')
                    payload=[pose(p) for p in x['poses']]
                else:
                    serials=x['serials'];poses=x['poses']
                    if len(serials)!=len(poses) or not 1<=len(serials)<=16:raise ValueError('invalid tracker count')
                    if any(not isinstance(s,str) or not s for s in serials) or len(set(serials))!=len(serials):raise ValueError('missing/duplicate tracker serial')
                    payload={s:pose(p) for s,p in zip(serials,poses)}
                stream.update(x['timestamp_ns'],payload,now)
            except (KeyError,TypeError,ValueError) as exc:
                stream.error=str(exc);stream.payload=None
        return self.status(now)
    def status(self,now):
        streams={name:s.status(now,self.max_age) for name,s in self.streams.items()}
        trackers=streams['motion']['payload'] or {}
        missing=[role for role,serial in self.roles.items() if not serial or serial not in trackers]
        return dict(mode='receive_only',robot_output_enabled=False,calibrated=False,
                    frame='PICO Unity frame; meters; quaternion xyzw; NOT robot joint targets',
                    streams=streams,visible_tracker_serials=list(trackers),missing_roles=missing,
                    role_poses={role:trackers[serial] for role,serial in self.roles.items() if serial in trackers},
                    five_tracker_capture_ready=not missing and streams['head']['fresh'],
                    body_capture_ready=streams['body']['fresh'],
                    note='Freshness is local time since device timestamp changed, not end-to-end latency.')

class SDKReader:
    def __init__(self,sdk):self.sdk=sdk
    def coherent(self,stamp,getter):
        # SDK getters lock separately. A new frame can arrive between calls;
        # retry the entire read, never attach a new timestamp to old payload.
        # Bound work; persistent races and getter failures remain invalid.
        for _ in range(3):
            a=stamp();data=getter();b=stamp()
            if a==b:return dict(timestamp_ns=a,**data)
        raise ValueError('frame changed during SDK read after 3 attempts')
    def read(self):
        s=self.sdk;sample={}
        getters={
            'head':(s.get_head_timestamp_ns,lambda:dict(pose=s.get_headset_pose())),
            'body':(s.get_body_timestamp_ns,lambda:dict(poses=s.get_body_joints_pose()) if s.is_body_data_available() else {}),
            'motion':(s.get_motion_timestamp_ns,lambda:dict(serials=s.get_motion_tracker_serial_numbers(),poses=s.get_motion_tracker_pose()))}
        for name,(stamp,getter) in getters.items():
            try:sample[name]=self.coherent(stamp,getter)
            except Exception:sample[name]=None
        return sample

def write_status(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    value=dict(value,updated_at_unix=time.time())
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');os.replace(temp,path)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path(__file__).with_name('devices.example.json'))
    p.add_argument('--source',choices=['sdk','replay'],required=True)
    p.add_argument('--replay',type=Path);p.add_argument('--record',type=Path)
    p.add_argument('--status',type=Path,default=Path('runs/pico-status.json'))
    p.add_argument('--duration',type=float,default=60,help='Seconds; 0 keeps receive-only monitoring running')
    a=p.parse_args()
    if not math.isfinite(a.duration) or not 0<=a.duration<=3600:p.error('duration must be 0..3600 seconds')
    monitor=Monitor(json.loads(a.config.read_text()));sdk=None;record=None
    try:
        if a.record:
            a.record.parent.mkdir(parents=True,exist_ok=True);record=a.record.open('x')
        if a.source=='replay':
            if not a.replay:p.error('--replay required')
            last=-1
            with a.replay.open() as f:
                for line in f:
                    x=json.loads(line);now=float(x['elapsed_s'])
                    if not math.isfinite(now) or now<last:raise ValueError('invalid replay monotonic time')
                    last=now;status=monitor.ingest(x['sample'],now)
            if last<0:raise ValueError('empty replay')
            status.update(data_source='replay',receiver_running=False)
            write_status(a.status,status)
        else:
            sdk=importlib.import_module('xrobotoolkit_sdk')
            sdk.init();reader=SDKReader(sdk);start=time.monotonic();last_save=-1
            while a.duration==0 or time.monotonic()-start<a.duration:
                now=time.monotonic()-start;sample=reader.read();status=monitor.ingest(sample,now)
                if record:record.write(json.dumps(dict(elapsed_s=now,sample=sample),allow_nan=False)+'\n')
                status.update(data_source='sdk',receiver_running=True)
                if now-last_save>=.02:write_status(a.status,status);last_save=now
                time.sleep(.01)
            # On exit don't leave a fresh-looking capture artifact.
            status=monitor.status(time.monotonic()-start+monitor.max_age+1)
            status.update(receiver_running=False,data_source='sdk');write_status(a.status,status)
        print(json.dumps({k:status[k] for k in ('mode','robot_output_enabled','missing_roles','five_tracker_capture_ready','body_capture_ready')},ensure_ascii=False))
    finally:
        if record:record.close()
        if sdk:
            status=monitor.status(time.monotonic()-start+monitor.max_age+1) if 'start' in locals() else {'mode':'receive_only','robot_output_enabled':False}
            status.update(receiver_running=False,data_source='sdk');write_status(a.status,status);sdk.close()

if __name__=='__main__':main()
