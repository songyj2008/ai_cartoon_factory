"""Run LiconMSR segment jobs from normalized beats."""
from __future__ import annotations

import json
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
import time
from typing import Any

from generation.scheduler import GenerationScheduler
from pipeline.events import ensure_event_fields_on_beats
from generation.pipelines.ltx_licon.asset_binder import LtxAssetBinder
from generation.pipelines.ltx_licon.prompt_compiler import LtxPromptCompiler
from generation.pipelines.ltx_licon.workflow_adapter import LtxLiconWorkflowAdapter
from services.brand import brand_trace
from services.context import CONFIG, get_project_dir
from services.file_utils import load_json_file_silent, save_json
from services.logger import log
from workflow.asset_resolver import prompt_with_character_descriptions, selected_project_materials
from workflow.licon_msr import MODEL_ID as LTX_LICON_MODEL_ID
from workflow.runninghub_usage import record_runninghub_attempt, refresh_runninghub_usage_summaries
from workflow.runninghub_queue import active_task_count, active_task_id, is_queue_full_error
from workflow.video_merge import existing_segment_video, load_video_jobs, merge_segment_videos, save_video_jobs, video_jobs_lock


def extract_director_prompt(prompt: str) -> str:
    """Return only the editable 【导演分镜】 body from a full LiconMSR prompt."""
    text = str(prompt or "").strip()
    marker = "【导演分镜】"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


_PROMPT_COMPILER = LtxPromptCompiler()
_ASSET_BINDER = LtxAssetBinder()
_WORKFLOW_ADAPTER = LtxLiconWorkflowAdapter()


def _final_prompt_from_director(
    director_prompt: str,
    reference_roles: list[str],
    reference_image_ids: dict[str, str] | None = None,
) -> str:
    return prompt_with_character_descriptions(
        extract_director_prompt(director_prompt),
        [str(role) for role in reference_roles or []],
        reference_image_ids,
    )


def _merge_existing_segments(new_payload: dict[str, Any], existing_payload: dict[str, Any], selected_indices: set[int] | None) -> dict[str, Any]:
    if not selected_indices:
        return new_payload
    existing_by_index = {
        int(item.get("segment_index") or 0): item
        for item in existing_payload.get("segments") or []
        if isinstance(item, dict)
    }
    merged = []
    for item in new_payload.get("segments") or []:
        idx = int(item.get("segment_index") or 0)
        merged.append(item if idx in selected_indices else existing_by_index.get(idx, item))
    new_payload["segments"] = merged
    return new_payload


def _payload_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "mode": "licon_msr_segmented",
        "generation_mode": "model_factory_segmented",
        "schema_version": 3,
        "default_model_id": LTX_LICON_MODEL_ID,
        "segment_count": len(results),
        "total_duration_sec": sum(int(float((item.get("job") or {}).get("duration_sec") or 0)) for item in results),
        "fps": int(CONFIG.get("fps") or 50),
        "brand_trace": brand_trace(),
        "segments": results,
    }
    refresh_runninghub_usage_summaries(payload)
    return payload


