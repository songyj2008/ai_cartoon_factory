"""Model-aware, append-only render history for video-job segments.

``video_jobs.json`` predates the model factory.  Its result fields live on a
segment itself (``video_path``, ``task_id``, ``workflow_path`` and so on), so a
new submission overwrites the facts needed to identify an older video.  This
module provides a small, dependency-light boundary for moving that information
into per-render records while retaining the old top-level fields as a
compatibility projection.

The helpers intentionally know nothing about Licon node ids, H3 node ids, or a
submission provider.  A pipeline starts a render before it submits work, then
updates that *same* render as an async provider reports progress.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Any


RENDER_HISTORY_SCHEMA_VERSION = 1

# These are result/provenance fields from the pre-render-history segment shape.
# They are deliberately not model-selection fields: a segment may now be
# prepared for H3 while its currently playable video remains an LTX render.
_TOP_LEVEL_RESULT_KEYS = (
    "backend",
    "submitted",
    "prompt_id",
    "task_id",
    "video_path",
    "output_urls",
    "actual_media_spec",
    "workflow_path",
    "submit_result",
    "raw_submit",
    "error",
    "status",
)

_TERMINAL_RENDER_STATUSES = {
    "success",
    "succeeded",
    "finished",
    "completed",
    "complete",
    "done",
    "failed",
    "failure",
    "error",
    "cancelled",
    "canceled",
    "deleted",
}

__all__ = [
    "RENDER_HISTORY_SCHEMA_VERSION",
    "active_render",
    "active_render_video_path",
    "begin_render",
    "ensure_render_history",
    "find_render",
    "model_snapshot",
    "normalize_segment_render_history",
    "project_active_render",
    "render_model_id",
    "sync_legacy_top_level_to_renders",
    "update_render",
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _stable_digest(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _stable_segment_uid(segment: dict[str, Any], collection: str, ordinal: int | None) -> str:
    """Return a deterministic owner for old records with no segment UID.

    Current code normally assigns UUID segment UIDs before this helper runs.
    Determinism here matters for a legacy file that is loaded more than once
    before its first normalized save.
    """
    return "legacy_segment_" + _stable_digest(
        collection,
        ordinal or 0,
        segment.get("segment_id"),
        segment.get("part_id"),
        segment.get("segment_index"),
        segment.get("title"),
    )


def _segment_uid(segment: dict[str, Any], collection: str = "segments", ordinal: int | None = None) -> str:
    uid = _text(segment.get("segment_uid"))
    if uid:
        return uid
    uid = _stable_segment_uid(segment, collection, ordinal)
    segment["segment_uid"] = uid
    return uid


def _nested_model_id(value: Any) -> str:
    data = _as_dict(value)
    return _text(data.get("id") or data.get("model_id"))


def _looks_like_legacy_ltx(segment: dict[str, Any], payload: dict[str, Any] | None = None) -> bool:
    """Recognize old Licon records without trusting a later project default."""
    payload = _as_dict(payload)
    mode = _text(payload.get("mode") or segment.get("mode")).casefold()
    if "licon" in mode or "ltx" in mode:
        return True

    job = _as_dict(segment.get("job"))
    paths = " ".join(
        _text(value)
        for value in (
            segment.get("workflow_path"),
            job.get("workflow_path"),
            job.get("output_prefix"),
            segment.get("video_path"),
            segment.get("stale_video_path"),
        )
    ).casefold()
    if "licon" in paths or "ltx-2" in paths or "ltx_" in paths:
        return True

    # Licon's historical job contract has this exact pair.  H3 has a generic
    # asset manifest rather than a required single background input.
    return "background_image" in job and "reference_images" in job


def _segment_selected_model_id(segment: dict[str, Any], default_model_id: str = "") -> str:
    job = _as_dict(segment.get("job"))
    return _text(
        _nested_model_id(segment.get("resolved_model"))
        or segment.get("model_id")
        or job.get("model_id")
        or segment.get("model_override")
        or default_model_id
    )


def _legacy_artifact_model_id(
    segment: dict[str, Any],
    default_model_id: str,
    *,
    payload: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
) -> str:
    """Infer a model for an old *artifact*, not for the next prepared job."""
    record = _as_dict(record)
    explicit = _text(
        _nested_model_id(record.get("model"))
        or _nested_model_id(record.get("resolved_model"))
        or record.get("model_id")
    )
    if explicit:
        return explicit
    if _looks_like_legacy_ltx(segment, payload):
        return "ltx23_licon_msr_v2"
    return _segment_selected_model_id(segment, default_model_id)


def model_snapshot(
    model_id: str | None,
    *,
    revision: str | None = None,
    pipeline_key: str | None = None,
    existing: dict[str, Any] | None = None,
    execution_profile: Any = None,
    workflow_profile: Any = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Return a durable model snapshot without requiring the model to exist.

    An old render must remain identifiable even after its model is removed from
    the registry.  Registry data therefore enriches a caller-supplied snapshot
    but never replaces its explicit id/revision.
    """
    snapshot = _copy(_as_dict(existing))
    selected = _text(model_id or snapshot.get("id") or snapshot.get("model_id"))
    if not selected:
        selected = "unknown"
    snapshot["id"] = selected
    snapshot.pop("model_id", None)

    try:
        from generation.registry import get_model_registry

        registry = get_model_registry()
        if registry.has(selected):
            spec = registry.get(selected).spec
            snapshot.setdefault("revision", str(spec.revision))
            snapshot.setdefault("pipeline_key", str(spec.pipeline_key))
    except Exception:
        # Persistence must remain readable when optional model modules are not
        # installed (for example an archived H3 project opened before H3 is).
        pass

    if _text(revision):
        snapshot["revision"] = _text(revision)
    if _text(pipeline_key):
        snapshot["pipeline_key"] = _text(pipeline_key)
    if _has_value(execution_profile):
        snapshot["execution_profile"] = _copy(execution_profile)
    if _has_value(workflow_profile):
        snapshot["workflow_profile"] = _copy(workflow_profile)
    if _text(template_id):
        snapshot["template_id"] = _text(template_id)
    return snapshot


