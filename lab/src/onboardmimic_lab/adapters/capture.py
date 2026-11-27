"""Explicit reference capture protocols; no implicit legacy SONIC fallback."""
from pathlib import Path
import json
from ..contracts import validate_spec


def capture_adapter(config):
    name = config.get('capture_adapter')
    if name in ('reference-v1', 'sonic-native-v2'):
        from .reference_capture import ReferenceCapture
        adapter = ReferenceCapture()
        adapter.id = name
        return adapter
    raise ValueError('unknown capture_adapter: '+str(name))


def validate_capture_config(value, base=None):
    capture_adapter(value)
    config = dict(value); base = Path(base or Path.cwd())
    for key in ('root', 'token_file', 'controller_spec_file'):
        if key in config:
            config[key] = str((base/Path(config[key]).expanduser()).resolve())
    if 'root' not in config: raise ValueError('dataset root is required')
    if 'controller_spec_file' in config:
        if 'controller_spec' in config: raise ValueError('choose inline spec or spec file, not both')
        if Path(config['controller_spec_file']).exists(): load_spec(config)
    else:
        config['controller_spec'] = load_spec(config)
    if config.get('same_host') is not True: raise ValueError('shared OS monotonic clock required')
    endpoint = config['telemetry_endpoint']
    if (not isinstance(endpoint, str) or not endpoint.startswith('tcp://127.0.0.1:')
            or not endpoint.rsplit(':', 1)[1].isdigit()
            or not 1 <= int(endpoint.rsplit(':', 1)[1]) <= 65535):
        raise ValueError('telemetry must publish on local TCP')
    port = config.get('camera_port', 18554)
    if type(port) is not int or not 1024 <= port <= 65535: raise ValueError('invalid camera UDP port')
    config['camera_port'] = port
    # Retired implicit recording deadline; old deployed configs may still carry it.
    config.pop('max_duration_s', None)
    return config


def load_spec(config):
    if 'controller_spec_file' in config:
        try: value = json.loads(Path(config['controller_spec_file']).read_text())
        except FileNotFoundError:
            raise ValueError('等待 OM 导出真实原生 spec / Waiting for OM native spec') from None
    else: value = config['controller_spec']
    spec = validate_spec(value)
    expected = 2 if config.get('capture_adapter') == 'sonic-native-v2' else 1
    if spec['schema_version'] != expected: raise ValueError('capture/spec version mismatch')
    if 'controller_spec_file' in config and expected == 2 and (spec.get('test_fixture') is not False or spec.get('physical_source') is not True):
        raise ValueError('exported native spec must identify a real physical source, not a fixture')
    return spec
