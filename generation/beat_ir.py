"""Model-neutral semantic view of a story beat.

The current writer still produces LTX-friendly prose in ``plot``.  This
module deliberately preserves that text for LTX while exposing the narrative
facts future model planners and compilers may consume without inheriting LTX
workflow node or media rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _texts(values: Any) -> list[str]:
    return [str(value).strip() for value in values or [] if str(value).strip()]


@dataclass(frozen=True)
class SemanticBeat:
    index: int
    title: str
    story_event: str
    scene_id: str
    visible_roles: list[str]
    reference_roles: list[str]
    dialogue_units: list[dict[str, Any]]
    action_units: list[dict[str, Any]]
    event_units: list[dict[str, Any]]
    duration_sec: float
    raw: dict[str, Any]


def semantic_beat_from_data(beat: dict[str, Any], index: int) -> SemanticBeat:
    data = beat if isinstance(beat, dict) else {}
    duration = data.get("estimated_duration_sec") or data.get("duration_sec") or 0
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = 0
    return SemanticBeat(
        index=int(index),
        title=str(data.get("title") or f"Segment {index}").strip(),
        story_event=str(data.get("plot") or "").strip(),
        scene_id=str(data.get("scene_id") or "").strip(),
        visible_roles=_texts(data.get("visible_roles")),
        reference_roles=_texts(data.get("reference_roles")),
        dialogue_units=[item for item in data.get("dialogue_units") or [] if isinstance(item, dict)],
        action_units=[item for item in data.get("action_units") or [] if isinstance(item, dict)],
        event_units=[item for item in data.get("event_units") or [] if isinstance(item, dict)],
        duration_sec=duration,
        raw=data,
    )
