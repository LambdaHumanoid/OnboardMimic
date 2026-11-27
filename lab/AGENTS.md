# Integrated Lab module

Lab is part of the OnboardMimic repository, not a separate release or Git destination.
Use the root VERSION as the only release-version source. Packaging is defined in
root pyproject.toml; the onboardmimic-lab distribution name and CLI entry points
remain for compatibility. Do not add a separate Lab version or nested Git repository.

Keep recording passive and preserve controller ownership, freshness checks and
protected exits. Offline checks run from the OM root with scripts/test.sh --all.
Do not start physical robot services or motion as part of tests.

OM and Lab are runtime-independent. Lab must install and run without the OM
Python package or checkout. Interoperation is via HTTP/telemetry contracts; do
not share internal Python modules or import OM to discover configuration. Small
asset-registry readers remain separate implementations of the same file contract.
OM-dependent tests belong to explicit integration checks, not Lab unit tests.
