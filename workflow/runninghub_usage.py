"""Persist and aggregate per-task RunningHub billing metadata."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


_COST_KEYS = ("consumeCoins", "consume_coins")
_DURATION_KEYS = ("taskCostTime", "task_cost_time", "task_cost_seconds")
_MONEY_KEYS = ("consumeMoney", "consume_money")
_THIRD_PARTY_MONEY_KEYS = ("thirdPartyConsumeMoney", "third_party_consume_money")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _model_id_from(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return ""
    return _text(value.get("id") or value.get("model_id"))


def _selected_model_id(segment: dict[str, Any]) -> str:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return _text(
        _model_id_from(segment.get("resolved_model"))
        or segment.get("model_id")
        or job.get("model_id")
        or segment.get("model_override")
    )


def _render_model_id(segment: dict[str, Any], attempt: dict[str, Any]) -> str:
    """Read immutable model provenance from the render linked to an attempt."""
    if not isinstance(segment.get("renders"), list):
        return ""
    try:
        from generation.render_history import find_render

        render_id = _text(attempt.get("render_id"))
        task_id = _text(attempt.get("task_id"))
        render = find_render(segment, render_id=render_id) if render_id else None
        render = render or (find_render(segment, task_id=task_id) if task_id else None)
        return _model_id_from(render.get("model")) if isinstance(render, dict) else ""
    except Exception:
        return ""


def _looks_like_legacy_ltx(segment: dict[str, Any]) -> bool:
    """Conservatively identify task history created by the former LTX-only path."""
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    mode = _text(segment.get("generation_mode") or segment.get("mode")).casefold()
    if "licon" in mode or "ltx" in mode:
        return True
    return "background_image" in job and "reference_images" in job


def runninghub_attempt_model_id(segment: dict[str, Any], attempt: dict[str, Any]) -> str:
    """Resolve the model that produced one RunningHub task.

    Attempt and render snapshots are immutable provenance.  The segment's
    currently selected model is only safe for the currently active task; it
    must never relabel an older retry after the user changes models.
    """
    explicit = _text(
        attempt.get("model_id")
        or _model_id_from(attempt.get("model"))
        or _model_id_from(attempt.get("resolved_model"))
    )
    if explicit:
        return explicit
    render_model_id = _render_model_id(segment, attempt)
    if render_model_id:
        return render_model_id
    task_id = _text(attempt.get("task_id"))
    active_task_id = _text(segment.get("task_id"))
    if task_id and task_id == active_task_id:
        selected = _selected_model_id(segment)
        if selected:
            return selected
    if _looks_like_legacy_ltx(segment):
        return "ltx23_licon_msr_v2"
    return ""


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _first_number(item: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = _number(item.get(key))
        if value is not None:
            return value
    return None


def task_usage_from_outputs(outputs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Extract billable task metadata from RunningHub's task-output response.

    ``taskCostTime`` is repeated for each output node when a workflow has more
    than one output, so a task's duration is the maximum reported value.  Coin
    charges are per output and are therefore summed after de-duplicating nodes.
    """
    seen: set[tuple[str, str, str]] = set()
    billing_outputs: list[dict[str, Any]] = []
    durations: list[Decimal] = []
    coins: list[Decimal] = []
    money: list[Decimal] = []
    third_party_money: list[Decimal] = []

    for output in outputs or []:
        if not isinstance(output, dict):
            continue
        task_cost = _first_number(output, _DURATION_KEYS)
        consume_coins = _first_number(output, _COST_KEYS)
        consume_money = _first_number(output, _MONEY_KEYS)
        third_party = _first_number(output, _THIRD_PARTY_MONEY_KEYS)
        node_id = str(output.get("nodeId") or output.get("node_id") or "").strip()
        file_url = str(output.get("fileUrl") or output.get("file_url") or output.get("url") or "").strip()
        signature = (node_id, file_url, str(output.get("fileType") or output.get("file_type") or "").strip())
        if signature in seen:
            continue
        seen.add(signature)
        if all(value is None for value in (task_cost, consume_coins, consume_money, third_party)):
            continue
        billing_outputs.append(
            {
                "node_id": node_id,
                "file_url": file_url,
                "file_type": str(output.get("fileType") or output.get("file_type") or "").strip(),
                "task_cost_seconds": _json_number(task_cost),
                "consume_coins": _json_number(consume_coins),
                "consume_money": _json_number(consume_money),
                "third_party_consume_money": _json_number(third_party),
            }
        )
        if task_cost is not None:
            durations.append(task_cost)
        if consume_coins is not None:
            coins.append(consume_coins)
        if consume_money is not None:
            money.append(consume_money)
        if third_party is not None:
            third_party_money.append(third_party)

    return {
        "task_cost_seconds": _json_number(max(durations)) if durations else None,
        "consume_coins": _json_number(sum(coins, Decimal("0"))) if coins else None,
        "consume_money": _json_number(sum(money, Decimal("0"))) if money else None,
        "third_party_consume_money": _json_number(sum(third_party_money, Decimal("0"))) if third_party_money else None,
        "billing_outputs": billing_outputs,
    }


