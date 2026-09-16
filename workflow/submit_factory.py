"""Factory for selecting the configured video submit backend."""
from __future__ import annotations

from typing import Any

from services.context import CONFIG
from workflow.submit_provider import BaseSubmitProvider


def model_config(model_id: str | None = None) -> dict[str, Any]:
    """Return configuration owned by one registered generation model."""
    from generation.registry import default_model_id

    selected = str(model_id or default_model_id()).strip()
    models = CONFIG.get("models") if isinstance(CONFIG.get("models"), dict) else {}
    config = models.get(selected) if isinstance(models, dict) else None
    return dict(config or {})


def submit_backend_config(name: str | None = None, model_id: str | None = None) -> dict[str, Any]:
    """Merge a backend's common options with its model deployment options."""
    backend = str(name or CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    all_backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
    common = all_backends.get(backend) if isinstance(all_backends, dict) else None
    config = dict(common or {})
    profiles = model_config(model_id).get("execution_profiles")
    profile = profiles.get(backend) if isinstance(profiles, dict) else None
    if isinstance(profile, dict):
        config.update(profile)
    return config


def get_submit_provider(model_id: str | None = None) -> BaseSubmitProvider:
    backend = str(CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    backend_config = submit_backend_config(backend, model_id=model_id)
    if backend_config and backend_config.get("enabled") is False:
        raise ValueError(f"video submit backend is disabled: {backend}")

    if backend == "comfyui":
        from workflow.submitters.comfyui_provider import ComfyUISubmitProvider

        return ComfyUISubmitProvider()

    if backend == "runninghub":
        from workflow.submitters.runninghub_provider import RunningHubSubmitProvider

        return RunningHubSubmitProvider(model_id=model_id)

    raise ValueError(f"Unsupported video_submit_backend: {backend}")
