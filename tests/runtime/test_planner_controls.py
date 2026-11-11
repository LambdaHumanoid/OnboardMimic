import unittest
from unittest.mock import Mock
import tests.runtime.test_native_lifecycle as fixtures
from onboardmimic.core.commands import PlannerLease

class PlannerTest(unittest.TestCase):
    setUp=fixtures.NativeTest.setUp
    refresh=fixtures.NativeTest.refresh
    packet=fixtures.NativeTest.packet
    command=fixtures.NativeTest.command
    activate=fixtures.NativeTest.activate

    def test_four_directions_and_custom_speed_on_physical_backend(self):
        self.activate();self.g.physical=True
        for direction in ('forward','backward','left','right'):
            self.g.request('/api/control',self.packet(move=direction,speed=.4))
            self.assertEqual(self.g.lease.move,direction)
            self.assertEqual(self.g.last_packet['speed'],.4)
        self.now+=.56;self.refresh();self.g.tick()
        self.assertEqual(self.g.last_packet['speed'],0)
        self.assertEqual(self.g.phase,'active')

    def test_invalid_speed_does_not_replace_live_command(self):
        self.activate()
        self.g.request('/api/control',self.packet(move='forward',speed=.2))
        for speed in (float('nan'),float('inf'),-.1,5.0001,True,'0.5'):
            with self.assertRaises(ValueError):
                self.g.request('/api/control',self.packet(move='left',speed=speed))
            self.assertEqual(self.g.lease.move,'forward')
            self.assertEqual(self.g.lease.speed,.2)

    def test_target_speed_range_reaches_planner_without_changing_default(self):
        self.activate();self.g.physical=True
        for speed in (0.,1.,2.5,5.):
            for direction in ('forward','backward','left','right'):
                self.g.request('/api/control',self.packet(move=direction,speed=speed))
                self.assertEqual(self.g.last_packet['speed'],speed)
        self.g.request('/api/control',self.packet(stop=True))
        self.assertEqual(self.g.last_packet['speed'],0.)
        self.assertEqual(self.g.status()['default_speed'],.5)

    def test_removed_actions_cannot_start_motion(self):
        self.activate()
        for kw in ({'gesture':'wave'},{'gesture':'story'},{'gesture':'dance'},{'run':True},{'action_id':'old'}):
            with self.assertRaises(ValueError):self.g.request('/api/control',self.packet(**kw))
        with self.assertRaises(ValueError):self.g.request('/api/action/reservation',self.packet())
        self.assertFalse(self.g.lease.move)
        self.assertNotIn('gesture',self.g.status())

    def test_custom_default_survives_prepare_and_stop(self):
        self.g.lease=PlannerLease(.4)
        self.activate()
        self.g.request('/api/control',self.packet(move='forward'))
        self.assertEqual(self.g.last_packet['speed'],.4)
        self.g.request('/api/control',self.packet(stop=True))
        self.assertEqual(self.g.status()['default_speed'],.4)

    def test_pose_stationary_requirement_and_stop_return(self):
        self.activate();self.g.pose_reader=Mock()
        self.g.pose_reader.snapshot.return_value={'ready':True,'packet':b'pose','reason':''}
        self.g.ingest('g1_debug',{'encoder_mode':0,'play':True})
        with self.assertRaises(ValueError):self.command('select_pose')
        self.now+=.6;self.refresh();self.g.tick()
        self.g.ingest('g1_debug',{'encoder_mode':0,'play':True})
        self.assertTrue(self.g.status()['stationary']);self.command('select_pose')
        with self.assertRaises(ValueError):self.g.request('/api/control',self.packet(move='left'))
        self.g.request('/api/control',self.packet(stop=True))
        self.assertEqual(self.g.input_mode,'planner')
        self.assertTrue(self.g.mode_pending)

if __name__=='__main__':unittest.main()
