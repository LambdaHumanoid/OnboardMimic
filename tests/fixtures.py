from onboardmimic.core.lifecycle import Lifecycle
from onboardmimic.robots.unitree_g1.adapter import G1Adapter
from onboardmimic.backends.sonic.adapter import SonicBackend

class NativeLifecycle(Lifecycle):
    def __init__(self,*args,**kwargs):
        kwargs.setdefault('robot',G1Adapter())
        kwargs.setdefault('backend',SonicBackend())
        kwargs.setdefault('require_continuity',False)
        super().__init__(*args,**kwargs)

class FeedbackLifecycle(NativeLifecycle):
    def __init__(self,*args,**kwargs):
        kwargs['require_continuity']=True
        super().__init__(*args,**kwargs)
        self.feedback_health=self.robot.health

from onboardmimic.robots.unitree_g1.telemetry import feedback, safe
