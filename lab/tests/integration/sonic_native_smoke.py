"""Synthetic native-protocol capture -> save -> real CPU ONNX/MuJoCo.
Usage: python tests/integration/sonic_native_smoke.py SIM_CONFIG NEW_OUTPUT
No OM/robot connection. Fixture is explicitly not physical evidence.
"""
import json
from copy import deepcopy
from pathlib import Path
import sys
import time
import numpy as np
from onboardmimic_lab.contracts import fingerprint
from onboardmimic_lab.collection import Collector
from onboardmimic_lab.dataset import digest,read_aligned
from onboardmimic_lab.replay import prepare_plan
from onboardmimic_lab.simulation import run_simulation
from onboardmimic_lab.settings import read_settings
from onboardmimic_lab.adapters.sonic_simulation import Decoder
from onboardmimic_lab.layout import G1_JOINT_NAMES
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_collection import make_video
from export_test_helpers import wait_export
from test_sonic_native import fixtures,shifted


def main():
    config=read_settings(Path(sys.argv[1]),('decoder_model','observation_config','policy_parameters','scene'))
    out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=False)
    spec,template=fixtures();decoder=Decoder(config);p=decoder.params
    mode_neutral='--mode-neutral' in sys.argv[3:]
    if mode_neutral:
        spec.update(recording_policy='actual_decoder_consumption',record_modes=['planner','pose','replay'])
    for source,target in [('default_angles','default_angles'),('g1_action_scale','action_scale'),('kps','kp'),('kds','kd'),('mujoco_to_isaaclab','mujoco_to_isaaclab'),('isaaclab_to_mujoco','isaaclab_to_mujoco')]:spec['loaded_parameters'][target]=p[source].tolist()
    spec['artifacts']['decoder_onnx_cache_validated']=digest(config['decoder_model'])['sha256']
    spec['artifacts']['loaded_parameters']=fingerprint(spec['loaded_parameters'])
    for key,field in spec['state'].items():
        width=field['shape'][1]
        template['state'][key]=[[0.]*width for _ in range(10)]
        if 'gravity_dir' in key:template['state'][key]=[[0.,0.,-1.] for _ in range(10)]
        if 'joint_names' in field:field['joint_names']=[G1_JOINT_NAMES[int(i)] for i in p['mujoco_to_isaaclab']]
    for field in spec['control_output'].values():field['joint_names']=list(G1_JOINT_NAMES)
    spec['diagnostic_tensors']['raw_policy_action']['joint_names']=[G1_JOINT_NAMES[int(i)] for i in p['mujoco_to_isaaclab']]
    template['reference']['token_state']=[0.]*64
    flat=[]
    for item in spec['decoder_input_layout']:
        if item['name']=='token_state':flat.extend(template['reference']['token_state'])
        else:flat.extend(np.asarray(template['state'][item['name']]).reshape(-1).tolist())
    template['decoder_input']=flat
    action=decoder.session.run([decoder.output_name],{decoder.input_name:np.asarray(flat,dtype=np.float32).reshape(1,-1)})[0].reshape(29)
    template['raw_policy_action']=action.tolist()
    template['control_output']={'q':(p['default_angles']+action[p['isaaclab_to_mujoco']]*p['g1_action_scale']).tolist(),
        'dq':[0.]*29,'tau':[0.]*29,'kp':p['kps'].tolist(),'kd':p['kds'].tolist()}
    template['contract_sha256']=fingerprint(spec)
    video=make_video(out/'fixture.ts')
    current=dict(mode='planner',switching=False)
    def worker(raw):
        raw.video(video)
        for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
            s=shifted(template,i)
            if mode_neutral:
                mode=(1,2)[(i//5)%2]
                current.update(mode='pose' if mode==2 else 'planner',switching=i%5<2)
                s.update(native_mode=mode,token_source='encoder' if s['encoder_input'] is not None else 'external',replay=None)
                # Different synthetic references per tick; actual decoder output,
                # never a claim that a live Planner/encoder produced these tokens.
                token=(.01*np.sin(i+np.arange(64)/64)).astype(np.float32).tolist()
                s['reference']['token_state']=token
                field=next(f for f in spec['decoder_input_layout'] if f['name']=='token_state')
                s['decoder_input'][field['offset']:field['offset']+64]=token
                actual=decoder.session.run([decoder.output_name],{decoder.input_name:np.asarray(s['decoder_input'],dtype=np.float32).reshape(1,-1)})[0].reshape(29)
                s['raw_policy_action']=actual.tolist()
                s['control_output']['q']=(p['default_angles']+actual[p['isaaclab_to_mujoco']]*p['g1_action_scale']).tolist()
            raw.controller(s,s['published_monotonic_ns'])
            raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=s['timestamp_monotonic_ns']))
        collector.stop_event.wait(10)
    runtime=lambda:dict(state='active',input_mode=current['mode'] if mode_neutral else 'pose',
                        mode_switch_pending=current['switching'],pose_input_ready=False,
                        lab=dict(record_ready=True,contract_sha256=fingerprint(spec),session_id=template['session_id']))
    collector=Collector(dict(capture_adapter='sonic-native-v2',root=str(out/'data'),controller_spec=spec),runtime,worker=worker)
    def wait(state):
        end=time.monotonic()+15
        while time.monotonic()<end:
            s=collector.status()
            if s['state']==state:return s
            if s['error']:raise RuntimeError(s['error'])
            time.sleep(.01)
        raise RuntimeError(str(collector.status()))
    try:
        collector.command(dict(action='start',task='synthetic SONIC input fixture'))
        collector.command(dict(action='stop'));review=wait('review')
        collector.command(dict(action='save'));wait('saved')
        episode=out/'data/episodes'/review['episode_id'];wait_export(episode)
        metadata,rows=read_aligned(episode)
        plan=prepare_plan(episode);(out/'plan.json').write_text(json.dumps(plan))
        if mode_neutral:
            assert {f['sample']['native_mode'] for f in plan['frames']}=={1,2}
            assert len({f['sample']['session_id'] for f in plan['frames']})==1
        bad=deepcopy(plan);bad['frames'][0]['sample']['raw_policy_action'][0]+=1
        try:run_simulation(bad,config,out/'must-not-run',video=False)
        except ValueError as e:assert 'recomputation' in str(e)
        else:raise AssertionError('incorrect raw action accepted')
        bad=deepcopy(plan);bad['controller_spec']['artifacts']['decoder_onnx_cache_validated']='0'*64
        bad['contract_sha256']=fingerprint(bad['controller_spec'])
        for frame in bad['frames']:frame['sample']['contract_sha256']=bad['contract_sha256']
        try:run_simulation(bad,config,out/'wrong-model',video=False)
        except ValueError as e:assert 'identity' in str(e)
        else:raise AssertionError('incorrect model accepted')
        result=run_simulation(plan,config,out/'simulation',video=False)
        assert len(rows)==20 and result['frames_completed']==20
        assert result['decoder_check']['max_abs_error']<1e-6
        (out/'result.json').write_text(json.dumps(dict(synthetic=True,physical_motion=False,rows=len(rows),simulation=result),indent=2))
        print(json.dumps(dict(rows=len(rows),frames=result['frames_completed'],decoder_check=result['decoder_check'],physical_motion=False)))
    finally:collector.close()


if __name__=='__main__':main()
