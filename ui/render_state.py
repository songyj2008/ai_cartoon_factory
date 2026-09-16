"""Render state tracking for LiconMSR video segments."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def render_log_path() -> Path:
    from services.context import get_project_dir

    return get_project_dir() / "logs" / "render_log.json"


def load_render_log_data() -> dict[str, Any]:
    path = render_log_path()
    if not path.exists():
        return {"parts": []}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"parts": []}


def save_render_log_data(data: dict[str, Any]) -> Path:
    path = render_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {"parts": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _resolve_video_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(str(path_text))
    try:
        path = path.resolve()
    except Exception:
        pass
    return path if path.exists() and path.is_file() else None


def _latest_part_video(part_id: str) -> Path | None:
    from services.context import get_project_dir

    videos_dir = get_project_dir() / "videos"
    if not videos_dir.exists():
        return None
    candidates = [p for p in videos_dir.glob(f"{part_id}_*.mp4") if p.is_file()]
    if not candidates:
        candidates = [p for p in videos_dir.glob(f"{part_id}*.mp4") if p.is_file()]
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0].resolve() if candidates else None


def _upsert_part(part_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = load_render_log_data()
    parts = data.setdefault("parts", [])
    for item in parts:
        if isinstance(item, dict) and str(item.get("id")) == str(part_id):
            return data, item
    item = {"id": str(part_id)}
    parts.append(item)
    return data, item


def sync_render_log_with_video_files() -> dict[str, Any]:
    from services.logger import now

    data = load_render_log_data()
    parts = data.setdefault("parts", [])
    by_id = {str(item.get("id")): item for item in parts if isinstance(item, dict) and item.get("id")}
    stale_statuses = {"needs_regenerate", "stale", "deleted"}
    changed = False

    for part_id, item in list(by_id.items()):
        if str(item.get("status") or "") in stale_statuses:
            continue
        video = _resolve_video_path(item.get("video_path")) or _latest_part_video(part_id)
        if video and (item.get("status") != "success" or item.get("video_path") != str(video)):
            item.update({"status": "success", "video_path": str(video), "error": None, "detected_at": now()})
            changed = True
        elif str(item.get("status") or "") == "success":
            item.update({"status": "deleted", "video_path": None, "error": None, "detected_at": now()})
            changed = True

    from services.context import get_project_dir

    videos_dir = get_project_dir() / "videos"
    if videos_dir.exists():
        for path in videos_dir.glob("part_*.mp4"):
            match = re.match(r"(part_\d+)", path.stem)
            if not match:
                continue
            part_id = match.group(1)
            item = by_id.get(part_id)
            if item is not None and str(item.get("status") or "") in stale_statuses:
                continue
            latest = _latest_part_video(part_id) or path.resolve()
            if item is None:
                item = {"id": part_id}
                parts.append(item)
                by_id[part_id] = item
            if item.get("status") != "success" or item.get("video_path") != str(latest):
                item.update({"id": part_id, "status": "success", "video_path": str(latest), "error": None, "detected_at": now()})
                changed = True

    if changed:
        save_render_log_data(data)
    return data


def render_log_parts_by_id() -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in sync_render_log_with_video_files().get("parts", []) or []
        if isinstance(item, dict) and item.get("id")
    }


def part_status_info(part_id: str):
    entry = render_log_parts_by_id().get(str(part_id)) or {}
    status = str(entry.get("status") or "pending")
    mapping = {
        "success": ("视频已生成", "status-success"),
        "failed": ("生成失败", "status-failed"),
        "running": ("生成中", "status-running"),
        "queued": ("排队中", "status-pending"),
        "needs_regenerate": ("需要重新生成", "status-stale"),
        "stale": ("需要重新生成", "status-stale"),
        "deleted": ("已删除", "status-pending"),
    }
    label, css_class = mapping.get(status, ("未生成", "status-pending"))
    return label, css_class, entry


def mark_part_running(part_id: str) -> None:
    from services.logger import now

    data, item = _upsert_part(str(part_id))
    previous_video_path = item.get("video_path")
    item.update({"id": str(part_id), "status": "running", "start_time": now(), "end_time": None, "error": None})
    if previous_video_path:
        item["video_path"] = previous_video_path
    save_render_log_data(data)


def mark_part_success(part_id: str, video_path: str | None = None) -> None:
    from services.logger import now

    data, item = _upsert_part(str(part_id))
    if not video_path:
        latest = _latest_part_video(str(part_id))
        video_path = str(latest) if latest else None
    item.update({"id": str(part_id), "status": "success", "end_time": now(), "error": None})
    if video_path:
        item["video_path"] = str(video_path)
    save_render_log_data(data)


def mark_part_failed(part_id: str, error: str = "") -> None:
    from services.logger import now

    data, item = _upsert_part(str(part_id))
    item.update({"id": str(part_id), "status": "failed", "end_time": now(), "error": str(error or "")})
    save_render_log_data(data)


def mark_part_needs_regenerate(part_id: str, reason: str = "segment changed") -> None:
    from services.logger import now

    data, item = _upsert_part(str(part_id))
    item.update({"status": "needs_regenerate", "stale_reason": reason, "updated_at": now(), "video_path": None, "error": None})
    save_render_log_data(data)


def mark_parts_from_index_needs_regenerate(start_index: int, old_count: int, reason: str = "segments changed") -> None:
    for index in range(int(start_index), int(old_count) + 1):
        mark_part_needs_regenerate(f"part_{index:03d}", reason)
