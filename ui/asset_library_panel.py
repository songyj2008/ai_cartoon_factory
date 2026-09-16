"""Asset library upload, metadata editing, and index refresh handlers."""
from __future__ import annotations

import json
import html
import re
import shutil
from pathlib import Path
from typing import Any

import gradio as gr

from asset_index import build_asset_index_with_llm, configure_asset_index, sync_asset_index_with_assets
from services.context import ASSET_INDEX_PATH, ASSETS_DIR, BASE_DIR
from services.logger import get_logs, log
from services.llm import call_llm
from ui.project_bible_panel import project_bible_ui_state


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
META_FILE_NAME = "asset_meta.json"


def _safe_id(value: str, label: str = "ID") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if not re.fullmatch(r"[\w-]+", text):
        raise ValueError(f"{label} 只能包含中文、字母、数字、下划线和短横线，不能包含空格或路径符号")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", text, re.IGNORECASE):
        raise ValueError(f"{label} 不能使用 Windows 保留文件名")
    return text


def _as_list_text(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，、;；\n]+", str(value or ""))
    return [str(item).strip() for item in raw if str(item).strip()]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _upload_path(upload_file: Any) -> Path:
    path_text = getattr(upload_file, "name", None) or upload_file
    path = Path(str(path_text or ""))
    if not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("Please upload a png/jpg/jpeg/webp image file")
    return path


def _path_from_text(path_text: Any) -> Path:
    text = str(path_text or "").strip().strip('"')
    if not text:
        raise ValueError("素材路径为空")
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    if not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("请选择或输入存在的 png/jpg/jpeg/webp 图片路径")
    return path


def _relative_display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR.resolve()))
    except Exception:
        return str(path.resolve())


def _asset_key_from_path(path: Path) -> str:
    path = path.resolve()
    try:
        rel = path.relative_to(ASSETS_DIR.resolve())
    except ValueError:
        return ""
    parts = rel.parts
    if len(parts) == 4 and parts[0] == "characters" and parts[1] == "identities":
        return f"character::{parts[2]}::{path.stem}"
    if len(parts) == 2 and parts[0] == "backgrounds":
        return f"background::{path.stem}"
    return ""


def _managed_asset_path_for_selection(path: Path) -> Path:
    path = path.resolve()
    if _asset_key_from_path(path):
        return path
    matches = []
    if ASSETS_DIR.exists():
        for candidate in ASSETS_DIR.rglob(path.name):
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS and _asset_key_from_path(candidate):
                matches.append(candidate.resolve())
    if len(matches) == 1:
        return matches[0]
    return path


def _target_dir_from_text(target_dir: Any) -> Path:
    text = str(target_dir or "").strip().strip('"')
    if not text:
        raise ValueError("target directory is required for images outside assets")
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _default_target_dir(asset_type: Any, source_path: Path) -> Path:
    key = _asset_key_from_path(source_path)
    if key:
        return source_path.resolve().parent
    if str(asset_type or "") == "background":
        return ASSETS_DIR / "backgrounds"
    return ASSETS_DIR / "characters" / "identities"


def _target_path_for_source(asset_type: Any, source_path: Path, asset_id: Any, target_dir: Any) -> Path:
    key = _asset_key_from_path(source_path)
    if key:
        return source_path.resolve()
    if target_dir:
        target_root = _target_dir_from_text(target_dir)
    elif str(asset_type or "") == "background":
        target_root = ASSETS_DIR / "backgrounds"
    else:
        raise ValueError("target directory is required for character images outside assets")
    filename = _safe_id(asset_id or source_path.stem, "asset_id") + source_path.suffix.lower()
    return target_root / filename


def _preview_image_path(path: Path) -> str:
    return str(path.resolve())


def _metadata_for_path(path: Path):
    key = _asset_key_from_path(path)
    if not key:
        return ("character", path.stem, "", "", "", "", "", "")
    meta = _metadata_for_key(key)
    return (meta[0], meta[2], meta[3], meta[4], meta[5], meta[6], meta[7], meta[8])


