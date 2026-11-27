"""Single UDP owner: idle video health and bounded recording delivery."""
from collections import deque
import socket
import threading
import time
from .video_timing import MpegTsPtsExtractor

class CameraIngress:
    def __init__(self, port):
        self.port=port;self.lock=threading.Lock();self.stop=threading.Event()
        self.queue=deque();self.bytes=0;self.active=False;self.error='';self.last=None
        self.parser=MpegTsPtsExtractor();self.capture_started_ns=0
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
            s.bind(('127.0.0.1',self.port));s.settimeout(.1)
            while not self.stop.is_set():
                try:payload,_=s.recvfrom(65535)
                except socket.timeout:continue
                mono,wall=time.monotonic_ns(),time.time_ns()
                with self.lock:
                    # begin() resets parser and delivery together. A packet that
                    # arrived before that boundary cannot seed the new recording.
                    if self.active and mono < self.capture_started_ns:continue
                    frames=self.parser.feed(payload)
                    if frames:self.last=mono
                    if self.active and not self.error:
                        if self.bytes+len(payload)>8*1024*1024:
                            self.error='camera recording buffer overflow'
                        else:
                            self.queue.append((payload,mono,wall,frames));self.bytes+=len(payload)
        except Exception as exc:
            with self.lock:self.error=type(exc).__name__+': '+str(exc)
        finally:s.close()

    def status(self):
        with self.lock:
            age=None if self.last is None else (time.monotonic_ns()-self.last)/1e6
            return dict(ready=not self.error and age is not None and 0<=age<=500,
                        age_ms=age,error=self.error,port=self.port,observed=self.last is not None)

    def begin(self):
        with self.lock:
            if self.error:raise ValueError('camera unavailable: '+self.error)
            self.queue.clear();self.bytes=0
            self.parser=MpegTsPtsExtractor()
            self.capture_started_ns=time.monotonic_ns();self.active=True

    def drain(self):
        with self.lock:
            if self.error:raise ValueError(self.error)
            result=[]
            for _ in range(min(64,len(self.queue))):
                item=self.queue.popleft();self.bytes-=len(item[0]);result.append(item)
            return result

    def end(self):
        with self.lock:self.active=False;self.queue.clear();self.bytes=0

    def close(self):
        self.stop.set();self.thread.join(timeout=2)
