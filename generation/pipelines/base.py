"""The small protocol implemented by each model generation pipeline."""
from __future__ import annotations

from typing import Any, Protocol

from generation.contracts import ModelSpec
from generation.model_rules import ModelRuleSet


class GenerationPipeline(Protocol):
    spec: ModelSpec
    rules: ModelRuleSet

    def plan(self, beats_data: dict[str, Any]) -> Any:
        """Validate or decompose semantic beats for this model."""

    def prepare(self, beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> dict[str, Any]:
        """Create editable model-specific segment jobs without submitting."""

    def reset_prompt_for_regeneration(self, segment: dict[str, Any]) -> None:
        """Clear only model-owned prompt state before compiling it again."""

    def generate(
        self,
        beats_data: dict[str, Any],
        *,
        submit: bool,
        workflow_only: bool,
        segment_indices: set[int] | None = None,
        merge_after: bool = False,
    ) -> dict[str, Any]:
        """Assemble and optionally submit the selected segments."""

    def submit_saved(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Submit persisted jobs belonging to this model."""