def _prompt_snapshot(data: dict[str, Any], segment: dict[str, Any]) -> str:
    job = _as_dict(data.get("job")) or _as_dict(segment.get("job"))
    return _text(
        data.get("prompt_snapshot")
        or data.get("final_prompt")
        or data.get("prompt")
        or job.get("final_prompt")
        or job.get("prompt")
        or job.get("director_prompt")
    )


def _asset_manifest(data: dict[str, Any], segment: dict[str, Any]) -> list[dict[str, Any]]:
    job = _as_dict(data.get("job")) or _as_dict(segment.get("job"))
    for source in (data.get("asset_manifest"), job.get("asset_manifest"), data.get("asset_slots"), job.get("asset_slots")):
        if isinstance(source, list):
            return [_copy(item) for item in source if isinstance(item, dict)]

    # Preserve enough semantics for an old Licon render to remain inspectable.
    manifest: list[dict[str, Any]] = []
    references = _as_list(job.get("reference_images"))
    for index, path in enumerate(references, start=1):
        if _text(path):
            manifest.append(
                {
                    "asset_id": f"legacy:reference:{index}",
                    "type": "image",
                    "semantic_role": "reference_image",
                    "order": index,
                    "path": _text(path),
                    "workflow_slot": f"reference_{index}",
                }
            )
    background = _text(job.get("background_image"))
    if background:
        manifest.append(
            {
                "asset_id": "legacy:background",
                "type": "image",
                "semantic_role": "background",
                "order": len(manifest) + 1,
                "path": background,
                "workflow_slot": "background",
            }
        )
    return manifest


