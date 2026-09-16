"""Synchronize completed RunningHub tasks back into local video_jobs.json."""
from __future__ import annotations

from typing import Any

from services.logger import log
from services.context import pin_runtime
from generation.job_schema import resolved_model_id
from generation.media import inspect_media
from generation.deferred_regeneration import (
    finalize_deferred_regeneration,
    recover_interrupted_deferred_regeneration,
)
from workflow.runninghub_usage import (
    attempt_needs_usage_sync,
    record_runninghub_attempt,
    refresh_runninghub_usage_summaries,
)
from ui.render_state import mark_part_failed, mark_part_success
from workflow.submitters.runninghub_provider import RunningHubSubmitProvider
from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock


DONE_STATUSES = {"success", "succeeded", "finished", "completed", "complete", "done"}
FAILED_STATUSES = {"failed", "failure", "error", "canceled", "cancelled"}
_CANCELLED_MARKERS = ("cancel", "cancell", "aborted", "terminated", "not_found", "\u53d6\u6d88", "\u5df2\u53d6\u6d88")


def _failure_message(status: dict[str, Any]) -> str:
    failed_reason = status.get("failedReason") if isinstance(status.get("failedReason"), dict) else {}
    for key in ("exception_message", "errorMessage", "message", "error"):
        message = str(failed_reason.get(key) or status.get(key) or "").strip()
        if message:
            return f"RunningHub task failed: {message}"
    return f"RunningHub task failed: {status}"


def _sync_render_status_on_failure(segment: dict[str, Any], status_text: str) -> None:
    """Update the active render's status so the UI reflects a terminal failure."""
    try:
        from generation.render_history import active_render
        render = active_render(segment)
        if render and str(render.get("status") or "").lower() in ("running", "pending", "queued"):
            render["status"] = "failed"
            if not render.get("error"):
                render["error"] = f"RunningHub task {status_text}"
    except Exception:
        pass


def _is_remote_missing_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in ("404", "not found", "notfound", "does not exist", "不存在"))


def _status_text(data: Any) -> str:
    if isinstance(data, str):
        return data.strip().lower()
    if isinstance(data, dict):
        for key in ("status", "taskStatus", "state", "data"):
            if data.get(key):
                found = _status_text(data[key])
                if found:
                    return found
        for value in data.values():
            found = _status_text(value)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _status_text(item)
            if found:
                return found
    return ""


def _has_terminal_failure_status(data: Any) -> bool:
    """Recognize provider cancellation variants without treating a live task as failed."""
    if isinstance(data, str):
        value = data.strip().lower()
        return value in FAILED_STATUSES or any(marker in value for marker in _CANCELLED_MARKERS)
    if isinstance(data, dict):
        # Status fields are authoritative.  Some RunningHub responses put the
        # human-readable cancellation message in a nested ``data`` object.
        for key in ("status", "taskStatus", "state", "message", "error", "failedReason", "data"):
            if key in data and _has_terminal_failure_status(data[key]):
                return True
        return False
    if isinstance(data, list):
        return any(_has_terminal_failure_status(item) for item in data)
    return False


def _saved_task_id(segment: dict[str, Any]) -> str:
    """Read task identity from current fields or legacy nested submit results."""
    direct = str(segment.get("task_id") or "").strip()
    if direct:
        return direct
    for key in ("submit_result", "raw_submit"):
        nested = segment.get(key)
        if not isinstance(nested, dict):
            continue
        for task_key in ("task_id", "taskId", "id"):
            task_id = str(nested.get(task_key) or "").strip()
            if task_id:
                return task_id
    return ""


def _is_runninghub_pending(segment: dict[str, Any]) -> bool:
    task_id = _saved_task_id(segment)
    backend = str(segment.get("backend") or "").strip().lower()
    # Older parallel regenerations could retain only submit_result.task_id.
    return bool(task_id) and backend in {"", "runninghub"}


def _promote_completed_task_render(segment: dict[str, Any]) -> bool:
    """Repair a completed task whose UI still points at an older render."""
    if str(segment.get("status") or "").strip().lower() != "success":
        return False
    task_id = _saved_task_id(segment)
    video_path = str(segment.get("video_path") or "").strip()
    if not task_id or not video_path:
        return False
    try:
        from generation.render_history import find_render, project_active_render

        render = find_render(segment, task_id=task_id)
        output = render.get("output") if isinstance(render, dict) and isinstance(render.get("output"), dict) else {}
        render_video = str(output.get("video_path") or "").strip()
        render_id = str((render or {}).get("render_id") or "").strip()
        if not render_id or not render_video:
            return False
        if render_video.replace("\\", "/").casefold() != video_path.replace("\\", "/").casefold():
            return False
        if str(segment.get("active_render_id") or "").strip() == render_id:
            return False
        segment["active_render_id"] = render_id
        segment.pop("manual_active_render_id", None)
        project_active_render(segment, include_status=True)
        return True
    except Exception:
        return False


