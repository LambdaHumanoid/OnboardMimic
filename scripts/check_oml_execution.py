#!/usr/bin/env python3
"""Integration check against the bundled Lab (no services)."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--oml', default=str(Path(__file__).resolve().parents[1]/'lab'), help='Override bundled Lab checkout')
parser.add_argument('--export-dir')
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(args.oml).resolve()/'src'))
from onboardmimic.core.execution import create
from onboardmimic_lab.contracts import validate_spec, validate_sample, fingerprint, ReferencePolicy, next_segment
from onboardmimic_lab.adapters.reference_capture import ready

for count in (3, 7):
    now = [1000000000]
    config = dict(joint_names=['joint_'+str(i) for i in range(count)], period_ns=20000000,
                  max_gap_ns=60000000, watchdog_ns=200000000, speed_rad_s=1., limit_rad=2.)
    a = create('software_joint.v1', config=config, clock_ns=lambda: now[0], realtime_ns=lambda: now[0]+100000000000)
    spec = validate_spec(a.controller_spec())
    assert fingerprint(spec) == a.contract
    a.start('conformance', 'teleoperation')
    assert not ready(a.status(), spec)
    samples = []
    for index in range(3):
        a.submit('conformance', a.session, a.contract, 'frame-'+str(index), {'q_target':[.1]*count})
        now[0] += config['period_ns']
        sample = validate_sample(a.step(), spec)
        assert next_segment(samples[-1] if samples else None, sample, 0, spec) == 0
        samples.append(sample)
        assert ready(a.status(), spec)
    for difference in ('semantic', 'joint_names', 'artifact'):
        changed = deepcopy(spec)
        if difference == 'semantic': changed['reference']['q_target']['semantic']='joint_velocity'
        elif difference == 'joint_names': changed['reference']['q_target']['joint_names'].reverse()
        else: changed['artifacts']['loaded_config']='f'*64
        try: ReferencePolicy(lambda _: [], changed, spec)
        except ValueError: pass
        else: raise AssertionError('OML accepted incompatible '+difference)
    a.stop()
    assert not ready(a.status(), spec)
    old = a.session
    a.reset()
    assert a.session != old and not ready(a.status(), spec)
    if args.export_dir:
        target = Path(args.export_dir); target.mkdir(parents=True, exist_ok=True)
        collection = dict(capture_adapter='reference-v1', root='./data-software-joint', same_host=True,
                          telemetry_endpoint='tcp://127.0.0.1:16688', camera_port=18554, max_duration_s=120,
                          controller_spec=spec)
        (target/('software-joint-%d.collection.json'%count)).write_text(json.dumps(collection, indent=2)+'\n')
        (target/('software-joint-%d.samples.jsonl'%count)).write_text(''.join(json.dumps(s)+'\n' for s in samples))
print('OML conformance passed: 3/7 joints, spec/hash/sample/segments/readiness/reset/semantic rejection')
