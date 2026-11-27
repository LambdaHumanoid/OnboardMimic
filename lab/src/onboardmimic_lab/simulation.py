"""Explicit simulation adapter selection. A missing simulator never becomes passthrough."""
def __getattr__(name):
    if name == 'Decoder':
        from .adapters.sonic_simulation import Decoder
        return Decoder
    raise AttributeError(name)

_SIMULATORS = {}


def register_simulator(execution_id, factory):
    if execution_id in _SIMULATORS: raise ValueError('simulator already registered')
    _SIMULATORS[execution_id] = factory


def run_simulation(plan, config, output, video=True):
    from .replay import validate_plan
    if plan.get('schema')=='onboardmimic.lab.reference-plan' and plan.get('schema_version')==1 and (plan.get('controller_spec',{}).get('test_fixture') is True or plan.get('controller_spec',{}).get('backend_id')=='fixture_joint' or plan.get('controller_spec',{}).get('hardware') is False):
        from .reference_replay import validate_plan as validate_fixture
        validate_fixture(plan)
    else:
        validate_plan(plan)
    if plan['schema'] == 'onboardmimic.lab.reference-plan' and plan.get('schema_version') == 2:
        from .adapters.sonic_native_simulation import run_simulation as native
        return native(plan, config, output, video=video)
    if plan['schema'] == 'onboardmimic.lab.reference-plan':
        from .contracts import validate_spec, fingerprint
        spec = validate_spec(config['controller_spec'])
        if fingerprint(spec) != plan['contract_sha256']: raise ValueError('simulation contract mismatch')
        factory = _SIMULATORS.get(spec['execution_id'])
        if factory is None:
            from importlib.metadata import entry_points
            entries = entry_points()
            plugins = (entries.select(group='onboardmimic_lab.simulators') if hasattr(entries, 'select')
                       else entries.get('onboardmimic_lab.simulators', []))
            matches = [entry for entry in plugins if entry.name == spec['execution_id']]
            if len(matches) > 1: raise ValueError('ambiguous simulation plugin')
            if matches: factory = matches[0].load()
        if factory is None: raise ValueError('no simulation adapter registered for '+spec['execution_id'])
        return factory(plan, config, output, video=video)
    from .adapters.sonic_simulation import run_simulation as sonic
    return sonic(plan, config, output, video=video)
