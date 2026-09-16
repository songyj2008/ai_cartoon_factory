"""Auto-populate H3 asset_manifest from beat auto_asset_match data.

The matcher (workflow/asset_matcher.py) already scores characters and
backgrounds against beat text.  This module converts that metadata into
the H3 ``generation_config.asset_manifest`` shape so the fullscreen editor
shows bound images and the prompt generator can reference them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from asset_index import load_asset_index
from services.project_bible import load_project_bible

_logger = logging.getLogger(__name__)


def _binding_source(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    # ``source=segment`` only describes where the editor persisted the item;
    # metadata records whether it was originally created by auto-binding.
    return str(metadata.get("binding_source") or item.get("source") or "").strip()


def _is_legacy_auto_background(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        not _binding_source(item)
        and str(metadata.get("asset_kind") or "").strip().lower() == "background"
        and bool(metadata.get("shot_indices"))
    )


def enrich_asset_manifest_metadata(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh registered character references and backfill their metadata.

    Older jobs may still point at a registered image variant that has since
    been deleted.  When the manifest retains the character ``identity_id``, use
    that stable identity to select its current primary image before validation.
    Unknown/manual files remain untouched so a genuinely missing source still
    produces the normal validation error.
    """
    asset_index = load_asset_index()
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    from workflow.asset_resolver import identity_image_options

    identity_by_id: dict[str, dict[str, Any]] = {}
    identity_by_path: dict[str, tuple[dict[str, Any], str]] = {}
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        identity_id = str(identity.get("id") or "").strip()
        if identity_id:
            identity_by_id[identity_id] = identity
        for option in identity_image_options(identity):
            path = str(option.get("image_path") or "").strip()
            if path:
                identity_by_path[_normalized_path(path)] = (identity, str(option.get("id") or ""))

    enriched: list[dict[str, Any]] = []
    for raw in manifest:
        item = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(item, dict) or str(item.get("type") or "").strip().lower() != "image":
            enriched.append(item)
            continue
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        existing_path = str(item.get("path") or "").strip()
        asset_kind = str(metadata.get("asset_kind") or "").strip().lower()
        identity_id = str(metadata.get("identity_id") or "").strip()
        if existing_path and not _safe_path(existing_path) and asset_kind == "character" and identity_id:
            identity = identity_by_id.get(identity_id)
            if identity:
                replacement_path = _resolve_identity_image_path(identity, str(metadata.get("image_id") or ""))
                if replacement_path:
                    item["path"] = replacement_path
                    metadata.update(
                        _character_metadata(
                            identity,
                            str(metadata.get("image_id") or ""),
                            replacement_path,
                        )
                    )
                    item["metadata"] = metadata
                    _logger.info(
                        "refreshed deleted H3 character reference %s to %s",
                        existing_path,
                        replacement_path,
                    )
        match = identity_by_path.get(_normalized_path(str(item.get("path") or "")))
        if match:
            identity, image_id = match
            metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
            generated = _character_metadata(identity, image_id, str(item.get("path") or ""))
            for key, value in generated.items():
                metadata.setdefault(key, value)
            item["metadata"] = metadata
        enriched.append(item)
    return enriched