def sync_runninghub_video_jobs() -> tuple[dict[str, Any], bool]:
    with pin_runtime(), video_jobs_lock():
        payload = load_video_jobs()
        segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
        retired_segments = [item for item in payload.get("retired_segments") or [] if isinstance(item, dict)]
        recovered_deferred = False
        for item in [*segments, *retired_segments]:
            if recover_interrupted_deferred_regeneration(item):
                recovered_deferred = True
            if _promote_completed_task_render(item):
                recovered_deferred = True
        pending = [
            (item, False)
            for item in segments
            if _is_runninghub_pending(item)
            and not str(item.get("video_path") or "").strip()
            and str(item.get("status") or "").strip().lower() in {"running", "pending", "workflow_ready", ""}
        ]
        # A task can complete after its source beat is deleted or split.  It
        # still belongs to the archived source segment and must never be
        # attached to whichever active part later inherited its number.
        pending.extend(
            (item, True)
            for item in retired_segments
            if _is_runninghub_pending(item)
            and not str(item.get("video_path") or "").strip()
            and str(item.get("status") or "").strip().lower() in {"running", "pending", "workflow_ready", ""}
        )
        pending_task_ids = {_saved_task_id(item) for item, _ in pending}
        usage_backfill = [
            item
            for item in segments
            if str(item.get("backend") or "").strip().lower() == "runninghub"
            and _saved_task_id(item)
            and _saved_task_id(item) not in pending_task_ids
            and attempt_needs_usage_sync(item, _saved_task_id(item))
        ]
        if not pending and not usage_backfill:
            if recovered_deferred:
                save_video_jobs(payload)
            from workflow.segment_runner import dispatch_queued_runninghub_segments

            dispatched = dispatch_queued_runninghub_segments()
            return (dispatched or payload), recovered_deferred or bool(dispatched)

        # Remote task ids belong to a particular model deployment.  Do not
        # assume that every persisted segment uses the project's *current*
        # default workflow: a project can change its default after jobs have
        # already been submitted.  Cache clients by model so one sync pass
        # still creates at most one client per deployment.
        providers: dict[str, RunningHubSubmitProvider] = {}

        def provider_for(segment: dict[str, Any]) -> RunningHubSubmitProvider:
            # A segment can later be prepared for another model.  Poll the
            # immutable render that owns this remote task rather than using the
            # segment's next selected model/workflow.
            model_id = ""
            try:
                from generation.render_history import find_render

                render = find_render(segment, task_id=_saved_task_id(segment))
                model = render.get("model") if isinstance(render, dict) else {}
                model_id = str((model or {}).get("id") or "").strip()
            except Exception:
                model_id = ""
            model_id = model_id or resolved_model_id(segment, payload)
            if model_id not in providers:
                providers[model_id] = RunningHubSubmitProvider(model_id=model_id)
            return providers[model_id]

        changed = recovered_deferred
        for segment, is_retired in pending:
            provider = provider_for(segment)
            task_id = _saved_task_id(segment)
            part_id = str(segment.get("part_id") or (segment.get("job") or {}).get("part_id") or "part").strip()
            # Repair the top-level fields before polling, so recovered jobs stay recoverable.
            if segment.get("backend") != "runninghub" or segment.get("task_id") != task_id or not segment.get("submitted"):
                segment["backend"] = "runninghub"
                segment["submitted"] = True
                segment["task_id"] = task_id
                changed = True
                log(f"[LICON_MSR][runninghub][sync] restored task metadata for {part_id}: {task_id}", "STEP")
            try:
                try:
                    status = provider.client.get_task_status(task_id)
                except Exception as status_exc:
                    outputs = provider.client.get_task_outputs(task_id)
                    if not outputs:
                        raise status_exc
                    status = {}
                else:
                    outputs = []
                status_text = _status_text(status)
                record_runninghub_attempt(segment, task_id, status=status_text, outputs=outputs if outputs else None)
                if status_text in FAILED_STATUSES or _has_terminal_failure_status(status):
                    try:
                        failure_outputs = provider.client.get_task_outputs(task_id)
                    except Exception:
                        failure_outputs = []
                    record_runninghub_attempt(segment, task_id, status=status_text, outputs=failure_outputs)
                    segment["status"] = "failed"
                    segment["error"] = _failure_message(status)
                    # This remote task has reached a terminal state.  Keep its
                    # id in the result for diagnosis, but do not advertise it
                    # as an active submission or block a local retry.
                    segment["submitted"] = False
                    segment["submit_result"] = {
                        **(segment.get("submit_result") or {}),
                        "task_id": task_id,
                        "status": status,
                    }
                    segment["raw_submit"] = {
                        **(segment.get("raw_submit") or {}),
                        "status": status,
                    }
                    if not is_retired:
                        mark_part_failed(part_id, segment["error"])
                    # Also update the active render so the UI shows this
                    # terminal failure instead of "running".
                    _sync_render_status_on_failure(segment, status_text)
                    finalize_deferred_regeneration(segment, task_id=task_id)
                    changed = True
                    continue
                if not outputs:
                    outputs = provider.client.get_task_outputs(task_id)
                record_runninghub_attempt(segment, task_id, status=status_text, outputs=outputs)
                if not outputs and status_text not in DONE_STATUSES:
                    continue
                video_path, output_urls = provider._download_first_video(outputs, part_id, task_id)
                segment["submit_result"] = {
                    **(segment.get("submit_result") or {}),
                    "task_id": task_id,
                    "status": status or (segment.get("submit_result") or {}).get("status") or {},
                    "outputs": outputs,
                }
                segment["raw_submit"] = {
                    **(segment.get("raw_submit") or {}),
                    "status": status or (segment.get("raw_submit") or {}).get("status") or {},
                    "outputs": outputs,
                }
                if output_urls:
                    segment["output_urls"] = output_urls
                if video_path:
                    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
                    actual_media_spec = inspect_media(video_path).to_dict()
                    record_runninghub_attempt(
                        segment,
                        task_id,
                        status="success",
                        outputs=outputs,
                        submitted_prompt=str(job.get("final_prompt") or job.get("prompt") or ""),
                        video_path=video_path,
                    )
                    segment["video_path"] = video_path
                    segment["status"] = "success"
                    segment["actual_media_spec"] = actual_media_spec
                    # H3 keeps immutable render history.  The async submit
                    # creates a pending render but deliberately leaves the
                    # previous successful render active until a new artifact
                    # exists.  Promote the render owned by this task now;
                    # otherwise the card combines the new task id with the
                    # previous render's video and Preview plays the old file.
                    try:
                        from generation.render_history import find_render, update_render

                        completed_render = find_render(segment, task_id=task_id)
                        if completed_render is not None:
                            update_render(
                                segment,
                                str(completed_render.get("render_id") or ""),
                                {
                                    "backend": "runninghub",
                                    "submitted": True,
                                    "task_id": task_id,
                                    "video_path": video_path,
                                    "output_urls": output_urls,
                                    "submit_result": segment["submit_result"],
                                    "raw_submit": segment["raw_submit"],
                                    "status": "success",
                                },
                                status="success",
                                actual_media_spec=actual_media_spec,
                                activate_on_success=True,
                                project=True,
                            )
                    except Exception as render_exc:
                        log(
                            f"[LICON_MSR][runninghub][sync][render_history_warn] "
                            f"{part_id or task_id}: {render_exc}",
                            "WARN",
                        )
                    segment.pop("stale_video_path", None)
                    segment.pop("stale_reason", None)
                    if isinstance(segment.get("job"), dict):
                        segment["job"].pop("stale_video_path", None)
                    if not is_retired:
                        mark_part_success(part_id, video_path)
                    if finalize_deferred_regeneration(segment, task_id=task_id):
                        log(
                            f"[LICON_MSR][runninghub][sync] {part_id} completed; saved edits now require regeneration",
                            "STEP",
                        )
                    log(f"[LICON_MSR][runninghub][sync] downloaded {part_id}: {video_path}", "STEP")
                changed = True
            except Exception as exc:
                if _is_remote_missing_error(exc):
                    segment["status"] = "failed"
                    segment["error"] = f"RunningHub task not found: {task_id}"
                    segment["submitted"] = False
                    segment["submit_result"] = {
                        **(segment.get("submit_result") or {}),
                        "task_id": task_id,
                        "status": {"status": "not_found", "error": str(exc)},
                    }
                    segment["raw_submit"] = {
                        **(segment.get("raw_submit") or {}),
                        "status": {"status": "not_found", "error": str(exc)},
                    }
                    if not is_retired:
                        mark_part_failed(part_id, segment["error"])
                    finalize_deferred_regeneration(segment, task_id=task_id)
                    changed = True
                    log(f"[LICON_MSR][runninghub][sync] marked missing task failed for {part_id}: {task_id}", "WARN")
                    continue
                log(f"[LICON_MSR][runninghub][sync][warn] {part_id or task_id}: {exc}", "WARN")

        # Completed jobs created before usage tracking was introduced have a
        # task id but no history record.  Fetch their output metadata once so
        # old episode data can be included in the same totals.
        for segment in usage_backfill:
            provider = provider_for(segment)
            task_id = _saved_task_id(segment)
            if not attempt_needs_usage_sync(segment, task_id):
                continue
            try:
                outputs = provider.client.get_task_outputs(task_id)
                record_runninghub_attempt(segment, task_id, outputs=outputs)
                changed = True
            except Exception as exc:
                log(f"[LICON_MSR][runninghub][usage][warn] {task_id}: {exc}", "WARN")

        refresh_runninghub_usage_summaries(payload)
        if changed:
            save_video_jobs(payload)
        from workflow.segment_runner import dispatch_queued_runninghub_segments

        dispatched = dispatch_queued_runninghub_segments()
        return (dispatched or payload), changed or bool(dispatched)
