"""SONIC native input/commit validation. No execution or compatibility conversion."""
from copy import deepcopy
from ..contracts import fields, integer, text, sha256, fingerprint, tensor

BOUNDARY = 'protected_writer_dds_publish_returned_not_motor_execution'
TOPIC = b'oml/controller/v2-draft'


def validate_spec(value):
    s = deepcopy(value)
    if (s.get('schema') != 'onboardmimic.lab.controller-spec' or type(s.get('schema_version')) is not int
            or s['schema_version'] != 2 or s.get('backend_id') != 'sonic'
            or s.get('protocol_status') != 'proposal'):
        raise ValueError('expected SONIC native v2 proposal')
    for name in ('robot_id','execution_id','reference_space'): text(s[name],name)
    if s['clock'] != 'same_host_monotonic_ns': raise ValueError('shared OS clock required')
    integer(s['period_ns'],'period',1);integer(s['max_gap_ns'],'gap',s['period_ns'])
    if not s['artifacts']: raise ValueError('model identity required')
    for v in s['artifacts'].values(): sha256(v)
    for key in ('state','reference','control_output'):
        fields(s[key])
        if any(f.get('dtype') != 'float32' for f in s[key].values()):raise ValueError('native tensors must declare float32')
    if set(s['state']) & set(s['reference']):raise ValueError('ambiguous state/reference name')
    if set(s['control_output']) != {'q','dq','kp','kd','tau'} or any(f['shape'] != [29] for f in s['control_output'].values()):raise ValueError('expected G1 body control output')
    if set(s['reference']) != {'token_state'} or s['reference']['token_state']['shape'] != [64]:
        raise ValueError('SONIC decoder reference must be token64')
    h=s['history']
    if h['sampling']!='per_field_actual' or h['reset']!='required' or set(h['fields'])!=set(s['state']):
        raise ValueError('invalid per-field history')
    for name,f in h['fields'].items():
        integer(f['length'],'length',1);integer(f['stride'],'stride',1);integer(f['nominal_period_ns'],'period',1)
        if f['order']!='oldest_first' or len(s['state'][name]['shape'])!=2 or s['state'][name]['shape'][0]!=f['length']:
            raise ValueError('history shape/order mismatch')
        for key in ('timestamp_semantic','source_timestamp_semantic','selection'):text(f[key],key)
    if s['control_output_boundary']!=BOUNDARY: raise ValueError('unknown output boundary')
    diagnostics=s['diagnostic_tensors']
    if set(diagnostics)!= {'decoder_input','raw_policy_action','encoder_input'}:raise ValueError('invalid diagnostics')
    for name,f in diagnostics.items():
        if f is not None:
            fields({name:f})
            if f.get('dtype') != 'float32':raise ValueError('native diagnostics must declare float32')
    if diagnostics['decoder_input'] is None or diagnostics['raw_policy_action'] is None:raise ValueError('decoder diagnostics required')
    if diagnostics['raw_policy_action']['shape'] != [29]:raise ValueError('expected 29D raw action')
    offset=0;seen=set();available=dict(s['state'],**s['reference'])
    for item in s['decoder_input_layout']:
        name=item['name'];integer(item['offset'],'offset');integer(item['dimension'],'dimension',1)
        if name not in available or name in seen or item['offset']!=offset:raise ValueError('invalid decoder layout')
        size=1
        for d in available[name]['shape']:size*=d
        if size!=item['dimension']:raise ValueError('layout dimension mismatch')
        seen.add(name);offset+=size
    if seen!=set(available) or diagnostics['decoder_input']['shape']!=[offset]:raise ValueError('incomplete decoder layout')
    policy=s.get('recording_policy')
    if policy is None:
        if s['record_modes']!=['pose']:raise ValueError('legacy native capture requires Pose')
    elif policy != 'actual_decoder_consumption' or s['record_modes'] != ['planner','pose','replay']:
        raise ValueError('invalid native recording policy')
    fingerprint(s)
    return s


def flatten(value):
    if isinstance(value,(list,tuple)):
        for x in value:yield from flatten(x)
    else:yield value


