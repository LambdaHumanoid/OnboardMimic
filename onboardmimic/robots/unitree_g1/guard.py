from pathlib import Path
from onboardmimic.core.admission import AdmissionCheckError
from .preflight import processes, service, SERVICES

def physical_guard(owned_pid=None):
    # Check both service managers before admission.
    for user in (False, True):
        for name in SERVICES:
            s = service(name, user=user)
            if ('error' in s or s.get('LoadState') not in ('loaded', 'not-found') or
                    s.get('ActiveState') not in ('inactive', 'failed')):
                raise RuntimeError('检测到其他运动服务运行或状态未知，请先停止现有部署')
    # Physical control requires the dedicated loopback bridge.
    bridge = service('onboardmimic-bridge.service', user=True)
    if bridge.get('ActiveState') != 'active':
        raise RuntimeError('桥接未就绪，请先点击连接服务并等待连接完成')
    bridge_pid = int(bridge.get('MainPID', '0'))
    running, errors = processes()
    if bridge_pid <= 0 or errors or any(p['pid'] not in (owned_pid, bridge_pid) for p in running):
        unexpected = [p for p in running if p['pid'] not in (owned_pid, bridge_pid)]
        # Never log complete argv: arguments may contain credentials.
        raise AdmissionCheckError('已有控制进程或无法完整检查进程；拒绝启动', dict(
            check='process_inventory', owned_pid=owned_pid, bridge_pid=bridge_pid,
            invalid_bridge_pid=bridge_pid <= 0, unexpected_count=len(unexpected),
            unexpected_processes=[dict(pid=p['pid'], program=Path(p['argv'][0]).name
                if p.get('argv') else '') for p in unexpected[:16]],
            inspection_error_count=len(errors), inspection_errors=[
                dict(pid=e.get('pid'), error=str(e.get('error',''))[:300]) for e in errors[:16]]))
    for protocol, port in (('tcp',16660),('tcp',16661),('udp',16663)):
        entries = [line.split() for line in Path('/proc/net/'+protocol).read_text().splitlines()[1:]]
        matching = [e for e in entries if int(e[1].split(':')[1],16) == port and
                    (protocol == 'udp' or e[3] == '0A')]
        if len(matching) != 1 or matching[0][1].split(':')[0] != '0100007F':
            raise RuntimeError('专用桥接端口必须只监听 127.0.0.1：%d' % port)
