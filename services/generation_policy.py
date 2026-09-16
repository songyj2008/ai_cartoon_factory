"""Read deterministic generation limits from a registered model pipeline.

The compatibility-free LTX helpers retain their existing defaults.  New
models expose their own constraints through ``ModelCapabilities`` instead of
adding model-id conditionals to beat generation or workflow code.
"""
from __future__ import annotations

from typing import Any


LICON_MSR_DIRECT = "licon_msr_direct"
DATA_PROMPT_ONLY = "data_prompt_only"
WORKFLOW_PATCHER = "licon_msr_workflow_patcher"
PROMPT_AUTHORING = "prompt_authoring"


ISSUE_ROUTING = {
    "prompt_quality": PROMPT_AUTHORING,
    "reference_image_count": WORKFLOW_PATCHER,
    "background_image": WORKFLOW_PATCHER,
    "duration_frames": WORKFLOW_PATCHER,
    "node_wiring": WORKFLOW_PATCHER,
    "story_text_special_case": DATA_PROMPT_ONLY,
}


def _config() -> dict[str, Any]:
    try:
        from services.context import CONFIG
        return CONFIG if isinstance(CONFIG, dict) else {}
    except Exception:
        return {}


def model_capabilities(model_id: str | None = None):
    """Return capabilities for a registered model, or ``None`` if unknown."""
    try:
        from generation.registry import default_model_id, get_model_registry

        selected = str(model_id or default_model_id()).strip()
        registry = get_model_registry()
        return registry.get(selected).spec.capabilities if registry.has(selected) else None
    except Exception:
        return None


def workflow_mode() -> str:
    return str(_config().get("workflow_mode") or LICON_MSR_DIRECT).strip() or LICON_MSR_DIRECT


def licon_msr_direct_mode() -> bool:
    return workflow_mode() == LICON_MSR_DIRECT


def workflow_fps(default: int = 50, model_id: str | None = None) -> int:
    capabilities = model_capabilities(model_id)
    if capabilities and capabilities.default_fps:
        return max(1, int(capabilities.default_fps))
    try:
        return max(1, int(_config().get("fps", default)))
    except Exception:
        return default


def reference_image_min(default: int = 0, model_id: str | None = None) -> int:
    capabilities = model_capabilities(model_id)
    if capabilities:
        return max(0, int(capabilities.reference_image_min))
    try:
        return max(0, int(_config().get("reference_image_min", default)))
    except Exception:
        return max(0, int(default or 0))


def reference_image_max(default: int = 4, model_id: str | None = None) -> int:
    capabilities = model_capabilities(model_id)
    if capabilities and capabilities.reference_image_max is not None:
        return max(reference_image_min(model_id=model_id), int(capabilities.reference_image_max))
    try:
        return max(reference_image_min(model_id=model_id), int(_config().get("reference_image_max", default)))
    except Exception:
        return default


def max_important_visible_roles(default: int = 4) -> int:
    """Beat-stage role limit. Kept because beats are still the story segmentation layer."""
    try:
        return max(1, int(_config().get("max_important_visible_roles", default)))
    except Exception:
        return default


def background_required(model_id: str | None = None) -> bool:
    capabilities = model_capabilities(model_id)
    if capabilities:
        return bool(capabilities.background_required)
    return bool(_config().get("background_required", True))


def total_frames(duration_sec: int | float, fps: int | None = None, model_id: str | None = None) -> int:
    return int(round(float(duration_sec) * int(fps or workflow_fps(model_id=model_id))))


def issue_owner(issue_type: str) -> str:
    return ISSUE_ROUTING.get(str(issue_type or ""), DATA_PROMPT_ONLY)