def preview_selected_asset(asset_type, image_file, asset_id, target_dir):
    if not image_file:
        return "character", "", "", "", "", "", "", "", "", None, "", "", get_logs()
    src = None
    try:
        selected_src = _upload_path(image_file)
        src = _managed_asset_path_for_selection(selected_src)
        key = _asset_key_from_path(src)
        if key:
            meta = _metadata_for_key(key)
            inferred_type = meta[0]
            inferred_asset_id = meta[2]
            inferred_target_dir = _relative_display_path(src.parent)
            return (
                inferred_type,
                inferred_asset_id,
                inferred_target_dir,
                meta[3],
                meta[4],
                meta[5],
                meta[6],
                meta[7],
                meta[8],
                _preview_image_path(src),
                str(src.resolve()),
                _relative_display_path(src),
                get_logs(),
            )
        if target_dir or str(asset_type or "") == "background":
            effective_target_dir = str(target_dir or _relative_display_path(_default_target_dir(asset_type, src)))
            target_preview = f"will save to: {_relative_display_path(_target_path_for_source(asset_type, src, asset_id, effective_target_dir))}"
        else:
            effective_target_dir = ""
            target_preview = "external image: enter a character target directory before saving"
        return (
            str(asset_type or "character"),
            str(asset_id or src.stem),
            effective_target_dir,
            "",
            "",
            "",
            "",
            "",
            "",
            _preview_image_path(src),
            str(src.resolve()),
            target_preview,
            get_logs(),
        )
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    return (
        str(asset_type or "character"), str(asset_id or (src.stem if src else "")),
        str(target_dir or ""), "", "", "", "", "", "",
        _preview_image_path(src) if src else None,
        str(src.resolve()) if src else "", "", get_logs(),
    )


def preview_selected_asset_path_only(asset_type, image_file, asset_id, target_dir, current_path=None):
    source = image_file
    if not source and current_path:
        source = current_path
    result = preview_selected_asset(asset_type, source, asset_id, target_dir)
    if len(result) < 13:
        result = tuple(result) + ("",) * (13 - len(result))
    return (
        gr.update(value=result[0]),
        gr.update(),
        gr.update(value=result[2]),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        result[9],
        result[10],
        result[11],
        result[12],
    )


def preview_selected_asset_for_upload(asset_type, image_file, asset_id, target_dir):
    if not image_file:
        result = preview_selected_asset(asset_type, image_file, asset_id, target_dir)
        return (
            result[0],
            result[1],
            result[2],
            result[3],
            result[4],
            result[5],
            result[6],
            result[7],
            result[8],
            result[9],
            result[10],
            result[11],
            result[12],
        )
    if image_file:
        try:
            src = _managed_asset_path_for_selection(_upload_path(image_file))
            if _asset_key_from_path(src):
                return preview_selected_asset(asset_type, image_file, asset_id, target_dir)
        except Exception as exc:
            log(f"[asset_library][error] {exc}", "ERROR")
    result = preview_selected_asset(asset_type, image_file, asset_id, target_dir)
    return (
        result[0],
        result[1],
        result[2],
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        result[9],
        result[10],
        result[11],
        result[12],
    )


def preview_upload_target(asset_type, upload_file, identity_id, asset_id):
    if not upload_file:
        return [], "", "", get_logs()
    try:
        src = _upload_path(upload_file)
        asset_id = _safe_id(asset_id or src.stem, "asset_id")
        if str(asset_type or "") == "character":
            identity_id = _safe_id(identity_id, "identity_id")
            target = ASSETS_DIR / "characters" / "identities" / identity_id / f"{asset_id}{src.suffix.lower()}"
        else:
            target = ASSETS_DIR / "backgrounds" / f"{asset_id}{src.suffix.lower()}"
        caption = f"待保存: {_relative_display_path(target)}"
        return _preview_image_path(src), _relative_display_path(target), str(target.resolve()), get_logs()
    except Exception as exc:
        return [], f"无法生成保存路径：{exc}", "", get_logs()


