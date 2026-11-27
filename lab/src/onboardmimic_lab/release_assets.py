"""Lab-local reader for the versioned asset registry (no OM runtime imports). Registry is installed atomically by deployment."""
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


def registry_path():
    return Path(os.environ.get('OM_ASSET_REGISTRY',str(Path.home()/'.config/onboardmimic/assets.json')))


def select(default, relative, request=''):
    path=registry_path()
    if not path.exists():return Path(default),None
    data=json.loads(path.read_text())
    if data.get('schema')!=1:raise ValueError('Unsupported asset registry')
    query=parse_qs(urlsplit(request).query)
    sid=query.get('asset_version',[data['active']])[0]
    if not re.fullmatch('[0-9a-f]{64}',sid) or sid not in data['releases']:raise ValueError('Unknown asset release')
    return Path(data['releases'][sid])/relative,sid


def pin(page, sid):
    if not sid:return page
    # Only local static references; no API URLs, credentials or external links.
    return re.sub(r'((?:src|href)="/[^"?]+\.(?:js|css))"',r'\1?asset_version='+sid+'"',page)


def status():
    path=registry_path()
    data=json.loads(path.read_text()) if path.exists() else {}
    return dict(schema=1,active=data.get('active'))
