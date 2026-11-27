"""Compact LeRobot tokens with live simulated history; no physical transport."""
import math
from ..contracts import fingerprint
from ..dataset_io import digest
from ..layout import G1_JOINT_NAMES
from ..replay import validate_plan
from .sonic_simulation import Decoder, rollout


def run_simulation(plan, config, output, video=True):
    import numpy as np
    validate_plan(plan)
    spec=plan['controller_spec']
    if spec['backend_id']!='sonic' or spec['schema_version']!=2:raise ValueError('expected native SONIC recording')
    if spec['period_ns']!=20_000_000:raise ValueError('SONIC rollout currently requires 50 Hz nominal rate')
    if digest(config['decoder_model'])['sha256']!=spec['artifacts']['decoder_onnx_cache_validated']:
        raise ValueError('decoder ONNX does not match native cache-validated identity')
    if spec['loaded_parameters'].get('is_using_encoder'):
        if digest(config['encoder_model'])['sha256']!=spec['artifacts']['encoder_onnx_cache_validated']:
            raise ValueError('encoder identity mismatch')
    decoder=Decoder(config);p=decoder.params;loaded=spec['loaded_parameters']
    for recorded,local in [('default_angles','default_angles'),('action_scale','g1_action_scale'),
                           ('kp','kps'),('kd','kds'),('mujoco_to_isaaclab','mujoco_to_isaaclab'),
                           ('isaaclab_to_mujoco','isaaclab_to_mujoco')]:
        # Native MotorCommand stores gains as float32; compare that exact cast,
        # rather than widening tolerances for unrelated parameter mismatches.
        expected_value=p[local].astype(np.float32).astype(float) if recorded in ('kp','kd') else p[local]
        if recorded not in loaded or np.asarray(loaded[recorded]).shape!=p[local].shape or not np.allclose(loaded[recorded],expected_value,rtol=0,atol=1e-7):
            raise ValueError('loaded SONIC parameter mismatch: '+recorded)
        if recorded in ('kp','kd'):p[local]=np.asarray(loaded[recorded])
    names={};expected=[]
    for key,n in decoder.fields:
        name='token_state' if key=='token' else 'his_'+key+'_'+str(n)+'frame_step1'
        expected.append(name)
        if key!='token':
            field=spec['state'].get(name)
            if not field or field['shape'][0]!=n:raise ValueError('decoder history layout mismatch')
            names[key]=name
            # Native contract uses hardware names; MuJoCo adds the _joint suffix.
            if 'joint_names' in field and field['joint_names']!=[G1_JOINT_NAMES[int(i)][:-6] for i in p['mujoco_to_isaaclab']]:
                raise ValueError('recorded policy joint ordering mismatch')
    if expected!=[r['name'] for r in spec['decoder_input_layout']]:raise ValueError('decoder input order mismatch')
    if len({spec['history']['fields'][name]['length'] for name in names.values()})!=1:
        raise ValueError('SONIC simulator currently requires equal history lengths')
    # Compact capture stores physical state and token, not decoder history/actions.
    # Never invent history for recorded-action equivalence checks.
    first=plan['frames'][0]['sample'];state=first['state']
    q=np.asarray(state['body_q']);dq=np.asarray(state['body_dq'])
    gravity=np.asarray(state['gravity']);norm=np.linalg.norm(gravity)
    if not .95<=norm<=1.05:raise ValueError('invalid recorded gravity')
    g=gravity/norm;down=np.array([0.,0.,-1.]);quat=np.r_[1+np.dot(g,down),np.cross(g,down)]
    if np.linalg.norm(quat)<1e-6:raise ValueError('inverted initial orientation')
    quat/=np.linalg.norm(quat)
    # Rollout builds history from physics, including the entry transition.
    numeric=dict(episode=plan['episode'],segment_index=plan['segment_index'],
                 model_hashes={'decoder':spec['artifacts']['decoder_onnx_cache_validated']},
                 initial=dict(q=q.tolist(),dq=dq.tolist(),quat_wxyz=quat.tolist()),
                 frames=[dict(offset_ns=f['offset_ns'],source_index=f['sample']['index'],token=f['sample']['reference']['token_state']) for f in plan['frames']])
    return rollout(numeric,config,output,video=video,decoder=decoder,extra_report=dict(
        schema_version=2,plan_sha256=fingerprint(plan),contract_sha256=plan['contract_sha256'],
        test_fixture=spec.get('test_fixture',False),physical_replay_allowed=False,
        decoder_check=dict(performed=False,reason='compact recording omits historical decoder input and raw action'),
        initialization='default standing joints; zero velocity; upright base; live simulated history; transition to first reference before playback',
        limitations='CPU ONNX + PD reference rollout; not native TRT/protected output or native blend/filter equivalence'))