def _meta_from_fields(display_name, aliases, tags_cn, description_cn, use_for, pose="") -> dict[str, Any]:
    data: dict[str, Any] = {}
    if str(display_name or "").strip():
        data["display_name"] = str(display_name).strip()
        data["name_cn"] = str(display_name).strip()
    if _as_list_text(aliases):
        data["aliases"] = _as_list_text(aliases)
    if _as_list_text(tags_cn):
        data["tags_cn"] = _as_list_text(tags_cn)
    if str(description_cn or "").strip():
        data["description_cn"] = str(description_cn).strip()
    if _as_list_text(use_for):
        data["use_for"] = _as_list_text(use_for)
    if str(pose or "").strip():
        data["pose"] = str(pose).strip()
    return data


def _asset_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    identity_root = ASSETS_DIR / "characters" / "identities"
    if identity_root.exists():
        for identity_dir in sorted([p for p in identity_root.iterdir() if p.is_dir()]):
            meta = _read_json(identity_dir / META_FILE_NAME)
            image_meta = meta.get("images") if isinstance(meta.get("images"), dict) else {}
            identity_meta = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
            for image in sorted([p for p in identity_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]):
                desc = image_meta.get(image.stem) if isinstance(image_meta.get(image.stem), dict) else {}
                status = "described" if desc.get("description_cn") else "missing_description"
                display = identity_meta.get("display_name") or identity_meta.get("name_cn") or identity_dir.name
                items.append({
                    "key": f"character::{identity_dir.name}::{image.stem}",
                    "label": f"character/{identity_dir.name}/{image.stem} - {status}",
                    "type": "character",
                    "identity_id": identity_dir.name,
                    "asset_id": image.stem,
                    "path": image,
                    "status": status,
                    "display": display,
                })
    bg_root = ASSETS_DIR / "backgrounds"
    bg_meta = _read_json(bg_root / META_FILE_NAME)
    if bg_root.exists():
        for image in sorted([p for p in bg_root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]):
            desc = bg_meta.get(image.stem) if isinstance(bg_meta.get(image.stem), dict) else {}
            status = "described" if desc.get("description_cn") else "missing_description"
            items.append({
                "key": f"background::{image.stem}",
                "label": f"background/{image.stem} - {status}",
                "type": "background",
                "identity_id": "",
                "asset_id": image.stem,
                "path": image,
                "status": status,
                "display": desc.get("display_name") or desc.get("name_cn") or image.stem,
            })
    return items


def asset_library_choices():
    return [(item["label"], item["key"]) for item in _asset_items()]


def asset_target_dir_choices():
    choices: list[str] = []
    identity_root = ASSETS_DIR / "characters" / "identities"
    if identity_root.exists():
        for path in sorted([p for p in identity_root.iterdir() if p.is_dir()]):
            choices.append(_relative_display_path(path))
    choices.append(_relative_display_path(ASSETS_DIR / "backgrounds"))
    return choices


def refresh_asset_target_dir_options(current_target_dir=None):
    current = str(current_target_dir or "").strip()
    choices = asset_target_dir_choices()
    if current and current not in choices:
        choices.append(current)
    return gr.update(choices=choices, value=current or None)


def refresh_asset_library_controls(current_path=None, current_selected_key=None, current_target_dir=None):
    choices = asset_library_choices()
    valid_keys = {str(value) for _, value in choices}
    selected_key = str(current_selected_key or "").strip()

    path_text = str(current_path or "").strip()
    if path_text:
        try:
            path_key = _asset_key_from_path(_path_from_text(path_text))
            if path_key in valid_keys:
                selected_key = path_key
        except Exception:
            pass

    if selected_key not in valid_keys:
        selected_key = None
    return (
        gr.update(choices=choices, value=selected_key),
        refresh_asset_target_dir_options(current_target_dir),
    )


def asset_library_gallery(selected_key: str | None = None):
    if not selected_key:
        return []
    gallery = []
    for item in _asset_items():
        if item.get("key") != selected_key:
            continue
        suffix = "character" if item["type"] == "character" else "background"
        gallery.append((str(item["path"].resolve()), f"{suffix}: {item['display']} / {item['asset_id']} / {item['status']}"))
        break
    return gallery