def _requested_media_spec(data: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    for source in (data.get("requested_media_spec"), segment.get("requested_media_spec")):
        if isinstance(source, dict) and source:
            return _copy(source)
    job = _as_dict(data.get("job")) or _as_dict(segment.get("job"))
    result = {
        "duration_sec": job.get("duration_sec"),
        "fps": job.get("fps"),
        "total_frames": job.get("total_frames"),
    }
    return {key: _copy(value) for key, value in result.items() if value is not None}


def _actual_media_spec(data: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    value = data.get("actual_media_spec")
    if not isinstance(value, dict):
        value = segment.get("actual_media_spec")
    return _copy(value) if isinstance(value, dict) else {}


def _workflow_data(data: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    workflow = _copy(_as_dict(data.get("workflow")))
    # A poll for an older task must not inherit the *current* segment workflow
    # merely because that poll has no workflow_path of its own.
    fallback_path = segment.get("workflow_path") if "workflow_path" not in data else ""
    path = _text(data.get("workflow_path") or workflow.get("path") or fallback_path)
    if path:
        workflow["path"] = path
    for key in ("workflow_profile", "template_id", "template_fingerprint"):
        if _has_value(data.get(key)):
            workflow[key] = _copy(data[key])
    return workflow


def _submission_data(data: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    submission = _copy(_as_dict(data.get("submission")))
    job = _as_dict(data.get("job"))
    values = {
        "backend": data.get("backend") if "backend" in data else segment.get("backend"),
        "submitted": data.get("submitted") if "submitted" in data else segment.get("submitted"),
        "prompt_id": data.get("prompt_id") if "prompt_id" in data else segment.get("prompt_id"),
        "task_id": data.get("task_id") if "task_id" in data else segment.get("task_id"),
        "output_urls": data.get("output_urls") if "output_urls" in data else segment.get("output_urls"),
        "submit_result": data.get("submit_result") if "submit_result" in data else segment.get("submit_result"),
        "raw": data.get("raw_submit") if "raw_submit" in data else segment.get("raw_submit"),
    }
    # Some adapters place a task id in their normalized result object while
    # others put it in a vendor result.  Keep both forms readable.
    if not _text(values["task_id"]):
        nested = _as_dict(values.get("submit_result"))
        values["task_id"] = nested.get("task_id") or nested.get("taskId") or job.get("task_id")
    for key, value in values.items():
        if key == "submitted":
            if value is not None:
                submission[key] = bool(value)
        elif _has_value(value):
            submission[key] = _copy(value)
    return submission


def _output_data(data: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    output = _copy(_as_dict(data.get("output")))
    video_path = _text(data.get("video_path") or output.get("video_path"))
    # As above, only a genuinely absent field may inherit a legacy top-level
    # path.  Otherwise an old RunningHub attempt would accidentally acquire
    # the newest task's video.
    if not video_path and "video_path" not in data:
        video_path = _text(segment.get("video_path"))
    if video_path:
        output["video_path"] = video_path
    actual = _actual_media_spec(data, segment)
    if actual:
        output["actual_media_spec"] = actual
    if data.get("video_deleted") or output.get("video_deleted"):
        output["video_deleted"] = True
        if _text(data.get("video_deleted_at") or output.get("video_deleted_at")):
            output["video_deleted_at"] = _text(data.get("video_deleted_at") or output.get("video_deleted_at"))
    return output


def _normalise_status(value: Any, *, has_video: bool = False, submitted: bool = False, task_id: str = "", prompt_id: str = "", error: str = "") -> str:
    text = _text(value).casefold()
    if has_video:
        return "success"
    if text in {"succeeded", "finished", "completed", "complete", "done"}:
        return "success"
    if text in {"failure", "error", "canceled", "cancelled"} or "not_found" in text:
        return "failed"
    if text:
        return text
    if error:
        return "failed"
    if task_id or prompt_id or submitted:
        return "running"
    return "queued"


def _render_identity(render: dict[str, Any]) -> tuple[str, str, str]:
    submission = _as_dict(render.get("submission"))
    output = _as_dict(render.get("output"))
    return (
        _text(submission.get("task_id")),
        _text(submission.get("prompt_id")),
        _text(output.get("video_path")),
    )


def _stable_legacy_render_id(
    segment: dict[str, Any],
    *,
    collection: str,
    ordinal: int | None,
    task_id: str = "",
    prompt_id: str = "",
    video_path: str = "",
    marker: str = "top_level",
) -> str:
    owner = _segment_uid(segment, collection, ordinal)
    return "legacy_render_" + _stable_digest(owner, marker, task_id, prompt_id, video_path)


def _ensure_renders(segment: dict[str, Any]) -> list[dict[str, Any]]:
    current = segment.get("renders")
    if not isinstance(current, list):
        current = []
        segment["renders"] = current
    return [item for item in current if isinstance(item, dict)]


def find_render(
    segment: dict[str, Any],
    *,
    render_id: str | None = None,
    task_id: str | None = None,
    prompt_id: str | None = None,
    video_path: str | None = None,
) -> dict[str, Any] | None:
    """Find a render by a durable id or a provider/artifact identity."""
    wanted_render = _text(render_id)
    wanted_task = _text(task_id)
    wanted_prompt = _text(prompt_id)
    wanted_video = _text(video_path)
    for render in reversed(_ensure_renders(segment)):
        submission = _as_dict(render.get("submission"))
        output = _as_dict(render.get("output"))
        if wanted_render and _text(render.get("render_id")) == wanted_render:
            return render
        if wanted_task and _text(submission.get("task_id")) == wanted_task:
            return render
        if wanted_prompt and _text(submission.get("prompt_id")) == wanted_prompt:
            return render
        if wanted_video and _text(output.get("video_path")) == wanted_video:
            return render
    return None


def _normalise_existing_render(
    render: dict[str, Any],
    segment: dict[str, Any],
    default_model_id: str,
    *,
    collection: str,
    ordinal: int | None,
    render_ordinal: int,
    payload: dict[str, Any] | None,
) -> bool:
    """Bring a partially written render into the public shape in-place."""
    changed = False
    submission = _as_dict(render.get("submission"))
    output = _as_dict(render.get("output"))
    if not isinstance(render.get("submission"), dict):
        render["submission"] = submission
        changed = True
    if not isinstance(render.get("output"), dict):
        render["output"] = output
        changed = True

    # Accept early experimental flat render fields without losing them.
    for key in ("backend", "submitted", "prompt_id", "task_id", "output_urls", "submit_result"):
        if key in render and key not in submission and _has_value(render.get(key)):
            submission[key] = _copy(render[key])
            changed = True
    for key in ("video_path", "actual_media_spec", "video_deleted", "video_deleted_at"):
        if key in render and key not in output and _has_value(render.get(key)):
            output[key] = _copy(render[key])
            changed = True

    task_id, prompt_id, video_path = _render_identity(render)
    rid = _text(render.get("render_id"))
    if not rid:
        render["render_id"] = _stable_legacy_render_id(
            segment,
            collection=collection,
            ordinal=ordinal,
            task_id=task_id,
            prompt_id=prompt_id,
            video_path=video_path,
            marker=f"existing:{render_ordinal}",
        )
        changed = True

    raw_model = render.get("model")
    existing_model = raw_model if isinstance(raw_model, dict) else {"id": raw_model} if _text(raw_model) else {}
    inferred = _text(existing_model.get("id")) or _legacy_artifact_model_id(
        segment,
        default_model_id,
        payload=payload,
        record=render,
    )
    normalized_model = model_snapshot(inferred, existing=existing_model)
    if normalized_model != existing_model or not isinstance(raw_model, dict):
        render["model"] = normalized_model
        changed = True

    workflow = _as_dict(render.get("workflow"))
    if not isinstance(render.get("workflow"), dict):
        render["workflow"] = workflow
        changed = True
    if not _text(workflow.get("path")) and _text(render.get("workflow_path")):
        workflow["path"] = _text(render.get("workflow_path"))
        changed = True

    if "asset_manifest" not in render:
        render["asset_manifest"] = _asset_manifest(render, segment)
        changed = True
    if "requested_media_spec" not in render:
        render["requested_media_spec"] = _requested_media_spec(render, segment)
        changed = True
    if "prompt_snapshot" not in render:
        render["prompt_snapshot"] = _prompt_snapshot(render, segment)
        changed = True
    if not _text(render.get("generation_mode")):
        render["generation_mode"] = _text(segment.get("generation_mode")) or (
            "licon_msr" if _looks_like_legacy_ltx(segment, payload) else "legacy"
        )
        changed = True
    if not _text(render.get("created_at")):
        render["created_at"] = _now()
        changed = True

    error = _text(render.get("error"))
    status = _normalise_status(
        render.get("status"),
        has_video=bool(_text(output.get("video_path"))) and not bool(output.get("video_deleted")),
        submitted=bool(submission.get("submitted")),
        task_id=_text(submission.get("task_id")),
        prompt_id=_text(submission.get("prompt_id")),
        error=error,
    )
    if render.get("status") != status:
        render["status"] = status
        changed = True
    return changed


def begin_render(
    segment: dict[str, Any],
    *,
    model_id: str | None = None,
    model_revision: str | None = None,
    model_snapshot_data: dict[str, Any] | None = None,
    generation_mode: str = "",
    prompt_snapshot: str = "",
    asset_manifest: list[dict[str, Any]] | None = None,
    requested_media_spec: dict[str, Any] | None = None,
    workflow_path: str = "",
    workflow_profile: Any = None,
    template_id: str | None = None,
    backend: str = "",
    status: str = "queued",
    source: str = "generation",
    render_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one pending render and return it.

    Call this immediately before a provider submission.  It intentionally does
    not change ``active_render_id``: an old successful render remains playable
    until this new render produces a real artifact.
    """
    if not isinstance(segment, dict):
        raise TypeError("segment must be a dictionary")
    renders = _ensure_renders(segment)
    requested_id = _text(render_id)
    if requested_id:
        existing = find_render(segment, render_id=requested_id)
        if existing is not None:
            return existing
    requested_id = requested_id or f"render_{uuid.uuid4().hex}"

    selected_model = _text(model_id) or _segment_selected_model_id(segment)
    job = _as_dict(segment.get("job"))
    render = {
        "render_id": requested_id,
        "source": _text(source) or "generation",
        "status": _normalise_status(status) if _text(status) else "queued",
        "model": model_snapshot(
            selected_model,
            revision=model_revision,
            existing=model_snapshot_data,
            workflow_profile=workflow_profile,
            template_id=template_id,
        ),
        "generation_mode": _text(generation_mode or segment.get("generation_mode")) or "generation",
        "prompt_snapshot": _text(prompt_snapshot) or _prompt_snapshot({"job": job}, segment),
        "asset_manifest": _copy(asset_manifest) if isinstance(asset_manifest, list) else _asset_manifest({"job": job}, segment),
        "requested_media_spec": _copy(requested_media_spec)
        if isinstance(requested_media_spec, dict)
        else _requested_media_spec({"job": job}, segment),
        "workflow": {"path": _text(workflow_path)} if _text(workflow_path) else {},
        "submission": {"backend": _text(backend)} if _text(backend) else {},
        "output": {},
        "created_at": _now(),
        "source_segment_uid": _text(segment.get("segment_uid")),
    }
    if isinstance(metadata, dict) and metadata:
        render["metadata"] = _copy(metadata)
    renders.append(render)
    segment["renders"] = renders
    return render


def _merge_nonempty(target: dict[str, Any], source: dict[str, Any], *, overwrite: bool = False) -> bool:
    changed = False
    for key, value in source.items():
        if not _has_value(value):
            continue
        if overwrite or not _has_value(target.get(key)):
            if target.get(key) != value:
                target[key] = _copy(value)
                changed = True
    return changed


def update_render(
    segment: dict[str, Any],
    render_id: str,
    result: dict[str, Any] | None = None,
    *,
    status: str | None = None,
    error: str | None = None,
    model_id: str | None = None,
    actual_media_spec: dict[str, Any] | None = None,
    activate_on_success: bool = True,
    project: bool = False,
) -> dict[str, Any]:
    """Merge a normalized provider result into exactly one render.

    Non-empty provenance values are retained rather than cleared by later poll
    responses.  A model conflict is recorded as metadata instead of mutating a
    render's historical model snapshot.
    """
    if not isinstance(segment, dict):
        raise TypeError("segment must be a dictionary")
    render = find_render(segment, render_id=render_id)
    if render is None:
        render = begin_render(segment, model_id=model_id, render_id=render_id, source="recovered_update")
    data = _as_dict(result)
    job = _as_dict(data.get("job"))

    existing_model = _as_dict(render.get("model"))
    # A segment's current job can already target a different model after the
    # user switched models.  Treat only an explicit update argument or a
    # top-level provider result as authority; the job fallback is safe solely
    # when the render has not received a model snapshot yet.
    requested_model = _text(model_id or data.get("model_id"))
    if not requested_model and not _text(existing_model.get("id")):
        requested_model = _text(job.get("model_id"))
    if requested_model and requested_model != _text(existing_model.get("id")):
        metadata = _as_dict(render.get("metadata"))
        conflicts = _as_list(metadata.get("model_conflicts"))
        conflict = {"reported_model_id": requested_model, "observed_at": _now()}
        if conflict not in conflicts:
            conflicts.append(conflict)
        metadata["model_conflicts"] = conflicts
        render["metadata"] = metadata

    prompt = _prompt_snapshot(data, segment)
    if prompt and not _text(render.get("prompt_snapshot")):
        render["prompt_snapshot"] = prompt
    assets = _asset_manifest(data, segment)
    if assets and not _as_list(render.get("asset_manifest")):
        render["asset_manifest"] = assets
    requested = _requested_media_spec(data, segment)
    if requested:
        existing_requested = _as_dict(render.get("requested_media_spec"))
        _merge_nonempty(existing_requested, requested)
        render["requested_media_spec"] = existing_requested

    workflow = _as_dict(render.get("workflow"))
    incoming_workflow = _workflow_data(data, segment)
    _merge_nonempty(workflow, incoming_workflow)
    render["workflow"] = workflow

    submission = _as_dict(render.get("submission"))
    incoming_submission = _submission_data(data, segment)
    # ``submitted=False`` in a later status probe must not erase a successful
    # prior submission, so only update it when the source explicitly says True.
    incoming_submitted = incoming_submission.pop("submitted", None)
    _merge_nonempty(submission, incoming_submission)
    if incoming_submitted is True:
        submission["submitted"] = True
    elif incoming_submitted is False and "submitted" not in submission:
        submission["submitted"] = False
    render["submission"] = submission

    output = _as_dict(render.get("output"))
    incoming_output = _output_data(data, segment)
    _merge_nonempty(output, incoming_output)
    if isinstance(actual_media_spec, dict) and actual_media_spec:
        actual = _as_dict(output.get("actual_media_spec"))
        _merge_nonempty(actual, actual_media_spec, overwrite=True)
        output["actual_media_spec"] = actual
    render["output"] = output

    reported_error = _text(error if error is not None else data.get("error"))
    if reported_error:
        render["error"] = reported_error
    has_video = bool(_text(output.get("video_path"))) and not bool(output.get("video_deleted"))
    desired_status = _normalise_status(
        status if status is not None else data.get("status"),
        has_video=has_video,
        submitted=bool(submission.get("submitted")),
        task_id=_text(submission.get("task_id")),
        prompt_id=_text(submission.get("prompt_id")),
        error=reported_error or _text(render.get("error")),
    )
    render["status"] = desired_status
    if (submission.get("submitted") or _text(submission.get("task_id")) or _text(submission.get("prompt_id"))) and not _text(render.get("submitted_at")):
        render["submitted_at"] = _now()
    if desired_status in _TERMINAL_RENDER_STATUSES:
        render.setdefault("completed_at", _now())
    if desired_status == "success" and has_video and activate_on_success:
        segment["active_render_id"] = _text(render.get("render_id"))
        # A newly completed artifact always wins over a video that the user
        # selected from history while waiting for regeneration.
        segment.pop("manual_active_render_id", None)
    if project:
        project_active_render(segment, include_status=desired_status == "success")
    return render


def active_render(segment: dict[str, Any]) -> dict[str, Any] | None:
    """Return the selected render, falling back only for unmigrated segments."""
    if not isinstance(segment, dict):
        return None
    renders = _ensure_renders(segment)
    active_id = _text(segment.get("active_render_id"))
    if active_id:
        selected = find_render(segment, render_id=active_id)
        if selected is None:
            return None
        if _as_dict(selected.get("output")).get("video_deleted"):
            return None
        return selected

    # Migration fallback: first match the legacy top-level artifact, then use
    # the newest playable historic render.  Once selected, persist the id so
    # the answer cannot change merely because filesystem mtimes change.
    top_video = _text(segment.get("video_path"))
    if top_video:
        candidate = find_render(segment, video_path=top_video)
        if candidate is not None and not _as_dict(candidate.get("output")).get("video_deleted"):
            segment["active_render_id"] = _text(candidate.get("render_id"))
            return candidate
    for candidate in reversed(renders):
        output = _as_dict(candidate.get("output"))
        metadata = _as_dict(candidate.get("metadata"))
        if (
            _text(output.get("video_path"))
            and not output.get("video_deleted")
            and not metadata.get("legacy_stale_video")
        ):
            segment["active_render_id"] = _text(candidate.get("render_id"))
            return candidate
    return None


def active_render_video_path(segment: dict[str, Any]) -> str:
    """Return the active artifact path without probing or guessing filenames."""
    render = active_render(segment)
    if render is None:
        return ""
    output = _as_dict(render.get("output"))
    if output.get("video_deleted"):
        return ""
    return _text(output.get("video_path"))


def render_model_id(segment: dict[str, Any], default_model_id: str = "") -> str:
    """Return the model that produced the active render, if one exists.

    If a segment has no output yet, returning its selected/prepared model is a
    useful UI fallback, but callers must not describe that value as the model
    that produced a video.
    """
    render = active_render(segment)
    if render is not None:
        value = _text(_as_dict(render.get("model")).get("id"))
        if value:
            return value
    return _segment_selected_model_id(segment, default_model_id)


def project_active_render(
    segment: dict[str, Any],
    *,
    include_status: bool = False,
    clear_when_missing: bool = False,
) -> dict[str, Any] | None:
    """Mirror an active render to legacy top-level result fields.

    The projection intentionally never writes ``model_id`` or
    ``resolved_model``; those fields describe the next compiled job, whereas
    the active render carries the actual video provenance.
    """
    render = active_render(segment)
    if render is None:
        if clear_when_missing:
            for key in ("video_path", "actual_media_spec", "task_id", "prompt_id", "output_urls"):
                segment.pop(key, None)
        return None

    submission = _as_dict(render.get("submission"))
    output = _as_dict(render.get("output"))
    workflow = _as_dict(render.get("workflow"))
    projection = {
        "backend": submission.get("backend"),
        "submitted": submission.get("submitted"),
        "prompt_id": submission.get("prompt_id"),
        "task_id": submission.get("task_id"),
        "output_urls": submission.get("output_urls"),
        "submit_result": submission.get("submit_result"),
        "raw_submit": submission.get("raw"),
        "workflow_path": workflow.get("path"),
        "video_path": output.get("video_path"),
        "actual_media_spec": output.get("actual_media_spec"),
    }
    for key, value in projection.items():
        if _has_value(value) or key == "submitted":
            segment[key] = _copy(value)
    if include_status:
        segment["status"] = _text(render.get("status")) or segment.get("status")
    return render


def _legacy_attempt_result(segment: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
    task_id = _text(attempt.get("task_id"))
    top_task = _text(segment.get("task_id"))
    same_as_top = bool(task_id and task_id == top_task)
    billing_outputs = _as_list(attempt.get("billing_outputs"))
    urls = [
        _text(item.get("file_url") or item.get("url"))
        for item in billing_outputs
        if isinstance(item, dict) and _text(item.get("file_url") or item.get("url"))
    ]
    return {
        "backend": "runninghub",
        "submitted": True,
        "task_id": task_id,
        "video_path": _text(attempt.get("video_path")) or (segment.get("video_path") if same_as_top else ""),
        "output_urls": urls or (segment.get("output_urls") if same_as_top else []),
        "submit_result": segment.get("submit_result") if same_as_top else {},
        "raw_submit": segment.get("raw_submit") if same_as_top else {},
        "workflow_path": segment.get("workflow_path") if same_as_top else "",
        "actual_media_spec": segment.get("actual_media_spec") if same_as_top else {},
        "status": attempt.get("status") or (segment.get("status") if same_as_top else "running"),
        "prompt_snapshot": attempt.get("submitted_prompt") or _prompt_snapshot({}, segment),
        "metadata": {"legacy_runninghub_attempt": _copy(attempt)},
    }


def _legacy_top_level_result(segment: dict[str, Any], *, use_stale_video: bool = False) -> dict[str, Any]:
    result = {key: _copy(segment.get(key)) for key in _TOP_LEVEL_RESULT_KEYS if key in segment}
    result["job"] = _copy(_as_dict(segment.get("job")))
    if use_stale_video:
        result["video_path"] = _text(segment.get("stale_video_path") or _as_dict(segment.get("job")).get("stale_video_path"))
        result["status"] = "success" if result["video_path"] else result.get("status")
        result["metadata"] = {"legacy_stale_video": True, "stale_reason": _text(segment.get("stale_reason"))}
    return result


def _top_level_has_render_evidence(segment: dict[str, Any]) -> bool:
    if any(_text(segment.get(key)) for key in ("video_path", "task_id", "prompt_id")):
        return True
    if _text(segment.get("backend")) and bool(segment.get("submitted")):
        return True
    status = _text(segment.get("status")).casefold()
    return status in {"running", "pending", "queued", "failed", "success"} and bool(
        _text(segment.get("backend")) or _text(segment.get("error"))
    )


def sync_legacy_top_level_to_renders(
    segment: dict[str, Any],
    payload_default_model_id: str = "",
    *,
    payload: dict[str, Any] | None = None,
    collection: str = "segments",
    ordinal: int | None = None,
) -> dict[str, int]:
    """Migrate old segment-level results into immutable render entries.

    It only fills absent provenance; it does not replace a modern render or
    erase old `runninghub_attempts`.  The latter receive a `render_id` link so
    billing history can continue to use the existing UI and summary code.
    """
    report = {"created": 0, "updated": 0, "linked_attempts": 0, "active_selected": 0}
    if not isinstance(segment, dict):
        return report
    _segment_uid(segment, collection, ordinal)

    attempts = _as_list(segment.get("runninghub_attempts"))
    for attempt_index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            continue
        task_id = _text(attempt.get("task_id"))
        if not task_id:
            continue
        render = find_render(segment, task_id=task_id)
        if render is None:
            legacy = _legacy_attempt_result(segment, attempt)
            render = begin_render(
                segment,
                model_id=_legacy_artifact_model_id(segment, payload_default_model_id, payload=payload, record=attempt),
                generation_mode="licon_msr" if _looks_like_legacy_ltx(segment, payload) else "legacy",
                prompt_snapshot=_text(legacy.get("prompt_snapshot")),
                requested_media_spec=_requested_media_spec(legacy, segment),
                workflow_path=_text(legacy.get("workflow_path")),
                backend="runninghub",
                status=_text(legacy.get("status")) or "running",
                source="legacy_runninghub",
                render_id=_stable_legacy_render_id(
                    segment,
                    collection=collection,
                    ordinal=ordinal,
                    task_id=task_id,
                    marker=f"runninghub:{attempt_index}",
                ),
                metadata=legacy.get("metadata"),
            )
            update_render(segment, _text(render.get("render_id")), legacy, activate_on_success=False)
            report["created"] += 1
        else:
            legacy = _legacy_attempt_result(segment, attempt)
            update_render(segment, _text(render.get("render_id")), legacy, activate_on_success=False)
            report["updated"] += 1
        attempt_linked = False
        if _text(attempt.get("render_id")) != _text(render.get("render_id")):
            attempt["render_id"] = _text(render.get("render_id"))
            attempt_linked = True
        render_model_id = _text(_as_dict(render.get("model")).get("id"))
        if render_model_id and not _text(attempt.get("model_id")):
            attempt["model_id"] = render_model_id
            attempt_linked = True
        if attempt_linked:
            report["linked_attempts"] += 1

    if _top_level_has_render_evidence(segment):
        legacy = _legacy_top_level_result(segment)
        task_id = _text(legacy.get("task_id"))
        prompt_id = _text(legacy.get("prompt_id"))
        video_path = _text(legacy.get("video_path"))
        render = find_render(segment, task_id=task_id) if task_id else None
        render = render or (find_render(segment, prompt_id=prompt_id) if prompt_id else None)
        render = render or (find_render(segment, video_path=video_path) if video_path else None)
        if render is None:
            render = begin_render(
                segment,
                model_id=_legacy_artifact_model_id(segment, payload_default_model_id, payload=payload, record=legacy),
                generation_mode="licon_msr" if _looks_like_legacy_ltx(segment, payload) else "legacy",
                prompt_snapshot=_prompt_snapshot(legacy, segment),
                asset_manifest=_asset_manifest(legacy, segment),
                requested_media_spec=_requested_media_spec(legacy, segment),
                workflow_path=_text(legacy.get("workflow_path")),
                backend=_text(legacy.get("backend")),
                status=_text(legacy.get("status")) or "running",
                source="legacy_top_level",
                render_id=_stable_legacy_render_id(
                    segment,
                    collection=collection,
                    ordinal=ordinal,
                    task_id=task_id,
                    prompt_id=prompt_id,
                    video_path=video_path,
                ),
            )
            report["created"] += 1
        else:
            report["updated"] += 1
        update_render(segment, _text(render.get("render_id")), legacy, activate_on_success=False)

    # A model switch in the old schema could retain only stale_video_path.
    # Keep that artifact auditable but do not auto-select it for final merge.
    stale_video = _text(segment.get("stale_video_path") or _as_dict(segment.get("job")).get("stale_video_path"))
    if stale_video and find_render(segment, video_path=stale_video) is None:
        legacy = _legacy_top_level_result(segment, use_stale_video=True)
        render = begin_render(
            segment,
            model_id=_legacy_artifact_model_id(segment, payload_default_model_id, payload=payload, record=legacy),
            generation_mode="licon_msr" if _looks_like_legacy_ltx(segment, payload) else "legacy",
            prompt_snapshot=_prompt_snapshot(legacy, segment),
            asset_manifest=_asset_manifest(legacy, segment),
            requested_media_spec=_requested_media_spec(legacy, segment),
            workflow_path=_text(legacy.get("workflow_path")),
            backend=_text(legacy.get("backend")),
            status="success",
            source="legacy_stale_artifact",
            render_id=_stable_legacy_render_id(
                segment,
                collection=collection,
                ordinal=ordinal,
                video_path=stale_video,
                marker="stale_video",
            ),
            metadata=_as_dict(legacy.get("metadata")),
        )
        update_render(segment, _text(render.get("render_id")), legacy, activate_on_success=False)
        report["created"] += 1

    before_active = _text(segment.get("active_render_id"))
    # ``active_render`` performs a deterministic migration fallback and writes
    # the id only when there is a normal top-level video or a playable history.
    active_render(segment)
    if not before_active and _text(segment.get("active_render_id")):
        report["active_selected"] += 1
    return report


def normalize_segment_render_history(
    segment: dict[str, Any],
    payload_default_model_id: str = "",
    *,
    payload: dict[str, Any] | None = None,
    collection: str = "segments",
    ordinal: int | None = None,
) -> dict[str, int]:
    """Idempotently normalize one segment's render history.

    This is the primary job-schema entry point.  `payload` is optional for
    backwards compatibility, but callers should pass it so legacy Licon
    detection can use its historical `mode` rather than a future default model.
    """
    report = {
        "created": 0,
        "updated": 0,
        "linked_attempts": 0,
        "active_selected": 0,
        "segment_uid_created": 0,
    }
    if not isinstance(segment, dict):
        return report
    had_uid = bool(_text(segment.get("segment_uid")))
    _segment_uid(segment, collection, ordinal)
    if not had_uid:
        report["segment_uid_created"] = 1

    original = segment.get("renders")
    if not isinstance(original, list):
        segment["renders"] = []
        if original is not None:
            report["updated"] += 1
    renders = _ensure_renders(segment)
    for index, render in enumerate(renders, start=1):
        if _normalise_existing_render(
            render,
            segment,
            payload_default_model_id,
            collection=collection,
            ordinal=ordinal,
            render_ordinal=index,
            payload=payload,
        ):
            report["updated"] += 1

    migration = sync_legacy_top_level_to_renders(
        segment,
        payload_default_model_id,
        payload=payload,
        collection=collection,
        ordinal=ordinal,
    )
    for key in ("created", "updated", "linked_attempts", "active_selected"):
        report[key] += migration[key]
    segment["render_history_schema_version"] = RENDER_HISTORY_SCHEMA_VERSION
    return report


def ensure_render_history(payload: dict[str, Any], payload_default_model_id: str = "") -> dict[str, int]:
    """Normalize active and retired segment records in a job payload."""
    if not isinstance(payload, dict):
        raise TypeError("video jobs payload must be a dictionary")
    default_id = _text(payload_default_model_id or payload.get("default_model_id"))
    report = {
        "segments": 0,
        "retired_segments": 0,
        "created": 0,
        "updated": 0,
        "linked_attempts": 0,
        "active_selected": 0,
        "segment_uid_created": 0,
    }
    for collection in ("segments", "retired_segments"):
        items = payload.get(collection)
        if not isinstance(items, list):
            continue
        for ordinal, segment in enumerate(items, start=1):
            if not isinstance(segment, dict):
                continue
            item_report = normalize_segment_render_history(
                segment,
                default_id,
                payload=payload,
                collection=collection,
                ordinal=ordinal,
            )
            report[collection] += 1
            for key in ("created", "updated", "linked_attempts", "active_selected", "segment_uid_created"):
                report[key] += item_report[key]
    payload["render_history_schema_version"] = RENDER_HISTORY_SCHEMA_VERSION
    return report
