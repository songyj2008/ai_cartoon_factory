"""Per-project cache for RunningHub uploaded media names."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.context import get_project_dir


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
    _cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_file_name(local_path: str | Path) -> str:
    path = Path(local_path).resolve()
    if not path.exists():
        return ""
    cache = load_cache()
    item = cache.get(str(path))
    if not isinstance(item, dict):
        return ""
    current = _stat(path)
    if int(item.get("size") or -1) != current["size"]:
        return ""
    if float(item.get("mtime") or -1) != current["mtime"]:
        return ""
    return str(item.get("file_name") or "").strip()


def put_cached_file_name(local_path: str | Path, file_name: str) -> None:
    path = Path(local_path).resolve()
    if not path.exists() or not str(file_name or "").strip():
        return
    cache = load_cache()
    cache[str(path)] = {
        "file_name": str(file_name).strip(),
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **_stat(path),
    }
    save_cache(cache)