def load_existing_asset_selection(selected_key):
    try:
        selected_key = str(selected_key or "").strip()
        if not selected_key:
            return "character", "", "", "", "", "", "", "", "", None, "", "", get_logs()
        meta = _metadata_for_key(selected_key)
        path = None
        for item in _asset_items():
            if item.get("key") == selected_key:
                path = item["path"].resolve()
                break
        if not path:
            raise ValueError(f"asset not found: {selected_key}")
        return (
            meta[0],
            meta[2],
            _relative_display_path(path.parent),
            meta[3],
            meta[4],
            meta[5],
            meta[6],
            meta[7],
            meta[8],
            _preview_image_path(path),
            str(path),
            _relative_display_path(path),
            get_logs(),
        )
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    return "character", "", "", "", "", "", "", "", "", None, "", "", get_logs()


def _metadata_for_key(selected_key: str):
    parts = str(selected_key or "").split("::")
    if parts[0] == "character" and len(parts) == 3:
        identity_id, asset_id = parts[1], parts[2]
        data = _read_json(ASSETS_DIR / "characters" / "identities" / identity_id / META_FILE_NAME)
        identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        item = images.get(asset_id) if isinstance(images.get(asset_id), dict) else {}
        display = item.get("display_name") or identity.get("display_name") or identity.get("name_cn") or ""
        return (
            "character",
            identity_id,
            asset_id,
            display,
            ", ".join(_as_list_text(item.get("aliases") or identity.get("aliases"))),
            ", ".join(_as_list_text(item.get("tags_cn"))),
            item.get("description_cn") or "",
            ", ".join(_as_list_text(item.get("use_for"))),
            item.get("pose") or "",
        )
    if parts[0] == "background" and len(parts) == 2:
        asset_id = parts[1]
        data = _read_json(ASSETS_DIR / "backgrounds" / META_FILE_NAME)
        item = data.get(asset_id) if isinstance(data.get(asset_id), dict) else {}
        return (
            "background",
            "",
            asset_id,
            item.get("display_name") or item.get("name_cn") or "",
            ", ".join(_as_list_text(item.get("aliases"))),
            ", ".join(_as_list_text(item.get("tags_cn"))),
            item.get("description_cn") or "",
            ", ".join(_as_list_text(item.get("use_for"))),
            item.get("pose") or "",
        )
    return "character", "", "", "", "", "", "", "", ""


def _save_metadata_for_image(path: Path, asset_type, display_name, aliases, tags_cn, description_cn, use_for, pose):
    key = _asset_key_from_path(path)
    if not key:
        raise ValueError(f"saved image must be inside assets: {path}")
    meta = _meta_from_fields(display_name, aliases, tags_cn, description_cn, use_for, pose)
    parts = key.split("::")
    if parts[0] == "character":
        identity_id, asset_id = parts[1], parts[2]
        meta_path = ASSETS_DIR / "characters" / "identities" / identity_id / META_FILE_NAME
        data = _read_json(meta_path)
        data.setdefault("identity", {})
        data.setdefault("images", {})
        if display_name or aliases:
            data["identity"].update(_meta_from_fields(display_name, aliases, "", "", "", ""))
        data["images"][asset_id] = meta
    else:
        asset_id = parts[1]
        meta_path = ASSETS_DIR / "backgrounds" / META_FILE_NAME
        data = _read_json(meta_path)
        data[asset_id] = meta
    _write_json(meta_path, data)
    return meta_path


def asset_library_summary() -> str:
    items = _asset_items()
    characters = len({item["identity_id"] for item in items if item["type"] == "character"})
    character_images = len([item for item in items if item["type"] == "character"])
    backgrounds = len([item for item in items if item["type"] == "background"])
    missing = len([item for item in items if item["status"] != "described"])
    return f"characters {characters} | character images {character_images} | backgrounds {backgrounds} | missing descriptions {missing}"


def _updates():
    _sync_asset_index_for_ui()
    char_update, bg_update, selected_gallery = project_bible_ui_state()
    return (
        [],
        asset_library_summary(),
        char_update,
        bg_update,
        selected_gallery,
        get_logs(),
    )


