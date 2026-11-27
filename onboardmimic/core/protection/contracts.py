"""Future actuator-side integration contract; no physical implementation enabled.

An executor must use independent robot feedback and a strategy admission check.
It must never infer control authority from a process exit or this shadow monitor.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class AuthorityToken:
    session: str
    generation: int
    owner: str

class ProtectionArbiter(ABC):
    @abstractmethod
    def revoke_and_claim(self, expected_wbc: AuthorityToken) -> AuthorityToken:
        """Atomically fence old WBC output before granting protection ownership.
        Reject stale generation, active estop, absent state or actuator faults.
        This must be enforced at the actual motor writer, not just in Python.
        """

    @abstractmethod
    def finish(self, protection: AuthorityToken):
        """Latch completion. WBC restart cannot reclaim without explicit reset."""

    @abstractmethod
    def estop(self):
        """Revoke every output token immediately; never initiate a roll."""
