"""Bounded, best-effort diagnostic journal, never on the motor producer thread."""
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import uuid
from .spikes import SpikeRecorder


class Audit:
    def __init__(self, path, capacity=4096, max_bytes=8*1024*1024, *,
                 start_thread=True, status_callback=None, owner_pid=None, receiver_pid=None):
        self.path=Path(path); self.max_bytes=max_bytes
        self.queue=queue.Queue(maxsize=capacity); self.dropped=0; self.error=''
        self.latest_incident = None
        self.spikes = SpikeRecorder(self.path.parent)
        self.status_callback = status_callback
        self.owner_pid = owner_pid
        self.receiver_pid = receiver_pid or os.getpid()
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True)
        if start_thread:self.thread.start()

    def emit(self, stage, **fields):
        if self.error:return
        try:self.queue.put_nowait(dict(stage=stage,at_ns=time.monotonic_ns(),**fields))
        except queue.Full:self.dropped+=1

    def _run(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('a',buffering=65536) as initial:
                out=initial; size=self.path.stat().st_size; flushed=time.monotonic()
                try:
                    while not self.stop.is_set() or not self.queue.empty():
                        if self.owner_pid is not None and os.getppid()!=self.owner_pid:break
                        if self.status_callback:self.status_callback(self)
                        try:row=self.queue.get(timeout=.05)
                        except queue.Empty:
                            out.flush()
                            if self.spikes.active and time.monotonic_ns()>self.spikes.active['deadline_ns']:
                                self.spikes.finish(True)
                            continue
                        if row is None:break  # FIFO shutdown barrier for the process writer.
                        self.dropped=max(self.dropped,row.get('audit_drops',0))
                        row['audit_drops']=self.dropped
                        if row.get('stage') == 'receiver_loop':
                            # Sample Linux scheduling counters on the journal
                            # thread, never add /proc I/O to the receiver. These
                            # are cumulative counters at this later sample time;
                            # retain audit lag so it cannot impersonate the exact
                            # start/end of a processing window.
                            sampled = time.monotonic_ns()
                            scheduler = dict(sampled_ns=sampled,
                                audit_lag_ns=max(0,sampled-row['at_ns']),
                                audit_queue_depth=self.queue.qsize())
                            try:
                                tid=row['thread_id']
                                if type(tid) is not int or tid <= 0: raise ValueError('Invalid receiver thread')
                                base=Path('/proc',str(self.receiver_pid))
                                values=[int(v) for v in (base/'task'/str(tid)/'schedstat').read_text().split()]
                                scheduler.update(runtime_ns=values[0],runqueue_ns=values[1],timeslices=values[2])
                                for line in (base/'status').read_text().splitlines():
                                    if line.startswith('VmRSS:'):scheduler['receiver_rss_kb']=int(line.split()[1])
                            except (OSError,ValueError,KeyError,IndexError) as exc:
                                scheduler['error']=type(exc).__name__+': '+str(exc)[:160]
                            row['scheduler']=scheduler
                        line=json.dumps(row,separators=(',',':'))+'\n'
                        if size+len(line)>self.max_bytes:
                            out.close();os.replace(self.path,str(self.path)+'.1')
                            out=self.path.open('w',buffering=65536);size=0
                        out.write(line);size+=len(line)
                        self.spikes.observe(row, line)
                        gap = row.get('stage')=='join_gap' and (row.get('consumption') or {}).get('reason') != 'superseded_before_commit'
                        if gap or (row.get('stage')=='inference_transition' and row.get('gap_ns',0)>60000000):
                            out.flush()
                            name = row.get('diagnostic_id') or uuid.uuid4().hex
                            folder = self.path.parent/'capture-incidents'/name
                            self.latest_incident = dict(id=name, state='saving')
                            try:
                                folder.mkdir(parents=True, exist_ok=False)
                                errors=[]
                                for source in self.path.parent.glob('consumption-*.jsonl*'):
                                    if source.name.endswith(('.jsonl','.jsonl.1')):
                                        try:
                                            target=folder/source.name
                                            shutil.copyfile(source,target)
                                            with target.open('rb') as f:os.fsync(f.fileno())
                                        except OSError as exc:errors.append(dict(file=source.name,error=str(exc)))
                                metadata=dict(event=row,copy_errors=errors)
                                with (folder/'snapshot.json').open('w') as f:
                                    json.dump(metadata,f);f.flush();os.fsync(f.fileno())
                                fd=os.open(folder,os.O_RDONLY)
                                try:os.fsync(fd)
                                finally:os.close(fd)
                                self.latest_incident=dict(id=name,state='failed' if errors else 'saved',errors=errors)
                            except Exception as exc:
                                self.latest_incident=dict(id=name,state='failed',error=str(exc))
                        if time.monotonic()-flushed>=.1:out.flush();flushed=time.monotonic()
                finally:
                    self.spikes.finish(False)
                    out.close()
        except Exception as exc:self.error=type(exc).__name__+': '+str(exc)
        finally:
            if self.status_callback:self.status_callback(self)

    def close(self):
        self.stop.set();self.thread.join(timeout=1)
