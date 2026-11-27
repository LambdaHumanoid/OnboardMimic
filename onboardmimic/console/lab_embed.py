"""Public iframe configuration only; no Lab imports or service management."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def describe(url=None):
    if not url:
        return {'enabled': False, 'url': None, 'origin': None, 'height_px': 540}
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or any(c in url for c in "\\\"'<>;"):
        raise ValueError('Lab embed URL must be an ASCII HTTP(S) URL without whitespace')
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError as exc:
        raise ValueError('Invalid Lab embed URL') from exc
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or
            p.password is not None or p.path != '/embed' or p.query or p.fragment):
        raise ValueError('Lab embed URL must be http(s)://host[:port]/embed, without credentials/query/fragment')
    host = p.hostname
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid Lab embed port')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
            raise ValueError('Lab embed host must be an exact hostname or IP address')
    if ':' in host:
        host = '[' + host + ']'
    authority = host + ((':' + str(port)) if port is not None else '')
    origin = urlunsplit((p.scheme, authority, '', '', ''))
    return {'enabled': True, 'url': origin + '/embed', 'origin': origin, 'height_px': 540}
