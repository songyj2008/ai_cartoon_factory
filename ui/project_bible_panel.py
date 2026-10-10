"""Project Bible and selected asset UI handlers."""
import traceback
import copy
from pathlib import Path
import gradio as gr
from asset_index import load_asset_index
from services.context import BASE_DIR, assert_current_runtime_writable, get_current_project_name
from services.project_assets import asset_project_owners, list_asset_project_names, set_asset_projects
from services.logger import log, get_logs
from services.project_bible import load_project_bible, save_project_bible
from workflow.asset_resolver import identity_image_options


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


def _stable_project_bible(topic_text, identities, backgrounds, project_name=None, preserve_supporting=False):
    """Keep established role/scene ids when the selected asset set changes.

    Beats and LiconMSR segment bindings use these ids. Rebuilding them from
    sorted asset-index order makes a newly uploaded asset silently take over a
    pre-existing role slot.
    """
    previous = load_project_bible(project_name) or {}
    old_characters = [
        item for item in list(previous.get("core_characters") or []) + list(previous.get("supporting_characters") or [])
        if isinstance(item, dict)
    ]
    old_backgrounds = [item for item in previous.get("allowed_backgrounds") or [] if isinstance(item, dict)]
    old_character_by_asset = {str(item.get("asset_identity_id") or ""): item for item in old_characters}
    old_supporting_asset_ids = {
        str(item.get("asset_identity_id") or "")
        for item in (previous.get("supporting_characters") or [])
        if isinstance(item, dict)
    }
    old_background_by_asset = {str(item.get("asset_background_id") or ""): item for item in old_backgrounds}

    core_characters = []
    supporting_characters = []
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
        if preserve_supporting and asset_id in old_supporting_asset_ids:
            supporting_characters.append(entry)
        else:
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
        "supporting_characters": supporting_characters,
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

def project_bible_selected_asset_ids(project_name=None):
    bible = load_project_bible(project_name)
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

def project_bible_ui_state(project_name=None):
    character_choices, background_choices = project_bible_asset_choices(project_name)
    character_ids, background_ids = project_bible_selected_asset_ids(project_name)
    valid_characters = {str(value) for _, value in character_choices}
    valid_backgrounds = {str(value) for _, value in background_choices}
    character_ids = [x for x in character_ids if x in valid_characters]
    background_ids = [x for x in background_ids if x in valid_backgrounds]
    return (
        gr.update(choices=character_choices, value=character_ids, interactive=project_name != UNASSIGNED_PROJECT_FILTER),
        gr.update(choices=background_choices, value=background_ids, interactive=project_name != UNASSIGNED_PROJECT_FILTER),
        selected_project_asset_gallery(character_ids, background_ids),
    )


UNASSIGNED_PROJECT_FILTER = "__unassigned__"


def _asset_management_records(project_name):
    asset_index = load_asset_index()
    owners = asset_project_owners()
    owned, unassigned = [], []
    for identity in ((asset_index.get("characters") or {}).get("identities") or []):
        if not isinstance(identity, dict) or not identity.get("id"):
            continue
        options = identity_image_options(identity)
        if not options:
            continue
        asset_id = str(identity["id"])
        project_names = owners["characters"].get(asset_id, [])
        record = {
            "key": f"character::{asset_id}", "kind": "characters", "id": asset_id,
            "name": _asset_display_name(identity), "image": options[0]["image_path"],
            "detail": f"人物 / 物品 · {len(options)} 张参考图 · {_asset_project_status(project_names)}", "projects": project_names,
        }
        if project_name == UNASSIGNED_PROJECT_FILTER:
            if not project_names:
                unassigned.append(record)
        else:
            if project_name in project_names:
                owned.append(record)
            elif not project_names:
                unassigned.append(record)

    for background in asset_index.get("backgrounds") or []:
        if not isinstance(background, dict) or not background.get("id"):
            continue
        path = _asset_abs_path(background.get("path") or background.get("relative_path"))
        if not path or not path.exists():
            continue
        asset_id = str(background["id"])
        project_names = owners["backgrounds"].get(asset_id, [])
        record = {
            "key": f"background::{asset_id}", "kind": "backgrounds", "id": asset_id,
            "name": _asset_display_name(background), "image": str(path),
            "detail": f"背景素材 · {_asset_project_status(project_names)}", "projects": project_names,
        }
        if project_name == UNASSIGNED_PROJECT_FILTER:
            if not project_names:
                unassigned.append(record)
        else:
            if project_name in project_names:
                owned.append(record)
            elif not project_names:
                unassigned.append(record)
    owned.sort(key=lambda item: item["name"].casefold())
    unassigned.sort(key=lambda item: item["name"].casefold())
    return owned, unassigned


def _asset_project_status(project_names):
    return f"已分配：{', '.join(project_names)}" if project_names else "未分配项目"


