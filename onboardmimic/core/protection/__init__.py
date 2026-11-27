"""Backend-independent WBC stop protection. Shadow monitoring by default; native executor requires explicit configuration."""
from .monitor import ProtectionMonitor, OutputEvidence
