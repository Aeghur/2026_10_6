"""Verify extracted release bytes without importing control or hardware code."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parent
manifest=json.loads((root/'MANIFEST.json').read_text(encoding='utf-8'))
bad=[]
for name,expected in manifest['files'].items():
    path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        bad.append(name)
if bad:
    raise SystemExit('SHA256 mismatch: '+', '.join(bad))
print('Verified',len(manifest['files']),'files (SHA256).')
