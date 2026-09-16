"""Versioned, model-aware persistence for ``video_jobs.json``."""
from __future__ import annotations

from typing import Any

from generation.registry import default_model_id, get_model_registry


VIDEO_JOBS_SCHEMA_VERSION = 3


def _model_snapshot(model_id: str) -> dict[str, str]:
    registry = get_model_registry()
    pipeline = registry.get(model_id)
    return {"id": pipeline.spec.id, "revision": pipeline.spec.revision}


def _legacy_licon_model(segment: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Keep untagged historical Licon artifacts out of a future H3 default."""
    mode = str(payload.get("mode") or segment.get("mode") or "").casefold()
    if "licon" in mode or "ltx" in mode:
        return True
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return "background_image" in job and "reference_images" in job


def _normalize_segment(
    segment: dict[str, Any],
    *,
    payload: dict[str, Any],
    default_id: str,
    registry: Any,
) -> None:
    override = segment.get("model_override")
    override = str(override).strip() if override not in (None, "") else None
    resolved = segment.get("resolved_model") if isinstance(segment.get("resolved_model"), dict) else {}
    explicit_model = str(resolved.get("id") or segment.get("model_id") or override or "").strip()
    if explicit_model:
        model_id = explicit_model
    elif _legacy_licon_model(segment, payload):
        model_id = "ltx23_licon_msr_v2"
    else:
        model_id = default_id
    registered = registry.has(model_id)
    segment["model_override"] = override
    segment["model_id"] = model_id
    snapshot = _model_snapshot(model_id) if registered else {"id": model_id}
    segment["resolved_model"] = {**snapshot, **resolved, "id": model_id}

    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    if job:
        job.setdefault("model_id", model_id)
        segment["job"] = job
    requested = segment.get("requested_media_spec") if isinstance(segment.get("requested_media_spec"), dict) else {}
    if not requested and job:
        requested = {
            "duration_sec": job.get("duration_sec"),
            "fps": job.get("fps"),
        }
    segment["requested_media_spec"] = {key: value for key, value in requested.items() if value is not None}
    segment.setdefault("actual_media_spec", {})
    if not isinstance(segment.get("generation_config"), dict):
        segment["generation_config"] = {}


def normalize_video_jobs_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Upgrade task metadata without altering an existing workflow or result."""
    data = payload if isinstance(payload, dict) else {}
    default_id = str(data.get("default_model_id") or default_model_id()).strip()
    registry = get_model_registry()
    if not registry.has(default_id):
        default_id = default_model_id()

    data["schema_version"] = max(int(data.get("schema_version") or 0), VIDEO_JOBS_SCHEMA_VERSION)
    data["default_model_id"] = default_id
    data.setdefault("generation_mode", "model_factory_segmented")
    for collection in ("segments", "retired_segments"):
        segments = data.get(collection) if isinstance(data.get(collection), list) else []
        data[collection] = segments
        for segment in segments:
            if isinstance(segment, dict):
                _normalize_segment(segment, payload=data, default_id=default_id, registry=registry)

    # Renders are immutable output provenance.  The top-level fields remain a
    # compatibility projection for LTX/UI code while migration is gradual.
    from generation.render_history import ensure_render_history

    ensure_render_history(data, default_id)
    return data


def resolved_model_id(segment: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    resolved = segment.get("resolved_model") if isinstance(segment.get("resolved_model"), dict) else {}
    return str(resolved.get("id") or segment.get("model_id") or segment.get("model_override") or (payload or {}).get("default_model_id") or default_model_id()).strip()
