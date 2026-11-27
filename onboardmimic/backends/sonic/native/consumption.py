"""Bounded, fail-closed join of inference / transport / protected DDS evidence.

No control methods, no robot connection, no relabelling of legacy g1_debug.
OML sonic-native-v2 accepts this schema. Readiness additionally binds the live runtime.
"""
from collections import OrderedDict
from copy import deepcopy
import math
import re
import time
from onboardmimic.core.execution.software import fingerprint

RAW_TOPIC = b'om/sonic/native/v2-draft'
TOPIC = b'oml/controller/v2-draft'
HW_JOINTS = [side+'_'+joint for side in ('left', 'right') for joint in
             ('hip_pitch', 'hip_roll', 'hip_yaw', 'knee', 'ankle_pitch', 'ankle_roll')]
HW_JOINTS += ['waist_yaw', 'waist_roll', 'waist_pitch']
HW_JOINTS += [side+'_'+joint for side in ('left', 'right') for joint in
              ('shoulder_pitch', 'shoulder_roll', 'shoulder_yaw', 'elbow', 'wrist_roll', 'wrist_pitch', 'wrist_yaw')]


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Missing actual artifact SHA256')
    return value


def vector(value, length):
    if not isinstance(value, list) or len(value) != length or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('Invalid observed tensor')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum: raise ValueError('Invalid native counter/time')
    return value


def controller_spec(native, bridge):
    if native.get('schema') != 'onboardmimic.sonic.native-manifest' or native.get('schema_version') != 2:
        raise ValueError('Unsupported native manifest')
    if bridge.get('schema') != 'onboardmimic.sonic.bridge-manifest' or bridge.get('schema_version') != 2:
        raise ValueError('Unsupported bridge manifest')
    if bridge.get('protected_writer') is not True: raise ValueError('Protected writer not connected')
    artifacts = {**native['artifacts'], **bridge['artifacts']}
    for key in ('decoder_loaded_trt', 'decoder_onnx_cache_validated', 'native_executable',
                'loaded_parameters', 'loaded_observation_layout', 'loaded_encoder_layout', 'bridge_executable'):
        digest(artifacts[key])
    if native['parameters']['is_using_encoder']:
        digest(artifacts['encoder_loaded_trt']); digest(artifacts['encoder_onnx_cache_validated'])
    for value in artifacts.values(): digest(value)
    mapping = native['parameters']['mujoco_to_isaaclab']
    if sorted(mapping) != list(range(29)): raise ValueError('Invalid loaded joint mapping')
    state_names = [HW_JOINTS[i] for i in mapping]
    state, references, histories = {}, {}, {}
    integer(native['period_ns'], 1)
    integer(native['input_dimension'], 1)
    offset = 0
    for field in native['layout']:
        name, size = field['name'], integer(field['dimension'], 1)
        if field['offset'] != offset or name in state or name in references: raise ValueError('Invalid active layout')
        offset += size
        if name == 'token_state':
            references[name] = dict(shape=[size], unit='latent', frame='encoder_space',
                                    semantic='sonic_decoder_token', dtype='float32')
            continue
        match = re.fullmatch(r'his_(base_angular_velocity|body_joint_positions|body_joint_velocities|last_actions|gravity_dir)_(\d+)frame_step(\d+)', name)
        if not match: raise ValueError('Uninstrumented decoder input: '+name)
        kind, length, stride = match[1], int(match[2]), int(match[3])
        width = 3 if kind in ('base_angular_velocity', 'gravity_dir') else 29
        if not 1 <= length <= 64 or stride < 1 or size != length*width: raise ValueError('Invalid history layout')
        unit = {'base_angular_velocity':'rad/s', 'body_joint_positions':'rad',
                'body_joint_velocities':'rad/s', 'last_actions':'policy_action', 'gravity_dir':'unit_vector'}[kind]
        semantic = {'body_joint_positions':'joint_position_relative_to_default',
                    'body_joint_velocities':'joint_velocity_after_observation_guard',
                    'last_actions':'joint_previous_action_after_command_postprocessing'}.get(kind, kind)
        state[name] = dict(shape=[length, width], unit=unit,
                           frame='policy_joint_order' if width == 29 else 'base', semantic=semantic, dtype='float32')
        if width == 29: state[name]['joint_names'] = state_names
        histories[name] = dict(length=length, stride=stride, order='oldest_first',
                               nominal_period_ns=native['period_ns']*stride,
                               timestamp_semantic='state_logger_entry_assembly', source_timestamp_semantic='local_state_buffer_receive_not_sensor_exposure', selection='native_GetLatest_actual_entries')
    if not state or set(references) != {'token_state'} or offset != native['input_dimension']:
        raise ValueError('Incomplete decoder layout')
    output = {name: dict(shape=[29], unit=unit, frame='hardware_joint_order',
                         semantic='joint_'+name+'_protected_dds_commit', joint_names=HW_JOINTS, dtype='float32')
              for name, unit in [('q','rad'),('dq','rad/s'),('kp','Nm/rad'),('kd','Nm*s/rad'),('tau','Nm')]}
    return dict(schema='onboardmimic.lab.controller-spec', schema_version=2,
        protocol_status='proposal', robot_id='unitree_g1', backend_id='sonic', execution_id='sonic.native.v2-draft',
        reference_space='sonic.decoder.token.v1', clock='same_host_monotonic_ns', period_ns=native['period_ns'],
        max_gap_ns=60000000, artifacts=artifacts, state=state, reference=references, control_output=output,
        history=dict(sampling='per_field_actual', reset='required', fields=histories),
        diagnostic_tensors=dict(
            decoder_input=dict(shape=[offset], dtype='float32', unit='mixed', frame='decoder_flat_input', semantic='exact_decoder_input'),
            raw_policy_action=dict(shape=[29], dtype='float32', unit='policy_action', frame='policy_joint_order',
                                   semantic='joint_raw_network_action', joint_names=state_names),
            encoder_input=(dict(shape=[sum(f['dimension'] for f in native['encoder_layout'])], dtype='float32',
                                unit='mixed', frame='encoder_flat_input', semantic='exact_encoder_input')
                           if native['parameters']['is_using_encoder'] else None)),
        decoder_input_layout=native['layout'], loaded_parameters=native['parameters'], encoder_layout=native['encoder_layout'],
        control_output_boundary='protected_writer_dds_publish_returned_not_motor_execution',
        record_modes=['planner', 'pose', 'replay'], recording_policy='actual_decoder_consumption', physical_source=bridge['physical'],
        test_fixture=bool(native.get('test_fixture') or bridge.get('test_fixture')))


