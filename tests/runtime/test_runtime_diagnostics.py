"""Timing attribution only: no runtime service, network or robot process."""
import gc
from pathlib import Path
import unittest
from unittest.mock import patch
from onboardmimic.core.runtime_diagnostics import RuntimeDiagnostics


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_gc_policy_unchanged_callback_removable_and_snapshot_bounded(self):
        enabled=gc.isenabled();before=list(gc.callbacks)
        d=RuntimeDiagnostics()
        try:
            self.assertEqual(gc.isenabled(),enabled)
            self.assertIn(d.callback,gc.callbacks)
            d.close();d.close()
            self.assertEqual(gc.callbacks,before)
            wall=[1000000000];cpu=[100000]
            with patch('time.monotonic_ns',side_effect=lambda:wall[0]),patch('time.thread_time_ns',side_effect=lambda:cpu[0]),patch.object(Path,'read_text',side_effect=AssertionError('GC did file I/O')):
                for _ in range(100):
                    d._gc('start',{'generation':1});wall[0]+=35000000;cpu[0]+=30000000
                    d._gc('stop',{})
            rows=d.snapshot()['gc_events'];self.assertEqual(len(rows),32)
            self.assertEqual(rows[-1]['end_ns']-rows[-1]['start_ns'],35000000)
            self.assertEqual(rows[-1]['thread_cpu_ns'],30000000)
            self.assertTrue(any('test_gc_policy' in frame[1] for frame in rows[-1]['trigger_stack']))
            self.assertEqual(gc.isenabled(),enabled)
        finally:d.close()

    def test_phase_cpu_and_wall_are_separate_and_idle_wait_is_excluded(self):
        d=RuntimeDiagnostics();d.close();wall=[1000000000];cpu=[100000]
        with patch('time.monotonic_ns',side_effect=lambda:wall[0]),patch('time.thread_time_ns',side_effect=lambda:cpu[0]):
            d.mark('receive','decode');wall[0]+=46000000;cpu[0]+=1000000;d.mark('receive','poll')
            wall[0]+=60000000;d.mark('receive','decode')
            for _ in range(80):
                wall[0]+=25000000;cpu[0]+=1000000;d.mark('receive','decode')
        rows=d.snapshot()['slow_phases']
        self.assertEqual(len(rows),64)
        self.assertTrue(all(row['stage']=='decode' for row in rows))
        self.assertTrue(all(row['thread_cpu_ns']==1000000 for row in rows))

    def test_sampler_has_no_inventory_or_control_lock_dependency(self):
        import threading
        from unittest.mock import Mock
        from onboardmimic.core.runtime import sample_runtime_diagnostics
        stop=threading.Event();d=Mock()
        d.sample_scheduler.side_effect=stop.set
        worker=threading.Thread(target=sample_runtime_diagnostics,args=(d,stop))
        worker.start();worker.join(1)
        self.assertFalse(worker.is_alive());d.sample_scheduler.assert_called_once()

    def test_scheduler_sampling_has_its_own_time_and_reports_read_failure(self):
        d=RuntimeDiagnostics();d.close();d.mark('control','gateway_tick')
        with patch.object(Path,'read_text',return_value='1000 2000 3'):
            d.sample_scheduler()
        row=d.snapshot()['scheduler'][-1]
        self.assertEqual(row['counters'],(1000,2000,3));self.assertEqual(row['error'],'')
        self.assertGreaterEqual(row['sample_start_ns'],row['stage_since_ns'])
        self.assertGreaterEqual(row['sample_end_ns'],row['sample_start_ns'])
        with patch.object(Path,'read_text',side_effect=FileNotFoundError):d.sample_scheduler()
        self.assertEqual(d.snapshot()['scheduler'][-1]['error'],'FileNotFoundError')

    def test_replay_incident_retains_process_diagnostics_without_changing_exit(self):
        from tests.sonic.test_reference_replay import ReplayLifecycleTests
        fixture=ReplayLifecycleTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        d=RuntimeDiagnostics();self.addCleanup(d.close);fixture.g.runtime_diagnostics=d
        fixture.start();fixture.r.stop('runtime_admission_lost')
        snapshot=fixture.save_incident.call_args.args[0]['runtime_diagnostics']
        self.assertIn('replay',snapshot['phases'])
        self.assertIn('gc_events',snapshot)
        self.assertEqual(fixture.proc.kills,1)
        self.assertEqual(fixture.r.state,'stopping')