def merge_auto_backgrounds(
    existing_manifest: list[dict[str, Any]],
    auto_manifest: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Refresh automatic bindings without duplicating configured semantics.

    A selected character identity or background is authoritative even when an
    automatic matcher finds a different source image for the same identity or
    another scene candidate. Automatic assets fill missing semantics only.
    """
    auto_sources = {"auto_background_match", "auto_character_match"}
    merged = [
        dict(item)
        for item in existing_manifest
        if isinstance(item, dict)
        and _binding_source(item) not in auto_sources
        and not _is_legacy_auto_background(item)
    ]
    seen_paths = {_normalized_path(str(item.get("path") or "")) for item in merged}
    seen_identities = {
        str((item.get("metadata") or {}).get("identity_id") or "").strip()
        for item in merged
        if isinstance(item.get("metadata"), dict)
        and str((item.get("metadata") or {}).get("asset_kind") or "").strip().lower() == "character"
        and str((item.get("metadata") or {}).get("identity_id") or "").strip()
    }
    seen_backgrounds = {
        str((item.get("metadata") or {}).get("background_id") or "")
        for item in merged
        if isinstance(item.get("metadata"), dict)
    }
    has_configured_background = any(
        isinstance(item.get("metadata"), dict)
        and str((item.get("metadata") or {}).get("asset_kind") or "").strip().lower() == "background"
        for item in merged
    )
    image_count = sum(1 for item in merged if str(item.get("type") or "").lower() == "image")
    for item in auto_manifest or []:
        if not isinstance(item, dict) or _binding_source(item) not in auto_sources:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        asset_kind = str(metadata.get("asset_kind") or "").strip().lower()
        identity_id = str(metadata.get("identity_id") or "").strip()
        background_id = str(metadata.get("background_id") or "")
        path_key = _normalized_path(str(item.get("path") or ""))
        if image_count >= 9 or len(merged) >= 12:
            break
        if (
            (path_key and path_key in seen_paths)
            or (identity_id and identity_id in seen_identities)
            or (background_id and background_id in seen_backgrounds)
            or (asset_kind == "background" and has_configured_background)
        ):
            continue
        copy_item = dict(item)
        copy_item["order"] = len(merged) + 1
        merged.append(copy_item)
        image_count += 1
        seen_paths.add(path_key)
        if identity_id:
            seen_identities.add(identity_id)
        seen_backgrounds.add(background_id)
    return merged


def auto_bind_asset_manifest(beat: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Build H3 asset_manifest entries from a beat's auto_asset_match.

    Returns a list of manifest dicts (characters first, background last),
    or ``None`` when no assets could be resolved.
    """
    auto = beat.get("auto_asset_match") if isinstance(beat, dict) else None
    if not isinstance(auto, dict):
        return None

    bible = load_project_bible()
    asset_index = load_asset_index()
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    identity_by_id: dict[str, dict[str, Any]] = {
        str(item.get("id") or ""): item
        for item in identities
        if isinstance(item, dict)
    }
    backgrounds = asset_index.get("backgrounds") or []
    background_by_id: dict[str, dict[str, Any]] = {
        str(item.get("id") or ""): item
        for item in backgrounds
        if isinstance(item, dict)
    }

    manifest: list[dict[str, Any]] = []
    order = 0

    # --- Characters ---
    # Prefer character_images (has per-role image_id), fall back to characters list.
    char_images = auto.get("character_images")
    if isinstance(char_images, list) and char_images:
        for entry in char_images:
            if not isinstance(entry, dict):
                continue
            item = _resolve_character_image(entry, identity_by_id)
            if item is None:
                continue
            order += 1
            item["order"] = order
            manifest.append(item)

    if not manifest:
        # Fallback: characters list without per-image selection.
        chars = auto.get("characters")
        if isinstance(chars, list):
            for entry in chars:
                if not isinstance(entry, dict):
                    continue
                item = _resolve_character_fallback(entry, identity_by_id)
                if item is None:
                    continue
                order += 1
                item["order"] = order
                manifest.append(item)

    # --- Shot backgrounds ---
    # New H3 matches can bind several scenes in chronological shot order. Old
    # single-background records remain readable, but zero-score fallback
    # records are deliberately ignored: H3 has no required/default backdrop.
    raw_backgrounds = auto.get("backgrounds")
    background_candidates = (
        [item for item in raw_backgrounds if isinstance(item, dict) and _is_real_background_match(item)]
        if isinstance(raw_backgrounds, list)
        else []
    )
    if not background_candidates:
        legacy = auto.get("background")
        if isinstance(legacy, dict) and _is_real_background_match(legacy):
            background_candidates = [legacy]
    seen_backgrounds: set[str] = set()
    image_slots_left = max(0, 9 - sum(1 for item in manifest if item.get("type") == "image"))
    for bg_info in background_candidates:
        background_id = str(bg_info.get("asset_background_id") or "").strip()
        if not background_id or background_id in seen_backgrounds or image_slots_left <= 0:
            continue
        bg_item = _resolve_background(bg_info, bible, background_by_id)
        if bg_item is None:
            continue
        seen_backgrounds.add(background_id)
        order += 1
        image_slots_left -= 1
        bg_item["order"] = order
        manifest.append(bg_item)

    return manifest if manifest else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_character_image(
    entry: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a single auto_asset_match.character_images entry."""
    identity_id = str(entry.get("asset_identity_id") or "").strip()
    identity = identity_by_id.get(identity_id)
    if not identity:
        _logger.debug("auto_binder: identity %s not found in asset index", identity_id)
        return None

    display_name = str(entry.get("display_name") or identity.get("display_name") or identity.get("name_cn") or identity_id)

    # Pick the requested image variant or the primary one.
    requested_image_id = str(entry.get("image_id") or "").strip()
    image_path = _resolve_identity_image_path(identity, requested_image_id)
    if not image_path:
        return None

    asset_id = _make_asset_id("char", identity_id, Path(image_path).stem)
    metadata = {
        **_character_metadata(identity, requested_image_id, image_path),
        "binding_source": "auto_character_match",
    }
    # Keep the Project Bible role id so the compiler can map a Beat written
    # with ``character_004``-style speaker ids back to this Subject.
    role_id = str(entry.get("role_id") or "").strip()
    if role_id:
        metadata["role_id"] = role_id
    return {
        "asset_id": asset_id,
        "type": "image",
        "path": image_path,
        "role": display_name,
        "label": display_name,
        "source": "auto_character_match",
        "metadata": metadata,
    }


def _resolve_character_fallback(
    entry: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve from auto_asset_match.characters (no per-image selection)."""
    identity_id = str(entry.get("asset_identity_id") or "").strip()
    identity = identity_by_id.get(identity_id)
    if not identity:
        return None
    display_name = str(entry.get("display_name") or identity.get("display_name") or identity.get("name_cn") or identity_id)
    image_path = _primary_identity_path(identity)
    if not image_path:
        return None
    asset_id = _make_asset_id("char", identity_id, Path(image_path).stem)
    metadata = {
        **_character_metadata(identity, "", image_path),
        "binding_source": "auto_character_match",
    }
    role_id = str(entry.get("role_id") or "").strip()
    if role_id:
        metadata["role_id"] = role_id
    return {
        "asset_id": asset_id,
        "type": "image",
        "path": image_path,
        "role": display_name,
        "label": display_name,
        "source": "auto_character_match",
        "metadata": metadata,
    }


def _resolve_background(
    bg_info: dict[str, Any],
    bible: dict[str, Any],
    background_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve auto_asset_match.background into a manifest entry."""
    scene_id = str(bg_info.get("scene_id") or "").strip()
    asset_bg_id = str(bg_info.get("asset_background_id") or "").strip()
    display_name = str(bg_info.get("display_name") or scene_id or asset_bg_id)

    bg = background_by_id.get(asset_bg_id)
    if not bg:
        _logger.debug("auto_binder: background %s not found in asset index", asset_bg_id)
        return None

    path = _resolve_background_path(bg)
    if not path:
        return None

    asset_id = _make_asset_id("bg", asset_bg_id or scene_id, Path(path).stem)
    return {
        "asset_id": asset_id,
        "type": "image",
        "path": path,
        "role": display_name,
        "label": display_name,
        "source": "auto_background_match",
        "metadata": {
            "asset_kind": "background",
            "binding_source": "auto_background_match",
            "background_id": asset_bg_id,
            "scene_id": scene_id,
            "shot_indices": [int(value) for value in bg_info.get("shot_indices") or [] if str(value).isdigit()],
            "description_cn": str(bg.get("description_cn") or "").strip(),
        },
    }


def _is_real_background_match(value: dict[str, Any]) -> bool:
    try:
        if float(value.get("score") or 0) >= 60:
            return True
    except (TypeError, ValueError):
        pass
    reasons = [str(item or "") for item in value.get("reason") or []]
    return any(reason.startswith(("explicit:", "manual:")) for reason in reasons)


def _resolve_identity_image_path(identity: dict[str, Any], requested_image_id: str = "") -> str | None:
    """Return the absolute path for the best image variant of an identity."""
    # Re-use the same resolution logic as workflow/asset_resolver.
    from workflow.asset_resolver import identity_image_options

    options = identity_image_options(identity)
    if not options:
        return None
    requested = str(requested_image_id or "").strip()
    if requested:
        for option in options:
            if str(option.get("id") or "") == requested:
                path = _safe_path(option.get("image_path"))
                if path:
                    return path
    # Fall back to primary or first available.
    for option in options:
        path = _safe_path(option.get("image_path"))
        if path:
            return path
    return None


def _primary_identity_path(identity: dict[str, Any]) -> str | None:
    from workflow.asset_resolver import identity_image_options

    options = identity_image_options(identity)
    for option in options:
        path = _safe_path(option.get("image_path"))
        if path:
            return path
    return None


def _character_metadata(
    identity: dict[str, Any],
    requested_image_id: str,
    selected_path: str,
) -> dict[str, Any]:
    """Persist the selected character's stable prompt description.

    Identity-level copy is preferred because image-level copy may describe a
    contact sheet, pose or source-image background.  The selected image copy is
    retained as a fallback for older assets that do not yet have a useful
    Chinese identity description.
    """
    from workflow.asset_resolver import identity_image_options

    selected: dict[str, Any] = {}
    requested = str(requested_image_id or "").strip()
    selected_norm = str(Path(selected_path).resolve()).lower()
    for option in identity_image_options(identity):
        option_path = str(option.get("image_path") or "").strip()
        if (requested and str(option.get("id") or "") == requested) or (
            option_path and str(Path(option_path).resolve()).lower() == selected_norm
        ):
            selected = option
            break

    identity_description = str(identity.get("description_cn") or "").strip()
    image_description = str(selected.get("description_cn") or "").strip()
    # Generated legacy placeholders such as "elon musk character identity."
    # are not suitable for the requested Chinese H3 prompt.
    subject_description = identity_description if _contains_cjk(identity_description) else image_description
    if not subject_description:
        subject_description = identity_description or image_description

    metadata: dict[str, Any] = {
        "asset_kind": "character",
        "identity_id": str(identity.get("id") or "").strip(),
        "aliases": [str(value).strip() for value in identity.get("aliases") or [] if str(value).strip()],
    }
    if subject_description:
        metadata["subject_description"] = subject_description
    if image_description:
        metadata["image_description"] = image_description
    if selected.get("id"):
        metadata["image_id"] = str(selected["id"])
    return metadata


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(value or ""))


def _resolve_background_path(bg: dict[str, Any]) -> str | None:
    from services.context import BASE_DIR

    rel = str(bg.get("relative_path") or bg.get("path") or "").strip()
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = (BASE_DIR / rel).resolve()
    return str(path) if path.exists() else None


def _safe_path(path_text: str | None) -> str | None:
    if not path_text:
        return None
    p = Path(path_text)
    return str(p) if p.exists() else None


def _make_asset_id(prefix: str, key: str, stem: str) -> str:
    safe = str(key or "unknown").strip().replace(" ", "_")
    return f"{prefix}_{safe}_{stem}"[:120]


def _normalized_path(path_text: str) -> str:
    text = str(path_text or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve()).replace("/", "\\").lower()
    except (OSError, ValueError):
        return text.replace("/", "\\").lower()
