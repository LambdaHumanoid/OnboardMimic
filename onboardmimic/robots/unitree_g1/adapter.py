"""Unitree G1 hardware contract. Construction is read-only and SDK-free."""
import json
import logging
from pathlib import Path
from onboardmimic.core.contracts import RobotAdapter
from .telemetry import feedback, safe
from .guard import physical_guard
from .continuity.health import FeedbackHealth

ROOT = Path(__file__).resolve().parent


class G1Adapter(RobotAdapter):
    id = 'unitree_g1'
    label = 'G1'
    joint_count = 29
    state_topics = frozenset(('rt/lowstate', 'rt/secondary_imu'))
    state_port = 16660
    command_port = 16661
    command_udp_port = 16663
    safe = staticmethod(safe)
    guard = staticmethod(physical_guard)
    telemetry = staticmethod(feedback)

    def __init__(self):
        self.health = FeedbackHealth()

    def ingest(self, topic, value, now):
        if topic == 'rt/continuity' and self.health.ingest(value, now):
            logging.getLogger('control_audit').info(json.dumps(
                dict(event='feedback_source_changed', feedback=self.health.status)))

    def feedback_ready(self, latest, now):
        return self.health.ready(now, latest)

    def feedback_status(self, latest, now):
        return {'feedback_continuity': dict(ready=self.feedback_ready(latest,now),
            source=(self.health.status or {}).get('source'),
            status=self.health.status, blind_hold_extended=False)}

    def service_units(self, root, config):
        from .service_units import render_units
        return render_units(root, config)

    def environment_template(self, root):
        return (root/"onboardmimic/robots/unitree_g1/systemd/bridge.env.example").read_text()

    def connection(self):
        from .connection import G1Connection
        return G1Connection()

    def controller_environment(self):
        from .bridge.network import detect, configure
        from onboardmimic.core.paths import state_dir
        directory = state_dir()
        interface = detect()
        target = directory/'controller-dds.xml'
        configure(directory/'command-dds.xml',target,interface)
        return interface, {'CYCLONEDDS_URI':target.resolve().as_uri(),
                           'UNITREE_DDS_INTERFACE':interface}
