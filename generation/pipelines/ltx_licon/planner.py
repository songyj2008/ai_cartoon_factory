"""LTX-specific segment planning that preserves the existing Beat boundaries."""
from __future__ import annotations

from typing import Any

from generation.beat_ir import SemanticBeat, semantic_beat_from_data


class LtxSegmentPlanner:
    """Adapt semantic beats to the current one-Beat/one-LTX-segment policy.

    This first migration intentionally does not split or rewrite a Beat: that
    would change existing LTX output.  Future model planners may apply their
    own duration limits and segment decomposition here.
    """

    def plan(self, beats_data: dict[str, Any]) -> list[SemanticBeat]:
        beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
        if not isinstance(beats, list) or not beats:
            raise ValueError("beats data is empty; cannot plan video segments")
        planned: list[SemanticBeat] = []
        for index, beat in enumerate(beats, start=1):
            if not isinstance(beat, dict):
                continue
            semantic = semantic_beat_from_data(beat, index)
            if not semantic.story_event:
                raise ValueError(f"Beat {index} plot is empty; cannot plan LTX segment")
            if semantic.duration_sec <= 0:
                raise ValueError(f"Beat {index} duration must be positive")
            planned.append(semantic)
        if not planned:
            raise ValueError("beats data has no valid video segments")
        return planned
