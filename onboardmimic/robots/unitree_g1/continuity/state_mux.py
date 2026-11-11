"""Fresh-state failover. No motor output or state extrapolation.

Two independently running DDS readers use one host's monotonic clock. A source
must have both fresh topics. Sticky failover avoids switching back and forth.
Stopping all readers MUST stop output: never refresh timestamps on old samples.
"""
from dataclasses import dataclass
import math

TOPICS = ('rt/lowstate', 'rt/secondary_imu')

@dataclass(frozen=True)
class Frame:
    source: str
    topic: str
    session: str
    sequence: int
    captured: float
    payload: bytes
    tick: int = None

class StateMux:
    def __init__(self, max_age=.05):
        if not 0 < max_age <= .1: raise ValueError('invalid freshness budget')
        self.max_age = max_age
        self.frames = {}
        self.sessions = {}
        self.retired = set()
        self.active = None
        self.sent = {}
        self.last_tick = None
        self.last_capture = {}
        self.events = []
        self.rejected = 0
        self.generation = 0

    def ingest(self, f, now):
        if (f.source not in ('primary','standby') or f.topic not in TOPICS
            or not isinstance(f.sequence,int) or f.sequence < 0
            or not isinstance(f.payload,bytes) or not f.payload or len(f.payload)>65536
            or not isinstance(f.session,str) or not f.session or len(f.session)>64
            or not math.isfinite(f.captured) or not 0 <= now-f.captured <= self.max_age
            or (f.source,f.session) in self.retired):
            self.rejected+=1;return False
        old_session=self.sessions.get(f.source)
        if old_session and old_session!=f.session:
            self.retired.add((f.source,old_session))
            for topic in TOPICS:self.frames.pop((f.source,topic),None)
        self.sessions[f.source]=f.session
        old=self.frames.get((f.source,f.topic))
        if old and (f.sequence<=old.sequence or f.captured<=old.captured):
            self.rejected+=1;return False
        if f.topic=='rt/lowstate':
            if type(f.tick)!=int or not 0<=f.tick<2**32:
                self.rejected+=1;return False
            if old and not 0 < (f.tick-old.tick) % 2**32 < 2**31:
                self.rejected+=1;return False
        self.frames[f.source,f.topic]=f
        return True

    def select(self,now):
        def ready(s):
            return all((s,t) in self.frames and 0<=now-self.frames[s,t].captured<=self.max_age for t in TOPICS)
        selected=self.active if self.active and ready(self.active) else next((s for s in ('primary','standby') if ready(s)),None)
        if selected!=self.active:
            self.generation+=1
            self.events.append(dict(generation=self.generation,at=now,previous=self.active,source=selected))
            self.events=self.events[-32:]
            self.active=selected
        if not selected:return []
        out=[]
        for t in TOPICS:
            f=self.frames[selected,t];key=(f.source,f.session,f.sequence)
            if self.sent.get(t)==key:continue
            if f.captured<=self.last_capture.get(t,-1):continue
            if t=='rt/lowstate' and self.last_tick is not None and not 0<(f.tick-self.last_tick)%2**32<2**31:continue
            out.append(f);self.sent[t]=key;self.last_capture[t]=f.captured
            if t=='rt/lowstate':self.last_tick=f.tick
        return out
