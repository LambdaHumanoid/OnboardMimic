#!/usr/bin/env python3
"""Export explicitly synthetic v2-draft protocol examples; never a robot config."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.sonic.test_native_consumption import fixture
from onboardmimic.backends.sonic.native.consumption import NativeJoin

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',default='examples/oml/sonic-native-v2-draft')
args=parser.parse_args()
native,bridge,attempt,mapping,commit,now=fixture()
join=NativeJoin(clock_ns=lambda:now)
for source,event in [('native',dict(kind='manifest',manifest=native)),
                     ('bridge',dict(kind='manifest',manifest=bridge)),
                     ('native',attempt),('native',mapping),('bridge',commit)]:
    sample=join.ingest(source,dict(event,native_session=source+'-TEST_FIXTURE',published_monotonic_ns=now))
assert sample is not None and join.spec['test_fixture']
target=Path(args.output);target.mkdir(parents=True,exist_ok=True)
for name,value in [('controller-spec.fixture.json',join.spec),('consumed.fixture.json',sample),
                   ('lab-status.fixture.json',{'lab':join.status()})]:
    (target/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
print('Exported synthetic v2-draft protocol examples; not validated hardware or loaded model evidence')
