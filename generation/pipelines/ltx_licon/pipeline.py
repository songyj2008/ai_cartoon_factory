"""Pipeline composition for the current LTX2.3 / LiconMSR workflow.

The existing LTX compiler, asset resolver, workflow adapter, and submit flow
remain authoritative here.  The generic generation service only selects and
orchestrates this pipeline; it does not learn Licon node ids or prompt rules.
"""
from __future__ import annotations

from typing import Any

from generation.contracts import ModelCapabilities, ModelSpec
from generation.pipelines.ltx_licon.planner import LtxSegmentPlanner
from generation.pipelines.ltx_licon.rules import build_ltx_rule_set


LTX_LICON_MODEL_ID = "ltx23_licon_msr_v2"


class LtxLiconPipeline:
    def __init__(self, spec: ModelSpec | None = None) -> None:
        if spec is None:
            from services.context import CONFIG

            spec = ModelSpec(
                id=LTX_LICON_MODEL_ID,
                display_name="LTX 2.3 / LiconMSR",
                pipeline_key="ltx_licon",
                revision=str((CONFIG.get("models") or {}).get(LTX_LICON_MODEL_ID, {}).get("revision") or "1"),
                capabilities=ModelCapabilities(
                    duration_min_sec=1,
                    duration_max_sec=30,
                    default_fps=int(CONFIG.get("fps") or 50),
                    fixed_fps=True,
                    reference_image_min=int(CONFIG.get("reference_image_min", 0) or 0),
                    reference_image_max=int(CONFIG.get("reference_image_max") or 4),
                    background_required=bool(CONFIG.get("background_required", True)),
                    supports_image_references=True,
                    produces_audio=True,
                ),
            )
        self.spec = spec
        self.rules = build_ltx_rule_set(spec.id, spec.display_name)
        self.planner = LtxSegmentPlanner()

    def plan(self, beats_data: dict[str, Any]):
        return self.planner.plan(beats_data)

    def prepare(self, beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> dict[str, Any]:
        from workflow.segment_runner import prepare_licon_msr_segment_prompts

        return prepare_licon_msr_segment_prompts(beats_data, segment_indices=segment_indices)

    def reset_prompt_for_regeneration(self, segment: dict[str, Any]) -> None:
        """LTX preparation always recompiles its prompt from the selected Beat."""

        return None

    def generate(
        self,
        beats_data: dict[str, Any],
        *,
        submit: bool,
        workflow_only: bool,
        segment_indices: set[int] | None = None,
        merge_after: bool = False,
    ) -> dict[str, Any]:
        from workflow.segment_runner import run_licon_msr_segments

        return run_licon_msr_segments(
            beats_data,
            submit_to_comfyui=submit,
            workflow_only=workflow_only,
            segment_indices=segment_indices,
            merge_after=merge_after,
        )

    def submit_saved(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        from workflow.segment_runner import submit_saved_licon_msr_segments

        return submit_saved_licon_msr_segments(segment_indices=segment_indices, merge_after=merge_after, wait=wait)
