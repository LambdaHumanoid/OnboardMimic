"""Service syntax and idle API/UI units, independent of robot transport."""
BOOT_UNITS=("onboardmimic.service","onboardmimic-console.service")

def quote(path):
    return '"'+str(path).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'

def unit_path(path):
    # systemd 245 parses these directives as one raw path, not ExecStart words.
    # Spaces are literal; quotes would become part of the filename.
    value=str(path)
    if not value.startswith('/') or any(ord(c)<32 for c in value) or value!=value.strip():
        raise ValueError('Service paths must be absolute and contain no control or edge whitespace')
    return value.replace('%','%%')

def idle_units(root, config, robot, backend):
    result = {}
    for name, module, extra in (
        (BOOT_UNITS[0], 'onboardmimic.core.runtime', '--physical'),
        (BOOT_UNITS[1], 'onboardmimic.console.server', '--host 127.0.0.1 --port 8769'),
    ):
        result[name] = '[Unit]\nDescription=OnboardMimic idle {module}\n[Service]\nType=simple\nWorkingDirectory={cwd}\nExecStart={python} -m {module} {extra} --token-file {token} --robot {robot} --backend {backend}\nKillMode=control-group\nRestart=always\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n'.format(module=module, cwd=unit_path(root), python=quote(root/'.venv/bin/python'),
                 extra=extra, token=quote(config/'http.token'), robot=quote(robot.id), backend=quote(backend.id))
    return result
