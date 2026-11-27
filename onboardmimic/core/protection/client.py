"""Explicit experimental writer connection; no motor commands or WBC signaling.
The writer owns monitoring and motion, independently of this Python process.
"""
from collections import deque
import resource
import uuid
import logging
import hashlib
import json
import os
from pathlib import Path
import struct
import socket
import threading
import time

class ProtectionClient:
    def __init__(self, path):
        self._init_diagnostics()
        self.path=str(path)
        self.generation=0
        self.cached={}
        self.received=0.
        self.error='尚未连接保护执行器'
        self.identity_cache={}
        self.identity={}
        self.closed=threading.Event()
        self.thread=threading.Thread(target=self._poll,daemon=True)
        self.thread.start()

    def _init_diagnostics(self):
        self.diagnostic_lock=threading.Lock()
        self.query_history=deque(maxlen=16)
        self.last_query_failure=None

    def _request(self, action, **fields):
        began=time.monotonic_ns();cpu=time.thread_time_ns();usage=resource.getrusage(resource.RUSAGE_THREAD)
        record=dict(id=uuid.uuid4().hex,action=action,started_ns=began,phase='connect')
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
                s.settimeout(.1)
                s.connect(self.path)
                record["connected_ns"]=time.monotonic_ns();record["phase"]="peer_identity"
                peer_pid, peer_uid, _ = struct.unpack('3i', s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if peer_uid != os.getuid(): raise RuntimeError('Protection peer UID mismatch')
                record['peer_pid']=peer_pid;record['phase']='send';record['send_started_ns']=time.monotonic_ns()
                s.sendall(json.dumps(dict(protocol=1,action=action,diagnostic_id=record['id'],**fields)).encode())
                record['sent_ns']=time.monotonic_ns();record['phase']='receive'
                raw=s.recv(8192)
                record['received_ns']=time.monotonic_ns();record['phase']='decode'
                result=json.loads(raw)
            record['phase']='validate'
            if not result.get('ok'):raise RuntimeError(result.get('error','Protection request failed'))
            if result.get('protocol')!=1:raise RuntimeError('Protection protocol mismatch')
            result['bridge_pid']=peer_pid
            self.cached=result;self.received=time.monotonic();self.error=''
            record['phase']='complete'
            return result
        except (OSError,ValueError,RuntimeError) as exc:
            self.error=str(exc)
            record.update(error=type(exc).__name__+': '+str(exc),errno=getattr(exc,'errno',None))
            raise RuntimeError('保护执行器未确认：'+str(exc)) from exc
        finally:
            end_usage=resource.getrusage(resource.RUSAGE_THREAD)
            record.update(finished_ns=time.monotonic_ns(),thread_cpu_ns=time.thread_time_ns()-cpu,
                voluntary_switches=end_usage.ru_nvcsw-usage.ru_nvcsw,
                involuntary_switches=end_usage.ru_nivcsw-usage.ru_nivcsw)
            with self.diagnostic_lock:
                self.query_history.append(record)
                if 'error' in record:self.last_query_failure=record

    def _poll(self):
        logged=None;last_log=0.
        while not self.closed.is_set():
            try:
                status=self._request('status')
                # Hash only on this diagnostic thread, never under the gateway
                # lock or in motor/control request handling.
                self.identity=self._runtime_identity(status)
            except (RuntimeError,OSError,ValueError,KeyError,TypeError,IndexError):
                self.identity={}
            with self.diagnostic_lock:
                failure=self.last_query_failure
                history=list(self.query_history)
            now=time.monotonic()
            if failure and failure['id']!=logged and now-last_log>=1.:
                logging.getLogger('runtime_audit').warning(json.dumps(dict(event='protection_query_failure',last_failure=failure,recent=history)))
                logged=failure['id'];last_log=now
            self.closed.wait(.1)

    def _executable_identity(self, role, pid):
        if type(pid) is not int or pid <= 1: raise ValueError('Missing process identity')
        proc=Path('/proc')/str(pid)
        def key():
            # starttime follows the parenthesized comm field; names may contain spaces.
            start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            st=(proc/'exe').stat()
            return (pid,start,st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
        before=key()
        cached=self.identity_cache.get(role)
        if cached and cached[0]==before:return cached[1]
        h=hashlib.sha256()
        with (proc/'exe').open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        if key()!=before:raise ValueError('Process changed during identity check')
        self.identity_cache[role]=(before,h.hexdigest())
        return h.hexdigest()

    def _runtime_identity(self, status):
        return dict(wbc_pid=status['wbc_pid'],bridge_pid=status['bridge_pid'],
            generation=status['generation'],
            native_executable=self._executable_identity('native',status['wbc_pid']),
            bridge_executable=self._executable_identity('bridge',status['bridge_pid']))

    def reset(self):
        result=self._request('reset')
        self.generation=result['generation']

    def arm(self,pid):
        status=self._request('status')
        if status['generation']!=self.generation:raise RuntimeError('保护执行器会话不一致')
        return self._request('arm',generation=self.generation,stream=status['stream'],pid=pid)

    def cancel(self):
        # Explicit estop must also be able to cancel a session whose runtime died.
        status=self._request('status')
        return self._request('cancel',generation=status['generation'])

    def snapshot(self):
        value=dict(self.cached)
        with self.diagnostic_lock:
            value['query_diagnostics']=dict(recent=list(self.query_history),last_failure=self.last_query_failure,server=self.cached.get('query_diagnostics'))
        value['recording_identity']=dict(self.identity)
        value.update(configured=True,fresh=time.monotonic()-self.received<.3,error=self.error)
        return value

    def close(self):
        self.closed.set();self.thread.join(timeout=.3)
