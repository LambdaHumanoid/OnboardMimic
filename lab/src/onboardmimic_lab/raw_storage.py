"""Lossless post-capture compression, with logical hashes stable across storage."""
import gzip
import hashlib
import json
import os
from pathlib import Path

from .dataset_io import digest, write_json


def raw_path(root, name, manifest=None):
    root = Path(root)
    manifest = manifest or json.loads((root/'manifest.json').read_text())
    entry = manifest.get('storage', {}).get(name)
    if entry is None: return root/name
    if entry.get('codec') != 'gzip' or entry.get('path') != name+'.gz' or not name.endswith('.jsonl'):
        raise ValueError('unsupported raw storage descriptor')
    return root/entry['path']


def logical_digest(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    h = hashlib.sha256(); size = 0
    with opener(path, 'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            h.update(block); size += len(block)
    return dict(bytes=size, sha256=h.hexdigest())


def verify_streams(root, manifest, names):
    if set(manifest['files']) != set(names): raise ValueError('invalid raw file manifest')
    if set(manifest.get('storage', {}))-set(names): raise ValueError('invalid raw storage manifest')
    for name in names:
        path = raw_path(root, name, manifest)
        if path.is_symlink(): raise ValueError('raw stream symlink is not allowed')
        entry = manifest.get('storage', {}).get(name)
        if entry and digest(path) != entry.get('physical'):
            raise ValueError('compressed raw integrity failed: '+name)
        if logical_digest(path) != manifest['files'][name]:
            raise ValueError('raw integrity failed: '+name)


def compact_raw(root):
    """Background worker only. Commit verified gzip files before unlinking originals."""
    root = Path(root); manifest = json.loads((root/'manifest.json').read_text())
    names = tuple(manifest['files']); verify_streams(root, manifest, names)
    storage = dict(manifest.get('storage', {}))
    for name in names:
        if not name.endswith('.jsonl') or name in storage: continue
        source = root/name; temporary = root/(name+'.gz.tmp'); target = root/(name+'.gz')
        if temporary.is_symlink() or target.is_symlink():
            raise ValueError('compression target symlink is not allowed')
        with source.open('rb') as inp, temporary.open('wb') as out:
            with gzip.GzipFile(filename='', mode='wb', fileobj=out, compresslevel=1, mtime=0) as compressed:
                for block in iter(lambda:inp.read(1024*1024), b''): compressed.write(block)
            out.flush(); os.fsync(out.fileno())
        # Read back the completed stream: verify the decompressed bytes exactly.
        h=hashlib.sha256(); size=0
        with gzip.open(temporary,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block);size+=len(block)
        if dict(bytes=size,sha256=h.hexdigest()) != manifest['files'][name]:
            raise ValueError('lossless compression verification failed')
        temporary.replace(target)
        storage[name] = dict(codec='gzip', path=target.name, physical=digest(target))
    manifest['storage'] = storage
    write_json(root/'manifest.json', manifest)  # fsync directory publishes all gz renames too
    for name in storage:
        original = root/name
        if original.exists(): original.unlink()
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
