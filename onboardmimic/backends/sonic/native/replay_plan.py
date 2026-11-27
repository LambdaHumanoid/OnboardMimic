"""OM admission for OML reference-plan v2; never supplies recorded feedback to WBC."""
from copy import deepcopy
from .consumption import digest, integer
from onboardmimic.core.execution.software import fingerprint


def compatible(recorded, live):
    # Executable hashes are provenance, not model identity: an updated executor
    # can play a recording from an earlier binary with identical model/config.
    for key in ('robot_id','backend_id','execution_id','reference_space','clock','period_ns',
                'max_gap_ns','state','reference','control_output','history','diagnostic_tensors',
                'decoder_input_layout','loaded_parameters','encoder_layout','control_output_boundary'):
        if recorded[key] != live[key]: raise ValueError('Controller contract mismatch: '+key)
    for key, value in recorded['artifacts'].items():
        if key not in ('native_executable','bridge_executable') and live['artifacts'].get(key) != value:
            raise ValueError('Loaded model/config mismatch: '+key)


def validate(plan):
    if plan.get('schema') != 'onboardmimic.lab.reference-plan' or type(plan.get('schema_version')) is not int or plan['schema_version'] != 2:
        raise ValueError('Only reference-plan v2 is supported')
    s=plan['controller_spec']
    if (s.get('schema') != 'onboardmimic.lab.controller-spec' or s.get('schema_version') != 2
        or s.get('backend_id') != 'sonic' or s.get('robot_id') != 'unitree_g1'
        or s.get('execution_id') != 'sonic.native.v2-draft' or s.get('reference_space') != 'sonic.decoder.token.v1'
        or s.get('physical_source') is not True or s.get('test_fixture') is not False):
        raise ValueError('Physical SONIC v2 recording required')
    if s['clock'] != 'same_host_monotonic_ns' or s['period_ns'] != 20000000 or s['max_gap_ns'] != 60000000:
        raise ValueError('Unsupported SONIC timing')
    if s['diagnostic_tensors']['decoder_input']['shape'] != [994] or len(s['history']['fields'])!=5 or any(h['length']!=10 or h['stride']!=1 for h in s['history']['fields'].values()):
        raise ValueError('First SONIC replay supports the current 994D / 10-frame live history layout only')
    if s['reference']['token_state']['shape'] != [64] or set(s['reference']) != {'token_state'}: raise ValueError('token64 required')
    for value in s['artifacts'].values(): digest(value)
    for key in ('decoder_loaded_trt','decoder_onnx_cache_validated','loaded_parameters','loaded_observation_layout','loaded_encoder_layout','native_executable','bridge_executable'): digest(s['artifacts'][key])
    if plan['contract_sha256'] != fingerprint(s): raise ValueError('Plan contract digest mismatch')
    digest(plan['source_sha256']);integer(plan['segment_index'])
    if s['control_output_boundary'] != 'protected_writer_dds_publish_returned_not_motor_execution': raise ValueError('Wrong output boundary')
    if s['history']['sampling'] != 'per_field_actual' or s['history']['reset'] != 'required': raise ValueError('Wrong history semantics')
    from .compact_replay import validate as compact_validate
    return compact_validate(plan, s)