def _sync_asset_index_for_ui() -> None:
    try:
        sync_asset_index_with_assets()
        log("[asset_library] asset_index synced with assets", "STEP")
    except Exception as exc:
        log(f"[asset_library][error] asset_index sync failed: {exc}", "ERROR")


def upload_asset(asset_type, upload_file, identity_id, asset_id, display_name, aliases, tags_cn, description_cn, use_for, pose):
    target = None
    try:
        src = _upload_path(upload_file)
        asset_id = _safe_id(asset_id or src.stem, "asset_id")
        meta = _meta_from_fields(display_name, aliases, tags_cn, description_cn, use_for, pose)
        if str(asset_type or "") == "character":
            identity_id = _safe_id(identity_id, "identity_id")
            target_dir = ASSETS_DIR / "characters" / "identities" / identity_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{asset_id}{src.suffix.lower()}"
            if target.exists():
                raise FileExistsError(f"asset already exists: {target}")
            shutil.copy2(src, target)
            meta_path = target_dir / META_FILE_NAME
            data = _read_json(meta_path)
            data.setdefault("identity", {})
            data.setdefault("images", {})
            if display_name:
                data["identity"].update(_meta_from_fields(display_name, aliases, "", "", "", ""))
            data["images"][asset_id] = meta
            _write_json(meta_path, data)
        else:
            target_dir = ASSETS_DIR / "backgrounds"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{asset_id}{src.suffix.lower()}"
            if target.exists():
                raise FileExistsError(f"asset already exists: {target}")
            shutil.copy2(src, target)
            meta_path = target_dir / META_FILE_NAME
            data = _read_json(meta_path)
            data[asset_id] = meta
            _write_json(meta_path, data)
        log(f"[asset_library] uploaded asset: {target}", "STEP")
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    _sync_asset_index_for_ui()
    char_update, bg_update, selected_gallery = project_bible_ui_state()
    gallery = _preview_image_path(target) if target else None
    current_path = str(target.resolve()) if target else ""
    target_preview = _relative_display_path(target) if target else ""
    return (
        gallery,
        current_path,
        target_preview,
        asset_library_summary(),
        char_update,
        bg_update,
        selected_gallery,
        get_logs(),
    )


def load_asset_metadata(selected_key):
    try:
        meta = _metadata_for_key(selected_key)
        return (*meta, asset_library_gallery(selected_key), "", get_logs())
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    return "character", "", "", "", "", "", "", "", "", [], "", get_logs()


def load_asset_from_path(path_value):
    try:
        path = _path_from_text(path_value)
        key = _asset_key_from_path(path)
        if key:
            meta = _metadata_for_key(key)
        else:
            meta = ("character", "", path.stem, "", "", "", "", "", "")
            log(f"[asset_library][warn] path is outside managed assets: {path}", "WARN")
        return (*meta, _preview_image_path(path), str(path), get_logs())
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    return "character", "", "", "", "", "", "", "", "", [], "", get_logs()


def save_asset_metadata(asset_type, identity_id, asset_id, display_name, aliases, tags_cn, description_cn, use_for, pose):
    try:
        asset_id = _safe_id(asset_id, "asset_id")
        meta = _meta_from_fields(display_name, aliases, tags_cn, description_cn, use_for, pose)
        if str(asset_type or "") == "character":
            identity_id = _safe_id(identity_id, "identity_id")
            meta_path = ASSETS_DIR / "characters" / "identities" / identity_id / META_FILE_NAME
            data = _read_json(meta_path)
            data.setdefault("identity", {})
            data.setdefault("images", {})
            if display_name or aliases:
                data["identity"].update(_meta_from_fields(display_name, aliases, "", "", "", ""))
            data["images"][asset_id] = meta
        else:
            meta_path = ASSETS_DIR / "backgrounds" / META_FILE_NAME
            data = _read_json(meta_path)
            data[asset_id] = meta
        _write_json(meta_path, data)
        log(f"[asset_library] saved metadata: {meta_path}", "STEP")
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    return _updates()


