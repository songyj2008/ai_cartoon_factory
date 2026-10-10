"""Project ownership for globally stored character and background assets."""
from __future__ import annotations

import json
from pathlib import Path

from services.context import PROJECTS_DIR, get_current_project_name


def _path(project_name: str) -> Path:
    return PROJECTS_DIR / str(project_name or "current").strip() / "asset_assignments.json"


def load_project_asset_assignments(project_name: str | None = None) -> dict[str, list[str]]:
    path = _path(project_name or get_current_project_name())
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    result = {}
    for kind in ("characters", "backgrounds"):
        values = data.get(kind, []) if isinstance(data, dict) else []
        result[kind] = list(dict.fromkeys(str(x) for x in values if str(x))) if isinstance(values, list) else []
    return result


def save_project_asset_assignments(
    assignments: dict,
    project_name: str | None = None,
    legacy_bible_migrated: bool | None = None,
) -> Path:
    path = _path(project_name or get_current_project_name())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: list(dict.fromkeys(str(x) for x in assignments.get(key, []) if str(x))) for key in ("characters", "backgrounds")}
    try:
        existing = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except (OSError, ValueError):
        existing = {}
    migrated = legacy_bible_migrated if legacy_bible_migrated is not None else (existing.get("legacy_project_bible_migrated") if isinstance(existing, dict) else None)
    if migrated is not None:
        payload["legacy_project_bible_migrated"] = bool(migrated)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def asset_project_owners() -> dict[str, dict[str, list[str]]]:
    owners: dict[str, dict[str, list[str]]] = {"characters": {}, "backgrounds": {}}
    if not PROJECTS_DIR.exists():
        return owners
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        assignments_path = _path(project_dir.name)
        assignments = load_project_asset_assignments(project_dir.name)
        try:
            existing = json.loads(assignments_path.read_text(encoding="utf-8-sig")) if assignments_path.exists() else {}
        except (OSError, ValueError):
            existing = {}
        migrated = isinstance(existing, dict) and existing.get("legacy_project_bible_migrated") is True
        if not migrated:
            # Older projects stored their creative materials only in
            # project_bible.json. Import those bindings once as project
            # ownership so existing projects keep their materials after the
            # asset manager becomes the single source of truth.
            try:
                from services.project_bible import load_project_bible

                bible = load_project_bible(project_dir.name)
            except Exception:
                bible = {}
            characters = [
                str(item.get("asset_identity_id") or "")
                for item in list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
                if isinstance(item, dict) and item.get("asset_identity_id")
            ]
            backgrounds = [
                str(item.get("asset_background_id") or "")
                for item in (bible.get("allowed_backgrounds") or [])
                if isinstance(item, dict) and item.get("asset_background_id")
            ]
            assignments = {
                "characters": list(dict.fromkeys(assignments["characters"] + characters)),
                "backgrounds": list(dict.fromkeys(assignments["backgrounds"] + backgrounds)),
            }
            if assignments_path.exists() or characters or backgrounds:
                save_project_asset_assignments(assignments, project_dir.name, legacy_bible_migrated=True)
        for kind in owners:
            for asset_id in assignments[kind]:
                owners[kind].setdefault(asset_id, []).append(project_dir.name)
    return owners


def list_asset_project_names() -> list[str]:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(path.name for path in PROJECTS_DIR.iterdir() if path.is_dir())
    current = get_current_project_name() or "current"
    return sorted(set(names) | {current})


def set_asset_projects(kind: str, asset_id: str, project_names: list[str]) -> None:
    if kind not in {"characters", "backgrounds"} or not asset_id:
        raise ValueError("素材类型或素材 ID 无效")
    valid_projects = set(list_asset_project_names())
    selected = list(dict.fromkeys(str(name) for name in project_names if str(name) in valid_projects))
    for name in valid_projects:
        assignments = load_project_asset_assignments(name)
        current = assignments[kind]
        updated = ([value for value in current if value != asset_id] + ([asset_id] if name in selected else []))
        if updated != current:
            assignments[kind] = updated
            save_project_asset_assignments(assignments, name)


def sync_generation_assets(project_name=None):
    """Refresh the generation registry from the project's actual memberships."""
    from ui.project_bible_panel import _sync_project_bible_to_assignments
    _sync_project_bible_to_assignments(project_name or get_current_project_name())
