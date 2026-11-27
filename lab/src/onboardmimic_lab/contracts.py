"""JSON-serializable black-box contracts. No robot SDK or SONIC imports."""
import hashlib
import json
import math
from copy import deepcopy

SCHEMA = 'onboardmimic.lab.controller-spec'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(name+' must be an integer >= '+str(minimum))
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name+' must be a nonempty string')
    return value


def sha256(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('expected SHA256')
    return value


def fields(spec):
    if not isinstance(spec, dict) or not spec:
        raise ValueError('field specification must be a nonempty object')
    for name, field in spec.items():
        text(name, 'field name')
        shape = field['shape']
        if not isinstance(shape, list) or not 1 <= len(shape) <= 4:
            raise ValueError('tensor shape requires 1..4 dimensions')
        size = 1
        for dim in shape:
            size *= integer(dim, 'dimension', 1)
        if size > 100000:
            raise ValueError('tensor too large')
        for key in ('unit', 'frame', 'semantic'):
            text(field[key], key)
        # Labels are mandatory when an axis describes joints; ordering is semantic.
        if 'joint' in field['semantic'] and 'joint_names' not in field:
            raise ValueError('joint fields require explicit joint ordering')
        if 'joint_names' in field:
            names = field['joint_names']
            if (not isinstance(names, list) or len(names) != shape[-1]
                    or len(set(names)) != len(names)):
                raise ValueError('joint names must match the last tensor dimension')
            for n in names: text(n, 'joint name')
    return spec


def validate_spec(value):
    if type(value.get("schema_version")) is int and value["schema_version"] == 2:
        from .adapters.sonic_native import validate_spec as native
        return native(value)
    s = deepcopy(value)
    if s.get('schema') != SCHEMA or type(s.get('schema_version')) is not int or s['schema_version'] != 1:
        raise ValueError('unsupported controller spec')
    for key in ('robot_id', 'backend_id', 'execution_id', 'reference_space'):
        text(s[key], key)
    if s['clock'] != 'same_host_monotonic_ns':
        raise ValueError('only a shared host monotonic clock is supported')
    integer(s['period_ns'], 'period_ns', 1)
    integer(s['max_gap_ns'], 'max_gap_ns', s['period_ns'])
    if not isinstance(s['artifacts'], dict) or not s['artifacts']:
        raise ValueError('controller artifact hashes are required')
    for name, digest in s['artifacts'].items():
        text(name, 'artifact name'); sha256(digest)
    fields(s['state']); fields(s['reference'])
    if s.get('control_output') is not None: fields(s['control_output'])
    history = s['history']
    integer(history['length'], 'history length', 1)
    integer(history['period_ns'], 'history period', 1)
    if history['order'] != 'oldest_first' or history['reset'] not in ('required', 'stateless'):
        raise ValueError('unsupported history/reset semantics')
    text(history['layout'], 'history layout')
    for field in s['state'].values():
        if len(field['shape']) < 2 or field['shape'][0] != history['length']:
            raise ValueError('state tensors require a leading history axis matching history.length')
    if not isinstance(s['record_modes'], list) or not s['record_modes']:
        raise ValueError('record_modes must be explicit')
    for mode in s['record_modes']: text(mode, 'record mode')
    fingerprint(s)  # reject NaN/non-JSON metadata too
    return s


def tensor(value, shape):
    if not shape:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('tensor contains a nonfinite/non-numeric value')
    elif not isinstance(value, (list, tuple)) or len(value) != shape[0]:
        raise ValueError('tensor shape mismatch')
    else:
        for item in value: tensor(item, shape[1:])


def validate_values(value, spec):
    if not isinstance(value, dict) or set(value) != set(spec):
        raise ValueError('tensor field names do not match contract')
    for name, field in spec.items(): tensor(value[name], field['shape'])
    return value


def validate_sample(sample, spec):
    if 'consumption' in sample:
        c = sample['consumption']
        if not isinstance(c, dict) or type(c.get('schema')) is not int or c['schema'] != 1:
            raise ValueError('invalid consumption continuity schema')
        for field in ('sequence', 'segment', 'skipped_attempts'): integer(c[field], field)
        if c.get('previous_inference_index') is not None: integer(c['previous_inference_index'], 'previous inference')
        if c.get('reason') not in ('start', 'adjacent', 'superseded_before_commit', 'unresolved', 'evidence_discontinuity'):
            raise ValueError('invalid consumption continuity reason')
    if sample['contract_sha256'] != fingerprint(spec):
        raise ValueError('sample controller contract mismatch')
    text(sample['session_id'], 'session_id')
    text(sample['reference_id'], 'reference_id')
    for key in ('index', 'timestamp_monotonic_ns', 'timestamp_realtime_ns'):
        integer(sample[key], key)
    validate_values(sample['state'], spec['state'])
    validate_values(sample['reference'], spec['reference'])
    if sample['reference_evidence'] not in ('submitted', 'consumed'):
        raise ValueError('reference evidence must distinguish submitted from consumed')
    if 'submitted_reference' in sample:
        validate_values(sample['submitted_reference'], spec['reference'])
    if 'control_output' in sample:
        if spec.get('control_output') is None: raise ValueError('undeclared control output')
        validate_values(sample['control_output'], spec['control_output'])
    if spec['schema_version'] == 2:
        from .adapters.sonic_native import validate_history_and_commit
        return validate_history_and_commit(sample, spec)
    stamps = sample['state_history_ns']
    h = spec['history']
    if not isinstance(stamps, list) or len(stamps) != h['length']:
        raise ValueError('state history length mismatch')
    for stamp in stamps: integer(stamp, 'state history timestamp')
    if any(b-a != h['period_ns'] for a, b in zip(stamps, stamps[1:])):
        raise ValueError('state history must match declared sampling period')
    if not 0 <= sample['timestamp_monotonic_ns']-stamps[-1] <= spec['max_gap_ns']:
        raise ValueError('state history is future or stale')
    return sample


def next_segment(previous, sample, current, spec):
    validate_sample(sample, spec)
    if previous is None: return current
    if (sample['session_id'] != previous['session_id'] or sample['index'] <= previous['index']
            or sample['timestamp_monotonic_ns'] <= previous['timestamp_monotonic_ns']
            or sample['timestamp_realtime_ns'] <= previous['timestamp_realtime_ns']):
        raise ValueError('controller session restarted or clock regressed; end episode')
    if spec['schema_version'] == 2:
        before, after = previous['output_commit'], sample['output_commit']
        if (before['stream'] != after['stream'] or before['generation'] != after['generation']
                or after['sequence'] <= before['sequence']
                or after['timestamp_monotonic_ns'] <= before['timestamp_monotonic_ns']):
            raise ValueError('protected output restarted or regressed; end episode')
    discontinuous = sample['index'] != previous['index']+1
    if spec['schema_version'] == 2 and ('consumption' in sample or 'consumption' in previous):
        a, b = previous.get('consumption', {}), sample.get('consumption', {})
        discontinuous = not (a.get('schema') == b.get('schema') == 1 and
            type(a.get('sequence')) is int and type(b.get('sequence')) is int and
            b['sequence'] == a['sequence']+1 and a.get('segment') == b.get('segment') and
            b.get('previous_inference_index') == previous['index'] and
            b.get('reason') in ('adjacent', 'superseded_before_commit') and
            type(b.get('skipped_attempts')) is int and
            b['skipped_attempts'] == sample['index']-previous['index']-1 and
            (b['reason'] == 'superseded_before_commit') == (b['skipped_attempts'] > 0))
    return current + int(discontinuous or
                         sample['timestamp_monotonic_ns']-previous['timestamp_monotonic_ns'] > spec['max_gap_ns'])


class ReferencePolicy:
    """Wrap any model callable; reject outputs for the wrong execution contract."""
    def __init__(self, predict, model_spec, execution_spec):
        self.spec = validate_spec(execution_spec)
        if fingerprint(validate_spec(model_spec)) != fingerprint(self.spec):
            raise ValueError('policy and execution contracts differ')
        self.predict = predict

    def __call__(self, observation):
        predictions = self.predict(observation)
        if not isinstance(predictions, (list, tuple)) or not predictions:
            raise ValueError('model must return a nonempty reference sequence')
        for value in predictions: validate_values(value, self.spec['reference'])
        return predictions
