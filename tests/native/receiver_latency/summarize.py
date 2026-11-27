import json
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
report = {}
for variant in ('baseline', 'poll'):
    rows = {}
    for path in sorted((root/variant).glob('*.log')):
        results = [json.loads(line[7:]) for line in path.read_text().splitlines() if line.startswith('RESULT ')]
        if not results:
            continue
        row, = results
        assert row['errors'] == 0 and row['command_output'] == 0, row
        if path.stem.startswith('normal'):
            assert row['callbacks'] == 1500 and row['imu_callbacks'] == 750, row
        else:
            assert row['max_callback_gap_ms'] >= 80 and row['callbacks'] >= 1400, row
        rows[path.stem] = row
    assert len(rows) == 4, rows
    normal = [v for k, v in rows.items() if k.startswith('normal')]
    report[variant] = dict(runs=rows, median_of_normal_runs={
        k: statistics.median(v[k] for v in normal)
        for k in ('idle_cpu_cores', 'stream_cpu_cores', 'median_ms', 'p95_ms', 'p99_ms', 'max_callback_gap_ms')})
(root/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({k:v['median_of_normal_runs'] for k,v in report.items()}, indent=2))
