"""Registry for model pipelines.

Adding a model means registering a new pipeline.  Callers never branch on a
particular model id.
"""
from __future__ import annotations

from typing import Any, Callable

from generation.contracts import ModelSpec
from generation.pipelines.base import GenerationPipeline
from generation.pipelines.ltx_licon.pipeline import LTX_LICON_MODEL_ID, LtxLiconPipeline
from generation.pipelines.minimax_h3.pipeline import H3_REF2VA_MODEL_ID, MiniMaxH3Ref2VAPipeline


class ModelRegistry:
    def __init__(self) -> None:
        self._pipelines: dict[str, GenerationPipeline] = {}
        self._factories: dict[str, Callable[[], GenerationPipeline]] = {}

    def register(
        self,
        pipeline: GenerationPipeline,
        *,
        factory: Callable[[], GenerationPipeline] | None = None,
    ) -> None:
        model_id = str(pipeline.spec.id or "").strip()
        if not model_id:
            raise ValueError("registered generation pipeline must have a model id")
        self._pipelines[model_id] = pipeline
        self._factories[model_id] = factory or type(pipeline)

    def refresh(self, model_id: str) -> GenerationPipeline:
        """Recreate one pipeline from current runtime configuration.

        The registry object itself remains stable, so already-created services
        immediately see the replacement without an application restart.
        """
        normalized = str(model_id or "").strip()
        if normalized not in self._pipelines:
            return self.get(normalized)
        factory = self._factories.get(normalized)
        if factory is None:
            raise ValueError(f"generation pipeline cannot be refreshed: {normalized}")
        refreshed = factory()
        refreshed_id = str(refreshed.spec.id or "").strip()
        if refreshed_id != normalized:
            raise ValueError(f"refreshed pipeline id changed from {normalized} to {refreshed_id or '<empty>'}")
        self._pipelines[normalized] = refreshed
        return refreshed

    def get(self, model_id: str) -> GenerationPipeline:
        normalized = str(model_id or "").strip()
        try:
            return self._pipelines[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self._pipelines)) or "none"
            raise ValueError(f"unsupported video model: {normalized or '<empty>'}; available: {available}") from exc

    def has(self, model_id: str) -> bool:
        return str(model_id or "").strip() in self._pipelines

    def specs(self) -> list[ModelSpec]:
        return [pipeline.spec for pipeline in self._pipelines.values()]


_REGISTRY: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ModelRegistry()
        _REGISTRY.register(LtxLiconPipeline(), factory=LtxLiconPipeline)
        from services.context import CONFIG

        models = CONFIG.get("models") if isinstance(CONFIG.get("models"), dict) else {}
        h3_config = models.get(H3_REF2VA_MODEL_ID) if isinstance(models, dict) else {}
        if not isinstance(h3_config, dict) or h3_config.get("enabled", True):
            _REGISTRY.register(MiniMaxH3Ref2VAPipeline(), factory=MiniMaxH3Ref2VAPipeline)
    return _REGISTRY


def refresh_model_pipeline(model_id: str) -> GenerationPipeline:
    """Reload one configured model pipeline in the current process."""
    return get_model_registry().refresh(model_id)


def default_model_id(config: dict[str, Any] | None = None) -> str:
    if config is None:
        from services.context import CONFIG

        config = CONFIG
    registry_config = config.get("model_registry") if isinstance(config.get("model_registry"), dict) else {}
    model_id = str((registry_config or {}).get("default_model_id") or LTX_LICON_MODEL_ID).strip()
    return model_id or LTX_LICON_MODEL_ID