def save_current_asset_metadata(current_path, asset_type, identity_id, asset_id, display_name, aliases, tags_cn, description_cn, use_for, pose):
    try:
        path = _path_from_text(current_path)
        key = _asset_key_from_path(path)
        if key:
            parts = key.split("::")
            if parts[0] == "character":
                asset_type, identity_id, asset_id = "character", parts[1], parts[2]
            else:
                asset_type, identity_id, asset_id = "background", "", parts[1]
        result = save_asset_metadata(asset_type, identity_id, asset_id, display_name, aliases, tags_cn, description_cn, use_for, pose)
        return (
            _preview_image_path(path),
            str(path),
            _relative_display_path(path),
            *result[1:],
        )
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
        result = _updates()
        return [], str(current_path or ""), "", *result[1:]


def save_selected_asset_metadata(current_path, asset_type, asset_id, target_dir, display_name, aliases, tags_cn, description_cn, use_for, pose):
    try:
        src = _managed_asset_path_for_selection(_path_from_text(current_path))
        managed_key = _asset_key_from_path(src)
        if managed_key:
            final_path = src.resolve()
            log(f"[asset_library] using existing asset without copying: {final_path}", "STEP")
        else:
            final_path = _target_path_for_source(asset_type, src, asset_id, target_dir)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists() and final_path.resolve() != src.resolve():
                raise FileExistsError(f"asset already exists: {final_path}")
            shutil.copy2(src, final_path)
            log(f"[asset_library] copied asset: {final_path}", "STEP")
        meta_path = _save_metadata_for_image(final_path, asset_type, display_name, aliases, tags_cn, description_cn, use_for, pose)
        log(f"[asset_library] saved metadata: {meta_path}", "STEP")
        _sync_asset_index_for_ui()
        char_update, bg_update, selected_gallery = project_bible_ui_state()
        return (
            _preview_image_path(final_path),
            str(final_path.resolve()),
            _relative_display_path(final_path),
            char_update,
            bg_update,
            selected_gallery,
            get_logs(),
        )
    except Exception as exc:
        log(f"[asset_library][error] {exc}", "ERROR")
    char_update, bg_update, selected_gallery = project_bible_ui_state()
    return None, str(current_path or ""), "", char_update, bg_update, selected_gallery, get_logs()


def refresh_asset_index_from_ui():
    try:
        configure_asset_index(BASE_DIR, ASSETS_DIR, ASSET_INDEX_PATH, log)
        build_asset_index_with_llm(call_llm)
        log("[asset_library] asset_index refreshed", "STEP")
    except Exception as exc:
        log(f"[asset_library][error] refresh failed: {exc}", "ERROR")
    return _updates()


_METADATA_FIELDS = ("显示名", "别名", "中文标签", "描述", "适用场景", "姿态/用途")


def parse_asset_metadata_text(value: Any) -> dict[str, str]:
    """Parse labeled plain text or Markdown without inferring missing fields."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\\([_*])", r"\1", text)
    header = re.compile(
        r"^(显示名|别名|中文标签|描述|适用场景|姿态\s*[/／]\s*用途)"
        r"(?:\s*[:：]\s*(.*))?$"
    )
    sections: dict[str, list[str]] = {}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip("\\").strip()
        candidate = re.sub(r"^(?:#{1,6}\s+|[-*]\s+|>\s*)", "", line)
        candidate = candidate.replace("**", "").strip()
        match = header.fullmatch(candidate)
        if match:
            current = re.sub(r"\s*[/／]\s*", "/", match.group(1))
            sections[current] = [match.group(2) or ""]
        elif current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()
            if "\n".join(lines).strip()}


def fill_asset_metadata_from_text(value: Any):
    parsed = parse_asset_metadata_text(value)
    updates = tuple(parsed.get(field, gr.update()) for field in _METADATA_FIELDS)
    if not parsed:
        return (*updates, "未识别到素材信息。请使用显示名、别名、中文标签、描述、适用场景、姿态/用途作为标题；原字段未改动。")
    names = "、".join(field for field in _METADATA_FIELDS if field in parsed)
    return (*updates, f"已填入 {len(parsed)} 项：{names}。未提供的字段保持原值；检查后点击“保存当前素材描述”。")
