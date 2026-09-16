"""Project Bible: per-project character, scene, and asset constraints."""
import json
from pathlib import Path

from services.context import BASE_DIR, get_current_project_name, get_project_dir


PROJECT_BIBLE_NAME = "project_bible.json"


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    return [str(value)] if str(value) else []


def _project_storage_dir():
    project_name = get_current_project_name() or "current"
    path = BASE_DIR / "projects" / project_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_bible_path():
    return _project_storage_dir() / PROJECT_BIBLE_NAME


def _legacy_project_bible_path():
    return get_project_dir() / PROJECT_BIBLE_NAME


def _migrate_legacy_project_bible():
    target = project_bible_path()
    legacy = _legacy_project_bible_path()
    if target.exists() or not legacy.exists():
        return target
    try:
        text = legacy.read_text(encoding="utf-8-sig")
        if text.strip():
            target.write_text(text.rstrip() + "\n", encoding="utf-8")
    except Exception:
        pass
    return target


def load_project_bible():
    path = _migrate_legacy_project_bible()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_project_bible(data):
    path = project_bible_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data or {}, f, ensure_ascii=False, indent=2)
    return path


def _identity_display_name(identity):
    aliases = _as_list(identity.get("aliases"))
    return str(identity.get("name_cn") or identity.get("display_name") or identity.get("name") or (aliases[0] if aliases else "") or identity.get("id") or "")


def _background_display_name(bg):
    aliases = _as_list(bg.get("aliases"))
    return str(bg.get("display_name") or bg.get("name_cn") or (aliases[0] if aliases else "") or bg.get("id") or "")


def build_default_project_bible(topic="", asset_index=None, max_characters=4, max_backgrounds=4):
    asset_index = asset_index or {}
    identities = ((asset_index.get("characters") or {}).get("identities") or [])
    backgrounds = asset_index.get("backgrounds") or []

    core_characters = []
    for idx, identity in enumerate(identities[:max_characters], start=1):
        identity_id = str(identity.get("id") or "")
        display = _identity_display_name(identity) or f"角色{idx}"
        core_characters.append({
            "id": f"character_{idx:03d}",
            "display_name": display,
            "role": "protagonist" if idx == 1 else "core",
            "asset_identity_id": identity_id,
            "aliases": sorted(set([display, identity_id] + _as_list(identity.get("aliases")))),
            "locked": True,
            "need_reference_image": True,
        })

    allowed_backgrounds = []
    for idx, bg in enumerate(backgrounds[:max_backgrounds], start=1):
        bg_id = str(bg.get("id") or "")
        display = _background_display_name(bg) or f"场景{idx}"
        allowed_backgrounds.append({
            "id": f"scene_{idx:03d}",
            "display_name": display,
            "asset_background_id": bg_id,
            "aliases": sorted(set([display, bg_id] + _as_list(bg.get("aliases")))),
            "locked": True,
            "need_reference_image": True,
        })

    return {
        "topic": str(topic or ""),
        "world_type": "自定义",
        "core_characters": core_characters,
        "supporting_characters": [],
        "allowed_backgrounds": allowed_backgrounds,
        "extras_policy": {
            "allow_background_extras": True,
            "need_reference_image": False,
            "can_speak": False,
            "can_drive_plot": False,
        },
        "strict_asset_mode": True,
    }


def ensure_project_bible(topic="", asset_index=None):
    bible = load_project_bible()
    if bible:
        # 主题跟着项目走：Bible 已存在时也要把 topic 同步到当前项目主题，
        # 否则项目主题变更后 Bible 里会残留旧故事的 topic（导致剧情对不上）。
        new_topic = str(topic or "").strip()
        if new_topic and str(bible.get("topic") or "").strip() != new_topic:
            bible["topic"] = new_topic
            save_project_bible(bible)
        return bible
    bible = build_default_project_bible(topic=topic, asset_index=asset_index)
    save_project_bible(bible)
    return bible


def project_bible_prompt(bible):
    bible = bible or {}
    return (
        "PROJECT_BIBLE_JSON:\n"
        + json.dumps(bible, ensure_ascii=False, indent=2)
        + "\nRules: Use only character ids from core_characters/supporting_characters. "
        "Use only background ids from allowed_backgrounds. Anonymous extras are allowed only by extras_policy. "
        "Do not invent named main characters or new primary scenes."
    )


def character_entries(bible):
    return list((bible or {}).get("core_characters") or []) + list((bible or {}).get("supporting_characters") or [])


def character_by_id(bible):
    return {str(item.get("id")): item for item in character_entries(bible) if item.get("id")}


def background_by_id(bible):
    return {str(item.get("id")): item for item in ((bible or {}).get("allowed_backgrounds") or []) if item.get("id")}


def normalize_character_id(name, bible):
    text = str(name or "").strip()
    if not text:
        return ""
    for item in character_entries(bible):
        if text == str(item.get("id")):
            return str(item.get("id"))
        aliases = set(_as_list(item.get("aliases")) + [str(item.get("display_name") or ""), str(item.get("asset_identity_id") or "")])
        if text in aliases:
            return str(item.get("id"))
    return ""


def normalize_background_id(name, bible):
    text = str(name or "").strip()
    if not text:
        return ""
    for item in (bible or {}).get("allowed_backgrounds") or []:
        if text == str(item.get("id")):
            return str(item.get("id"))
        aliases = set(_as_list(item.get("aliases")) + [str(item.get("display_name") or ""), str(item.get("asset_background_id") or "")])
        if text in aliases:
            return str(item.get("id"))
    return ""


def default_character_ids(bible):
    return [str(item.get("id")) for item in character_entries(bible) if item.get("id")][:1]


def default_background_id(bible):
    bgs = (bible or {}).get("allowed_backgrounds") or []
    return str(bgs[0].get("id")) if bgs else ""

