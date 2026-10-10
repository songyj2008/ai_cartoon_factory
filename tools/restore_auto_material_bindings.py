"""Recover auto-matched segment materials after an accidental manual override.

The full-screen prompt editor used to persist its material picker on every
save.  Each beat retains the pre-override auto-match cache, which this tool
uses to restore the list-facing material binding without touching prompts,
videos, or video-OK state.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow direct execution from the tools directory on Windows.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.context import get_project_dir
from services.file_utils import save_json
from workflow.asset_resolver import background_image_for_scene, reference_images_for_roles, selected_project_materials


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _unique_roles(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def _primary_image_ids(reference_roles: list[str]) -> dict[str, str]:
    materials = selected_project_materials()
    characters = {
        str(item.get("id") or "").strip(): item
        for item in materials.get("characters") or []
        if isinstance(item, dict)
    }
    result: dict[str, str] = {}
    for role in reference_roles:
        images = characters.get(role, {}).get("images") or []
        available = [item for item in images if isinstance(item, dict) and item.get("available")]
        image = next((item for item in available if item.get("is_primary")), available[0] if available else None)
        image_id = str((image or {}).get("id") or "").strip()
        if image_id:
            result[role] = image_id
    return result


def _auto_binding(beat: dict[str, Any]) -> tuple[list[str], dict[str, str], str] | None:
    auto_match = beat.get("auto_asset_match")
    if not isinstance(auto_match, dict):
        return None
    characters = auto_match.get("characters") if isinstance(auto_match.get("characters"), list) else []
    roles = _unique_roles(
        [item.get("role_id") for item in characters if isinstance(item, dict)]
    )[:4]
    background = auto_match.get("background") if isinstance(auto_match.get("background"), dict) else {}
    scene_id = str(background.get("scene_id") or "").strip()
    if not roles and not scene_id:
        return None
    return roles, _primary_image_ids(roles), scene_id


def _restore_beat(beat: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    binding = _auto_binding(beat)
    if not binding:
        return beat, False
    roles, image_ids, scene_id = binding
    restored = copy.deepcopy(beat)
    restored["reference_roles"] = roles
    restored["reference_image_ids"] = image_ids
    restored["visible_roles"] = roles
    if scene_id:
        restored["scene_id"] = scene_id
    auto_match = restored.get("auto_asset_match") if isinstance(restored.get("auto_asset_match"), dict) else {}
    auto_match.pop("manual_override", None)
    auto_match.pop("manual_selection", None)
    restored["auto_asset_match"] = auto_match
    constraints = restored.get("identity_constraints") if isinstance(restored.get("identity_constraints"), dict) else {}
    constraints["locked_roles"] = roles
    restored["identity_constraints"] = constraints
    return restored, True


def restore(project_dir: Path, dry_run: bool = False) -> list[int]:
    beats_path = project_dir / "beats.json"
    jobs_path = project_dir / "video_jobs.json"
    beats_data = _load_json(beats_path)
    jobs_data = _load_json(jobs_path)
    beats = beats_data.get("beats") if isinstance(beats_data.get("beats"), list) else []
    jobs = jobs_data.get("segments") if isinstance(jobs_data.get("segments"), list) else []
    restored_by_index: dict[int, tuple[list[str], dict[str, str], str]] = {}

    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        restored, changed = _restore_beat(beat)
        if not changed:
            continue
        beats[index - 1] = restored
        restored_by_index[index] = (
            list(restored["reference_roles"]),
            dict(restored["reference_image_ids"]),
            str(restored.get("scene_id") or ""),
        )

    for segment in jobs:
        if not isinstance(segment, dict):
            continue
        index = int(segment.get("segment_index") or 0)
        binding = restored_by_index.get(index)
        if not binding:
            continue
        roles, image_ids, scene_id = binding
        images = reference_images_for_roles(roles, image_ids, limit=4)
        background = background_image_for_scene(scene_id) if scene_id else ""
        for target in (segment, segment.get("job")):
            if not isinstance(target, dict):
                continue
            target["reference_roles"] = list(roles)
            target["reference_image_ids"] = dict(image_ids)
            target["visible_roles"] = list(roles)
            if scene_id:
                target["scene_id"] = scene_id
            target["reference_images"] = list(images)
            target["background_image"] = background
            target.pop("asset_binding_source", None)

    changed_indices = sorted(restored_by_index)
    if dry_run or not changed_indices:
        return changed_indices

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for path in (beats_path, jobs_path):
        backup = path.with_name(f"{path.stem}.before_auto_material_restore_{stamp}{path.suffix}")
        backup.write_bytes(path.read_bytes())
    save_json("beats.json", beats_data)
    save_json("video_jobs.json", jobs_data)
    return changed_indices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=get_project_dir())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changed = restore(args.project_dir.resolve(), dry_run=args.dry_run)
    action = "Would restore" if args.dry_run else "Restored"
    print(f"{action} automatic material bindings for {len(changed)} part(s): {changed}")


if __name__ == "__main__":
    main()
