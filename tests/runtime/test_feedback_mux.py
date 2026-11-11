import unittest
from onboardmimic.robots.unitree_g1.continuity.state_mux import Frame,StateMux,TOPICS

class Tests(unittest.TestCase):
 def pair(self,m,source,tick,at,session='s'):
  for topic in TOPICS:
   m.ingest(Frame(source,topic,session,tick,at,b'payload',tick if topic==TOPICS[0] else None),at)
 def test_sticky_failover_no_replayed_tick(self):
  m=StateMux();self.pair(m,'primary',10,1);self.pair(m,'standby',10,1);self.assertEqual(len(m.select(1)),2)
  self.pair(m,'standby',11,1.06);self.assertEqual([f.source for f in m.select(1.06)],['standby']*2)
  self.pair(m,'primary',12,1.07);self.pair(m,'standby',12,1.07);m.select(1.07);self.assertEqual(m.active,'standby')
 def test_complete_outage_emits_nothing(self):
  m=StateMux();self.pair(m,'primary',1,1);m.select(1);self.assertEqual(m.select(1.2),[]);self.assertIsNone(m.active)
 def test_stale_and_future_frames(self):
  m=StateMux()
  for captured in (1,3,float('nan')):
   self.assertFalse(m.ingest(Frame('primary',TOPICS[0],'s',1,captured,b'x',1),2))
 def test_frozen_tick_cannot_be_refreshed(self):
  m=StateMux();self.pair(m,'primary',10,1);m.select(1)
  self.assertFalse(m.ingest(Frame('primary',TOPICS[0],'s',11,1.04,b'x',10),1.04));self.assertEqual(m.select(1.06),[])
 def test_partial_source_not_selected(self):
  m=StateMux();m.ingest(Frame('primary',TOPICS[0],'s',1,1,b'x',1),1);self.assertEqual(m.select(1),[])
 def test_no_resend_and_no_older_tick_after_switch(self):
  m=StateMux();self.pair(m,'primary',20,1);m.select(1);self.assertEqual(m.select(1.01),[])
  self.pair(m,'standby',19,1.06);self.assertNotIn(TOPICS[0],[f.topic for f in m.select(1.06)])
 def test_restart_requires_both_topics_and_retired_session_rejected(self):
  m=StateMux();self.pair(m,'primary',1,1);m.select(1)
  m.ingest(Frame('primary',TOPICS[0],'new',2,1.06,b'x',2),1.06);self.assertEqual(m.select(1.06),[])
  self.assertFalse(m.ingest(Frame('primary',TOPICS[1],'s',3,1.07,b'x'),1.07))
 def test_tick_wrap(self):
  m=StateMux();self.pair(m,'primary',2**32-1,1);m.select(1)
  for t in TOPICS:m.ingest(Frame('primary',t,'s',2**32,1.01,b'x',0 if t==TOPICS[0] else None),1.01)
  self.assertEqual(len(m.select(1.01)),2)

if __name__=='__main__':unittest.main()
