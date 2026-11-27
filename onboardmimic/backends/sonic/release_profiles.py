"""Platform identity and target-inference evidence for prebuilt releases."""
from pathlib import Path


def release_platform(profile, native, bridge, validation=None):
    if profile not in ('jp5', 'jp6'):
        raise ValueError('Unknown JetPack profile: '+profile)
    expected = ('20.04', '35.', '11.', '8.5.2') if profile == 'jp5' else ('22.04', '36.', '12.', '10.')
    for report in (native, bridge):
        facts=report.get('sysroot', {})
        if (facts.get('ubuntu') != expected[0] or
                not str(facts.get('l4t','')).startswith(expected[1]) or
                not str(facts.get('cuda','')).startswith(expected[2]) or
                not str(facts.get('tensorrt','')).startswith(expected[3])):
            raise ValueError('Build report does not match '+profile)
    facts=native['sysroot']
    if native['sysroot'] != bridge['sysroot']:
        raise ValueError('Native and bridge target platforms differ')
    if profile == 'jp6' and validation is not None:
        from .platforms import source_environment_errors
        from .source_validation import check_timing
        validation=validation or {}
        environment=validation.get('environment',{})
        if not validation.get('software_passed') or validation.get('host_test') is not False:
            raise ValueError('JP6 requires target Jetson inference validation')
        if source_environment_errors(environment) or environment.get('ubuntu')!='22.04':
            raise ValueError('JP6 validation must come from a supported Jetson')
        for key in ('ubuntu','l4t','tensorrt'):
            if environment.get(key)!=facts.get(key):raise ValueError('JP6 build/validation mismatch: '+key)
        major,minor=map(int,str(facts['cuda']).split('.')[:2])
        if environment.get('cuda_runtime')!=major*1000+minor*10:
            raise ValueError('JP6 CUDA build/validation mismatch')
        numerical=validation.get('numerical',{})
        check_timing(numerical.get('benchmark',{}))
        planner=numerical.get('planner',{})
        policy=numerical.get('policy',[])
        if not planner.get('passed') or len(planner.get('cases',[]))!=24 or len(policy)!=2 or not all(p.get('passed') for p in policy):
            raise ValueError('Incomplete JP6 numerical validation')
    return dict(facts,board='Jetson Orin NX 16GB',arch='aarch64',gpu_cache_name='Orin',precision='FP32, TF32 disabled')


def verify_engine_evidence(directory, validation, digest):
    prefix='onboardmimic/backends/sonic/deploy/onboard/engines/'
    hashes=validation.get('sha256',{})
    for name in ('encoder_fp32.engine','decoder_fp32.engine','planner_fp32.engine'):
        if hashes.get(prefix+name)!=digest(Path(directory)/name):
            raise ValueError('JP6 engine differs from target validation: '+name)
