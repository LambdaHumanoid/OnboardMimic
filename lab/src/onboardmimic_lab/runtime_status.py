"""Read-only adapter for the public OnboardMimic controller status API."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class RuntimeStatus:
    def __init__(self, config):
        self.url = config.get('gateway', 'http://127.0.0.1:18921')
        u = urlsplit(self.url)
        if (u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port
                or u.path or u.query or u.fragment or u.username or u.password):
            raise ValueError('runtime gateway must be local http://127.0.0.1:PORT')
        path = Path(config.get('token_file', Path.home()/'.config/onboardmimic/http.token'))
        if path.stat().st_mode & 0o077: raise ValueError('gateway token file must be private')
        self.token = path.read_text().strip()
        if len(self.token) < 32: raise ValueError('invalid gateway token')

    def __call__(self):
        request = Request(self.url+'/api/controller', headers={'Authorization':'Bearer '+self.token})
        with urlopen(request, timeout=1) as response: value = json.load(response)
        if not isinstance(value, dict): raise ValueError('invalid runtime status')
        # Do not forward control tickets, epochs or credentials into the Lab UI.
        result = {key:value[key] for key in ('state','input_mode','mode_switch_pending','pose_input_ready',
                'pose_input_detail','telemetry','robot_id','backend','workflow_error','feedback_continuity') if key in value}
        if isinstance(value.get('lab'), dict):
            result['lab'] = {key:value['lab'][key] for key in
                             ('record_ready', 'contract_sha256', 'session_id', 'reason', 'native_consumption_fresh', 'diagnostics', 'recording_validation') if key in value['lab']}
        return result
