"""Read-only status must not monopolize control or refresh delayed feedback."""
import threading
import unittest
from unittest.mock import Mock
from tests.runtime import test_native_lifecycle as lifecycle_fixture


class StatusContentionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle_fixture.NativeTest()
        self.fixture.setUp()
        self.g = self.fixture.g

    def blocked_status(self, component):
        entered, release = threading.Event(), threading.Event()
        result, errors = [], []
        def block(*args, **kwargs):
            entered.set()
            if not release.wait(3): raise TimeoutError('test did not release reader')
            return {'ready': True, 'record_ready': True, 'native_consumption_fresh': True}
        if component == 'pose':
            self.g.pose_reader = Mock(snapshot=block, skeleton=Mock(return_value=None))
        elif component == 'process':
            self.g.process = Mock(pid=123, poll=lambda: (block(), None)[1])
        else:
            self.g.native_consumption = Mock(status=block)
        def render():
            try: result.append(self.g.status())
            except BaseException as exc: errors.append(exc)
        worker = threading.Thread(target=render)
        worker.start()
        self.addCleanup(lambda: (release.set(), worker.join(3)))
        self.assertTrue(entered.wait(2))
        return release, worker, result, errors

    def test_concurrent_queries_share_render_and_do_not_block_feedback(self):
        self.fixture.activate()
        self.g.transport_driven=True
        entered,release=threading.Event(),threading.Event()
        def slow(*args):
            entered.set()
            if not release.wait(3):raise TimeoutError('blocked render')
            return dict(record_ready=True,native_consumption_fresh=True)
        native=Mock(status=Mock(side_effect=slow));self.g.native_consumption=native
        values=[];errors=[]
        def query():
            try:values.append(self.g.query_status())
            except BaseException as exc:errors.append(exc)
        workers=[threading.Thread(target=query) for _ in range(8)]
        for t in workers:t.start()
        try:
            self.assertTrue(entered.wait(1))
            done=threading.Event()
            ingest=threading.Thread(target=lambda:(self.fixture.refresh(),done.set()))
            ingest.start()
            self.assertTrue(done.wait(1),'GET queries blocked ingest')
            ingest.join(1)
            # Credential generation must not wait for status rendering either.
            credentials=self.g.control_ticket()
            self.assertEqual(set(credentials),{'epoch','ticket','ticket_ttl_ms'})
        finally:
            release.set()
            for t in workers:t.join(3)
        self.assertFalse(errors);self.assertEqual(len(values),8)
        self.assertEqual(native.status.call_count,1)
        values[0]['lab']['record_ready']=False
        self.assertTrue(values[-1]['lab']['record_ready'])
        self.fixture.now+=.051
        self.g.query_status()
        self.assertEqual(native.status.call_count,2)
        self.assertIsNone(self.g._query_cache[1]._query_cache)

    def test_cached_query_rechecks_fault_and_invalidates_session(self):
        self.fixture.activate();self.g.transport_driven=True
        self.g.native_consumption=Mock(status=Mock(return_value=dict(record_ready=True,native_consumption_fresh=True)))
        first=self.g.query_status()
        self.fixture.refresh(fault=1)
        self.assertFalse(self.g.query_status()['lab']['record_ready'])
        self.assertEqual(self.g.native_consumption.status.call_count,1)
        with self.g.lock:self.g.halt(estop=True)
        latest=self.g.query_status()
        self.assertNotEqual(latest['epoch'],first['epoch'])
        self.assertEqual(latest['state'],'stopped')

    def test_ticket_http_is_authenticated_and_never_renders_status(self):
        import json
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from onboardmimic.core.gateway import make_server
        self.g.status=Mock(side_effect=AssertionError('full status requested'))
        server=make_server(self.g,'t'*32)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url='http://127.0.0.1:%s/api/controller/ticket'%server.server_port
        try:
            with self.assertRaises(HTTPError) as rejected:urlopen(url,timeout=2)
            self.assertEqual(rejected.exception.code,401)
            with urlopen(Request(url,headers={'Authorization':'Bearer '+'t'*32}),timeout=2) as response:
                auth=json.load(response)
            self.g.validate(dict(auth,client='ticket-test',seq=1))
            self.fixture.now+=.251
            with self.assertRaises(ValueError):self.g.validate(dict(auth,client='ticket-test',seq=2))
            self.g.status.assert_not_called()
        finally:
            server.shutdown();worker.join(2);server.server_close()

    def test_production_get_does_not_tick_or_read_log(self):
        self.fixture.activate()
        self.g.transport_driven = True
        self.g.tick = Mock(side_effect=AssertionError('GET tick'))
        self.g.read_log = Mock(side_effect=AssertionError('GET log read'))
        self.assertEqual(self.g.status()['state'], 'active')
        self.g.tick.assert_not_called()

    def test_slow_readers_do_not_block_feedback_ingest(self):
        for component in ('pose', 'process', 'native'):
            with self.subTest(component=component):
                self.setUp()
                self.g.transport_driven = True
                release, worker, result, errors = self.blocked_status(component)
                ingested = threading.Event()
                receiver = threading.Thread(target=lambda: (self.fixture.refresh(), ingested.set()))
                receiver.start()
                try: self.assertTrue(ingested.wait(1), 'status retained the control lock')
                finally: release.set(); worker.join(3); receiver.join(3)
                self.assertFalse(errors)
                self.assertTrue(result)

    def test_estop_during_native_status_invalidates_old_snapshot(self):
        self.fixture.activate()
        self.g.transport_driven = True
        old_epoch = self.g.epoch
        release, worker, result, errors = self.blocked_status('native')
        with self.g.lock: self.g.halt(estop=True)
        release.set(); worker.join(3)
        self.assertFalse(errors)
        self.assertEqual(self.g.phase, 'stopped')
        self.assertEqual(result[0]['epoch'], old_epoch)
        self.assertFalse(result[0]['lab']['record_ready'])
        with self.assertRaises(ValueError):
            self.g.validate(dict(epoch=result[0]['epoch'], ticket=result[0]['ticket'], client='old', seq=1))

    def test_lock_wait_does_not_refresh_feedback_timestamp(self):
        entered = threading.Event()
        def clock():
            now = self.fixture.now
            if threading.current_thread().name == 'blocked-ingest': entered.set()
            return now
        self.g.clock = clock
        packet = self.g.latest['rt/lowstate'][1]
        with self.g.lock:
            thread = threading.Thread(name='blocked-ingest', target=self.g.ingest, args=('rt/lowstate', packet))
            thread.start()
            self.assertTrue(entered.wait(2))
            self.fixture.now += .6
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.g.latest['rt/lowstate'][0], 100.)
        self.assertGreater(self.g.feedback_timing['rt/lowstate']['ingest_lock_wait_ms'], 599)
        self.assertFalse(self.g.telemetry()['fresh'])

    def test_active_tick_still_stops_fault_without_reading_log(self):
        self.fixture.activate()
        self.g.transport_driven = True
        self.g.read_log = Mock(side_effect=AssertionError('active log scan'))
        self.g.tick()
        self.assertEqual(self.g.phase, 'active')
        self.fixture.refresh(dq=13.)
        self.g.tick()
        self.assertEqual(self.g.phase, 'error')
        self.assertEqual(self.fixture.proc.kills, 1)

    def test_delayed_status_is_not_record_ready(self):
        self.fixture.activate()
        self.g.transport_driven = True
        release, worker, result, errors = self.blocked_status('native')
        self.fixture.now += .3
        release.set(); worker.join(3)
        self.assertFalse(errors)
        self.assertFalse(result[0]['lab']['record_ready'])
        self.assertFalse(result[0]['planner_control_allowed'])
        gate=result[0]['lab']['recording_validation']
        self.assertIn('recording_status_snapshot_expired',gate['failures'])
        self.assertGreater(gate['snapshot_age_ms'],250)
        self.assertTrue(result[0]['lab']['reason'])

    def test_fault_arriving_during_render_disables_record_ready(self):
        self.fixture.activate()
        self.g.transport_driven = True
        release, worker, result, errors = self.blocked_status('native')
        self.fixture.refresh(fault=1)
        release.set(); worker.join(3)
        self.assertFalse(errors)
        self.assertFalse(result[0]['lab']['record_ready'])
        self.assertFalse(result[0]['planner_control_allowed'])
        self.assertEqual(result[0]['lab']['reason'],'recording_joint_fault')
        self.assertTrue(result[0]['lab']['recording_validation']['joint_faults'])

    def test_mode_change_during_render_keeps_record_ready(self):
        self.fixture.activate()
        self.g.transport_driven = True
        release, worker, result, errors = self.blocked_status('native')
        with self.g.lock: self.g.begin_mode('pose')
        release.set(); worker.join(3)
        self.assertFalse(errors)
        self.assertTrue(result[0]['lab']['record_ready'])
        self.assertFalse(result[0]['input_mode_switch_available'])


    def test_final_gate_distinguishes_speed_process_and_native_fault(self):
        self.fixture.activate();self.g.transport_driven=True
        self.g.native_consumption=Mock(status=Mock(side_effect=lambda *a:dict(record_ready=True,native_consumption_fresh=True,reason='')))
        view=self.g._capture_status();value=view._render_status()
        self.fixture.refresh(dq=13.)
        result=self.g._validate_status(view,value)['lab']
        self.assertEqual(result['reason'],'recording_joint_overspeed')
        self.assertEqual(result['recording_validation']['max_joint_speed_rad_s'],13.)
        self.fixture.refresh()
        view=self.g._capture_status();value=view._render_status()
        self.g.process=Mock(pid=999)
        result=self.g._validate_status(view,value)['lab']
        self.assertEqual(result['reason'],'recording_control_process_changed')
        self.assertTrue(result['recording_validation']['process_changed'])
        value['lab'].update(reason='native_consumption_fault',record_ready=False)
        result=self.g._validate_status(view,value)['lab']
        self.assertEqual(result['reason'],'native_consumption_fault')

    def test_healthy_final_gate_records_reason_free_evidence(self):
        self.fixture.activate();self.g.transport_driven=True
        self.g.native_consumption=Mock(status=Mock(side_effect=lambda *a:dict(record_ready=True,native_consumption_fresh=True,reason='')))
        result=self.g.status()['lab']
        self.assertTrue(result['record_ready'])
        self.assertEqual(result['recording_validation']['failures'],[])
        self.assertTrue(result['recording_validation']['feedback_ready'])
        self.assertTrue(result['recording_validation']['captured_feedback_ready'])
        self.assertIn('rt/lowstate',result['recording_validation']['captured_feedback_ages_ms'])
