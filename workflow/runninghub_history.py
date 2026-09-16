"""Stable ownership and repair helpers for RunningHub task history.

``part_id`` and ``segment_index`` are display positions.  Beat editing changes
both, therefore neither may be used as the identity of a remote generation
task.  This module keeps the existing, per-segment history format compatible
with the UI while enforcing one active owner for each RunningHub task id.
"""
from __future__ import annotations

import copy
import hashlib
import time
import uuid
from collections import defaultdict
from typing import Any


HISTORY_SCHEMA_VERSION = 2


def new_segment_uid() -> str:
    return f"segment_{uuid.uuid4().hex}"


def _task_id(attempt: dict[str, Any]) -> str:
    return str(attempt.get("task_id") or "").strip()


def _segment_label(segment: dict[str, Any]) -> str:
    return str(segment.get("part_id") or segment.get("segment_id") or segment.get("segment_uid") or "unknown")


def ensure_segment_uids(payload: dict[str, Any]) -> None:
    """Assign stable IDs to active and retired segments missing one."""
    for collection in ("segments", "retired_segments"):
        for segment in payload.get(collection) or []:
            if isinstance(segment, dict) and not str(segment.get("segment_uid") or "").strip():
                segment["segment_uid"] = new_segment_uid()


def _merge_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep every non-empty piece of audit data when legacy copies disagree."""
    merged = copy.deepcopy(attempts[0])
    for attempt in attempts[1:]:
        for key, value in attempt.items():
            current = merged.get(key)
            if current in (None, "", [], {}) and value not in (None, "", [], {}):
                merged[key] = copy.deepcopy(value)
    return merged


def _archive_key(task_id: str) -> str:
    digest = hashlib.sha1(task_id.encode("utf-8")).hexdigest()[:12]
    return f"legacy_duplicate_{digest}"


def normalize_runninghub_history(payload: dict[str, Any]) -> dict[str, int]:
    """Repair legacy duplicate task references without discarding audit data.

    A task referenced by exactly one segment remains there.  A task copied to
    multiple *active* segments cannot safely be attributed after the fact, so
    it is removed from all active cards and preserved once in the append-only
    ``retired_runninghub_attempts`` archive with every former owner recorded.
    """
    ensure_segment_uids(payload)
    active = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
    retired = [item for item in payload.get("retired_segments") or [] if isinstance(item, dict)]
    all_segments = active + retired
    active_object_ids = {id(item) for item in active}
    occurrences: dict[str, list[tuple[dict[str, Any], dict[str, Any], bool]]] = defaultdict(list)

    for segment in all_segments:
        is_active = id(segment) in active_object_ids
        attempts = segment.get("runninghub_attempts")
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if isinstance(attempt, dict) and _task_id(attempt):
                occurrences[_task_id(attempt)].append((segment, attempt, is_active))

    archived = payload.get("retired_runninghub_attempts")
    if not isinstance(archived, list):
        archived = []
        payload["retired_runninghub_attempts"] = archived
    archived_by_task = {
        _task_id(item): item
        for item in archived
        if isinstance(item, dict) and _task_id(item)
    }

    repaired = 0
    for task_id, rows in occurrences.items():
        active_rows = [row for row in rows if row[2]]
        active_owners = {str(row[0].get("segment_uid") or "") for row in active_rows}
        if len(active_owners) <= 1:
            continue

        merged = _merge_attempts([row[1] for row in rows])
        merged["task_id"] = task_id
        merged["history_state"] = "retired_ambiguous_legacy_duplicate"
        merged["retired_reason"] = "legacy duplicate after beat split/delete; active owner cannot be inferred safely"
        merged["legacy_owner_segment_uids"] = sorted(active_owners)
        merged["legacy_owner_parts"] = sorted({_segment_label(row[0]) for row in active_rows})
        merged["retired_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing = archived_by_task.get(task_id)
        if existing is None:
            archived.append(merged)
            archived_by_task[task_id] = merged
        else:
            archived_by_task[task_id] = _merge_attempts([existing, merged])
            archived[archived.index(existing)] = archived_by_task[task_id]

        for segment, attempt, is_active in active_rows:
            attempts = segment.get("runninghub_attempts") or []
            segment["runninghub_attempts"] = [item for item in attempts if item is not attempt]
        repaired += 1

    # Keep retry counters meaningful after the repair.
    for segment in all_segments:
        attempts = [item for item in segment.get("runninghub_attempts") or [] if isinstance(item, dict)]
        for index, attempt in enumerate(attempts, start=1):
            attempt["attempt"] = index
        if attempts:
            segment["runninghub_attempts"] = attempts
        else:
            segment.pop("runninghub_attempts", None)
            segment.pop("runninghub_usage", None)

    payload["runninghub_history_schema_version"] = HISTORY_SCHEMA_VERSION
    return {"repaired_duplicate_task_ids": repaired, "archived_task_ids": len(archived)}


def retire_segment(payload: dict[str, Any], segment: dict[str, Any], reason: str) -> dict[str, Any]:
    """Move one segment to an audit archive without touching its artifacts."""
    ensure_segment_uids(payload)
    retired = payload.get("retired_segments")
    if not isinstance(retired, list):
        retired = []
        payload["retired_segments"] = retired
    archived = copy.deepcopy(segment)
    archived["retired"] = True
    archived["retired_reason"] = reason
    archived["retired_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    archived["former_part_id"] = str(segment.get("part_id") or "")
    if not any(str(item.get("segment_uid") or "") == str(archived.get("segment_uid") or "") for item in retired if isinstance(item, dict)):
        retired.append(archived)
    return archived
