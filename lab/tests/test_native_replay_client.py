"""Isolated protocol tests: synthetic provenance substitution, no robot endpoints."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from io import BytesIO
from unittest.mock import patch

from onboardmimic_lab.native_replay_client import NativeReplay, NativeReplayClient, Rejected, CommandUnconfirmed, BOUNDARY, STOP_BOUNDARY
from onboardmimic_lab.replay import plan_digest
from onboardmimic_lab.contracts import fingerprint
from test_sonic_native import fixtures, shifted


def test_plan():
    spec,sample=fixtures();spec.update(test_fixture=False,physical_source=True)
    sample['contract_sha256']=fingerprint(spec)
    return dict(schema='onboardmimic.lab.reference-plan',schema_version=2,episode='UNIT_TEST_ONLY',sample_profile='lerobot_body_v21',reference_evidence='consumed_at_capture',
        segment_index=0,source_sha256='a'*64,controller_spec=spec,contract_sha256=fingerprint(spec),
        frames=[dict(offset_ns=i*20_000_000,sample=dict(index=shifted(sample,i)['index'],timestamp_monotonic_ns=shifted(sample,i)['timestamp_monotonic_ns'],timestamp_realtime_ns=shifted(sample,i)['timestamp_realtime_ns'],state=dict(body_q=[0.]*29,body_dq=[0.]*29,gravity=[0.,0.,-1.]),reference=sample['reference'])) for i in range(3)])


def status(plan,state='loaded',run=0,pos=None,ack=False):
    sample=plan['frames'][pos]['sample'] if pos is not None else None
    return dict(schema='onboardmimic.execution.status',schema_version=2,plan_id=plan_digest(plan),
        state=state,run_id=run,source_frame_position=pos,source_frame_index=sample['index'] if sample else None,
        source_reference_id=('parquet:'+plan['source_sha256']+':'+str(sample['index'])) if sample else None,output_commit=deepcopy(fixtures()[1]['output_commit']) if sample else None,
        task_lifetime='runtime_owned',browser_lease_required=False,stop_ack=ack,progress_boundary=BOUNDARY,stop_boundary=STOP_BOUNDARY,model_validation='matched_live',reason='')


class NativeReplayTest(unittest.TestCase):
    def setUp(self):
        self.plan=test_plan();self.client=Mock(client='TEST_OWNER');self.manager=NativeReplay(self.client)
        self.client.load.return_value=status(self.plan)
        self.manager.load(self.plan,'test',0)
        self.owner='browser-owner-0123456789';self.digest=plan_digest(self.plan)

    def start(self):
        self.client.command.return_value=status(self.plan,'loading',run=11)
        return self.manager.command('start',self.owner,self.digest)

    def test_completed_planner_handoff_requires_new_protected_commit(self):
        self.start()
        done=status(self.plan,'completed',11,2)
        handoff=deepcopy(done['output_commit']);handoff['sequence']+=1
        done.update(completion_ack=True,completion_boundary='live_planner_protected_output_confirmed',planner_handoff=handoff)
        self.manager._accept(done)
        self.assertTrue(self.manager.snapshot()['status']['completion_ack'])
        self.assertFalse(self.manager.snapshot()['status']['stop_ack'])
        self.assertFalse(self.manager.snapshot()['can_stop'])
        for key,value in [('sequence',done['output_commit']['sequence']),('generation',-1),('boundary','sent')]:
            bad=deepcopy(done);bad['planner_handoff'][key]=value
            with self.assertRaises(ValueError):self.manager._accept(bad)

    def test_second_replay_after_confirmed_planner_handoff(self):
        self.start()
        done=status(self.plan,'completed',11,2)
        handoff=deepcopy(done['output_commit']);handoff['sequence']+=1
        done.update(completion_ack=True,completion_boundary='live_planner_protected_output_confirmed',planner_handoff=handoff)
        self.manager._accept(done)
        self.client.load.return_value=status(self.plan)
        loaded=self.manager.load(self.plan,'test',0)
        self.assertTrue(loaded['can_start'])
        self.client.command.return_value=status(self.plan,'loading',run=12)
        second=self.manager.command('start',self.owner,self.digest)
        self.assertEqual(second['status']['run_id'],12)
        self.assertFalse(second['browser_lease_required'])
        self.assertIsNone(second['status']['source_frame_position'])
        self.assertNotIn('planner_handoff',second['status'])
        with self.assertRaises(ValueError):self.manager.command('renew',self.owner,self.digest,11)

    def test_unconfirmed_exit_cannot_reload(self):
        self.start()
        self.manager._accept(status(self.plan,'stopping',11,2))
        count=self.client.load.call_count
        with self.assertRaisesRegex(ValueError,'confirmed'):self.manager.load(self.plan,'test',0)
        self.assertEqual(self.client.load.call_count,count)
        bad=status(self.plan,'completed',11,2)
        bad.update(completion_ack=True,completion_boundary='live_planner_protected_output_confirmed')
        with self.assertRaises(ValueError):self.manager._accept(bad)
        with self.assertRaises(ValueError):self.manager.load(self.plan,'test',0)

    def test_controller_running_gate_is_fresh_and_fails_closed(self):
        self.assertFalse(self.manager.snapshot()['controller_running'])
        self.client.status.return_value=status(self.plan)
        for state,pids,expected in [('stopped',[],False),('starting_policy',[42],False),('active',[],False),('active',[42],True)]:
            self.client.controller.return_value=dict(state=state,pids=pids,backend='sonic',execution=status(self.plan))
            snapshot=self.manager.poll()
            self.assertEqual(snapshot['controller_running'],expected)
            self.assertEqual(snapshot['controller_status'],'running' if expected else 'not_ready')
        self.manager.controller_checked-=2
        self.assertEqual(self.manager.snapshot()['controller_status'],'unknown')
        self.assertFalse(self.manager.snapshot()['controller_running'])
        self.assertTrue(self.manager.poll()['controller_running'])
        self.client.controller.side_effect=TimeoutError('offline')
        self.assertFalse(self.manager.poll()['controller_running'])
        self.assertEqual(self.manager.snapshot()['controller_status'],'unknown')

    def test_load_never_starts_and_wrong_browser_cannot_renew(self):
        self.client.command.assert_not_called();self.assertTrue(self.manager.snapshot()['can_start'])
        started=self.start();self.assertFalse(started['browser_lease_required']);self.assertIsNone(started['status']['source_frame_position'])
        with self.assertRaises(ValueError):self.manager.command('renew','other-browser-owner',self.digest,11)
        with self.assertRaises(ValueError):self.manager.command('start',self.owner,self.digest)

    def test_actual_progress_stop_and_confirmation(self):
        self.start();self.client.status.return_value=status(self.plan,'running',11,1)
        self.client.controller.return_value=dict(execution=self.client.status.return_value)
        result=self.manager.poll()
        self.client.status.assert_not_called()
        self.assertEqual(result['status']['source_frame_index'],self.plan['frames'][1]['sample']['index'])
        self.client.command.return_value=status(self.plan,'stopping',11,1)
        result=self.manager.command('stop',self.owner,self.digest,11)
        self.assertFalse(result['status']['stop_ack']);self.assertFalse(result['browser_lease_required'])
        self.client.controller.return_value=dict(execution=status(self.plan,'stopped',11,1,True))
        self.client.status.return_value=status(self.plan,'stopped',11,1,True)
        self.assertTrue(self.manager.poll()['status']['stop_ack'])
        self.assertFalse(self.manager.snapshot()['can_stop'])

    def test_new_browser_can_stop_and_poll_partial_planner_handoff(self):
        self.start();v=status(self.plan,'running',11,0)
        v['entry_phase']='returning';self.client.command.return_value=v
        self.assertFalse(self.manager.command('stop','another-browser-owner',self.digest,11)['browser_lease_required'])
        v.update(state='completed',reason='operator_stop',completion_ack=True,completion_boundary='live_planner_protected_output_confirmed')
        v['planner_handoff']=deepcopy(v['output_commit']);v['planner_handoff']['sequence']+=1
        self.client.status.return_value=v
        self.client.controller.return_value=dict(execution=v)
        result=self.manager.poll()
        self.assertTrue(result['status']['completion_ack']);self.assertFalse(result['browser_lease_required'])
        invalid=deepcopy(v);invalid['reason']='completed'
        with self.assertRaises(ValueError):self.manager._accept(invalid)

    def test_disconnect_and_reconnect_only_query_never_renew_or_restart(self):
        self.start();self.client.controller.side_effect=TimeoutError('Disconnected')
        self.assertFalse(self.manager.poll()['connected'])
        self.client.controller.side_effect=None
        self.client.controller.return_value=dict(execution=status(self.plan,'running',11))
        self.client.status.return_value=status(self.plan,'running',11)
        self.client.controller.return_value['execution']=self.client.status.return_value
        result=self.manager.poll();self.assertTrue(result['can_stop'])
        self.assertEqual(self.client.command.call_count,1)
        self.assertFalse(result['browser_lease_required'])

    def test_poll_reports_executor_failure_without_commands(self):
        self.start()
        failed=status(self.plan,'failed',11,1,True);failed.update(reason='schedule_overrun',entry_phase='playing')
        self.client.controller.return_value=dict(execution=failed)
        self.client.status.return_value=failed
        self.client.controller.return_value['execution']=failed
        result=self.manager.poll()
        self.assertEqual(result['status']['reason'],'schedule_overrun')
        self.assertEqual(self.client.command.call_count,1)

    def test_browser_owned_old_executor_is_rejected(self):
        old=status(self.plan)
        old.pop('browser_lease_required')
        with self.assertRaisesRegex(ValueError,'runtime-owned'):self.manager._accept(old)

    def test_lost_start_reply_never_restarts_can_query_owner_then_stop(self):
        self.client.command.side_effect=CommandUnconfirmed('Lost reply')
        pending=self.manager.command('start',self.owner,self.digest)
        self.assertEqual(pending['pending_action'],'start')
        self.assertEqual(pending['error'],'')
        with self.assertRaises(ValueError):self.manager.command('start',self.owner,self.digest)
        self.client.controller.return_value=dict(execution=status(self.plan,'running',11),control_owner='TEST_OWNER')
        self.client.status.return_value=status(self.plan,'running',11)
        self.client.controller.return_value['execution']=self.client.status.return_value
        result=self.manager.poll();self.assertIsNone(result['pending_action']);self.assertTrue(result['can_stop']);self.assertFalse(result['browser_lease_required'])
        self.assertEqual(self.client.command.call_count,1)
        with self.assertRaises(ValueError):self.manager.load(self.plan,'test',0)
        with self.assertRaises(ValueError):self.manager.command('stop',None,self.digest,12)

    def test_pending_start_requires_owner_and_valid_status(self):
        self.client.command.side_effect=CommandUnconfirmed('Lost reply')
        self.manager.command('start',self.owner,self.digest)
        for state,owner in [('loaded','TEST_OWNER'),('running','OTHER')]:
            self.client.controller.return_value=dict(execution={},control_owner=owner)
            self.client.status.return_value=status(self.plan,state,11 if state=='running' else 0)
            self.client.controller.return_value['execution']=self.client.status.return_value
            result=self.manager.poll()
            self.assertEqual(result['pending_action'],'start')
            self.assertFalse(result['can_start'])
        self.client.controller.return_value=dict(execution={},control_owner='TEST_OWNER')
        invalid=status(self.plan,'running',11);invalid['progress_boundary']='sent_only'
        self.client.status.return_value=invalid
        self.client.controller.return_value['execution']=invalid
        result=self.manager.poll()
        self.assertEqual(result['pending_action'],'start')
        self.assertIsNone(self.manager.run_id)
        self.assertFalse(result['connected'])
        self.assertEqual(self.client.command.call_count,1)

    def test_pending_stop_waits_for_return_or_terminal(self):
        self.start()
        self.client.command.side_effect=CommandUnconfirmed('Lost reply')
        result=self.manager.command('stop',None,self.digest,11)
        self.assertEqual(result['pending_action'],'stop')
        self.assertFalse(result['can_stop'])
        self.client.controller.return_value=dict(execution={})
        self.client.status.return_value=status(self.plan,'running',11)
        self.client.controller.return_value['execution']=self.client.status.return_value
        self.assertEqual(self.manager.poll()['pending_action'],'stop')
        failed=status(self.plan,'failed',11,ack=True);failed['reason']='consumption_timeout'
        self.client.status.return_value=failed
        self.client.controller.return_value['execution']=failed
        result=self.manager.poll()
        self.assertIsNone(result['pending_action'])
        self.assertEqual(result['status']['reason'],'consumption_timeout')
        self.assertEqual(self.client.command.call_count,2)

    def test_explicit_start_rejection_can_be_corrected_without_auto_retry(self):
        self.client.command.side_effect=Rejected('Recorded start pose differs')
        with self.assertRaises(Rejected):self.manager.command('start',self.owner,self.digest)
        self.client.controller.return_value=dict(execution=status(self.plan))
        self.client.status.return_value=status(self.plan)
        self.assertTrue(self.manager.poll()['can_start'])
        self.assertEqual(self.client.command.call_count,1)

    def test_reject_fake_completion_wrong_run_and_no_commit(self):
        self.start()
        for state in (status(self.plan,'completed',11,1,True),status(self.plan,'running',12,1),status(self.plan,'running',11,1)):
            if state['state']=='running' and state['run_id']==11:state['output_commit']=None
            self.client.controller.return_value=dict(execution=state);self.client.status.return_value=state
            result=self.manager.poll();self.assertFalse(result['connected']);self.assertFalse(result['browser_lease_required'])
            self.assertNotEqual(result['status']['state'],'completed')

    def test_no_credentials_leaked_and_fixture_refused(self):
        value=status(self.plan);value.update(ticket='SECRET',epoch='SECRET',token='SECRET')
        self.client.controller.return_value=dict(execution=value);self.client.status.return_value=value
        self.assertNotIn('SECRET',json.dumps(self.manager.poll()))
        p=deepcopy(self.plan);p['controller_spec']['test_fixture']=True;p['contract_sha256']=fingerprint(p['controller_spec'])
        for f in p['frames']:f['sample']['contract_sha256']=p['contract_sha256']
        with self.assertRaisesRegex(ValueError,'fixtures'):self.manager.load(p,'fixture',0)


class NativeHttpClientTest(unittest.TestCase):
    def test_only_submitted_transport_failure_is_unconfirmed(self):
        client=NativeReplayClient('http://127.0.0.1:18921','t'*32)
        with patch.object(client,'call',side_effect=TimeoutError('ticket timeout')):
            with self.assertRaises(TimeoutError): client.command('a'*64,'start')
        for action in ('start','stop'):
            with patch.object(client,'call',side_effect=[dict(epoch='e',ticket='t'),TimeoutError('reply lost')]) as call:
                with self.assertRaises(CommandUnconfirmed): client.command('a'*64,action,11)
                self.assertEqual(call.call_count,2)
        with patch.object(client,'call',side_effect=[dict(epoch='e',ticket='t'),Rejected('denied')]):
            with self.assertRaises(Rejected): client.command('a'*64,'start')

    def test_fresh_tickets_monotonic_seq_and_error_reason(self):
        client=NativeReplayClient('http://127.0.0.1:18921','t'*32)
        calls=[]
        def fake(path,payload=None,**kwargs):
            calls.append((path,payload))
            if path=='/api/controller/ticket':return dict(epoch='e',ticket='ticket-'+str(len(calls)))
            return {}
        with patch.object(client,'call',side_effect=fake):
            client.command('a'*64,'start');client.command('a'*64,'stop',123);client.command('a'*64,'stop',123)
        self.assertEqual([calls[i][1]['seq'] for i in (1,3,5)],[1,2,3])
        self.assertEqual([calls[i][1]['ticket'] for i in (1,3,5)],['ticket-1','ticket-3','ticket-5'])
        self.assertEqual(calls[3][1]['run_id'],123)
        with patch('onboardmimic_lab.native_replay_client.urlopen',side_effect=HTTPError('url',409,'conflict',{},BytesIO(b'{"error":"Recorded start pose differs"}'))):
            with self.assertRaisesRegex(Rejected,'Recorded start pose differs'):client.status('a'*64)
        with self.assertRaises(ValueError):NativeReplayClient('http://remote:18921','t'*32)


class LongCompactPlanTest(unittest.TestCase):
    def test_long_plan_admitted_by_lab(self):
        from onboardmimic_lab.lerobot_replay import validate_plan
        plan=test_plan();first=plan['frames'][0]['sample'];frames=[]
        for i in range(6002):
            sample=deepcopy(first)
            sample.update(index=first['index']+i,
                timestamp_monotonic_ns=first['timestamp_monotonic_ns']+i*20000000,
                timestamp_realtime_ns=first['timestamp_realtime_ns']+i*20000000)
            frames.append(dict(offset_ns=i*20000000,sample=sample))
        plan['frames']=frames
        self.assertEqual(len(validate_plan(plan)['frames']),6002)
