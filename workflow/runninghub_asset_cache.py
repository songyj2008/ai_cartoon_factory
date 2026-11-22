"""Per-project cache for RunningHub uploaded media names."""
from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any

from services.context import get_project_dir

_cache_lock = Lock()


def _cache_path() -> Path:
    path = get_project_dir() / "temp" / "runninghub_uploaded_assets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _stat(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime": float(stat.st_mtime)}


def load_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(cache, handle, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_cached_file_name(local_path: str | Path, key_id: str) -> str:
    path = Path(local_path).resolve()
    if not path.exists():
        return ""
    cache = load_cache()
    item = cache.get(f"{key_id}:{path}")
    if not isinstance(item, dict):
        return ""
    current = _stat(path)
    if int(item.get("size") or -1) != current["size"]:
        return ""
    if float(item.get("mtime") or -1) != current["mtime"]:
        return ""
    return str(item.get("file_name") or "").strip()


def put_cached_file_name(local_path: str | Path, file_name: str, key_id: str) -> None:
    path = Path(local_path).resolve()
    if not path.exists() or not str(file_name or "").strip():
        return
    with _cache_lock:
        cache = load_cache()
        cache[f"{key_id}:{path}"] = {
            "file_name": str(file_name).strip(),
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            **_stat(path),
        }
        save_cache(cache)
