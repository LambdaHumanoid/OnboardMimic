"""Reproduce offline failure cases. Does not connect to hardware."""
import argparse
import concurrent.futures
import json
from pathlib import Path
from simulate import simulate


def run(case):
    name, config, output = case
    return name, simulate(config, output / name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    cases = []
    for mode in ('zero_torque', 'last_command_pd'):
        for delay in (0., .05, .1, .2):
            name = f'{mode}-{round(delay*1000):03d}ms'
            cases.append((name, dict(config, failure_mode=mode, takeover_delay_s=delay), args.output))
        cases.append((mode+'-no-takeover', dict(config, failure_mode=mode, no_takeover=True), args.output))
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(run, cases))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'comparison.json').write_text(json.dumps(results, indent=2))
    for name, result in results.items():
        print(name, 'knees_first=', result['knees_before_upper_body'],
              'head_contact_s=', result['first_head_contact_s'],
              'geometry_pass=', result['knee_first_geometry_pass'])