def _gallery_items(records):
    return [(item["image"], f'{item["name"]} · {item["detail"]}') for item in records]


def project_asset_management_state(project_name):
    if project_name == UNASSIGNED_PROJECT_FILTER:
        owned, unassigned = _asset_management_records(project_name)
        heading = f"未分配项目素材 · {len(unassigned)} 项"
        topic_update = gr.update(value="", interactive=False)
        owner_value = []
    else:
        owned, unassigned = _asset_management_records(project_name)
        heading = f"{project_name} 的素材 · 已分配 {len(owned)} 项 · 尚未分配给此项目 {len(unassigned)} 项"
        topic_update = gr.update(value=str((load_project_bible(project_name) or {}).get("topic") or ""), interactive=True)
        owner_value = []
    return (
        heading,
        topic_update,
        _gallery_items(owned),
        _gallery_items(unassigned),
        owned,
        unassigned,
        [],
        gr.update(value="请选择右侧素材卡片，可连续多选。"),
        gr.update(choices=[(name, name) for name in list_asset_project_names()], value=owner_value),
    )


def open_project_asset_manager():
    project_names = list_asset_project_names()
    project_name = get_current_project_name() or "current"
    if project_name not in project_names:
        project_names.append(project_name)
    choices = [(name, name) for name in project_names] + [("未分配素材", UNASSIGNED_PROJECT_FILTER)]
    return gr.update(visible=True), gr.update(choices=choices, value=project_name), *project_asset_management_state(project_name)


def _toggle_project_asset(records, owned_records, unassigned_records, selected_keys, evt):
    index = evt.index
    if isinstance(index, (tuple, list)):
        index = index[0] if index else -1
    try:
        key = records[int(index)]["key"]
    except (TypeError, ValueError, IndexError):
        key = ""
    selected = list(dict.fromkeys(str(value) for value in (selected_keys or []) if str(value)))
    # The Gallery's native selected_index is single-select. This manager keeps
    # its own multi-select set, so each card click toggles membership directly.
    if key and key in selected:
        selected.remove(key)
    elif key:
        selected.append(key)
    by_key = {item["key"]: item for item in list(owned_records or []) + list(unassigned_records or [])}
    selected_records = [by_key[item] for item in selected if item in by_key]
    if selected_records:
        names = "、".join(item["name"] for item in selected_records[:5])
        if len(selected_records) > 5:
            names += f" 等 {len(selected_records)} 项"
        label = f"已选择 {len(selected_records)} 项：{names}"
    else:
        label = "请选择右侧素材卡片，可连续多选。"
    return selected, label


def toggle_owned_project_asset(records, owned_records, unassigned_records, selected_keys, evt: gr.SelectData):
    return _toggle_project_asset(records, owned_records, unassigned_records, selected_keys, evt)


def toggle_unassigned_project_asset(records, owned_records, unassigned_records, selected_keys, evt: gr.SelectData):
    return _toggle_project_asset(records, owned_records, unassigned_records, selected_keys, evt)


def save_project_asset_memberships(asset_keys, project_names, view_project, owned_records=None, unassigned_records=None, topic_text=""):
    try:
        asset_keys = list(dict.fromkeys(str(value) for value in (asset_keys or []) if str(value)))
        if not asset_keys:
            raise ValueError("请先从右侧多选素材")
        asset_index = load_asset_index()
        valid = {
            "character": {str(item.get("id")) for item in ((asset_index.get("characters") or {}).get("identities") or []) if isinstance(item, dict)},
            "background": {str(item.get("id")) for item in (asset_index.get("backgrounds") or []) if isinstance(item, dict)},
        }
        record_by_key = {
            item["key"]: item
            for item in list(owned_records or []) + list(unassigned_records or [])
            if isinstance(item, dict) and item.get("key")
        }
        selected_records = [record_by_key[key] for key in asset_keys if key in record_by_key]
        if len(selected_records) != len(asset_keys):
            raise ValueError("部分素材已不在当前列表中，请重新选择")
        project_names = list(dict.fromkeys(str(name) for name in (project_names or []) if str(name)))
        # The lower project checklist is the complete target ownership set for
        # the selected assets. An empty set intentionally makes them unassigned.
        affected_projects = set(project_names)
        existing_owners = asset_project_owners()
        validated_assets = []
        for record in selected_records:
            kind_text, asset_id = record["key"].split("::", 1)
            if kind_text not in valid or asset_id not in valid[kind_text]:
                raise ValueError(f"素材“{record.get('name') or asset_id}”已不存在，请刷新页面")
            kind = "characters" if kind_text == "character" else "backgrounds"
            affected_projects.update(existing_owners[kind].get(asset_id, []))
            validated_assets.append((kind, asset_id))
        for kind, asset_id in validated_assets:
            set_asset_projects(kind, asset_id, project_names)
        for project_name in sorted(affected_projects):
            topic = topic_text if project_name == view_project and view_project != UNASSIGNED_PROJECT_FILTER else None
            _sync_project_bible_to_assignments(project_name, topic)
        if project_names:
            log(f"已将 {len(selected_records)} 项素材的归属更新为 {len(project_names)} 个所选项目。")
            status = f"已保存 {len(selected_records)} 项素材的项目归属。"
        else:
            log(f"已解除 {len(selected_records)} 项素材与项目的绑定。")
            status = f"已解除 {len(selected_records)} 项素材与项目的绑定。"
    except Exception as exc:
        traceback.print_exc()
        log(f"保存素材项目分配失败: {exc}", "ERROR")
        status = f"保存素材项目分配失败：{exc}"
    return status, *project_asset_management_state(view_project)


