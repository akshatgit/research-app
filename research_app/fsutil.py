"""Safe workspace filesystem operations."""
from pathlib import Path
import hashlib
import os
import tempfile


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def safe_join(root, relative):
    root = Path(root).resolve()
    p = Path(relative)
    if not p.parts or p.is_absolute() or any(x in ('', '.', '..') or x.startswith('.') for x in p.parts):
        raise ValueError('invalid workspace path')
    if p.parts[0] in ('threads', 'runs') or p.parts[0] == 'topic.json':
        raise ValueError('internal workspace path')
    result = (root / p).resolve()
    if not result.is_relative_to(root):
        raise ValueError('path outside workspace')
    return result


def snapshot(root):
    root = Path(root)
    result = {}
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part.startswith('.') for part in rel.parts) or rel.parts[0] in ('threads', 'runs') or rel.name == 'topic.json':
            continue
        result[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def diff_snapshots(before, after):
    return {'created': sorted(after.keys() - before.keys()),
            'modified': sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
            'deleted': sorted(before.keys() - after.keys())}
