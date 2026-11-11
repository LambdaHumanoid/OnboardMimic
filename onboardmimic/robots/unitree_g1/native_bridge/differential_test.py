"""Compare native watchdog transitions with the installed Python reference.
Deliberately changed checks (local packet age, CRC every packet, fresh measured
recovery positions) are covered by protocol and integration tests separately.
"""
import argparse,random,subprocess,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--binary',required=True);a=p.parse_args()
    sys.path.insert(0,str(Path(a.reference).resolve().parent))
    from lowcmd_transport import LowCmdWatchdog,LowCmdTransportMetadata,CommandDisposition
    r=random.Random(7633);w=LowCmdWatchdog();now=1.;seqs={};rows=[];expected=[]
    for i in range(20000):
        # Include regular input, occasional long silence, duplicates, competing
        # streams and enough consecutive new-stream packets to recover.
        now+=r.choice([.002]*30+[.003,.08,.101,.151,.6]);stream=1+(i//100)%4
        seq=seqs.get(stream,0)+(0 if r.random()<.03 else 1);seqs[stream]=seq
        op='p' if r.random()<.9 else 't';send=int(now*1e9)
        if op=='p':d=w.observe(LowCmdTransportMetadata(stream,seq,send),now);apply=d.disposition!=CommandDisposition.HOLD
        else:w.advance(now);apply=False
        rows.append('%s %.17g %d %d %d'%(op,now,stream,seq,send));expected.append('%s %d'%(w.state.value,apply))
    result=subprocess.check_output([a.binary,'trace'],input='\n'.join(rows)+'\n',text=True).splitlines()
    for i,(x,y) in enumerate(zip(result,expected)):
        if x!=y:raise AssertionError((i,rows[max(0,i-3):i+1],x,y))
    assert len(result)==len(expected)
    print('PASS: 20,000 independent-reference watchdog transitions')
if __name__=='__main__':main()
