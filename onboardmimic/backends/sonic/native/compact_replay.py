"""Compact dataset admission; live WBC still builds all decoder history."""
from copy import deepcopy
from .consumption import integer, vector
from onboardmimic.core.execution.software import fingerprint


def validate(plan, spec):
    if plan.get('sample_profile')!='lerobot_body_v21' or plan.get('reference_evidence')!='consumed_at_capture':
        raise ValueError('Unknown compact replay provenance')
    frames=plan['frames']
    if not isinstance(frames,list) or not 2<=len(frames):raise ValueError('at least 2 frames required')
    previous=None;first=integer(frames[0]['sample']['timestamp_monotonic_ns'],1)
    for row in frames:
        s=row['sample'];at=integer(s['timestamp_monotonic_ns'],1)
        integer(s['timestamp_realtime_ns'],1);integer(s['index'])
        if integer(row['offset_ns'])!=at-first:raise ValueError('Wrong offset')
        for value,size in ((s['state']['body_q'],29),(s['state']['body_dq'],29),(s['state']['gravity'],3),(s['reference']['token_state'],64)):
            vector(value,size)
            if any(abs(v)>3.402823466e38 for v in value):raise ValueError('float32 overflow')
        if not .8<=sum(v*v for v in s['state']['gravity'])<=1.2:raise ValueError('Invalid gravity')
        if previous and (s['index']!=previous['index']+1 or not 0<at-previous['timestamp_monotonic_ns']<=spec['max_gap_ns'] or s['timestamp_realtime_ns']<=previous['timestamp_realtime_ns']):
            raise ValueError('Replay cannot cross a discontinuity')
        previous=s
    period=spec['period_ns'];duration=len(frames)*period
    q=frames[0]['sample']['state']['body_q'];gravity=frames[0]['sample']['state']['gravity']
    return dict(id=fingerprint(plan),spec=deepcopy(spec),
        timing=dict(mode='sequential_50hz',period_ns=period,source_duration_ns=frames[-1]['offset_ns']+period,scheduled_duration_ns=duration),
        source_indices=[f['sample']['index'] for f in frames],
        source_reference_ids=['parquet:'+plan['source_sha256']+':'+str(f['sample']['index']) for f in frames],
        start_q=q,start_gravity=gravity,
        native=dict(entry_protocol=3,period_ns=period,artifacts=deepcopy(spec['artifacts']),start_q=q,start_gravity=gravity,
            frames=[dict(offset_ns=f['offset_ns'],token=f['sample']['reference']['token_state']) for f in frames]))
