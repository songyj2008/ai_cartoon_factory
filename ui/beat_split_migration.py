"""Keep segment identity and audit history correct after Beat edits.

Part numbers are display positions only.  Split/delete operations re-number
active cards but never move, rename, or delete existing task artifacts.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from services.context import CONFIG, get_project_dir
from services.logger import log, now
from workflow.video_merge import json_text, load_video_jobs, save_video_jobs
from workflow.runninghub_history import new_segment_uid, retire_segment


PART_RE = re.compile(r"part_(\d{3})")


def _part_id(index: int) -> str:
    return f"part_{int(index):03d}"


def _part_index(part_id: str) -> int:
    match = PART_RE.search(str(part_id or ""))
    return int(match.group(1)) if match else 0


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(payload), encoding="utf-8")


def _retarget_segment(segment: dict[str, Any], new_index: int, moved_paths: dict[str, str]) -> dict[str, Any]:
    item = copy.deepcopy(segment)
    old_index = int(item.get("segment_index") or _part_index(str(item.get("part_id") or "")) or new_index)
    old_part = str(item.get("part_id") or _part_id(old_index))
    new_part = _part_id(new_index)
    item["segment_index"] = int(new_index)
    item["segment_id"] = f"segment_{int(new_index):03d}"
    item["part_id"] = new_part
    # Existing artifacts belong to this stable segment, not to its display
    # number.  Never rename their paths merely because a beat was inserted or
    # removed; doing so breaks task/video audit ownership.
    job = item.get("job") if isinstance(item.get("job"), dict) else {}
    job = copy.deepcopy(job)
    job["part_id"] = new_part
    # Output prefix is for a future generation only.  Existing local paths are
    # deliberately retained above, while a re-generated video gets the new
    # visible part number through the normal submission path.
    item["job"] = job
    return item


def _split_placeholder(template: dict[str, Any] | None, beat: dict[str, Any], new_index: int, archived_paths: dict[str, str]) -> dict[str, Any]:
    item = copy.deepcopy(template or {})
    part_id = _part_id(new_index)
    item.update(
        {
            "segment_index": int(new_index),
            "segment_id": f"segment_{int(new_index):03d}",
            "segment_uid": new_segment_uid(),
            "part_id": part_id,
            "title": str((beat or {}).get("title") or f"剧情段落{new_index}"),
            "scene_id": str((beat or {}).get("scene_id") or item.get("scene_id") or ""),
            "important_roles": list((beat or {}).get("important_roles") or item.get("important_roles") or []),
            "visible_roles": list((beat or {}).get("visible_roles") or item.get("visible_roles") or []),
            "reference_roles": list((beat or {}).get("reference_roles") or item.get("reference_roles") or []),
            "offscreen_speakers": list((beat or {}).get("offscreen_speakers") or item.get("offscreen_speakers") or []),
            "mentioned_roles": list((beat or {}).get("mentioned_roles") or item.get("mentioned_roles") or []),
            "background_extras": list((beat or {}).get("background_extras") or item.get("background_extras") or []),
            "status": "needs_regenerate",
            "prompt_saved": False,
            "stale_reason": "beat split; regenerate this split part",
        }
    )
    for key in (
        "video_path", "workflow_path", "backend", "task_id", "prompt_id",
        "runninghub_attempts", "runninghub_usage", "submit_result", "raw_submit",
        "output_urls", "stale_video_path", "video_ok", "preserve_on_reset",
        "protected_from_reset", "video_ok_reason", "renders", "active_render_id",
        "stale_render_id", "render_history_schema_version",
    ):
        item.pop(key, None)
    # 拆分段是全新内容，不能继承拆分前旧段的素材绑定。与
    # migrate_after_beat_insert 的占位段保持一致：剥掉 asset_manifest 与
    # prompt_override，交给下一次自动绑定按新 Beat 重新匹配。
    item.pop("compiled_prompt_snapshot", None)
    old_config = item.get("generation_config") if isinstance(item.get("generation_config"), dict) else {}
    item["generation_config"] = {
        key: copy.deepcopy(value)
        for key, value in old_config.items()
        if key in {"generation_mode", "duration_sec", "aspect_ratio", "resolution"}
    }
    if archived_paths:
        item["archived_from_original_part"] = archived_paths
    job = item.get("job") if isinstance(item.get("job"), dict) else {}
    job = copy.deepcopy(job)
    duration = int(round(float((beat or {}).get("estimated_duration_sec") or (beat or {}).get("duration_sec") or job.get("duration_sec") or 15)))
    fps = int(CONFIG.get("fps") or job.get("fps") or 50)
    job.update({"part_id": part_id, "duration_sec": duration, "fps": fps, "total_frames": duration * fps})
    for key in ("video_path", "workflow_path", "output_prefix", "stale_video_path"):
        job.pop(key, None)
    for key in ("prompt", "director_prompt", "final_prompt"):
        job[key] = ""
    item["job"] = job
    return item


def _update_render_log(split_index: int, replacement_count: int, moved_paths: dict[str, str], archived_paths: dict[str, str]) -> None:
    path = get_project_dir() / "logs" / "render_log.json"
    data = _read_json(path, {"parts": []})
    parts = [item for item in data.get("parts") or [] if isinstance(item, dict)]
    delta = max(int(replacement_count) - 1, 0)
    migrated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(parts, key=lambda x: _part_index(str(x.get("id") or ""))):
        old_index = _part_index(str(item.get("id") or ""))
        if old_index <= 0:
            migrated.append(item)
            continue
        if old_index < split_index:
            new_item = item
        elif old_index == split_index:
            data["retired_parts"] = list(data.get("retired_parts") or []) + [
                {**copy.deepcopy(item), "retired_at": now(), "retired_reason": "beat split"}
            ]
            continue
        else:
            old_part = _part_id(old_index)
            new_part = _part_id(old_index + delta)
            new_item = copy.deepcopy(item)
            new_item["id"] = new_part
        item_id = str(new_item.get("id") or "")
        if item_id and item_id not in seen:
            migrated.append(new_item)
            seen.add(item_id)
    for offset in range(int(replacement_count)):
        part_id = _part_id(split_index + offset)
        migrated.append(
            {
                "id": part_id,
                "status": "needs_regenerate",
                "stale_reason": "beat split; regenerate this split part",
                "updated_at": now(),
                "video_path": None,
                "error": None,
                "archived_from_original_part": archived_paths if offset == 0 and archived_paths else {},
            }
        )
    migrated.sort(key=lambda x: _part_index(str(x.get("id") or "part_999")) or 999999)
    data["parts"] = migrated
    _write_json(path, data)


def migrate_after_complex_beat_split(split_index: int, replacement_count: int, split_beats_data: dict[str, Any]) -> dict[str, Any]:
    """Shift old generated parts after a split and archive the stale original part."""
    split_index = int(split_index or 0)
    replacement_count = int(replacement_count or 0)
    if split_index <= 0 or replacement_count <= 0:
        return load_video_jobs()

    old_payload = load_video_jobs()
    old_segments = [item for item in old_payload.get("segments") or [] if isinstance(item, dict)]
    old_count = len(old_segments)
    if not old_segments:
        return old_payload

    delta = max(replacement_count - 1, 0)
    moved_paths: dict[str, str] = {}
    archived_paths: dict[str, str] = {}

    by_old_index = {int(item.get("segment_index") or 0): item for item in old_segments if int(item.get("segment_index") or 0) > 0}
    original = by_old_index.get(split_index)
    if original:
        # Retire the pre-split segment as an audit record.  Its task history,
        # workflow and downloaded video stay exactly where they are.
        retire_segment(old_payload, original, "beat split; replaced by new segments")
    beats = split_beats_data.get("beats") if isinstance(split_beats_data, dict) else []
    new_count = len(beats) if isinstance(beats, list) and beats else old_count + delta
    new_segments: list[dict[str, Any]] = []
    for new_index in range(1, new_count + 1):
        if new_index < split_index:
            source = by_old_index.get(new_index)
            if source:
                new_segments.append(_retarget_segment(source, new_index, moved_paths))
            continue
        if split_index <= new_index < split_index + replacement_count:
            beat = beats[new_index - 1] if isinstance(beats, list) and new_index - 1 < len(beats) and isinstance(beats[new_index - 1], dict) else {}
            new_segments.append(_split_placeholder(by_old_index.get(split_index), beat, new_index, archived_paths if new_index == split_index else {}))
            continue
        old_index = new_index - delta
        source = by_old_index.get(old_index)
        if source:
            new_segments.append(_retarget_segment(source, new_index, moved_paths))

    payload = {
        **old_payload,
        "mode": old_payload.get("mode") or "licon_msr_segmented",
        "segment_count": len(new_segments),
        "total_duration_sec": sum(int(float((item.get("job") or {}).get("duration_sec") or 0)) for item in new_segments),
        "fps": int(CONFIG.get("fps") or old_payload.get("fps") or 50),
        "segments": new_segments,
    }
    payload.pop("final_video_path", None)
    save_video_jobs(payload)
    _update_render_log(split_index, replacement_count, moved_paths, archived_paths)
    log(
        f"[beat_splitter] 已迁移旧 part 顺序：Beat {split_index} -> {replacement_count} 段，"
        f"后续 part 顺延 {delta} 位；原段任务已归档，拆分段需重新生成。",
        "STEP",
    )
    return payload


def migrate_after_beat_insert(insert_after_index: int, beats_data: dict[str, Any]) -> dict[str, Any]:
    """Insert an empty segment after one Beat without disturbing task ownership."""
    insert_after_index = int(insert_after_index or 0)
    if insert_after_index <= 0:
        return load_video_jobs()

    old_payload = load_video_jobs()
    old_segments = [item for item in old_payload.get("segments") or [] if isinstance(item, dict)]
    if not old_segments:
        return old_payload

    insert_index = insert_after_index + 1
    by_old_index = {
        int(item.get("segment_index") or 0): item
        for item in old_segments
        if int(item.get("segment_index") or 0) > 0
    }
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    new_count = len(beats) if isinstance(beats, list) else len(old_segments) + 1
    inserted_beat = (
        beats[insert_index - 1]
        if isinstance(beats, list) and insert_index - 1 < len(beats) and isinstance(beats[insert_index - 1], dict)
        else {}
    )
    template = by_old_index.get(insert_after_index)
    new_segments: list[dict[str, Any]] = []
    for new_index in range(1, new_count + 1):
        if new_index < insert_index:
            source = by_old_index.get(new_index)
            if source:
                new_segments.append(_retarget_segment(source, new_index, {}))
        elif new_index == insert_index:
            placeholder = _split_placeholder(template, inserted_beat, new_index, {})
            placeholder["stale_reason"] = "beat added; edit and regenerate this part"
            placeholder["title"] = str(inserted_beat.get("title") or f"新增 Beat {new_index}")
            placeholder["scene_id"] = str(inserted_beat.get("scene_id") or "")
            for key in (
                "important_roles", "visible_roles", "reference_roles",
                "offscreen_speakers", "mentioned_roles", "background_extras",
            ):
                placeholder[key] = []
            placeholder.pop("compiled_prompt_snapshot", None)
            placeholder.pop("asset_manifest", None)
            old_config = placeholder.get("generation_config") if isinstance(placeholder.get("generation_config"), dict) else {}
            placeholder["generation_config"] = {
                key: copy.deepcopy(value)
                for key, value in old_config.items()
                if key in {"generation_mode", "duration_sec", "aspect_ratio", "resolution"}
            }
            placeholder["job"]["prompt"] = ""
            placeholder["job"]["director_prompt"] = ""
            placeholder["job"]["final_prompt"] = ""
            placeholder["job"]["visible_roles"] = []
            placeholder["job"]["reference_roles"] = []
            placeholder["job"]["important_roles"] = []
            placeholder["job"]["offscreen_speakers"] = []
            placeholder["job"]["mentioned_roles"] = []
            placeholder["job"]["reference_image_ids"] = {}
            placeholder["job"]["reference_images"] = []
            placeholder["job"]["background_image"] = ""
            new_segments.append(placeholder)
        else:
            source = by_old_index.get(new_index - 1)
            if source:
                new_segments.append(_retarget_segment(source, new_index, {}))

    payload = {
        **old_payload,
        "mode": old_payload.get("mode") or "licon_msr_segmented",
        "segment_count": len(new_segments),
        "total_duration_sec": sum(int(float((item.get("job") or {}).get("duration_sec") or 0)) for item in new_segments),
        "fps": int(CONFIG.get("fps") or old_payload.get("fps") or 50),
        "segments": new_segments,
    }
    payload.pop("final_video_path", None)
    save_video_jobs(payload)
    log(f"[beat_editor] inserted Beat {insert_index}; later part display numbers shifted by 1", "STEP")
    return payload


def _assert_no_active_parts_from(start_index: int, segments: list[dict[str, Any]]) -> None:
    active: list[str] = []
    for item in segments:
        old_index = int(item.get("segment_index") or _part_index(str(item.get("part_id") or "")) or 0)
        if old_index < int(start_index):
            continue
        status = str(item.get("status") or "").strip().lower()
        task_id = str(item.get("task_id") or item.get("prompt_id") or "").strip()
        if status in {"running", "queued"} and task_id:
            active.append(str(item.get("part_id") or _part_id(old_index)))
    if active:
        raise RuntimeError("删除 Beat 前请等待这些分段完成或失败：" + ", ".join(active))


def _update_render_log_after_delete(delete_index: int, moved_paths: dict[str, str], archived_paths: dict[str, str]) -> None:
    path = get_project_dir() / "logs" / "render_log.json"
    data = _read_json(path, {"parts": []})
    parts = [item for item in data.get("parts") or [] if isinstance(item, dict)]
    migrated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(parts, key=lambda x: _part_index(str(x.get("id") or ""))):
        old_index = _part_index(str(item.get("id") or ""))
        if old_index <= 0:
            migrated.append(item)
            continue
        if old_index == delete_index:
            data["retired_parts"] = list(data.get("retired_parts") or []) + [
                {**copy.deepcopy(item), "retired_at": now(), "retired_reason": "beat deleted"}
            ]
            continue
        new_item = copy.deepcopy(item)
        if old_index > delete_index:
            old_part = _part_id(old_index)
            new_part = _part_id(old_index - 1)
            new_item["id"] = new_part
        item_id = str(new_item.get("id") or "")
        if item_id and item_id not in seen:
            migrated.append(new_item)
            seen.add(item_id)
    data["parts"] = migrated
    data["deleted_parts"] = list(data.get("deleted_parts") or []) + [
        {
            "id": _part_id(delete_index),
            "deleted_at": now(),
            "archived_paths": archived_paths,
        }
    ]
    _write_json(path, data)


def migrate_after_beat_delete(delete_index: int, beats_data: dict[str, Any]) -> dict[str, Any]:
    """Shift generated parts after deleting one beat and archive the deleted part."""
    delete_index = int(delete_index or 0)
    if delete_index <= 0:
        return load_video_jobs()

    old_payload = load_video_jobs()
    old_segments = [item for item in old_payload.get("segments") or [] if isinstance(item, dict)]
    old_count = len(old_segments)
    if not old_segments:
        return old_payload
    if delete_index > old_count:
        return old_payload

    _assert_no_active_parts_from(delete_index, old_segments)

    moved_paths: dict[str, str] = {}
    archived_paths: dict[str, str] = {}

    by_old_index = {int(item.get("segment_index") or 0): item for item in old_segments if int(item.get("segment_index") or 0) > 0}
    deleted = by_old_index.get(delete_index)
    if deleted:
        retire_segment(old_payload, deleted, "beat deleted")
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    new_count = len(beats) if isinstance(beats, list) else max(old_count - 1, 0)
    new_segments: list[dict[str, Any]] = []
    for new_index in range(1, new_count + 1):
        old_index = new_index if new_index < delete_index else new_index + 1
        source = by_old_index.get(old_index)
        if source:
            new_segments.append(_retarget_segment(source, new_index, moved_paths))

    payload = {
        **old_payload,
        "mode": old_payload.get("mode") or "licon_msr_segmented",
        "segment_count": len(new_segments),
        "total_duration_sec": sum(int(float((item.get("job") or {}).get("duration_sec") or 0)) for item in new_segments),
        "fps": int(CONFIG.get("fps") or old_payload.get("fps") or 50),
        "segments": new_segments,
    }
    payload.pop("final_video_path", None)
    save_video_jobs(payload)
    _update_render_log_after_delete(delete_index, moved_paths, archived_paths)
    log(
        f"[beat_editor] 已删除 Beat {delete_index}；原段任务已归档，后续 part 已前移 1 位",
        "STEP",
    )
    return payload
