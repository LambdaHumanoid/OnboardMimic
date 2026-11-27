"""Reference execution boundary, independent of the legacy Planner lifecycle."""
from abc import ABC, abstractmethod


class ExecutionAdapter(ABC):
    """Adapters own live input sampling and report only committed consumption.

    Hardware adapters must integrate the native protected writer, not the
    in-process software fence. Registration alone never authorizes hardware.
    """
    @abstractmethod
    def controller_spec(self): pass

    @abstractmethod
    def reset(self): pass

    @abstractmethod
    def start(self, owner, mode): pass

    @abstractmethod
    def submit(self, owner, session, contract, reference_id, reference): pass

    @abstractmethod
    def step(self): pass

    @abstractmethod
    def status(self): pass

    @abstractmethod
    def stop(self, reason='operator_stop'): pass


EXECUTIONS = {'software_joint.v1': 'onboardmimic.core.execution.software:SoftwareJoint'}


def create(execution_id, *, physical=False, **kwargs):
    from importlib import import_module
    if execution_id not in EXECUTIONS:
        raise ValueError('Unregistered execution')
    if physical:
        raise ValueError('No reference execution adapter authorized for hardware')
    module, name = EXECUTIONS[execution_id].split(':')
    adapter = getattr(import_module(module), name)(**kwargs)
    if not isinstance(adapter, ExecutionAdapter):
        raise TypeError('Invalid execution adapter')
    return adapter
