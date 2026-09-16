"""Local MiniMax H3 Ref2VA pipeline building blocks.

This package is intentionally unregistered.  The model factory can register it
only after its workflow template and deployment profile have been configured.
"""
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder, H3AssetBinding, H3AssetBindings
from generation.pipelines.minimax_h3.contracts import (
    H3AssetType,
    H3GenerationConfig,
    H3ReferenceAsset,
    H3_REF2VA_GENERATION_MODE,
    H3_REF2VA_MODEL_ID,
    h3_generation_config_from_segment,
)
from generation.pipelines.minimax_h3.input_stager import H3ComfyInputStager
from generation.pipelines.minimax_h3.prompt_compiler import H3CompiledPrompt, H3PromptCompiler
from generation.pipelines.minimax_h3.pipeline import H3VideoJob, MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.planner import H3SegmentPlanner
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator, H3ValidationIssue, H3ValidationReport, validate_generation_config
from generation.pipelines.minimax_h3.workflow_adapter import (
    DEFAULT_H3_REF2VA_WORKFLOW_PROFILE,
    H3ComfyNodeTypes,
    H3NodeInput,
    H3ReferenceControl,
    H3Ref2VAWorkflowAdapter,
    H3Ref2VAWorkflowProfile,
    H3WorkflowBuild,
)

__all__ = [
    "DEFAULT_H3_REF2VA_WORKFLOW_PROFILE",
    "H3AssetBinder",
    "H3AssetBinding",
    "H3AssetBindings",
    "H3AssetType",
    "H3ComfyNodeTypes",
    "H3ComfyInputStager",
    "H3CompiledPrompt",
    "H3GenerationConfig",
    "H3NodeInput",
    "H3ReferenceControl",
    "H3PromptCompiler",
    "H3SegmentPlanner",
    "H3VideoJob",
    "H3Ref2VAValidator",
    "H3Ref2VAWorkflowAdapter",
    "H3Ref2VAWorkflowProfile",
    "H3ReferenceAsset",
    "H3ValidationIssue",
    "H3ValidationReport",
    "H3WorkflowBuild",
    "H3_REF2VA_GENERATION_MODE",
    "H3_REF2VA_MODEL_ID",
    "MiniMaxH3Ref2VAPipeline",
    "h3_generation_config_from_segment",
    "validate_generation_config",
]
