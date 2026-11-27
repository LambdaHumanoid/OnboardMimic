"""Model identity check retained for simulation adapters."""
from ..dataset_io import digest


def verify_models(plan, config):
    for name, expected in plan['model_hashes'].items():
        if digest(config[name+'_model'])['sha256'] != expected:
            raise ValueError('episode model mismatch: '+name)