class NativeJoin:
    def __init__(self, clock_ns=time.monotonic_ns, capacity=256):
        self.clock_ns, self.capacity = clock_ns, capacity
        from .continuity import ConsumptionContinuity
        self.continuity = ConsumptionContinuity()
        self.trace = lambda *a, **kw: None
        self.profile = None
        self.manifests = {}
        self.sessions = {}
        self.spec = None
        # Validated manifests/spec are owned and read-only until replaced.
        # Do not serialize the same model contract on every inference.
        self.contract = None
        self._session_key = self._session_digest = None
        self.pending = OrderedDict()
        self.consumed_mappings = OrderedDict()
        self.last = None
        self.last_index = -1
        self.generation = 0
        self.writer_generation = 0
        self.failed = False
        self.seen_index = self.reject_through = -1

    def _clear(self):
        self.pending.clear(); self.consumed_mappings.clear(); self.last = None; self.last_index = -1
        self.generation = self.writer_generation = 0
        self.seen_index = self.reject_through = -1

    def ingest(self, source, event, *, take_ownership=False):
        """Source is socket provenance, never a payload choice.

        Default callers retain mutable inputs and receive independent outputs.
        A receiver may transfer a freshly decoded event instead: it must never
        mutate that event or the returned, borrowed sample afterwards. Stored
        records and completed samples are never mutated by this joiner.
        """
        if source not in ('native', 'bridge'): raise ValueError('Invalid evidence source')
        now = self.clock_ns()
        if not 0 <= now-integer(event['published_monotonic_ns']) <= 250000000:
            self.trace('rejected',source=source,reason='event_stale',index=event.get('index'))
            self.last = None
            return None
        session = event['native_session']
        if not isinstance(session, str) or not 1 <= len(session) <= 128: raise ValueError('Invalid session')
        self.continuity.observe(source, event)
        if event['kind'] == 'manifest':
            manifest = event['manifest']
            if self.sessions.get(source) == session and self.manifests.get(source) == manifest:
                return None
            self._clear(); self.spec = self.contract = None
            self.sessions[source] = session
            self.manifests[source] = manifest if take_ownership else deepcopy(manifest)
            if len(self.manifests) == 2:
                self.spec = controller_spec(self.manifests['native'], self.manifests['bridge'])
                self.contract = fingerprint(self.spec)
            return None
        if self.sessions.get(source) != session or self.spec is None:
            self.trace('rejected',source=source,index=event.get('index'),reason='session_or_manifest');return None
        kind = event['kind']
        if (source == 'bridge') != (kind == 2): raise ValueError('Wrong evidence socket')
        if kind not in (0, 1, 2): raise ValueError('Unknown evidence kind')
        if kind == 0:
            if type(event['inference_ok']) is not bool or type(event['command_ok']) is not bool:
                raise ValueError('Invalid inference result')
            generation = integer(event['generation'], 1)
            if generation < self.generation: return None
            if generation > self.generation:
                self.consumed_mappings.clear()
                self.pending = OrderedDict((k, v) for k, v in self.pending.items()
                    if k[0] == 2 or v[1]['generation'] >= generation)
                self.last = None; self.generation = generation
            self.seen_index = max(self.seen_index, integer(event['index'], 1))
            if not event['inference_ok'] or not event['command_ok'] or type(event.get('mode')) is not int:
                self.last = None
                self.trace('rejected',source=source,index=event.get('index'),seq=event.get('seq'),reason='inference_or_command_failed' if kind==0 else 'authority_lost')
                self.failed = True
                self.pending.clear(); self.reject_through = self.seen_index
                return None
            self.failed = False
        if kind == 2:
            generation = integer(event['generation'])
            if generation < self.writer_generation: return None
            if generation > self.writer_generation:
                self.consumed_mappings.clear()
                self.last = None; self.writer_generation = generation
            if event['authority'] is not True:
                self.last = None
                self.trace('rejected',source=source,index=event.get('index'),seq=event.get('seq'),reason='inference_or_command_failed' if kind==0 else 'authority_lost')
                self.failed = True
                self.pending.clear(); self.reject_through = self.seen_index
                return None
        key = (kind, integer(event['index']) if kind == 0 else
               (integer(event['stream'], 1), integer(event['seq'], 1)))
        # One inference may be transmitted ten times. Once its first protected
        # commit is confirmed, later mappings cannot produce a new sample.
        if kind in (0, 1) and event['index'] <= max(self.last_index, self.reject_through):
            if kind == 1:
                # Only an observed mapping proves that this exact stream/seq
                # cannot yield a new inference. Never infer it from commit.index.
                self.consumed_mappings[key[1]] = now
                self.consumed_mappings.move_to_end(key[1])
                while len(self.consumed_mappings) > self.capacity:
                    self.consumed_mappings.popitem(last=False)
                self.pending.pop((2, key[1]), None)
            self.trace('rejected',source=source,index=event['index'],seq=event.get('seq'),reason='already_joined_or_superseded',last_index=self.last_index,reject_through=self.reject_through)
            return None
        # Freshness, continuity, session and authority checks above still run
        # for every commit, including commits from already consumed mappings.
        if kind == 2:
            mapped_at = self.consumed_mappings.pop(key[1], None)
            if mapped_at is not None and 0 <= now-mapped_at <= 200000000:
                self.trace('redundant_commit',stream=event['stream'],seq=event['seq'])
                return None
        self.pending[key] = (now, event if take_ownership else deepcopy(event))
        self.pending.move_to_end(key)
        # TTL and finite capacity bound both dropped-feedback and malicious floods.
        while self.pending and now-next(iter(self.pending.values()))[0] > 200000000:
            removed=self.pending.popitem(last=False)
            self.trace('evicted',reason='ttl',key=removed[0],index=removed[1][1].get('index'))
        while len(self.pending) > self.capacity:
            removed=self.pending.popitem(last=False)
            self.trace('evicted',reason='capacity',key=removed[0],index=removed[1][1].get('index'))
        # Only the arriving evidence can complete a join. Avoid scanning every
        # retained command on both 500 Hz streams; still handle inference arriving last.
        if kind == 0:
            candidates = [(k, v) for k, v in self.pending.items()
                          if k[0] == 1 and v[1]['index'] == event['index']]
        else:
            mapping_key = (1, key[1])
            candidates = [(mapping_key, self.pending[mapping_key])] if mapping_key in self.pending else []
        for k, (_, mapping) in candidates:
            if mapping['index'] <= max(self.last_index, self.reject_through): continue
            attempt = self.pending.get((0, mapping['index']))
            commit = self.pending.get((2, k[1]))
            if not attempt or not commit: continue
            sample = self._join(attempt[1], mapping, commit[1], now)
            if sample:
                continuity = self.continuity.annotate(sample, self.generation)
                if self.profile: self.profile.mark('sample_assembly_and_continuity')
                if continuity['reason'] not in ('start', 'adjacent'):
                    self.trace('consumption_continuity',index=sample['index'],**continuity)
                self.last_index = sample['index']; self.last = sample; self.failed = False
                self.pending.pop((0, mapping['index']), None)
                self.pending.pop(k, None)
                self.pending.pop((2, k[1]), None)
                return sample if take_ownership else deepcopy(sample)
            self.continuity.epoch += 1
            self.trace('join_rejected',index=mapping['index'],stream=mapping['stream'],seq=mapping['seq'],reason='evidence_validation')
            self.last = None
            self.failed = True
        return None

    def _join(self, attempt, mapping, commit, now):
        def reject(reason):
            self.trace('join_rejected',index=attempt['index'],stream=mapping['stream'],seq=mapping['seq'],reason=reason)
            return None
        if attempt['generation'] != self.generation or mapping['generation'] != self.generation: return reject('native_generation')
        if commit['generation'] != self.writer_generation or not commit['authority']: return reject('writer_generation_or_authority')
        at = integer(attempt['at_ns'], 1)
        if not at <= attempt['finished_ns'] <= mapping['send_ns'] <= commit['at_ns'] <= now: return reject('timestamp_order')
        if now-at > 250000000 or commit['at_ns']-mapping['send_ns'] > 50000000: return reject('evidence_expired')
        if mapping['send_ns'] != commit['send_ns'] or type(attempt['mode']) is not int: return reject('send_identity_or_mode')
        if attempt['mode']==3:
            replay=attempt.get('replay')
            if not isinstance(replay,dict) or type(replay.get('scheduled_frame')) is not bool:raise ValueError('Missing replay provenance')
            integer(replay['run'],1);integer(replay['frame'])
        if self.profile: self.profile.mark('lookup_and_identity')
        values = vector(attempt['input'], self.manifests['native']['input_dimension'])
        if self.profile: self.profile.mark('decoder_validation')
        state, reference, stamps, indices, sources = {}, {}, {}, {}, {}
        for i, field in enumerate(self.spec['decoder_input_layout']):
            name, offset, size = field['name'], field['offset'], field['dimension']
            part = values[offset:offset+size]
            if name == 'token_state': reference[name] = part; continue
            shape = self.spec['state'][name]['shape']
            history = attempt['histories'][i]
            times, ids = history['ns'], history['index']
            if len(times) != shape[0] or len(ids) != shape[0]: return reject('history_shape')
            for t in times: integer(t, 1)
            for idx in ids: integer(idx)
            if any(b <= a for a, b in zip(times, times[1:])): return reject('history_clock_order')
            if any(b <= a for a, b in zip(ids, ids[1:])): return reject('history_index_order')
            if times[0] < attempt['reset_ns'] or not 0 <= at-times[-1] <= self.spec['max_gap_ns']: return reject('history_freshness')
            state[name] = [part[j:j+shape[1]] for j in range(0, size, shape[1])]
            stamps[name], indices[name] = times, ids
            sources[name] = {}
            for source in ('low_state_received_ns', 'torso_imu_received_ns'):
                receipts = history[source]
                if len(receipts) != len(times): return reject('feedback_history_shape')
                for receipt, assembled in zip(receipts, times):
                    integer(receipt, 1)
                    if receipt > assembled: return reject('feedback_from_future')
                sources[name][source] = receipts
        if self.profile: self.profile.mark('history_validation_and_assembly')
        for command in (attempt['command'], commit['command']):
            if set(command) != set(self.spec['control_output']): raise ValueError('Incomplete output')
            for v in command.values(): vector(v, 29)
        # Active protected writer must have committed exactly this postprocessed
        # command. A recovery blend / replacement cannot impersonate this tick.
        if attempt['command'] != commit['command']: return reject('protected_command_differs')
        raw = vector(attempt['raw_action'], 29)
        if self.profile: self.profile.mark('output_validation')
        encoder_input = None
        encoder_at = None
        if self.spec['loaded_parameters']['is_using_encoder'] and attempt['mode'] != 3:
            encoder_input = vector(attempt['encoder_input'], sum(f['dimension'] for f in self.spec['encoder_layout']))
            encoder_at = integer(attempt['encoder_at_ns'], 1)
            if encoder_at > at: return reject('encoder_from_future')
        if self.profile: self.profile.mark('encoder_validation')
        key = (self.sessions['native'],self.generation,self.sessions['bridge'],self.writer_generation)
        if key != self._session_key:
            self._session_digest = fingerprint(dict(native=key[0],native_generation=key[1],
                writer=key[2],writer_generation=key[3]))
            self._session_key = key
        session, contract = self._session_digest, self.contract
        if self.profile: self.profile.mark('contract_and_session_hash')
        return dict(contract_sha256=contract, session_id=session, index=attempt['index'],
            timestamp_monotonic_ns=at, timestamp_realtime_ns=integer(attempt['realtime_ns'], 1),
            state_history_ns=stamps, state_history_indices=indices, state_history_sources=sources, state=state,
            reference_id=session+':'+str(attempt['index']), reference_evidence='consumed', reference=reference,
            control_output=commit['command'], raw_policy_action=raw, decoder_input=values,
            encoder_input=encoder_input, encoder_input_monotonic_ns=encoder_at,
            inference_finished_monotonic_ns=attempt['finished_ns'],
            output_commit=dict(boundary=self.spec['control_output_boundary'], timestamp_monotonic_ns=commit['at_ns'],
                               stream=mapping['stream'], sequence=mapping['seq'], generation=commit['generation']),
            native_mode=attempt['mode'], token_source=('replay' if attempt['mode']==3 else 'encoder' if encoder_input is not None else 'external'),
            published_monotonic_ns=now, replay=deepcopy(attempt.get('replay')) if attempt['mode']==3 else None)

    def status(self, runtime=None):
        fresh = self.last is not None and 0 <= self.clock_ns()-self.last['timestamp_monotonic_ns'] <= 60000000
        result = dict(record_ready=False, contract_sha256=fingerprint(self.spec) if self.spec else None,
                      session_id=self.last['session_id'] if self.last else None,
                      native_consumption_fresh=bool(fresh and not self.failed))
        reason = self._readiness_reason(runtime, fresh)
        result.update(record_ready=not reason, reason=reason)
        return result

    def _readiness_reason(self, runtime, fresh):
        if self.spec is None: return 'native_spec_unavailable'
        if self.spec.get('test_fixture') is not False: return 'fixture_not_recordable'
        if self.failed: return 'native_consumption_fault'
        if not fresh: return 'native_consumption_stale_or_warming'
        if self.last.get('reference_evidence') != 'consumed': return 'native_consumption_unconfirmed'
        if self.last['contract_sha256'] != fingerprint(self.spec): return 'native_contract_mismatch'
        expected = fingerprint(dict(native=self.sessions.get('native'), native_generation=self.generation,
                                    writer=self.sessions.get('bridge'), writer_generation=self.writer_generation))
        if self.last['session_id'] != expected: return 'native_session_mismatch'
        if runtime is None: return 'runtime_identity_unverified'
        if runtime.get('active') is not True: return 'runtime_inactive'
        if runtime.get('feedback_ready') is not True:
            return 'runtime_feedback_unavailable'
        if self.spec.get('physical_source') is not True or runtime.get('physical') is not True:
            return 'physical_native_source_required'
        protection = runtime.get('protection', {})
        if (any(protection.get(k) is not True for k in ('configured','fresh','armed')) or
            any(protection.get(k) is not False for k in ('fenced','estopped','takeover_requested')) or
            protection.get('error')):
            return 'protected_output_unavailable'
        pid = runtime.get('pid')
        commit = self.last['output_commit']
        if (type(pid) is not int or pid <= 1 or protection.get('wbc_pid') != pid or
            protection.get('generation') != commit['generation'] or protection.get('stream') != commit['stream']):
            return 'protected_session_mismatch'
        identity = protection.get('recording_identity', {})
        if (identity.get('wbc_pid') != pid or identity.get('bridge_pid') != protection.get('bridge_pid') or
            type(identity.get('bridge_pid')) is not int or identity['bridge_pid'] <= 1 or
            identity.get('generation') != protection.get('generation') or
            any(identity.get(k) != self.spec['artifacts'][k] for k in ('native_executable','bridge_executable'))):
            return 'runtime_identity_mismatch'
        return ''
