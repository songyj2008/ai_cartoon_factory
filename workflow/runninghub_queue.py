"""Model-neutral helpers for the local RunningHub submission queue."""
from __future__ import annotations

from typing import Any, Mapping


def active_task_id(segment: Mapping[str, Any]) -> str:
    task_id = str(segment.get("task_id") or "").strip()
    if task_id:
        return task_id
    for key in ("submit_result", "raw_submit"):
        nested = segment.get(key)
        if not isinstance(nested, Mapping):
            continue
        for task_key in ("task_id", "taskId", "id"):
            task_id = str(nested.get(task_key) or "").strip()
            if task_id:
                return task_id
    return ""


def active_task_count(segments: list[Mapping[str, Any]]) -> int:
    """Count accepted remote tasks occupying the account's execution slots."""
    return sum(
        1
        for segment in segments
        if str(segment.get("backend") or "").strip().lower() == "runninghub"
        and str(segment.get("status") or "").strip().lower() in {"running", "pending"}
        and bool(active_task_id(segment))
        and not str(segment.get("video_path") or "").strip()
    )


def is_queue_full_error(error: Exception | str) -> bool:
    return "TASK_QUEUE_MAXED" in str(error).upper()
