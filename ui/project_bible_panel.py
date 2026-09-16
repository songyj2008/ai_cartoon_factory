"""Project Bible and selected asset UI handlers."""
import traceback
import copy
from pathlib import Path
import gradio as gr
from asset_index import load_asset_index
from services.context import BASE_DIR, assert_current_runtime_writable
from services.logger import log, get_logs
from services.project_bible import load_project_bible, save_project_bible


def _asset_display_name(asset):
    aliases = asset.get("aliases") if isinstance(asset.get("aliases"), list) else []
    return str(
        asset.get("display_name")
        or asset.get("name_cn")
        or asset.get("name")
        or (aliases[0] if aliases else "")
        or asset.get("id")
        or ""
    ).strip()


def _asset_aliases(asset, display_name):
    aliases = asset.get("aliases") if isinstance(asset.get("aliases"), list) else []
    return sorted({str(value).strip() for value in [display_name, asset.get("id")] + aliases if str(value).strip()})


def _next_stable_id(prefix, entries):
    used = {str(item.get("id") or "") for item in entries if isinstance(item, dict)}
    number = 1
    while f"{prefix}_{number:03d}" in used:
        number += 1
    return f"{prefix}_{number:03d}"


def _stable_project_bible(topic_text, identities, backgrounds):
    """Keep established role/scene ids when the selected asset set changes.

    Beats and LiconMSR segment bindings use these ids. Rebuilding them from
    sorted asset-index order makes a newly uploaded asset silently take over a
    pre-existing role slot.
    """
    previous = load_project_bible() or {}
    old_characters = [
        item for item in list(previous.get("core_characters") or []) + list(previous.get("supporting_characters") or [])
        if isinstance(item, dict)
    ]
    old_backgrounds = [item for item in previous.get("allowed_backgrounds") or [] if isinstance(item, dict)]
    old_character_by_asset = {str(item.get("asset_identity_id") or ""): item for item in old_characters}
    old_background_by_asset = {str(item.get("asset_background_id") or ""): item for item in old_backgrounds}

    core_characters = []
    for identity in identities:
        asset_id = str(identity.get("id") or "").strip()
        if not asset_id:
            continue
        display_name = _asset_display_name(identity)
        entry = copy.deepcopy(old_character_by_asset.get(asset_id) or {})
        if not entry:
            entry = {
                "id": _next_stable_id("character", old_characters + core_characters),
                "role": "protagonist" if not core_characters else "core",
                "locked": True,
                "need_reference_image": True,
            }
        entry.update({
            "display_name": display_name,
            "asset_identity_id": asset_id,
            "aliases": _asset_aliases(identity, display_name),
            "locked": True,
            "need_reference_image": True,
        })
        core_characters.append(entry)

    allowed_backgrounds = []
    for background in backgrounds:
        asset_id = str(background.get("id") or "").strip()
        if not asset_id:
            continue
        display_name = _asset_display_name(background)
        entry = copy.deepcopy(old_background_by_asset.get(asset_id) or {})
        if not entry:
            entry = {
                "id": _next_stable_id("scene", old_backgrounds + allowed_backgrounds),
                "locked": True,
                "need_reference_image": True,
            }
        entry.update({
            "display_name": display_name,
            "asset_background_id": asset_id,
            "aliases": _asset_aliases(background, display_name),
            "locked": True,
            "need_reference_image": True,
        })
        allowed_backgrounds.append(entry)

    return {
        **previous,
        "topic": str(topic_text or previous.get("topic") or ""),
        "core_characters": core_characters,
        "supporting_characters": [],
        "allowed_backgrounds": allowed_backgrounds,
        "extras_policy": previous.get("extras_policy") or {
            "allow_background_extras": True,
            "need_reference_image": False,
            "can_speak": False,
            "can_drive_plot": False,
        },
        "strict_asset_mode": True,
    }

def _asset_abs_path(path_text):
    if not str(path_text or "").strip():
        return None
    path = Path(str(path_text or ""))
    if not path.is_absolute():
        path = BASE_DIR / path
    return path

