"""H3-specific duration planning without modifying LTX beat behavior."""
from __future__ import annotations

from typing import Any, Iterable

from generation.beat_ir import SemanticBeat, semantic_beat_from_data


class H3SegmentPlanner:
    """Validate the one-Beat/one-Ref2VA contract currently supported.

    Splitting a Beat changes stable segment indexes and therefore needs an
    explicit migration/UI decision.  Until that exists, this planner rejects a
    Beat that cannot be represented as one H3 clip rather than silently
    shortening it or changing the story.
    """

    duration_min_sec = 4
    duration_max_sec = 15

    def plan(self, beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> list[SemanticBeat]:
        beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
        if not isinstance(beats, list) or not beats:
            raise ValueError("beats data is empty; cannot plan H3 segments")
        selected = {int(index) for index in segment_indices or set() if int(index) > 0}
        planned: list[SemanticBeat] = []
        for index, beat in enumerate(beats, start=1):
            if selected and index not in selected:
                continue
            if not isinstance(beat, dict):
                raise ValueError(f"Beat {index} is not an object")
            semantic = semantic_beat_from_data(beat, index)
            if not semantic.story_event:
                raise ValueError(f"Beat {index} plot is empty; cannot plan H3 segment")
            self.validate_duration(semantic.duration_sec, index)
            planned.append(semantic)
        if not planned:
            raise ValueError("beats data has no selected H3 segments")
        return planned

    def validate_duration(self, duration_sec: float, index: int | None = None) -> None:
        label = f"Beat {index}" if index is not None else "H3 segment"
        try:
            value = float(duration_sec)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} duration must be numeric") from exc
        if value != int(value):
            raise ValueError(f"{label} duration must be an integer for H3")
        if value < self.duration_min_sec or value > self.duration_max_sec:
            raise ValueError(
                f"{label} duration must be {self.duration_min_sec}-{self.duration_max_sec} seconds for H3 Ref2VA"
            )
