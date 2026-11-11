from onboardmimic.core.connection import ServiceConnection
from onboardmimic.core.paths import STATE

class G1Connection(ServiceConnection):
    def __init__(self):
        super().__init__('onboardmimic-console-connect.service',
                         'onboardmimic-bridge.service',STATE/'connect-status.json')
