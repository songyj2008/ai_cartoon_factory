"""Resolve project bible roles and scenes to concrete asset image paths."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from asset_index import load_asset_index
from services.context import ASSETS_DIR, BASE_DIR, PROJECTS_DIR, get_current_project_name
from services.project_bible import load_project_bible


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _image_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=lambda p: ("5shitu" not in p.name.lower(), len(str(p)), str(p).lower()))


def _abs_asset_path(path_text: str) -> Path | None:
    text = str(path_text or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _image_path(image: dict[str, Any]) -> Path | None:
    path = _abs_asset_path(image.get("relative_path") or image.get("path"))
    return path if path and path.exists() else None


def identity_image_options(identity: dict[str, Any]) -> list[dict[str, Any]]:
    """Return usable image variants for one identity, with the primary image first."""
    primary = identity.get("primary_reference") if isinstance(identity.get("primary_reference"), dict) else {}
    primary_path = _image_path(primary)
    primary_id = str(primary.get("id") or (primary_path.stem if primary_path else "")).strip()
    options: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for image in identity.get("images") or []:
        if not isinstance(image, dict):
            continue
        path = _image_path(image)
        if not path:
            continue
        path_text = str(path)
        if path_text in seen_paths:
            continue
        seen_paths.add(path_text)
        image_id = str(image.get("id") or path.stem).strip()
        options.append(
            {
                "id": image_id,
                "display_name": str(
                    image.get("display_name")
                    or image.get("name_cn")
                    or identity.get("display_name")
                    or identity.get("name_cn")
                    or identity.get("name")
                    or image_id
                ).strip(),
                "image_path": path_text,
                "available": True,
                "is_primary": bool(
                    (primary_id and image_id == primary_id)
                    or (primary_path and path == primary_path)
                ),
                "aliases": _as_text_list(image.get("aliases")),
                "tags_cn": _as_text_list(image.get("tags_cn")),
                "description_cn": str(image.get("description_cn") or "").strip(),
                "use_for": _as_text_list(image.get("use_for")),
                "pose": str(image.get("pose") or "").strip(),
            }
        )

    if primary_path and str(primary_path) not in seen_paths:
        options.insert(
            0,
            {
                "id": primary_id or primary_path.stem,
                "display_name": str(
                    primary.get("display_name")
                    or primary.get("name_cn")
                    or identity.get("display_name")
                    or identity.get("name_cn")
                    or identity.get("name")
                    or primary_id
                    or primary_path.stem
                ).strip(),
                "image_path": str(primary_path),
                "available": True,
                "is_primary": True,
                "aliases": _as_text_list(primary.get("aliases")),
                "tags_cn": _as_text_list(primary.get("tags_cn")),
                "description_cn": str(primary.get("description_cn") or "").strip(),
                "use_for": _as_text_list(primary.get("use_for")),
                "pose": str(primary.get("pose") or "").strip(),
            },
        )

    if options and not any(option.get("is_primary") for option in options):
        options[0]["is_primary"] = True
    options.sort(key=lambda option: (not bool(option.get("is_primary")), str(option.get("id") or "").lower()))
    return options


def primary_image_id_for_identity(identity: dict[str, Any]) -> str:
    options = identity_image_options(identity)
    return str(options[0].get("id") or "") if options else ""


def _identity_image_option(identity: dict[str, Any], image_id: str = "") -> dict[str, Any] | None:
    options = identity_image_options(identity)
    requested = str(image_id or "").strip()
    if requested:
        for option in options:
            if str(option.get("id") or "") == requested:
                return option
    return options[0] if options else None


def _identity_primary_path(identity: dict[str, Any]) -> Path | None:
    option = _identity_image_option(identity)
    path = _abs_asset_path(str((option or {}).get("image_path") or ""))
    return path if path and path.exists() else None


def _background_path(bg: dict[str, Any]) -> Path | None:
    path = _abs_asset_path(bg.get("relative_path") or bg.get("path"))
    return path if path and path.exists() else None


def _asset_context() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    bible = load_project_bible()
    asset_index = load_asset_index()
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    backgrounds = asset_index.get("backgrounds") or []
    identity_by_id = {str(item.get("id") or ""): item for item in identities if isinstance(item, dict)}
    background_by_id = {str(item.get("id") or ""): item for item in backgrounds if isinstance(item, dict)}
    return bible, identity_by_id, background_by_id


def selected_project_materials() -> dict[str, list[dict[str, Any]]]:
    """Return only the character/background materials selected by the active project."""
    bible, identity_by_id, background_by_id = _asset_context()
    characters: list[dict[str, Any]] = []
    backgrounds: list[dict[str, Any]] = []

    character_entries = list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
    for entry in character_entries:
        if not isinstance(entry, dict):
            continue
        role_id = str(entry.get("id") or "").strip()
        asset_id = str(entry.get("asset_identity_id") or "").strip()
        if not role_id or not asset_id:
            continue
        identity = identity_by_id.get(asset_id) or {}
        image_options = identity_image_options(identity) if identity else []
        image_path = _identity_primary_path(identity) if identity else None
        characters.append(
            {
                "id": role_id,
                "asset_id": asset_id,
                "display_name": str(
                    entry.get("display_name")
                    or entry.get("name_cn")
                    or identity.get("display_name")
                    or identity.get("name_cn")
                    or identity.get("name")
                    or asset_id
                ).strip(),
                "image_path": str(image_path) if image_path else "",
                "available": bool(image_path),
                "image_count": len(image_options),
                "images": image_options,
                "subject_description": str(identity.get("description_cn") or "").strip(),
            }
        )

    for entry in bible.get("allowed_backgrounds") or []:
        if not isinstance(entry, dict):
            continue
        scene_id = str(entry.get("id") or "").strip()
        asset_id = str(entry.get("asset_background_id") or "").strip()
        if not scene_id or not asset_id:
            continue
        background = background_by_id.get(asset_id) or {}
        image_path = _background_path(background) if background else None
        backgrounds.append(
            {
                "id": scene_id,
                "asset_id": asset_id,
                "display_name": str(
                    entry.get("display_name")
                    or entry.get("name_cn")
                    or background.get("display_name")
                    or background.get("name_cn")
                    or background.get("name")
                    or asset_id
                ).strip(),
                "image_path": str(image_path) if image_path else "",
                "available": bool(image_path),
                "description_cn": str(background.get("description_cn") or "").strip(),
            }
        )

    return {"characters": characters, "backgrounds": backgrounds}


def _entry_names(entry: dict[str, Any]) -> set[str]:
    names = {
        str(entry.get("id") or "").strip(),
        str(entry.get("display_name") or "").strip(),
        str(entry.get("name") or "").strip(),
        str(entry.get("name_cn") or "").strip(),
        str(entry.get("asset_identity_id") or "").strip(),
        str(entry.get("asset_background_id") or "").strip(),
    }
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = [aliases]
    names.update(str(alias or "").strip() for alias in aliases)
    return {name for name in names if name}


def _character_entry_for_role(role: str, bible: dict[str, Any]) -> dict[str, Any] | None:
    role_text = str(role or "").strip()
    if not role_text:
        return None
    entries = list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
    for entry in entries:
        if isinstance(entry, dict) and role_text in _entry_names(entry):
            return entry
    return None


def _background_entry_for_scene(scene_id: str, bible: dict[str, Any]) -> dict[str, Any] | None:
    scene_text = str(scene_id or "").strip()
    if not scene_text:
        return None
    for entry in bible.get("allowed_backgrounds") or []:
        if isinstance(entry, dict) and scene_text in _entry_names(entry):
            return entry
    return None


def _selected_asset_images(limit: int = 4) -> tuple[list[str], str | None]:
    bible, identity_by_id, background_by_id = _asset_context()
    reference_paths: list[str] = []
    for item in (bible.get("core_characters") or []) + (bible.get("supporting_characters") or []):
        asset_id = str((item or {}).get("asset_identity_id") or "")
        identity = identity_by_id.get(asset_id)
        if not identity:
            continue
        path = _identity_primary_path(identity)
        if path:
            reference_paths.append(str(path))
        if len(reference_paths) >= limit:
            break

    background_path = None
    for item in bible.get("allowed_backgrounds") or []:
        bg_id = str((item or {}).get("asset_background_id") or "")
        bg = background_by_id.get(bg_id)
        if not bg:
            continue
        path = _background_path(bg)
        if path:
            background_path = str(path)
            break

    return reference_paths, background_path


def default_reference_images(limit: int = 4) -> list[str]:
    selected_refs, _ = _selected_asset_images(limit=limit)
    if selected_refs:
        return selected_refs[:limit]
    files = _image_files(ASSETS_DIR / "characters")
    selected: list[Path] = []
    seen_parent: set[Path] = set()
    for file in files:
        if file.parent in seen_parent and len(files) > limit:
            continue
        selected.append(file)
        seen_parent.add(file.parent)
        if len(selected) >= limit:
            break
    if not selected:
        raise FileNotFoundError(f"no character/object reference images found under {ASSETS_DIR / 'characters'}")
    return [str(p.resolve()) for p in selected]


def default_background_image() -> str:
    _, selected_bg = _selected_asset_images(limit=1)
    if selected_bg:
        return selected_bg
    background_root = ASSETS_DIR / "backgrounds"
    preferred = background_root / "boss_office_day.png"
    if preferred.exists():
        return str(preferred.resolve())
    files = _image_files(background_root)
    if not files:
        raise FileNotFoundError(f"no background image found under {background_root}")
    return str(files[0].resolve())


def _identity_description_for_role(role: str, entry: dict[str, Any], identity: dict[str, Any]) -> str:
    description = str(identity.get("description_cn") or "").strip()
    features = identity.get("identity_features") if isinstance(identity.get("identity_features"), dict) else {}
    display = str(entry.get("display_name") or entry.get("name_cn") or role or identity.get("name_cn") or identity.get("name") or "").strip()
    if description:
        return f"{display}：{description}"
    parts: list[str] = []
    compact_fields = (
        ("age_range", 10),
        ("face", 14),
        ("hair", 14),
        ("clothing", 18),
        ("body", 14),
    )
    for key, limit in compact_fields:
        value = str(features.get(key) or "").strip().replace("；", "，")
        if value:
            parts.append(value[:limit])
    return f"{display}：" + "，".join(parts[:5]) + "。" if parts else f"{display}：参考对应人物图，保持同脸、同发型、同服装、同体型。"


def character_prompt_descriptions_for_roles(
    reference_roles: list[str],
    reference_image_ids: dict[str, str] | None = None,
) -> list[str]:
    bible, identity_by_id, _background_by_id = _asset_context()
    descriptions: list[str] = []
    for role in reference_roles or []:
        role_id = str(role)
        entry = _character_entry_for_role(role_id, bible)
        if not entry:
            continue
        identity = identity_by_id.get(str(entry.get("asset_identity_id") or ""))
        if not identity:
            continue
        # Reference images are bound to the workflow separately.  Keep this
        # prompt section focused on each character's identity, without exposing
        # an image id, sheet pose, or image-specific reference description.
        descriptions.append(_identity_description_for_role(role_id, entry, identity))
    return descriptions


def load_global_prompt() -> str:
    project_name = str(get_current_project_name() or "current").strip() or "current"
    path = PROJECTS_DIR / project_name / "global_prompt"
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8-sig").strip()
    except Exception:
        return ""
    return ""


def prompt_with_character_descriptions(
    prompt: str,
    reference_roles: list[str],
    reference_image_ids: dict[str, str] | None = None,
) -> str:
    base_prompt = str(prompt or "").strip()
    blocks: list[str] = []
    global_prompt = load_global_prompt()
    if global_prompt:
        blocks.append("【项目全局风格 / Global Prompt】\n" + global_prompt)
    descriptions = character_prompt_descriptions_for_roles(reference_roles, reference_image_ids)
    if descriptions:
        blocks.append("【人物身份锁定】\n\n" + "\n\n".join(descriptions))
    if len([role for role in reference_roles or [] if str(role or "").strip()]) > 1:
        blocks.append(
            "【多角色身份隔离】\n"
            "每个角色必须独立保持自己的脸型、发型、服装、颜色和号码；"
            "不得把任一角色的发型、服装、号码或身体特征复制、交换或混合到其他角色。"
        )
    if base_prompt:
        blocks.append(base_prompt)
    return "\n\n".join(blocks).strip()


def global_prompt_status() -> dict[str, str]:
    project_name = str(get_current_project_name() or "current").strip() or "current"
    path = PROJECTS_DIR / project_name / "global_prompt"
    try:
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8-sig").strip()
            if text:
                return {"path": str(path), "text": text}
    except Exception:
        return {"path": "", "text": ""}
    return {"path": "", "text": ""}


def reference_images_for_roles(
    reference_roles: list[str],
    reference_image_ids: dict[str, str] | None = None,
    limit: int = 4,
) -> list[str]:
    if not [role for role in reference_roles or [] if str(role or "").strip()]:
        return []
    bible, identity_by_id, _background_by_id = _asset_context()
    reference_paths: list[str] = []
    image_ids = reference_image_ids if isinstance(reference_image_ids, dict) else {}
    for role in reference_roles or []:
        role_id = str(role)
        entry = _character_entry_for_role(role_id, bible)
        if not entry:
            continue
        identity = identity_by_id.get(str(entry.get("asset_identity_id") or ""))
        if not identity:
            continue
        option = _identity_image_option(identity, str(image_ids.get(role_id) or ""))
        path = _abs_asset_path(str((option or {}).get("image_path") or ""))
        if path and path.exists() and str(path) not in reference_paths:
            reference_paths.append(str(path))
        if len(reference_paths) >= limit:
            break
    if reference_paths:
        return reference_paths[:limit]
    return default_reference_images(limit=limit)


def background_image_for_scene(scene_id: str) -> str:
    bible, _identity_by_id, background_by_id = _asset_context()
    entry = _background_entry_for_scene(scene_id, bible)
    if entry:
        bg = background_by_id.get(str(entry.get("asset_background_id") or ""))
        if bg:
            path = _background_path(bg)
            if path:
                return str(path)
    return default_background_image()