def record_runninghub_attempt(
    segment: dict[str, Any],
    task_id: str,
    *,
    status: str = "",
    outputs: list[dict[str, Any]] | None = None,
    submitted_prompt: str = "",
    video_path: str = "",
    model_id: str = "",
) -> dict[str, Any] | None:
    """Create or update the one task-history entry belonging to ``task_id``.

    A task can be polled several times.  The submission prompt and downloaded
    local video are immutable audit data for that task, so only non-empty
    values are written and later polls never erase them.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    attempts = segment.get("runninghub_attempts")
    if not isinstance(attempts, list):
        attempts = []
        segment["runninghub_attempts"] = attempts

    attempt = next((item for item in attempts if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id), None)
    if attempt is None:
        attempt = {"attempt": len(attempts) + 1, "task_id": task_id, "status": "submitted"}
        attempts.append(attempt)
    # Capture model provenance once.  Existing attempts are never relabelled
    # from the segment's current selection after a model switch.
    resolved_model_id = _text(model_id) or _render_model_id(segment, attempt)
    if not resolved_model_id and _text(segment.get("task_id")) == task_id:
        resolved_model_id = _selected_model_id(segment)
    if not resolved_model_id and _looks_like_legacy_ltx(segment):
        resolved_model_id = "ltx23_licon_msr_v2"
    if resolved_model_id and not _text(attempt.get("model_id")):
        attempt["model_id"] = resolved_model_id
    if status:
        attempt["status"] = str(status).strip().lower()
    prompt_text = str(submitted_prompt or "").strip()
    if prompt_text:
        attempt["submitted_prompt"] = prompt_text
    local_video = str(video_path or "").strip()
    if local_video:
        attempt["video_path"] = local_video
        attempt.pop("video_deleted", None)
        attempt.pop("video_deleted_at", None)
    if outputs is not None:
        usage = task_usage_from_outputs(outputs)
        attempt.update({key: value for key, value in usage.items() if value is not None})
        # An empty response is meaningful while a task is still running, so
        # leave the entry open for a later sync rather than recording a fake 0.
        if usage["task_cost_seconds"] is not None or usage["consume_coins"] is not None:
            attempt["usage_status"] = "recorded"
        elif attempt.get("status") in {"success", "succeeded", "finished", "completed", "complete", "done"}:
            attempt["usage_status"] = "unavailable"
        else:
            attempt["usage_status"] = "pending"
    return attempt


def attempt_needs_usage_sync(segment: dict[str, Any], task_id: str) -> bool:
    """Return whether a saved RunningHub task still needs its billing lookup."""
    task_id = str(task_id or "").strip()
    for item in segment.get("runninghub_attempts") or []:
        if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id:
            return str(item.get("usage_status") or "").strip().lower() == "pending"
    return bool(task_id)


def _summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [_number(item.get("task_cost_seconds")) for item in attempts]
    coins = [_number(item.get("consume_coins")) for item in attempts]
    known_durations = [value for value in durations if value is not None]
    known_coins = [value for value in coins if value is not None]
    return {
        "generation_count": len(attempts),
        "total_task_cost_seconds": _json_number(sum(known_durations, Decimal("0"))),
        "task_cost_seconds_known": bool(known_durations),
        "total_consume_coins": _json_number(sum(known_coins, Decimal("0"))),
        "consume_coins_known": bool(known_coins),
    }


def refresh_runninghub_usage_summaries(payload: dict[str, Any]) -> dict[str, Any]:
    """Refresh per-segment and final-video usage totals in ``video_jobs.json``."""
    segment_summaries: list[dict[str, Any]] = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        attempts = [item for item in segment.get("runninghub_attempts") or [] if isinstance(item, dict)]
        if not attempts:
            continue
        summary = _summary(attempts)
        segment["runninghub_usage"] = summary
        segment_summaries.append(summary)

    durations = [_number(item.get("total_task_cost_seconds")) for item in segment_summaries]
    coins = [_number(item.get("total_consume_coins")) for item in segment_summaries]
    known_duration_count = sum(bool(item.get("task_cost_seconds_known")) for item in segment_summaries)
    known_coin_count = sum(bool(item.get("consume_coins_known")) for item in segment_summaries)
    overall = {
        "generation_count": sum(int(item.get("generation_count") or 0) for item in segment_summaries),
        "total_task_cost_seconds": _json_number(sum((value for value in durations if value is not None), Decimal("0"))),
        "task_cost_seconds_known_segment_count": known_duration_count,
        "total_consume_coins": _json_number(sum((value for value in coins if value is not None), Decimal("0"))),
        "consume_coins_known_segment_count": known_coin_count,
        "segment_count": len(segment_summaries),
    }
    payload["runninghub_usage"] = overall
    # Merging segments runs locally, so the final video's RunningHub cost is
    # precisely the accumulated cost of all of its source segment attempts.
    payload["final_video_usage"] = {"source": "sum_of_segments", **overall}
    return overall