def unbind_selected_project_assets(asset_keys, view_project, owned_records=None, unassigned_records=None, topic_text=""):
    return save_project_asset_memberships(asset_keys, [], view_project, owned_records, unassigned_records, topic_text)


def _sync_project_bible_to_assignments(project_name, topic_override=None):
    """Keep the generation-time project bible aligned with project ownership."""
    from services.project_assets import load_project_asset_assignments

    asset_index = load_asset_index()
    assignments = load_project_asset_assignments(project_name)
    character_ids = set(assignments.get("characters") or [])
    background_ids = set(assignments.get("backgrounds") or [])
    identities = [
        item for item in ((asset_index.get("characters") or {}).get("identities") or [])
        if isinstance(item, dict) and str(item.get("id") or "") in character_ids
    ]
    backgrounds = [
        item for item in (asset_index.get("backgrounds") or [])
        if isinstance(item, dict) and str(item.get("id") or "") in background_ids
    ]
    previous = load_project_bible(project_name) or {}
    topic = str(topic_override).strip() if topic_override is not None else ""
    topic = topic or str(previous.get("topic") or "")
    bible = _stable_project_bible(topic, identities, backgrounds, project_name, preserve_supporting=True)
    save_project_bible(bible, project_name)

def _asset_choice_label(asset):
    aliases = asset.get("aliases") if isinstance(asset.get("aliases"), list) else []
    display = str(asset.get("display_name") or asset.get("name_cn") or asset.get("name") or (aliases[0] if aliases else "") or asset.get("id") or "")
    asset_id = str(asset.get("id") or "")
    label = f"{display} ({asset_id})" if display and display != asset_id else asset_id
    image_count = len([image for image in asset.get("images") or [] if isinstance(image, dict)])
    return f"{label} · {image_count}张参考图" if image_count else label


def _scoped_asset_label(label, owners, project_name):
    owners = list(owners or [])
    if project_name in owners:
        return f"【本项目】{label}"
    if not owners:
        return f"【未分配】{label}"
    return f"【其他项目：{'、'.join(owners)}】{label}"

def project_bible_asset_choices(project_name=None):
    asset_index = load_asset_index()
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    backgrounds = asset_index.get("backgrounds") or []
    owners = asset_project_owners()
    project_name = project_name or get_current_project_name()
    character_choices = [
        (_scoped_asset_label(_asset_choice_label(item), owners["characters"].get(str(item.get("id"))), project_name), str(item.get("id")))
        for item in identities
        if isinstance(item, dict) and item.get("id")
    ]
    background_choices = [
        (_scoped_asset_label(_asset_choice_label(item), owners["backgrounds"].get(str(item.get("id"))), project_name), str(item.get("id")))
        for item in backgrounds
        if isinstance(item, dict) and item.get("id")
    ]
    priority = {"【本项目】": 0, "【未分配】": 1, "【其他项目：": 2}
    character_choices.sort(key=lambda item: (next((rank for prefix, rank in priority.items() if item[0].startswith(prefix)), 3), item[0]))
    background_choices.sort(key=lambda item: (next((rank for prefix, rank in priority.items() if item[0].startswith(prefix)), 3), item[0]))
    return character_choices, background_choices


def manual_plan_project_bible(topic_text, character_ids, background_ids, project_name=None):
    try:
        if project_name in (None, get_current_project_name()):
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
        bible = _stable_project_bible(topic_text, identities, backgrounds, project_name)
        bible["extras_policy"]["allow_background_extras"] = True
        save_project_bible(bible, project_name)
        log("已保存已选人物场景")
        char_update, bg_update, gallery = project_bible_ui_state(project_name)
        return get_logs(), gallery, char_update, bg_update
    except Exception as e:
        traceback.print_exc()
        log(f"保存已选人物场景失败: {e}", "ERROR")
        char_update, bg_update, gallery = project_bible_ui_state(project_name)
        return get_logs(), gallery, char_update, bg_update
