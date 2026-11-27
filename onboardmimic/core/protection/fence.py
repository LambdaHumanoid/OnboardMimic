"""In-process reference fence for isolated executors; NOT the physical bridge.

Every writer must go through submit for this fence to enforce exclusivity.
The deployed native motor writer is not connected to this reference fence.
"""
import threading
from .contracts import AuthorityToken, ProtectionArbiter

class ReferenceFence(ProtectionArbiter):
    def __init__(self):
        self.lock=threading.RLock();self.token=None;self.generation=0;self.estopped=False
        self.latched=False

    def reset_for_session(self, session):
        with self.lock:
            if not session:raise ValueError('Session required')
            self.generation+=1;self.estopped=False;self.latched=False
            self.token=AuthorityToken(session,self.generation,'wbc')
            return self.token

    def _require(self, token):
        if self.estopped or self.latched or token is None or token!=self.token:
            raise PermissionError('Stale or revoked control authority')

    def revoke_and_claim(self, expected_wbc):
        with self.lock:
            self._require(expected_wbc)
            if expected_wbc.owner!='wbc':raise PermissionError('Expected WBC owner')
            self.generation+=1
            self.token=AuthorityToken(expected_wbc.session,self.generation,'protection')
            return self.token

    def submit(self, token, write):
        with self.lock:
            self._require(token)
            # The write and transfer serialize; checking then writing unlocked
            # would let a revoked producer sneak a command past the handoff.
            return write()

    def finish(self, protection):
        with self.lock:
            self._require(protection)
            if protection.owner!='protection':raise PermissionError('Expected protection owner')
            self.token=None;self.latched=True

    def estop(self):
        with self.lock:self.token=None;self.estopped=True;self.latched=True
