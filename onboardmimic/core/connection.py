"""Systemd connection presentation, independent of robot protocols."""
import json

class ServiceConnection:
    def __init__(self, connect_unit, bridge_unit, status_path):
        self.connect_unit, self.bridge_unit = connect_unit, bridge_unit
        self.connect_status = status_path

    def systemctl(self, runner, *args):
        return runner(['systemctl','--user',*args],capture_output=True,text=True,timeout=4)

    def status(self, base, runner):
        s = dict(base)
        result = self.systemctl(runner, 'show', self.connect_unit, '--property=ActiveState,Result', '--no-pager')
        props = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        setup = props.get('ActiveState','unknown')
        bridge = self.systemctl(runner, 'show', self.bridge_unit, '--property=ActiveState,MainPID', '--no-pager')
        bridge_props = dict(line.split('=',1) for line in bridge.stdout.splitlines() if '=' in line)
        connected = (not result.returncode and not bridge.returncode and setup == 'active'
                     and props.get('Result') == 'success' and bridge_props.get('ActiveState') == 'active'
                     and bridge_props.get('MainPID', '0').isdigit() and int(bridge_props.get('MainPID', '0')) > 0)
        s['setup_state'] = setup
        s['setup_available'] = s.get('state') in ('stopped','error') and not s.get('pids') and setup != 'activating'
        s['setup_error'], s['setup_message'] = '', ''
        try:
            progress = json.loads(self.connect_status.read_text())
            if not isinstance(progress, dict): progress = {}
        except (OSError, ValueError):
            progress = {}
        connected = bool(connected and progress.get('phase') == 'ready'
                         and progress.get('bridge_pid') == int(bridge_props.get('MainPID','0')))
        s['setup_ready'] = connected
        if setup == 'activating':
            s['setup_message'] = progress.get('detail') if progress.get('phase') in ('feedback','release','bridge') else '正在连接控制服务'
        elif setup == 'failed' or props.get('Result') not in ('','success',None):
            detail = progress.get('detail') if progress.get('phase') == 'error' else props.get('Result','unknown')
            s['setup_error'] = '连接服务失败：'+str(detail)
        elif setup == 'active' and not connected:
            s['setup_error'] = '桥接未就绪，请重新连接服务'
        elif connected and s.get('state') in ('stopped','error'):
            s['setup_message'] = '控制服务已连接'
        if not connected:
            s['prepare_available'] = False
            s['pose_ready'] = False
        return s

    def connect(self, runner):
        return self.systemctl(runner,'restart','--no-block',self.connect_unit)

    def cancel(self, runner):
        return self.systemctl(runner,'stop',self.connect_unit)