def _preserved_runninghub_history(segment: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the billing audit trail when prompts/workflows are rebuilt."""
    if not isinstance(segment, dict):
        return {}
    preserved = {
        key: copy.deepcopy(segment[key])
        for key in (
            "segment_uid",
            "runninghub_attempts",
            "runninghub_usage",
            "renders",
            "active_render_id",
            "manual_active_render_id",
            "stale_render_id",
            "render_history_schema_version",
        )
        if key in segment
    }
    return preserved


def _record_runninghub_result(segment: dict[str, Any], result: dict[str, Any]) -> None:
    if str(result.get("backend") or "").strip().lower() != "runninghub":
        return
    task_id = str(result.get("task_id") or "").strip()
    if not task_id:
        return
    submitted = result.get("submit_result") if isinstance(result.get("submit_result"), dict) else {}
    outputs = submitted.get("outputs") if isinstance(submitted.get("outputs"), list) else []
    status = "success" if result.get("video_path") else "running"
    result_job = result.get("job") if isinstance(result.get("job"), dict) else {}
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    submitted_prompt = str(
        result_job.get("prompt")
        or job.get("final_prompt")
        or job.get("prompt")
        or ""
    ).strip()
    record_runninghub_attempt(
        segment,
        task_id,
        status=status,
        outputs=outputs,
        submitted_prompt=submitted_prompt,
        video_path=str(result.get("video_path") or ""),
    )


def _video_submit_concurrency_limit() -> int:
    """Return the safe submission width for the configured backend."""
    backend = str(CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    return GenerationScheduler().concurrency_limit(LTX_LICON_MODEL_ID, backend)


def _serialize_video_jobs(func):
    """Keep a whole job-file read/modify/write operation together."""
    def wrapped(*args, **kwargs):
        from services.context import pin_runtime
        with pin_runtime(), video_jobs_lock():
            return func(*args, **kwargs)

    return wrapped


def _active_task_id(segment: dict[str, Any]) -> str:
    return active_task_id(segment)


def _segment_preserve_on_reset(segment: dict[str, Any]) -> bool:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return bool(
        segment.get("preserve_on_reset")
        or segment.get("video_ok")
        or segment.get("protected_from_reset")
        or job.get("preserve_on_reset")
        or job.get("video_ok")
        or job.get("protected_from_reset")
    )


def _runninghub_active_task_count(segments: list[dict[str, Any]]) -> int:
    """Count remote tasks that occupy one of RunningHub's execution slots."""
    return active_task_count(segments)


def _is_runninghub_queue_full_error(error: Exception | str) -> bool:
    return is_queue_full_error(error)


def run_licon_msr_segments(
    beats_data: dict[str, Any],
    submit_to_comfyui: bool = True,
    workflow_only: bool = False,
    segment_indices: set[int] | None = None,
    merge_after: bool = False,
) -> dict[str, Any]:
    # The incoming Beat payload already owns visibility and reference-role
    # structure. Re-running text asset matching here can erase those fields
    # when an alias is absent from the prose.
    beats_data = ensure_event_fields_on_beats(beats_data)
    submit = bool(submit_to_comfyui) and not bool(workflow_only)
    segment_jobs = _PROMPT_COMPILER.compile_jobs(beats_data)
    existing_payload = load_video_jobs()
    existing_by_index = {
        int(item.get("segment_index") or 0): item
        for item in existing_payload.get("segments") or []
        if isinstance(item, dict)
    }
    results = []

    def _current_payload() -> dict[str, Any]:
        return _payload_from_results(results)

    log("[LICON_MSR] segmented generation", "STEP")
    log(f"[LICON_MSR] segments={len(segment_jobs)} selected={sorted(segment_indices) if segment_indices else 'all'}", "STEP")

    for job in segment_jobs:
        idx = int(job["segment_index"])
        part_id = str(job.get("part_id") or f"part_{idx:03d}")
        existing_segment = existing_by_index.get(idx)
        if not segment_indices and isinstance(existing_segment, dict) and _segment_preserve_on_reset(existing_segment):
            results.append(existing_segment)
            log(f"[LICON_MSR] keep protected segment {part_id}", "STEP")
            continue
        reference_image_ids = {
            str(role): str(image_id).strip()
            for role, image_id in (job.get("reference_image_ids") or {}).items()
            if str(role).strip() and str(image_id).strip()
        }
        references, background = _ASSET_BINDER.bind(
            [str(role) for role in job.get("reference_roles") or []],
            reference_image_ids,
            str(job.get("scene_id") or ""),
        )
        director_prompt = extract_director_prompt(job["prompt"])
        final_prompt = _final_prompt_from_director(
            director_prompt,
            [str(role) for role in job.get("reference_roles") or []],
            reference_image_ids,
        )
        if segment_indices and idx not in segment_indices:
            results.append(
                {
                    "segment_id": job["segment_id"],
                    "segment_index": idx,
                    "part_id": part_id,
                    "title": job.get("title"),
                    "job": {
                        "prompt": director_prompt,
                        "director_prompt": director_prompt,
                        "final_prompt": final_prompt,
                        "duration_sec": job["duration_sec"],
                        "fps": int(CONFIG.get("fps") or 50),
                        "total_frames": int(job["duration_sec"]) * int(CONFIG.get("fps") or 50),
                        "reference_images": references,
                        "reference_image_ids": reference_image_ids,
                        "background_image": background,
                        "part_id": part_id,
                    },
                    "scene_id": job.get("scene_id") or "",
                    "reference_roles": job.get("reference_roles") or [],
                    "reference_image_ids": reference_image_ids,
                    "visible_roles": job.get("visible_roles") or [],
                    "offscreen_speakers": job.get("offscreen_speakers") or [],
                    "mentioned_roles": job.get("mentioned_roles") or [],
                    "skipped": True,
                }
            )
            continue

        prefix = f"LTX-2/MSR_segments/{part_id}"
        pending_result = {
            "segment_id": job["segment_id"],
            "segment_index": idx,
            "part_id": part_id,
            "title": job.get("title"),
            "job": {
                "prompt": director_prompt,
                "director_prompt": director_prompt,
                "final_prompt": final_prompt,
                "duration_sec": job["duration_sec"],
                "fps": int(CONFIG.get("fps") or 50),
                "total_frames": int(job["duration_sec"]) * int(CONFIG.get("fps") or 50),
                "reference_images": references,
                "reference_image_ids": reference_image_ids,
                "background_image": background,
                "part_id": part_id,
            },
            "scene_id": job.get("scene_id") or "",
            "important_roles": job.get("important_roles") or [],
            "visible_roles": job.get("visible_roles") or [],
            "reference_roles": job.get("reference_roles") or [],
            "reference_image_ids": reference_image_ids,
            "offscreen_speakers": job.get("offscreen_speakers") or [],
            "mentioned_roles": job.get("mentioned_roles") or [],
            "background_extras": job.get("background_extras") or [],
            "prompt_saved": True,
            "status": "running" if submit else "workflow_ready",
        }
        pending_result.update(_preserved_runninghub_history(existing_segment))
        results.append(pending_result)
        save_video_jobs(_merge_existing_segments(_current_payload(), existing_payload, segment_indices))
        log(f"[LICON_MSR] segment {idx}/{len(segment_jobs)} part={part_id} duration={job['duration_sec']}s", "STEP")
        log(
            f"[LICON_MSR] {part_id} reference_roles={job.get('reference_roles') or []} "
            f"reference_images={len(references)} scene_id={job.get('scene_id') or ''}",
            "STEP",
        )
        try:
            from ui.render_state import mark_part_running, mark_part_success

            if submit:
                mark_part_running(part_id)
            result = _WORKFLOW_ADAPTER.build_and_optionally_submit(
                prompt=final_prompt,
                duration_sec=job["duration_sec"],
                reference_images=references,
                background_image=background,
                output_prefix=prefix,
                submit=submit,
                part_id=part_id,
                wait=submit,
            )
            if result.get("video_path"):
                mark_part_success(part_id, result.get("video_path"))
        except Exception as exc:
            from ui.render_state import mark_part_failed

            mark_part_failed(part_id, str(exc))
            raise

        result["segment_id"] = job["segment_id"]
        result["segment_index"] = idx
        result["part_id"] = part_id
        result["title"] = job.get("title")
        result["scene_id"] = job.get("scene_id") or ""
        result["important_roles"] = job.get("important_roles") or []
        result["visible_roles"] = job.get("visible_roles") or []
        result["reference_roles"] = job.get("reference_roles") or []
        result["reference_image_ids"] = reference_image_ids
        result["offscreen_speakers"] = job.get("offscreen_speakers") or []
        result["mentioned_roles"] = job.get("mentioned_roles") or []
        result["background_extras"] = job.get("background_extras") or []
        result["prompt_saved"] = True
        result["status"] = "success" if result.get("video_path") else ("running" if result.get("submitted") and result.get("task_id") else "workflow_ready")
        result["job"] = {
            **(result.get("job") or {}),
            "prompt": director_prompt,
            "director_prompt": director_prompt,
            "final_prompt": final_prompt,
            "duration_sec": job["duration_sec"],
            "fps": int(CONFIG.get("fps") or 50),
            "total_frames": int(job["duration_sec"]) * int(CONFIG.get("fps") or 50),
            "reference_images": references,
            "reference_image_ids": reference_image_ids,
            "background_image": background,
            "part_id": part_id,
        }
        result.update(_preserved_runninghub_history(existing_segment))
        _record_runninghub_result(result, result)
        results[-1] = result
        save_video_jobs(_merge_existing_segments(_current_payload(), existing_payload, segment_indices))
        log(f"[LICON_MSR] workflow={result.get('workflow_path')}", "STEP")

    payload = _payload_from_results(results)
    payload = _merge_existing_segments(payload, existing_payload, segment_indices)
    refresh_runninghub_usage_summaries(payload)
    if merge_after:
        final_path = merge_segment_videos(payload)
        payload["final_video_path"] = str(final_path) if final_path else ""
    save_video_jobs(payload)
    return payload


def prepare_licon_msr_segment_prompts(beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> dict[str, Any]:
    """Generate editable director prompts and workflow JSON files without submitting video jobs."""
    log("[LICON_MSR] prepare editable segment prompts", "STEP")
    return run_licon_msr_segments(
        beats_data,
        submit_to_comfyui=False,
        workflow_only=True,
        segment_indices=segment_indices,
        merge_after=False,
    )


def _validated_material_binding(
    reference_roles: list[str],
    reference_image_ids: dict[str, str] | None,
    scene_id: str,
) -> tuple[list[str], dict[str, str], str]:
    materials = selected_project_materials()
    valid_characters = {
        str(item.get("id") or ""): item
        for item in materials.get("characters") or []
        if isinstance(item, dict) and item.get("available") and item.get("id")
    }
    valid_roles = set(valid_characters)
    valid_scenes = {
        str(item.get("id") or "")
        for item in materials.get("backgrounds") or []
        if isinstance(item, dict) and item.get("available")
    }
    roles = list(dict.fromkeys(str(role).strip() for role in reference_roles or [] if str(role).strip()))
    if len(roles) > 4:
        raise ValueError("人物参考图最多选择 4 张")
    invalid_roles = [role for role in roles if role not in valid_roles]
    if invalid_roles:
        raise ValueError("人物参考图不属于当前项目已选素材：" + "、".join(invalid_roles))
    requested_image_ids = reference_image_ids if isinstance(reference_image_ids, dict) else {}
    selected_image_ids: dict[str, str] = {}
    for role in roles:
        requested_id = str(requested_image_ids.get(role) or "").strip()
        options = [
            image
            for image in valid_characters[role].get("images") or []
            if isinstance(image, dict) and image.get("available")
        ]
        valid_images = {
            str(image.get("id") or ""): image
            for image in options
            if str(image.get("id") or "").strip()
        }
        if requested_id:
            if requested_id not in valid_images:
                raise ValueError(f"人物 {role} 的参考图 {requested_id} 不属于当前项目已选素材")
            selected_image_ids[role] = requested_id
            continue
        primary = next((image for image in options if image.get("is_primary")), options[0] if options else None)
        if primary and str(primary.get("id") or "").strip():
            selected_image_ids[role] = str(primary.get("id")).strip()
    selected_scene = str(scene_id or "").strip()
    if not valid_scenes:
        raise ValueError("当前项目没有可用背景图，请先在项目素材选择中添加背景")
    if not selected_scene:
        raise ValueError("必须选择一张背景参考图")
    if selected_scene not in valid_scenes:
        raise ValueError("背景参考图不属于当前项目已选素材")
    return roles, selected_image_ids, selected_scene


def _save_manual_binding_to_beat(
    segment_index: int,
    reference_roles: list[str],
    reference_image_ids: dict[str, str],
    scene_id: str,
) -> None:
    raw = load_json_file_silent("beats.json") or ""
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise ValueError("beats.json 内容无效，无法同步素材绑定") from exc
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or not (0 < segment_index <= len(beats)):
        raise ValueError(f"beats.json 中找不到 Beat {segment_index}")
    beat = beats[segment_index - 1]
    if not isinstance(beat, dict):
        raise ValueError(f"Beat {segment_index} 内容无效")

    # The full-screen editor owns the cast for the edited segment: its selected
    # character references are exactly the in-frame characters for that beat.
    previous_important_roles = [
        str(role).strip()
        for role in (beat.get("important_roles") or [])
        if str(role or "").strip()
    ]
    beat["visible_roles"] = list(reference_roles)
    beat["reference_roles"] = list(reference_roles)
    beat["important_roles"] = list(dict.fromkeys([*reference_roles, *previous_important_roles]))
    beat["offscreen_speakers"] = [
        str(role).strip()
        for role in (beat.get("offscreen_speakers") or [])
        if str(role or "").strip() and str(role).strip() not in reference_roles
    ]
    beat["mentioned_roles"] = [
        str(role).strip()
        for role in (beat.get("mentioned_roles") or [])
        if str(role or "").strip() and str(role).strip() not in reference_roles
    ]
    beat["reference_image_ids"] = dict(reference_image_ids)
    beat["scene_id"] = scene_id

    constraints = beat.get("identity_constraints") if isinstance(beat.get("identity_constraints"), dict) else {}
    beat["identity_constraints"] = {
        **constraints,
        "registered_identity_only": True,
        "locked_roles": list(reference_roles),
    }
    auto_match = beat.get("auto_asset_match") if isinstance(beat.get("auto_asset_match"), dict) else {}
    beat["auto_asset_match"] = {
        **auto_match,
        "manual_override": True,
        "manual_selection": {
            "reference_roles": list(reference_roles),
            "reference_image_ids": dict(reference_image_ids),
            "scene_id": scene_id,
        },
    }
    save_json("beats.json", data)


def _segment_reusable_video_path(segment: dict[str, Any]) -> str:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    for value in (
        segment.get("video_path"),
        job.get("video_path"),
        segment.get("stale_video_path"),
        job.get("stale_video_path"),
    ):
        path_text = str(value or "").strip()
        if path_text and Path(path_text).exists():
            return path_text
    return existing_segment_video(segment)


@_serialize_video_jobs
def set_segment_video_ok(segment_index: int, video_ok: bool | None = None) -> dict[str, Any]:
    idx = int(segment_index or 0)
    if idx <= 0:
        raise ValueError("invalid segment index")
    payload = load_video_jobs()
    segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    target = None
    for segment in segments:
        if int(segment.get("segment_index") or 0) == idx:
            target = segment
            break
    if not target:
        raise ValueError(f"segment {idx} not found in video_jobs.json")

    job = target.get("job") if isinstance(target.get("job"), dict) else {}
    part_id = str(target.get("part_id") or job.get("part_id") or f"part_{idx:03d}")
    current = _segment_preserve_on_reset(target)
    next_value = (not current) if video_ok is None else bool(video_ok)
    if next_value:
        reusable_video = _segment_reusable_video_path(target)
        if not reusable_video:
            raise ValueError(f"{part_id} 没有可保留的视频文件，不能标记为视频OK")
        target["video_ok"] = True
        target["preserve_on_reset"] = True
        target["protected_from_reset"] = True
        target["video_ok_reason"] = "user confirmed generated video"
        job["video_ok"] = True
        job["preserve_on_reset"] = True
        job["protected_from_reset"] = True
        if reusable_video:
            target["video_path"] = reusable_video
            job["video_path"] = reusable_video
            target["status"] = "success"
            target.pop("stale_video_path", None)
            target.pop("stale_reason", None)
            job.pop("stale_video_path", None)
            job.pop("stale_reason", None)
            try:
                from ui.render_state import mark_part_success

                mark_part_success(part_id, reusable_video)
            except Exception:
                pass
        log(f"[LICON_MSR] marked {part_id} video OK; preserve on reset", "STEP")
    else:
        for key in ("video_ok", "preserve_on_reset", "protected_from_reset", "video_ok_reason"):
            target.pop(key, None)
            job.pop(key, None)
        log(f"[LICON_MSR] removed video OK protection from {part_id}", "STEP")
    target["job"] = job
    payload["segments"] = segments
    save_video_jobs(payload)
    return payload


def _attempt_video_path(segment: dict[str, Any], attempt: dict[str, Any], task_id: str) -> Path | None:
    """Return only the local video explicitly associated with this task."""
    raw_path = str(attempt.get("video_path") or "").strip()
    if not raw_path and str(segment.get("task_id") or "").strip() == task_id:
        raw_path = str(segment.get("video_path") or "").strip()
    if not raw_path:
        return None
    try:
        path = Path(raw_path).resolve()
        videos_dir = (get_project_dir() / "videos").resolve()
        path.relative_to(videos_dir)
    except (OSError, ValueError):
        raise ValueError("任务视频路径不在当前项目的视频目录中，已拒绝删除")
    if path.suffix.lower() != ".mp4":
        raise ValueError("任务视频不是可删除的 MP4 文件")
    return path


@_serialize_video_jobs
def delete_runninghub_attempt_video(segment_index: int, task_id: str) -> dict[str, Any]:
    """Delete one locally downloaded task video while preserving its audit trail."""
    idx = int(segment_index or 0)
    normalized_task_id = str(task_id or "").strip()
    if idx <= 0 or not normalized_task_id:
        raise ValueError("segment_index 和 task_id 不能为空")

    payload = load_video_jobs()
    segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    target = next((item for item in segments if int(item.get("segment_index") or 0) == idx), None)
    if not target:
        raise ValueError(f"segment {idx} not found in video_jobs.json")
    attempts = target.get("runninghub_attempts") if isinstance(target.get("runninghub_attempts"), list) else []
    attempt = next(
        (item for item in attempts if isinstance(item, dict) and str(item.get("task_id") or "").strip() == normalized_task_id),
        None,
    )
    if not attempt:
        raise ValueError("未找到对应的生成任务记录")

    video_path = _attempt_video_path(target, attempt, normalized_task_id)
    if not video_path:
        raise ValueError("该历史任务没有可删除的本地视频文件")
    if video_path.exists():
        video_path.unlink()

    attempt["video_path"] = ""
    attempt["video_deleted"] = True
    attempt["video_deleted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    deleted_render_id = ""
    try:
        from generation.render_history import find_render

        deleted_render = find_render(target, task_id=normalized_task_id)
        if isinstance(deleted_render, dict):
            deleted_render_id = str(deleted_render.get("render_id") or "").strip()
            output = deleted_render.get("output") if isinstance(deleted_render.get("output"), dict) else {}
            output["video_deleted"] = True
            output["video_deleted_at"] = attempt["video_deleted_at"]
            deleted_render["output"] = output
            deleted_render["status"] = "deleted"
    except Exception:
        deleted_render_id = ""

    if deleted_render_id and str(target.get("active_render_id") or "").strip() == deleted_render_id:
        replacement = None
        for candidate in reversed([item for item in target.get("renders") or [] if isinstance(item, dict)]):
            output = candidate.get("output") if isinstance(candidate.get("output"), dict) else {}
            candidate_path = str(output.get("video_path") or "").strip()
            if output.get("video_deleted") or not candidate_path or not Path(candidate_path).is_file():
                continue
            replacement = candidate
            break
        if replacement is None:
            target.pop("active_render_id", None)
        else:
            target["active_render_id"] = str(replacement.get("render_id") or "").strip()
        target.pop("manual_active_render_id", None)

    job = target.get("job") if isinstance(target.get("job"), dict) else {}
    if str(target.get("task_id") or "").strip() == normalized_task_id:
        for container in (target, job):
            for key in ("video_path", "stale_video_path"):
                if str(container.get(key) or "").strip():
                    container[key] = ""
            for key in ("video_ok", "preserve_on_reset", "protected_from_reset", "video_ok_reason"):
                container.pop(key, None)
        target["submitted"] = False
        target["status"] = "workflow_ready"
        target["error"] = ""
    current_provider_id = str(target.get("task_id") or target.get("prompt_id") or "").strip()
    generation_active = str(target.get("status") or "").strip().lower() in {"running", "pending", "queued"} and bool(current_provider_id)
    if not generation_active and str(target.get("active_render_id") or "").strip():
        try:
            from generation.render_history import project_active_render

            project_active_render(target, include_status=True, clear_when_missing=True)
        except Exception:
            pass
    target["job"] = job
    payload["segments"] = segments
    # The existing final file is left intact, but it no longer represents the
    # current source set after one source task has been removed.
    payload.pop("final_video_path", None)
    save_video_jobs(payload)
    log(f"[LICON_MSR] deleted local video for task {normalized_task_id}", "STEP")
    return payload


@_serialize_video_jobs
def select_runninghub_attempt_video(segment_index: int, task_id: str) -> dict[str, Any]:
    """Use one downloaded history video for preview and the next final merge.

    The current provider task remains untouched.  This is essential when the
    user selects an older video while a newer regeneration is still running.
    """
    idx = int(segment_index or 0)
    normalized_task_id = str(task_id or "").strip()
    if idx <= 0 or not normalized_task_id:
        raise ValueError("segment_index 和 task_id 不能为空")

    payload = load_video_jobs()
    segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    target = next((item for item in segments if int(item.get("segment_index") or 0) == idx), None)
    if not target:
        raise ValueError(f"segment {idx} not found in video_jobs.json")
    attempts = target.get("runninghub_attempts") if isinstance(target.get("runninghub_attempts"), list) else []
    attempt = next(
        (item for item in attempts if isinstance(item, dict) and str(item.get("task_id") or "").strip() == normalized_task_id),
        None,
    )
    if not attempt:
        raise ValueError("未找到对应的生成任务记录")
    if attempt.get("video_deleted"):
        raise ValueError("该历史任务的视频已删除，不能设为合并来源")
    video_path = _attempt_video_path(target, attempt, normalized_task_id)
    if not video_path or not video_path.is_file():
        raise ValueError("该历史任务没有可用的本地视频文件")

    from generation.render_history import find_render, project_active_render

    render = find_render(target, task_id=normalized_task_id)
    if not isinstance(render, dict):
        raise ValueError("该历史任务缺少 Render 记录，无法安全选择")
    output = render.get("output") if isinstance(render.get("output"), dict) else {}
    if output.get("video_deleted"):
        raise ValueError("该历史任务的视频已删除，不能设为合并来源")
    render_video = str(output.get("video_path") or "").strip()
    if not render_video or not Path(render_video).is_file():
        raise ValueError("该历史 Render 没有可用的视频文件")

    render_id = str(render.get("render_id") or "").strip()
    if not render_id:
        raise ValueError("该历史 Render 缺少唯一标识")
    target["active_render_id"] = render_id
    target["manual_active_render_id"] = render_id

    status = str(target.get("status") or "").strip().lower()
    current_provider_id = str(target.get("task_id") or target.get("prompt_id") or "").strip()
    generation_active = status in {"running", "pending", "queued"} and bool(current_provider_id)
    if not generation_active:
        project_active_render(target, include_status=True)

    payload["segments"] = segments
    payload.pop("final_video_path", None)
    save_video_jobs(payload)
    log(f"[LICON_MSR] selected task video for {target.get('part_id') or idx}: {normalized_task_id}", "STEP")
    return payload


@_serialize_video_jobs
def save_segment_director_prompt(
    segment_index: int,
    director_prompt: str,
    reference_roles: list[str] | None = None,
    reference_image_ids: dict[str, str] | None = None,
    scene_id: str | None = None,
    full_prompt_override: str | None = None,
) -> dict[str, Any]:
    """Persist one edited director prompt to video_jobs.json and its workflow JSON."""
    idx = int(segment_index or 0)
    prompt = extract_director_prompt(director_prompt)
    full_prompt = str(full_prompt_override or "").strip() if full_prompt_override is not None else ""
    if idx <= 0:
        raise ValueError("invalid segment index")
    if not prompt and not full_prompt:
        raise ValueError("导演分镜不能为空")
    if full_prompt_override is not None and not full_prompt:
        raise ValueError("完整工作流提示词不能为空")

    payload = load_video_jobs()
    segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    target = None
    for segment in segments:
        if int(segment.get("segment_index") or 0) == idx:
            target = segment
            break
    if not target:
        raise ValueError(f"segment {idx} not found in video_jobs.json")

    if str(target.get("status") or "").strip().lower() == "running" and _active_task_id(target):
        part_id = str(target.get("part_id") or f"part_{idx:03d}")
        raise RuntimeError(f"{part_id} 正在 RunningHub 生成中，请等待完成后再重新生成")

    job = target.get("job") if isinstance(target.get("job"), dict) else {}
    old_video_path = (
        str(target.get("stale_video_path") or "").strip()
        or str(job.get("stale_video_path") or "").strip()
        or existing_segment_video(target)
    )
    material_override = reference_roles is not None or reference_image_ids is not None or scene_id is not None
    if reference_roles is not None and not isinstance(reference_roles, list):
        raise ValueError("reference_roles 必须是人物素材列表")
    if reference_image_ids is not None and not isinstance(reference_image_ids, dict):
        raise ValueError("reference_image_ids 必须是人物到参考图的映射")
    current_roles = [str(role) for role in target.get("reference_roles") or []]
    current_image_ids = target.get("reference_image_ids")
    if not isinstance(current_image_ids, dict):
        current_image_ids = job.get("reference_image_ids") if isinstance(job.get("reference_image_ids"), dict) else {}
    current_scene = str(target.get("scene_id") or job.get("scene_id") or "").strip()
    selected_roles = current_roles if reference_roles is None else list(reference_roles)
    selected_image_ids = dict(current_image_ids) if reference_image_ids is None else dict(reference_image_ids)
    selected_scene = current_scene if scene_id is None else str(scene_id or "").strip()
    current_visible_roles = [
        str(role).strip()
        for role in (target.get("visible_roles") or job.get("visible_roles") or [])
        if str(role or "").strip()
    ]
    current_important_roles = [
        str(role).strip()
        for role in (target.get("important_roles") or job.get("important_roles") or [])
        if str(role or "").strip()
    ]
    current_offscreen_speakers = [
        str(role).strip()
        for role in (target.get("offscreen_speakers") or job.get("offscreen_speakers") or [])
        if str(role or "").strip()
    ]
    current_mentioned_roles = [
        str(role).strip()
        for role in (target.get("mentioned_roles") or job.get("mentioned_roles") or [])
        if str(role or "").strip()
    ]
    selected_visible_roles = list(current_visible_roles)
    selected_important_roles = list(current_important_roles)
    selected_offscreen_speakers = list(current_offscreen_speakers)
    selected_mentioned_roles = list(current_mentioned_roles)
    if material_override:
        selected_roles, selected_image_ids, selected_scene = _validated_material_binding(
            selected_roles,
            selected_image_ids,
            selected_scene,
        )
        selected_visible_roles = list(selected_roles)
        selected_important_roles = list(dict.fromkeys([*selected_visible_roles, *current_important_roles]))
        selected_offscreen_speakers = [
            role for role in current_offscreen_speakers if role not in selected_visible_roles
        ]
        selected_mentioned_roles = [
            role for role in current_mentioned_roles if role not in selected_visible_roles
        ]
        reference_images, background_image = _ASSET_BINDER.bind(selected_roles, selected_image_ids, selected_scene)
    else:
        reference_images = [str(x) for x in job.get("reference_images") or []]
        background_image = str(job.get("background_image") or "")

    final_prompt = full_prompt or _final_prompt_from_director(prompt, selected_roles, selected_image_ids)
    part_id = str(target.get("part_id") or job.get("part_id") or f"part_{idx:03d}")
    prefix = str(job.get("output_prefix") or f"LTX-2/MSR_segments/{part_id}")
    result = _WORKFLOW_ADAPTER.build_and_optionally_submit(
        prompt=final_prompt,
        duration_sec=float(job.get("duration_sec") or 15),
        reference_images=reference_images,
        background_image=background_image,
        output_prefix=prefix,
        submit=False,
        part_id=part_id,
        wait=False,
    )

    target["workflow_path"] = result.get("workflow_path") or target.get("workflow_path") or ""
    target["backend"] = result.get("backend", "")
    target["submitted"] = False
    target["prompt_id"] = ""
    target["task_id"] = ""
    target["output_urls"] = []
    target["video_path"] = ""
    target.pop("video_ok", None)
    target.pop("preserve_on_reset", None)
    target.pop("protected_from_reset", None)
    if old_video_path:
        target["stale_video_path"] = old_video_path
        target["stale_reason"] = "prompt changed"
    else:
        target.pop("stale_video_path", None)
        target.pop("stale_reason", None)
    target["submit_result"] = {}
    target["raw_submit"] = {}
    target["error"] = ""
    target["prompt_saved"] = True
    target["manual_full_prompt"] = bool(full_prompt)
    target["status"] = "needs_regenerate"
    if material_override:
        target["visible_roles"] = list(selected_visible_roles)
        target["reference_roles"] = list(selected_roles)
        target["important_roles"] = list(selected_important_roles)
        target["offscreen_speakers"] = list(selected_offscreen_speakers)
        target["mentioned_roles"] = list(selected_mentioned_roles)
        target["reference_image_ids"] = dict(selected_image_ids)
        target["scene_id"] = selected_scene
        target["asset_binding_source"] = "manual"
    target["job"] = {
        **job,
        "prompt": prompt,
        "director_prompt": prompt,
        "final_prompt": final_prompt,
        "manual_full_prompt": bool(full_prompt),
        "duration_sec": float(job.get("duration_sec") or 15),
        "fps": int(job.get("fps") or CONFIG.get("fps") or 50),
        "total_frames": int(float(job.get("duration_sec") or 15)) * int(job.get("fps") or CONFIG.get("fps") or 50),
        "visible_roles": list(selected_visible_roles),
        "reference_roles": list(selected_roles),
        "important_roles": list(selected_important_roles),
        "offscreen_speakers": list(selected_offscreen_speakers),
        "mentioned_roles": list(selected_mentioned_roles),
        "reference_image_ids": dict(selected_image_ids),
        "scene_id": selected_scene,
        "reference_images": reference_images,
        "background_image": background_image,
        "video_path": "",
        "stale_video_path": old_video_path if old_video_path else "",
        "output_prefix": prefix,
        "part_id": part_id,
    }
    for key in ("video_ok", "preserve_on_reset", "protected_from_reset"):
        target["job"].pop(key, None)
    save_video_jobs(payload)
    if material_override:
        _save_manual_binding_to_beat(idx, selected_roles, selected_image_ids, selected_scene)
    log(f"[LICON_MSR] saved edited director prompt for {part_id}", "STEP")
    return payload


@_serialize_video_jobs
def save_segment_director_prompts_batch(edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist every currently edited director prompt before a video submission."""
    if not isinstance(edits, list):
        raise ValueError("分段编辑数据无效")
    latest_by_index: dict[int, dict[str, Any]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        index = int(edit.get("segment_index") or 0)
        prompt = str(edit.get("director_prompt") or "").strip()
        if index > 0 and prompt:
            latest_by_index[index] = {"segment_index": index, "director_prompt": prompt}
    if not latest_by_index:
        return load_video_jobs()

    for index in sorted(latest_by_index):
        edit = latest_by_index[index]
        save_segment_director_prompt(edit["segment_index"], edit["director_prompt"])
    log(f"[LICON_MSR] saved {len(latest_by_index)} edited segment prompts before submit", "STEP")
    return load_video_jobs()


@_serialize_video_jobs
def submit_saved_licon_msr_segments(segment_indices: set[int] | None = None, merge_after: bool = False, wait: bool = True) -> dict[str, Any]:
    """Submit existing saved segment workflows without regenerating prompts from beats."""
    payload = load_video_jobs()
    segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    if not segments:
        raise ValueError("video_jobs.json 为空，请先点击生成分段提示词")

    selected = set(int(x) for x in segment_indices) if segment_indices else None
    concurrency_limit = _video_submit_concurrency_limit()
    backend = str(CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    wait_for_result = bool(wait) and concurrency_limit == 1
    log(f"[LICON_MSR] submit saved workflows selected={sorted(selected) if selected else 'all'}", "STEP")
    log(f"[LICON_MSR] video submit concurrency={concurrency_limit} wait={wait_for_result}", "STEP")

    submission_items: list[dict[str, Any]] = []

    for segment in segments:
        idx = int(segment.get("segment_index") or 0)
        if selected and idx not in selected:
            continue
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        part_id = str(segment.get("part_id") or job.get("part_id") or f"part_{idx:03d}")
        if selected is None and _segment_preserve_on_reset(segment):
            log(f"[LICON_MSR] skip protected video-ok segment {part_id}", "STEP")
            continue
        active_task_id = _active_task_id(segment)
        if str(segment.get("status") or "").strip().lower() == "running" and active_task_id:
            if selected is not None:
                raise RuntimeError(f"{part_id} 正在 RunningHub 生成中（任务 {active_task_id}），请勿重复提交")
            log(f"[LICON_MSR] skip active RunningHub task {part_id}: {active_task_id}", "STEP")
            continue
        director_prompt = extract_director_prompt(job.get("director_prompt") or job.get("prompt") or job.get("final_prompt"))
        reference_roles = [str(role) for role in (segment.get("reference_roles") or job.get("reference_roles") or [])]
        reference_image_ids = segment.get("reference_image_ids")
        if not isinstance(reference_image_ids, dict):
            reference_image_ids = job.get("reference_image_ids") if isinstance(job.get("reference_image_ids"), dict) else {}
        manual_full_prompt = bool(job.get("manual_full_prompt"))
        saved_full_prompt = str(job.get("final_prompt") or "").strip()
        final_prompt = (
            saved_full_prompt
            if manual_full_prompt and saved_full_prompt
            else _final_prompt_from_director(director_prompt, reference_roles, reference_image_ids)
            if director_prompt
            else saved_full_prompt
        )
        if not final_prompt or (not director_prompt and not manual_full_prompt):
            raise ValueError(f"part_{idx:03d} 缺少已保存提示词，请先生成并保存分段提示词")
        prefix = str(job.get("output_prefix") or f"LTX-2/MSR_segments/{part_id}")
        job["prompt"] = director_prompt
        job["director_prompt"] = director_prompt
        job["final_prompt"] = final_prompt
        job["manual_full_prompt"] = manual_full_prompt
        job["reference_roles"] = reference_roles
        job["reference_image_ids"] = dict(reference_image_ids)
        job["video_path"] = ""
        segment["reference_image_ids"] = dict(reference_image_ids)
        # A re-submit supersedes every result from the prior task.  Until the
        # provider returns a new task_id, this part is intentionally task-less.
        segment["backend"] = ""
        segment["submitted"] = False
        segment["prompt_id"] = ""
        segment["task_id"] = ""
        segment["output_urls"] = []
        segment["video_path"] = ""
        segment["submit_result"] = {}
        segment["raw_submit"] = {}
        segment["error"] = ""
        segment["job"] = job
        submission_items.append(
            {
                "segment": segment,
                "job": job,
                "part_id": part_id,
                "prefix": prefix,
                "director_prompt": director_prompt,
                "final_prompt": final_prompt,
            }
        )

    # RunningHub returns a task id as soon as a request is accepted. Limiting
    # HTTP workers alone therefore does not limit its live GPU queue. Keep any
    # excess parts in the local job file until a remote execution slot opens.
    if backend == "runninghub":
        active_count = _runninghub_active_task_count(segments)
        available_slots = max(0, concurrency_limit - active_count)
        to_submit = submission_items[:available_slots]
        queued_items = submission_items[available_slots:]
        log(
            f"[LICON_MSR] RunningHub active={active_count} limit={concurrency_limit} "
            f"submit_now={len(to_submit)} queued={len(queued_items)}",
            "STEP",
        )
    else:
        to_submit = submission_items
        queued_items = []

    for item in queued_items:
        item["segment"]["status"] = "queued"
        item["segment"]["error"] = ""

    for item in to_submit:
        from ui.render_state import mark_part_running

        mark_part_running(item["part_id"])
        item["segment"]["status"] = "running"

    refresh_runninghub_usage_summaries(payload)
    save_video_jobs(payload)

    def _submit_one(item: dict[str, Any]) -> dict[str, Any]:
        job = item["job"]
        return _WORKFLOW_ADAPTER.build_and_optionally_submit(
            prompt=item["final_prompt"],
            duration_sec=float(job.get("duration_sec") or 15),
            reference_images=[str(x) for x in job.get("reference_images") or []],
            background_image=str(job.get("background_image") or ""),
            output_prefix=item["prefix"],
            submit=True,
            part_id=item["part_id"],
            wait=wait_for_result,
        )

    failures: list[str] = []
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(concurrency_limit, len(to_submit) or 1)),
        thread_name_prefix="licon-video-submit",
    )
    futures = {executor.submit(copy_context().run, _submit_one, item): item for item in to_submit}

    try:
        for future in as_completed(futures):
            item = futures[future]
            segment = item["segment"]
            job = item["job"]
            part_id = item["part_id"]
            try:
                result = future.result()
                if result.get("video_path"):
                    from ui.render_state import mark_part_success

                    mark_part_success(part_id, result.get("video_path"))
                segment.update({key: value for key, value in result.items() if key != "job"})
                segment["job"] = {
                    **job,
                    **(result.get("job") or {}),
                    "prompt": item["director_prompt"],
                    "director_prompt": item["director_prompt"],
                    "final_prompt": item["final_prompt"],
                    "output_prefix": item["prefix"],
                    "part_id": part_id,
                }
                segment["prompt_saved"] = True
                _record_runninghub_result(segment, result)
                segment["status"] = "success" if result.get("video_path") else ("running" if result.get("submitted") and result.get("task_id") else "workflow_ready")
                if segment["status"] == "success":
                    segment.pop("stale_video_path", None)
                    segment.pop("stale_reason", None)
                    if isinstance(segment.get("job"), dict):
                        segment["job"].pop("stale_video_path", None)
                log(f"[LICON_MSR] submitted saved workflow for {part_id}", "STEP")
            except Exception as exc:
                if backend == "runninghub" and _is_runninghub_queue_full_error(exc):
                    segment["status"] = "queued"
                    segment["error"] = ""
                    log(f"[LICON_MSR] RunningHub queue full; kept {part_id} in local queue", "WARN")
                    continue
                from ui.render_state import mark_part_failed

                mark_part_failed(part_id, str(exc))
                segment["status"] = "failed"
                segment["error"] = str(exc)
                failures.append(f"{part_id}: {exc}")
            finally:
                refresh_runninghub_usage_summaries(payload)
                save_video_jobs(payload)
    finally:
        executor.shutdown(wait=True)

    if failures:
        raise RuntimeError("video submission failed: " + "; ".join(failures))

    payload["segment_count"] = len(segments)
    payload["total_duration_sec"] = sum(int(float((item.get("job") or {}).get("duration_sec") or 0)) for item in segments)
    payload["fps"] = int(CONFIG.get("fps") or 50)
    payload["segments"] = segments
    if merge_after and wait_for_result:
        final_path = merge_segment_videos(payload)
        payload["final_video_path"] = str(final_path) if final_path else ""
    elif merge_after:
        log("[LICON_MSR] skip merge until remote video tasks complete", "STEP")
    refresh_runninghub_usage_summaries(payload)
    save_video_jobs(payload)
    return payload


@_serialize_video_jobs
def dispatch_queued_runninghub_segments() -> dict[str, Any] | None:
    """Submit queued parts through their owning model when a remote slot is free."""
    if str(CONFIG.get("video_submit_backend") or "comfyui").strip().lower() != "runninghub":
        return None

    payload = load_video_jobs()
    queued_indices = [
        int(segment.get("segment_index") or 0)
        for segment in payload.get("segments") or []
        if isinstance(segment, dict)
        and int(segment.get("segment_index") or 0) > 0
        and (
            str(segment.get("status") or "").strip().lower() == "queued"
            or (
                str(segment.get("status") or "").strip().lower() == "failed"
                and _is_runninghub_queue_full_error(segment.get("error") or "")
            )
        )
    ]
    if not queued_indices:
        return None
    active_count = _runninghub_active_task_count(
        [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    )
    if active_count >= _video_submit_concurrency_limit():
        return None
    available_slots = max(0, _video_submit_concurrency_limit() - active_count)
    selected = set(sorted(queued_indices)[:available_slots])
    if not selected:
        return None
    log(f"[generation] dispatch local RunningHub queue: {sorted(selected)}", "STEP")
    from generation.service import get_generation_service

    return get_generation_service().submit_saved_segments(selected, merge_after=False, wait=False)
