"""Replay admission for compact body recordings; never reconstruct fake history."""
from pathlib import Path
import math
from .contracts import fingerprint, validate_spec, integer, sha256
from .lerobot_dataset import verify, records


def validate_plan(plan):
    if plan.get('schema')!='onboardmimic.lab.reference-plan' or plan.get('schema_version')!=2 or plan.get('sample_profile')!='lerobot_body_v21':
        raise ValueError('Invalid compact replay schema')
    spec=validate_spec(plan['controller_spec'])
    if fingerprint(spec)!=plan['contract_sha256']:raise ValueError('Plan contract mismatch')
    sha256(plan['source_sha256']);integer(plan['segment_index'],'segment')
    if plan.get('reference_evidence')!='consumed_at_capture':raise ValueError('Missing capture provenance')
    frames=plan['frames']
    if not 2<=len(frames):raise ValueError('Plan needs at least 2 frames')
    previous=None;first=frames[0]['sample']['timestamp_monotonic_ns']
    for frame in frames:
        s=frame['sample']
        for name in ('index','timestamp_monotonic_ns','timestamp_realtime_ns'):integer(s[name],name)
        for value,dim in ((s['state']['body_q'],29),(s['state']['body_dq'],29),(s['state']['gravity'],3),(s['reference']['token_state'],64)):
            if not isinstance(value,list) or len(value)!=dim or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>3.402823466e38 for v in value):raise ValueError('Invalid body replay vector')
        if frame['offset_ns']!=s['timestamp_monotonic_ns']-first:raise ValueError('Wrong reference offset')
        if previous and (s['index']!=previous['index']+1 or not 0<s['timestamp_monotonic_ns']-previous['timestamp_monotonic_ns']<=spec['max_gap_ns'] or s['timestamp_realtime_ns']<=previous['timestamp_realtime_ns']):
            raise ValueError('Replay cannot cross a discontinuity')
        previous=s
    return plan


def segment_options(root):
    meta=verify(root);spec=meta['source_manifest']['controller_spec']
    data=list(records(root));start=data[0]['source.robot_monotonic_ns'];result=[]
    for segment in meta['segments']:
        first,last=segment['start_frame'],segment['end_frame_exclusive']
        result.append(dict(index=segment['segment_index'],frames=last-first,
            start_s=(data[first]['source.robot_monotonic_ns']-start)/1e9,
            end_s=(data[last-1]['source.robot_monotonic_ns']-start+spec['period_ns'])/1e9))
    return result


def prepare_plan(root, segment_index=None):
    root=Path(root);meta=verify(root);spec=meta['source_manifest']['controller_spec']
    choices=[s['segment_index'] for s in meta['segments']]
    if segment_index is None:
        if len(choices)!=1:raise ValueError('select one --segment from: '+str(choices))
        segment_index=choices[0]
    selected=[r for r in records(root) if r['segment_index']==segment_index]
    if not selected:raise ValueError('selected segment is empty')
    frames=[];first=selected[0]['source.robot_monotonic_ns']
    for row in selected:
        s=dict(index=row.get('source.consumption_sequence',row['source.robot_index']),inference_index=row['source.robot_index'],timestamp_monotonic_ns=row['source.robot_monotonic_ns'],
            timestamp_realtime_ns=row['source.robot_realtime_ns'],
            state=dict(body_q=row['observation.body_joint_position'],body_dq=row['observation.body_joint_velocity'],
                       gravity=row['observation.projected_gravity']),
            reference=dict(token_state=row['action.motion_token']))
        frames.append(dict(offset_ns=s['timestamp_monotonic_ns']-first,sample=s))
    return validate_plan(dict(schema='onboardmimic.lab.reference-plan',schema_version=2,
        sample_profile='lerobot_body_v21',reference_evidence='consumed_at_capture',
        episode=str(root.resolve()),segment_index=segment_index,controller_spec=spec,
        contract_sha256=fingerprint(spec),source_sha256=meta['files']['data.parquet']['sha256'],frames=frames))
