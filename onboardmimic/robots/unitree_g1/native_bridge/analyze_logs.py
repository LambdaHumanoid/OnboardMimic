"""Print per-run native bridge watchdog transitions and delay evidence."""
import argparse,json
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();root=Path(a.directory)
    rows=[]
    for line in (root/'events.jsonl').read_text().splitlines():
        try:rows.append(json.loads(line))
        except json.JSONDecodeError:print('Incomplete final log line (possible abrupt shutdown)')
    starts=[r for r in rows if r.get('event')=='native_bridge_start']
    stats=[r for r in rows if r.get('event')=='native_bridge_stats']
    events=[r for r in rows if r.get('event') in ('watchdog_transition','bridge_delay','scheduler_snapshot')]
    print(json.dumps(dict(start=starts,last_stats=stats[-1] if stats else None,events=events,window_file=str(root/'windows.jsonl')),indent=2))
if __name__=='__main__':main()
