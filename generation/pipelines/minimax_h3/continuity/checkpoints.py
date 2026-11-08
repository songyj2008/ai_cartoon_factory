"""Private operation checkpoints; never use the live project as a cache root."""
import hashlib,json
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def _path():
    from services.text_jobs import current
    from services.context import get_project_dir
    operation=current()
    if not operation or not operation.get('work'): return None
    work=Path(operation['work']).resolve()
    if work!=Path(get_project_dir()).resolve() or not (work/'.draft').is_file(): return None
    return work/'temp'/'h3_continuity_source.json'

def load(key):
    path=_path()
    if not path or not path.is_file(): return None
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value['result'] if value.get('key')==key else None
    except (OSError,ValueError,KeyError,TypeError): return None

def save(key,result):
    path=_path()
    if not path:return
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'key':key,'result':result},ensure_ascii=False,sort_keys=True),encoding='utf-8')
    temporary.replace(path)
