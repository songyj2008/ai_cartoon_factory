"""Copy editable Beat/segment data without copying output provenance."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from pipeline.beat_splitter.io import ensure_beat_uids
from ui.beat_split_migration import _retarget_segment, _split_placeholder
from workflow.runninghub_history import new_segment_uid
from workflow.video_merge import _VIDEO_JOBS_LOCK, load_video_jobs, save_video_jobs


# Explicit input fields keep task/render metadata out of a new segment.
SEGMENT_INPUTS = {
    "title", "model_id", "model_override", "resolved_model", "generation_mode",
    "generation_config", "compiled_prompt_snapshot", "workflow_profile",
    "requested_media_spec", "scene_id", "important_roles", "visible_roles",
    "reference_roles", "reference_image_ids", "offscreen_speakers",
    "mentioned_roles", "background_extras", "asset_manifest", "prompt_saved",
}
JOB_INPUTS = {
    "prompt", "director_prompt", "final_prompt", "duration_sec", "fps",
    "total_frames", "aligned_frame_count", "asset_manifest", "asset_slots",
    "model_id", "model_revision", "generation_mode", "workflow_profile",
    "reference_images", "background_image", "reference_roles", "reference_image_ids",
    "scene_id", "important_roles", "visible_roles", "offscreen_speakers",
    "mentioned_roles", "resolution", "aspect_ratio", "seed",
}
SOURCE_FIELDS = {"source_excerpt", "source_text", "source_span", "source_start", "source_end",
                 "novel_text", "novel_excerpt", "original_text"}


def _position(value, maximum, label):
    try:
        number = float(value)
        index = int(number)
        if number != index or not 1 <= index <= maximum:
            raise ValueError
        return index
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label}必须是 1 到 {maximum} 之间的整数") from None


def build_beat_copy(beats_data, jobs, source_index, target_index):
    """Return independent updated payloads; never mutate the caller's data."""
    data = ensure_beat_uids(copy.deepcopy(beats_data))
    beats = data.get("beats") or []
    source = _position(source_index, len(beats), "源 Beat 序号")
    target = _position(target_index, len(beats) + 1, "目标 Beat 序号")
    duplicate = copy.deepcopy(beats[source - 1])
    for key in SOURCE_FIELDS:
        duplicate.pop(key, None)
    duplicate["beat_uid"] = f"beat_{uuid.uuid4().hex}"
    beats.insert(target - 1, duplicate)
    for index, beat in enumerate(beats, 1):
        beat.update(id=index, order=index, node_id=f"beat_{index:03d}")
    data["total_duration_sec"] = sum(
        float(b.get("estimated_duration_sec") or b.get("duration_sec") or 0) for b in beats
    )

    payload = copy.deepcopy(jobs)
    segments = payload.get("segments") or []
    template = next((s for s in segments if s.get("segment_index") == source), None)
    if template is not None:
        new_segment = {k: copy.deepcopy(v) for k, v in template.items() if k in SEGMENT_INPUTS}
        new_segment["job"] = {k: copy.deepcopy(v) for k, v in template.get("job", {}).items() if k in JOB_INPUTS}
        new_segment.update(segment_uid=new_segment_uid(), status="pending", submitted=False)
        new_segment = _retarget_segment(new_segment, target, {})
        new_segment["beat_uid"] = duplicate["beat_uid"]
    else:
        new_segment = _split_placeholder(None, duplicate, target, {})
        new_segment["stale_reason"] = "beat copied; generate segment prompt"
        new_segment["beat_uid"] = duplicate["beat_uid"]
    # An active request can still address a part by its old display position.
    for segment in segments:
        if int(segment.get("segment_index") or 0) >= target and str(segment.get("status") or "").lower() in {"running", "queued"}:
            raise ValueError("目标位置及后续分段正在生成，请等待完成后再复制")
    updated = [
        _retarget_segment(s, int(s["segment_index"]) + (int(s["segment_index"]) >= target), {})
        for s in segments
    ]
    if segments:
        updated.append(new_segment)
    payload["segments"] = sorted(updated, key=lambda s: s["segment_index"])
    payload["segment_count"] = len(updated)
    payload["total_duration_sec"] = sum(float(s.get("job", {}).get("duration_sec") or 0) for s in updated)
    for key in list(payload):
        if key.startswith("final_video"):
            payload.pop(key)
    return data, payload


def persist_beat_copy(project_dir: Path, beats_data, source_index, target_index):
    """Commit both payloads together, rolling back file errors from either write."""
    with _VIDEO_JOBS_LOCK:
        beats, jobs = build_beat_copy(beats_data, load_video_jobs(), source_index, target_index)
        paths = [project_dir / "beats.json", project_dir / "video_jobs.json", project_dir / "logs/render_log.json"]
        originals = {p: p.read_bytes() if p.exists() else None for p in paths}
        staged = project_dir / "beats.copy.tmp"
        try:
            staged.write_text(json.dumps(beats, ensure_ascii=False, indent=2), encoding="utf-8")
            save_video_jobs(jobs)
            staged.replace(paths[0])
            if originals[paths[2]] is not None:
                render_log = json.loads(originals[paths[2]])
                for part in render_log.get("parts", []):
                    part_id = str(part.get("id") or "")
                    if part_id.startswith("part_") and part_id[5:].isdigit():
                        index = int(part_id[5:])
                        if index >= int(target_index):
                            part["id"] = f"part_{index + 1:03d}"
                temp_log = paths[2].with_suffix(".copy.tmp")
                temp_log.write_text(json.dumps(render_log, ensure_ascii=False, indent=2), encoding="utf-8")
                temp_log.replace(paths[2])
        except Exception:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise
        finally:
            staged.unlink(missing_ok=True)
        return beats, jobs
