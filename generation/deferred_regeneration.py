"""Preserve active provider jobs while newer segment inputs are saved."""
from __future__ import annotations

from typing import Any


DEFERRED_REGENERATION_KEY = "deferred_regeneration"
_ACTIVE_STATUSES = {"running", "pending"}
_ACTIVE_ATTEMPT_STATUSES = {"submitted", "running", "pending", "queued", "executing", "processing"}


def active_submission_identity(segment: dict[str, Any]) -> tuple[str, str]:
    """Return the provider identity only while the segment is actively polling."""

    status = str(segment.get("status") or "").strip().lower()
    if status not in _ACTIVE_STATUSES:
        return "", ""
    task_id = str(segment.get("task_id") or "").strip()
    prompt_id = str(segment.get("prompt_id") or "").strip()
    if not (task_id or prompt_id):
        return "", ""
    return task_id, prompt_id


def defer_regeneration_for_active_submission(
    segment: dict[str, Any],
    *,
    reason: str = "prompt changed",
) -> bool:
    """Mark newer inputs stale without replacing the active job's status."""

    task_id, prompt_id = active_submission_identity(segment)
    if not (task_id or prompt_id):
        return False
    segment[DEFERRED_REGENERATION_KEY] = {
        "reason": str(reason or "prompt changed").strip() or "prompt changed",
        "task_id": task_id,
        "prompt_id": prompt_id,
    }
    return True


def recover_interrupted_deferred_regeneration(segment: dict[str, Any]) -> bool:
    """Repair records written by the old save-during-render behavior."""

    if str(segment.get("status") or "").strip().lower() != "needs_regenerate":
        return False
    if str(segment.get("video_path") or "").strip():
        return False
    task_id = str(segment.get("task_id") or "").strip()
    if not task_id or str(segment.get("backend") or "").strip().lower() not in {"", "runninghub"}:
        return False
    attempts = segment.get("runninghub_attempts") if isinstance(segment.get("runninghub_attempts"), list) else []
    attempt = next(
        (
            item
            for item in attempts
            if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id
        ),
        None,
    )
    attempt_status = str((attempt or {}).get("status") or "").strip().lower()
    if attempt_status not in _ACTIVE_ATTEMPT_STATUSES:
        return False
    segment["status"] = "running"
    segment["submitted"] = True
    return defer_regeneration_for_active_submission(
        segment,
        reason=str(segment.get("stale_reason") or "prompt changed"),
    )


def finalize_deferred_regeneration(
    segment: dict[str, Any],
    *,
    task_id: str = "",
    prompt_id: str = "",
) -> bool:
    """Apply a deferred stale state after its owning job becomes terminal."""

    marker = segment.get(DEFERRED_REGENERATION_KEY)
    if not isinstance(marker, dict):
        return False
    expected_task = str(marker.get("task_id") or "").strip()
    expected_prompt = str(marker.get("prompt_id") or "").strip()
    actual_task = str(task_id or "").strip()
    actual_prompt = str(prompt_id or "").strip()
    if expected_task and expected_task != actual_task:
        return False
    if expected_prompt and expected_prompt != actual_prompt:
        return False

    reason = str(marker.get("reason") or "prompt changed").strip() or "prompt changed"
    completed_video = str(segment.get("video_path") or "").strip()
    if completed_video:
        segment["stale_video_path"] = completed_video
    segment["stale_reason"] = reason
    segment["status"] = "needs_regenerate"
    segment["submitted"] = False
    segment.pop(DEFERRED_REGENERATION_KEY, None)
    return True
