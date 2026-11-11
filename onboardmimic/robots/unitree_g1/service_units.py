"""G1 transport and explicit hardware-connection services; never enabled at boot."""
from onboardmimic.core.service_units import quote, unit_path

def render_units(root, config):
    env=config/'bridge.env'
    bridge='''[Unit]
Description=OnboardMimic G1 dedicated loopback bridge
[Service]
Type=simple
EnvironmentFile={env}
ExecStart=/bin/bash {script}
KillMode=control-group
Restart=no
'''.format(env=unit_path(env),script=quote(root/'onboardmimic/robots/unitree_g1/bridge/run_bridge.sh'))
    connect='''[Unit]
Description=OnboardMimic G1 explicit connection and vendor-mode release
[Service]
Type=oneshot
RemainAfterExit=yes
{connect_environment}TimeoutStartSec=60
ExecStart=/bin/bash {script}
Restart=no
'''.format(connect_environment='EnvironmentFile='+unit_path(env)+'\n',script=quote(root/'onboardmimic/robots/unitree_g1/connect_services.sh'))
    units={'onboardmimic-bridge.service':bridge,'onboardmimic-console-connect.service':connect}
    readers=' '.join('onboardmimic-state-'+role+'.service' for role in ('primary','standby','relay'))
    units['onboardmimic-bridge.service']=bridge.replace('[Service]', 'Wants='+readers+'\nAfter='+readers+'\n[Service]')
    for role in ('primary','standby','relay'):
        units['onboardmimic-state-'+role+'.service']='''[Unit]
Description=OnboardMimic G1 read-only feedback {role}
PartOf=onboardmimic-bridge.service
[Service]
Type=simple
EnvironmentFile={env}
ExecStart=/bin/bash {script} {role}
Restart=on-failure
RestartSec=1
KillMode=control-group
UMask=0077
'''.format(role=role,env=unit_path(env),script=quote(root/'onboardmimic/robots/unitree_g1/continuity/run_feedback.sh'))
    return units
