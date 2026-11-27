"""Offline serialization, decomposition, rotation and shutdown checks."""
import json,tempfile,time
from pathlib import Path
import numpy as np
from recording import Recorder,sample
with tempfile.TemporaryDirectory() as folder:
    out=Path(folder)/'test';r=Recorder(out,capacity=32,segment_bytes=2000,segments=3)
    frame=dict(tick=123,imu_state=dict(quaternion=[1,0,0,0]),motor_state=[dict(q=.2,dq=.1,tau_est=1.5,temperature=[30,32],motorstate=0) for _ in range(29)])
    command=np.tile([.3,0,2,10,2],(29,1));x=sample(time.monotonic(),frame,command,'limited_hold',emitted=False,sequence=1)
    assert np.allclose(x['estimated_pd_nm'],.8)
    assert np.allclose(x['estimated_total_target_nm'],2.8)
    r.submit(x)
    for i in range(30):
        r.submit(dict(kind='sample',sequence=i,payload=np.zeros(300)));time.sleep(.002)
    r.submit(dict(kind='fault',reason='test',joint=4,requested_nm=41));r.close()
    assert not r.error and not r.thread.is_alive()
    files=sorted(out.glob('*.jsonl'));assert len(files)<=3 and (out/'000000.jsonl').exists()
    rows=[json.loads(line) for p in files for line in p.read_text().splitlines()]
    assert any(x.get('kind')=='fault' for x in rows)
    assert json.loads((out/'manifest.json').read_text())['dropped']==0
print('recording checks passed; no hardware access')
with tempfile.TemporaryDirectory() as folder:
    r=Recorder(Path(folder)/'invalid-sensor')
    r.submit(dict(kind='fault',q=np.array([np.nan,np.inf,-np.inf])))
    r.submit(dict(kind='sample',after_fault=True));r.close()
    rows=[json.loads(line) for line in (r.directory/'000000.jsonl').read_text().splitlines()]
    assert not r.error and len(rows)==2
    assert rows[0]['q'][0]=={'invalid_float':'nan'}
print('invalid feedback retained without stopping recorder')
