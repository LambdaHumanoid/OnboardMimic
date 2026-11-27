import unittest
from onboardmimic.core.protection.fence import ReferenceFence

class FenceTest(unittest.TestCase):
    def test_old_wbc_cannot_write_or_reclaim(self):
        f=ReferenceFence();w=f.reset_for_session('wbc-1');p=f.revoke_and_claim(w)
        for fn in (lambda:f.submit(w,lambda:None),lambda:f.revoke_and_claim(w)):
            with self.assertRaises(PermissionError):fn()
        self.assertEqual(f.submit(p,lambda:42),42)
        f.finish(p)
        with self.assertRaises(PermissionError):f.submit(p,lambda:None)
    def test_estop_revokes_every_owner_until_explicit_reset(self):
        f=ReferenceFence();w=f.reset_for_session('wbc-1');f.estop()
        with self.assertRaises(PermissionError):f.revoke_and_claim(w)
        w2=f.reset_for_session('wbc-2')
        with self.assertRaises(PermissionError):f.submit(w,lambda:None)
        self.assertEqual(f.submit(w2,lambda:43),43)