def selected_project_asset_gallery(character_ids=None, background_ids=None):
    character_ids = set(str(x) for x in (character_ids or []) if str(x))
    background_ids = set(str(x) for x in (background_ids or []) if str(x))
    asset_index = load_asset_index()
    images = []

    for identity in ((asset_index.get("characters") or {}).get("identities") or []):
        if not isinstance(identity, dict) or str(identity.get("id") or "") not in character_ids:
            continue
        aliases = identity.get("aliases") if isinstance(identity.get("aliases"), list) else []
        label = str(
            identity.get("display_name")
            or identity.get("name_cn")
            or identity.get("name")
            or (aliases[0] if aliases else "")
            or identity.get("id")
            or ""
        )
        seen_paths = set()
        primary = identity.get("primary_reference") if isinstance(identity.get("primary_reference"), dict) else {}
        candidate_images = [primary] if primary else []
        candidate_images.extend(image for image in identity.get("images") or [] if isinstance(image, dict))
        for image in candidate_images:
            if not isinstance(image, dict):
                continue
            path = _asset_abs_path(image.get("path") or image.get("relative_path"))
            if path and path.exists() and str(path) not in seen_paths:
                seen_paths.add(str(path))
                title = f"角色：{label} / {image.get('pose') or image.get('id') or path.stem}"
                images.append((str(path), title))

    for bg in asset_index.get("backgrounds") or []:
        if not isinstance(bg, dict) or str(bg.get("id") or "") not in background_ids:
            continue
        path = _asset_abs_path(bg.get("path") or bg.get("relative_path"))
        if path and path.exists():
            images.append((str(path), f"背景：{_asset_choice_label(bg)}"))

    return images

def project_bible_selected_asset_ids():
    bible = load_project_bible()
    character_ids = [
        str(item.get("asset_identity_id") or "")
        for item in (bible.get("core_characters") or [])
        if item.get("asset_identity_id")
    ]
    background_ids = [
        str(item.get("asset_background_id") or "")
        for item in (bible.get("allowed_backgrounds") or [])
        if item.get("asset_background_id")
    ]
    return character_ids, background_ids

def project_bible_ui_state():
    character_choices, background_choices = project_bible_asset_choices()
    character_ids, background_ids = project_bible_selected_asset_ids()
    valid_characters = {str(value) for _, value in character_choices}
    valid_backgrounds = {str(value) for _, value in background_choices}
    character_ids = [x for x in character_ids if x in valid_characters]
    background_ids = [x for x in background_ids if x in valid_backgrounds]
    return (
        gr.update(choices=character_choices, value=character_ids),
        gr.update(choices=background_choices, value=background_ids),
        selected_project_asset_gallery(character_ids, background_ids),
    )

def _asset_choice_label(asset):
    aliases = asset.get("aliases") if isinstance(asset.get("aliases"), list) else []
    display = str(asset.get("display_name") or asset.get("name_cn") or asset.get("name") or (aliases[0] if aliases else "") or asset.get("id") or "")
    asset_id = str(asset.get("id") or "")
    label = f"{display} ({asset_id})" if display and display != asset_id else asset_id
    image_count = len([image for image in asset.get("images") or [] if isinstance(image, dict)])
    return f"{label} · {image_count}张参考图" if image_count else label

def project_bible_asset_choices():
    asset_index = load_asset_index()
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    backgrounds = asset_index.get("backgrounds") or []
    character_choices = [
        (_asset_choice_label(item), str(item.get("id")))
        for item in identities
        if isinstance(item, dict) and item.get("id")
    ]
    background_choices = [
        (_asset_choice_label(item), str(item.get("id")))
        for item in backgrounds
        if isinstance(item, dict) and item.get("id")
    ]
    return character_choices, background_choices

def manual_plan_project_bible(topic_text, character_ids, background_ids):
    try:
        assert_current_runtime_writable("保存项目素材")
        character_ids = set(str(x) for x in (character_ids or []) if str(x))
        background_ids = set(str(x) for x in (background_ids or []) if str(x))
        asset_index = load_asset_index()
        identities = [
            item for item in ((asset_index.get("characters") or {}).get("identities") or [])
            if isinstance(item, dict) and str(item.get("id") or "") in character_ids
        ]
        backgrounds = [
            item for item in (asset_index.get("backgrounds") or [])
            if isinstance(item, dict) and str(item.get("id") or "") in background_ids
        ]
        if not identities:
            raise ValueError("请至少选择一个核心角色素材")
        if not backgrounds:
            raise ValueError("请至少选择一个主要场景素材")
        bible = _stable_project_bible(topic_text, identities, backgrounds)
        bible["extras_policy"]["allow_background_extras"] = True
        save_project_bible(bible)
        log("已保存已选人物场景")
        char_update, bg_update, gallery = project_bible_ui_state()
        return get_logs(), gallery, char_update, bg_update
    except Exception as e:
        traceback.print_exc()
        log(f"保存已选人物场景失败: {e}", "ERROR")
        char_update, bg_update, gallery = project_bible_ui_state()
        return get_logs(), gallery, char_update, bg_update
