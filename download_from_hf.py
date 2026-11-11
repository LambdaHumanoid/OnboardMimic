"""Compatibility entrypoint; implementation lives in its adapter module."""
from onboardmimic.backends.sonic.download_models import main

if __name__ == "__main__":
    main()
