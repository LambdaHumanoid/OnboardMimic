"""Read host OS metadata without importing a controller or hardware SDK."""
from pathlib import Path


def os_release(path=Path('/etc/os-release')):
    return {key: value.strip('"') for key, sep, value in
            (line.partition('=') for line in path.read_text().splitlines()) if sep}