def validate_history_and_commit(sample,spec):
    for key in ('state_history_ns','state_history_indices','state_history_sources'):
        if not isinstance(sample[key],dict) or set(sample[key])!=set(spec['state']):raise ValueError('history fields mismatch')
    anchor=sample['timestamp_monotonic_ns']
    for name,h in spec['history']['fields'].items():
        stamps=sample['state_history_ns'][name];indices=sample['state_history_indices'][name]
        for values,minimum in ((stamps,1),(indices,0)):
            if not isinstance(values,list) or len(values)!=h['length']:raise ValueError('history length mismatch')
            for v in values:integer(v,'history time/index',minimum)
            if any(b<=a for a,b in zip(values,values[1:])):raise ValueError('history must increase')
        if not 0<=anchor-stamps[-1]<=spec['max_gap_ns']:raise ValueError('future/stale history')
        sources=sample['state_history_sources'][name]
        if set(sources)!={'low_state_received_ns','torso_imu_received_ns'}:raise ValueError('history source fields mismatch')
        for values in sources.values():
            if not isinstance(values,list) or len(values)!=len(stamps):raise ValueError('source length mismatch')
            for v,t in zip(values,stamps):
                integer(v,'source time',1)
                if v>t:raise ValueError('source received after assembly')
            if any(b<a for a,b in zip(values,values[1:])):raise ValueError('source clock regressed')
    bypass_encoder=False
    if spec.get('recording_policy') == 'actual_decoder_consumption':
        mode=sample.get('native_mode');source=sample.get('token_source')
        if type(mode) is not int or source not in ('encoder','external','replay'):
            raise ValueError('missing native token provenance')
        replay=sample.get('replay')
        if source=='replay':
            if mode!=3 or not isinstance(replay,dict) or type(replay.get('scheduled_frame')) is not bool:
                raise ValueError('invalid replay provenance')
            integer(replay['run'],'replay run',1);integer(replay['frame'],'replay frame')
        elif mode==3 or replay is not None:raise ValueError('inconsistent replay provenance')
        if source=='encoder' and spec['diagnostic_tensors']['encoder_input'] is None:
            raise ValueError('encoder source without encoder')
        if source=='external' and spec['diagnostic_tensors']['encoder_input'] is not None:
            raise ValueError('external token cannot impersonate loaded encoder')
        bypass_encoder=source!='encoder'
    for name,field in spec['diagnostic_tensors'].items():
        if name=='encoder_input' and bypass_encoder:
            if sample[name] is not None:raise ValueError('unused encoder input must be null')
            continue
        if field is None:
            if sample[name] is not None:raise ValueError('unexpected diagnostic tensor')
        else:tensor(sample[name],field['shape'])
    reconstructed=[]
    for item in spec['decoder_input_layout']:
        name=item['name'];value=sample['reference'][name] if name in spec['reference'] else sample['state'][name]
        reconstructed.extend(flatten(value))
    if reconstructed!=sample['decoder_input']:raise ValueError('decoder input differs from recorded state/reference')
    commit=sample['output_commit']
    if commit['boundary']!=spec['control_output_boundary']:raise ValueError('output boundary mismatch')
    for key in ('stream','sequence','generation'):integer(commit[key],key)
    times=[anchor,sample['inference_finished_monotonic_ns'],commit['timestamp_monotonic_ns'],sample['published_monotonic_ns']]
    for t in times:integer(t,'execution timestamp',1)
    if any(b<a for a,b in zip(times,times[1:])) or times[-1]-anchor>250_000_000:raise ValueError('invalid execution timing')
    enc=sample['encoder_input_monotonic_ns']
    if spec['diagnostic_tensors']['encoder_input'] is None or bypass_encoder:
        if enc is not None:raise ValueError('encoder timestamp without input')
    else:
        integer(enc,'encoder time',1)
        if not 0<=anchor-enc<=spec['max_gap_ns']:raise ValueError('invalid encoder timestamp')
    if sample['reference_evidence']=='consumed' and 'control_output' not in sample:raise ValueError('consumed sample requires output')
    return sample
