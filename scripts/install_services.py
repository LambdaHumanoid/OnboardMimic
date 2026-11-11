"""Install or migrate idle API/console user services; never start robot processes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
from onboardmimic.core.service_units import BOOT_UNITS, quote, unit_path, idle_units
from onboardmimic.core.registry import select, DEFAULT_ROBOT, DEFAULT_BACKEND


def render_units(root, config, robot=DEFAULT_ROBOT, backend=DEFAULT_BACKEND):
    body, controller = select(robot, backend)
    units = body.service_units(root, config)
    units.update(idle_units(root, config, body, controller))
    return units


def installed_root(text):
    match=re.search(r'^ExecStart="(.+)/\.venv/bin/python" ',text,re.M)
    if not match:raise SystemExit('Unrecognized service layout / 无法识别旧服务格式；请先人工核对。')
    # Generated systemd quoting, not shell evaluation.
    raw=match[1].replace('%%','%').replace('\\"','"').replace('\\\\','\\')
    return Path(raw)

def require_stopped(names):
    for name in names:
        result=subprocess.run(['systemctl','--user','show',name,'--property=LoadState,ActiveState','--no-pager'],capture_output=True,text=True)
        fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode or fields.get('LoadState') not in ('loaded','not-found') or fields.get('ActiveState') not in ('inactive','failed'):
            raise SystemExit('Stop services before upgrading / 升级前请先停止服务: '+name+' (no service was stopped automatically / 未自动停止任何服务)')

def write_atomic(path, data, mode=0o644):
    temp=path.with_name(path.name+'.new-'+secrets.token_hex(4))
    try:
        temp.write_text(data);temp.chmod(mode);temp.replace(path)
    finally:
        if temp.exists():temp.unlink()

def migrate_generated_dropin(name, text, previous_root, root, config, previous=None, wanted=None):
    """Rebuild only exact generated command overrides; preserve custom drop-ins."""
    old = previous or render_units(previous_root, config)
    new = wanted or render_units(root, config)
    if name not in old or name not in new:
        return None
    old_command = next((line for line in old[name].splitlines() if line.startswith('ExecStart=')), '')
    new_command = next((line for line in new[name].splitlines() if line.startswith('ExecStart=')), '')
    variants = []
    if name == 'onboardmimic.service':
        path = 'onboardmimic/inputs/pico/runs/live/status.json'
        variants.append((old_command+' --pose-status '+quote(previous_root/path),
                         new_command+' --pose-status '+quote(root/path)))
    elif name == 'onboardmimic-console.service':
        for access in ('127.0.0.1', '0.0.0.0'):
            variants.append((old_command.replace('--host 127.0.0.1','--host '+access),
                             new_command.replace('--host 127.0.0.1','--host '+access)))
    for before, after in variants:
        if text.strip() == ('[Service]\nExecStart=\n'+before).strip():
            return '[Service]\nExecStart=\n'+after+'\n'
    return None


def install(upgrade=False, robot=DEFAULT_ROBOT, backend=DEFAULT_BACKEND):
    config=Path.home()/'.config/onboardmimic'
    units=Path.home()/'.config/systemd/user'
    wanted=render_units(ROOT,config,robot=robot,backend=backend)
    existing={name:(units/name).read_text() for name in wanted if (units/name).exists()}
    changed=[name for name,data in wanted.items() if name in existing and existing[name]!=data]
    manifest=config/'installed_services.json'
    relocated={}
    if changed:
        if not upgrade:
            raise SystemExit('Existing services differ. Use --upgrade after stopping SONIC and its services. / 服务已存在且不同；请先停止 SONIC 和服务，再加 --upgrade 迁移。')
        # Validate the whole migration before writing any unit or changing boot settings.
        require_stopped(wanted)
        if manifest.exists():
            baseline=json.loads(manifest.read_text())
            previous_root=Path(baseline['root']);previous=baseline['units']
            valid=lambda name:existing[name]==previous.get(name)
        else:
            runtime=existing.get('onboardmimic.service',existing.get('onboardmimic-console.service',''))
            previous_root=installed_root(runtime)
            previous=render_units(previous_root,config,robot=robot,backend=backend)
            valid=lambda name:existing[name]==previous.get(name)
        for name in existing:
            if not valid(name):
                raise SystemExit('Custom unit preserved / 已保留自定义服务，请先将自定义参数迁入 systemd drop-in: '+name)
        if changed:
            for name in wanted:
                for dropin in (units/(name+'.d')).glob('*.conf'):
                    if str(previous_root) in dropin.read_text() or quote(previous_root)[1:-1] in dropin.read_text():
                        migrated=migrate_generated_dropin(name,dropin.read_text(),previous_root,ROOT,config,previous,wanted)
                        if migrated is None:
                            raise SystemExit('Drop-in references the old installation; update it first / drop-in 引用了旧安装目录，请先更新: '+str(dropin))
                        relocated[dropin]=(dropin.read_text(),migrated)
    config.mkdir(parents=True,exist_ok=True,mode=0o700)
    units.mkdir(parents=True,exist_ok=True)
    token=config/'http.token'
    if token.exists() and token.stat().st_mode & 0o077:raise SystemExit('Existing token must have mode 0600 / token 权限必须为 0600')
    if not token.exists():
        fd=os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(48)+'\n')
    env=config/'bridge.env'
    if not env.exists():write_atomic(env,select(robot,backend)[0].environment_template(ROOT),0o600)
    if changed:
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)
        backup=config/'backups'/stamp;backup.mkdir(parents=True,mode=0o700)
        for name in existing:shutil.copy2(units/name,backup/name)
        for name in wanted:
            dropins=units/(name+'.d')
            if dropins.exists():shutil.copytree(dropins,backup/dropins.name)
        for file in (env,manifest):
            if file.exists():shutil.copy2(file,backup/file.name)
        print('Upgrade backup / 升级备份:',backup)
    # Enable lingering before updating files, so a permission failure leaves old units intact.
    user=pwd.getpwuid(os.getuid()).pw_name
    result=subprocess.run(['loginctl','enable-linger',user],capture_output=True,text=True)
    if result.returncode:
        raise SystemExit('Boot setup incomplete / 开机自启设置未完成. Run / 请执行: sudo loginctl enable-linger '+user+'\nThen rerun this installer / 然后重新运行本安装脚本.\n'+result.stderr)
    try:
        for name,data in wanted.items():
            if existing.get(name)!=data:write_atomic(units/name,data)
        for path,(_,data) in relocated.items():write_atomic(path,data)
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable',*BOOT_UNITS],check=True)
    except Exception:
        for path,(data,_) in relocated.items():write_atomic(path,data)
        for name in wanted:
            if name in existing:write_atomic(units/name,existing[name])
            elif (units/name).exists():(units/name).unlink()
        subprocess.run(['systemctl','--user','daemon-reload'],check=False)
        raise
    # Store generated baselines, not user secrets or drop-in customizations.
    write_atomic(manifest,json.dumps({'schema':2,'root':str(ROOT),'robot':robot,'backend':backend,'units':wanted},indent=2)+'\n',0o600)
    print('Console and API enabled at boot; no services started now. / 控制台和后台接口已设为开机自启；本次未启动服务。')
    print('Bridge connection, posture preparation and SONIC start remain manual. / 桥接连接、准备姿态和启动 SONIC 仍需手动触发。')
    print('Configure the bridge / 请配置桥接:',env)
    print('Open console / 打开控制台:')
    print('  Robot / 机器人: systemctl --user start onboardmimic.service onboardmimic-console.service')
    print('  Laptop / 电脑: ssh -N -L 8769:127.0.0.1:8769 USER@ROBOT_HOST')
    print('  Browser / 浏览器: http://127.0.0.1:8769/')
    print('Replace USER and ROBOT_HOST; keep the tunnel open. / 请替换机器人用户名和地址，并保持 SSH 隧道运行。')
    print('Opening the console does not prepare the robot or start SONIC. / 打开控制台不会回正或启动 SONIC。')
    print('First-run setup: docs/DEPLOYMENT.md; Upgrade: docs/UPGRADE.md / 首次部署: docs/DEPLOYMENT_zh.md; 升级: docs/UPGRADE_zh.md')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upgrade',action='store_true',help='Back up and migrate stopped, generated service units; preserve config and drop-ins / 备份迁移已停止的服务并保留配置')
    parser.add_argument("--robot",default=DEFAULT_ROBOT)
    parser.add_argument("--backend",default=DEFAULT_BACKEND)
    args=parser.parse_args(argv)
    install(args.upgrade,args.robot,args.backend)

if __name__=='__main__':main()
