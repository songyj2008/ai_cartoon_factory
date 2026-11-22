"""Resolve authored shot facts; never infer visibility from narrative keywords."""
from __future__ import annotations

import copy
import re
from .speech_contract import has_onscreen_speech

from services.project_bible import (character_entries, resolve_project_reference as resolve,
                                    ProjectBindingError as BindingError)

BINDING_SCHEMA_VERSION = 1
MODEL = "minimax_h3_local_ref2va"
_REFERENCE_TOKEN = re.compile(r"<\s*(?:Subject|Picture|Video|Audio)\s+\d+\s*>|character_\d+", re.I)


def resolve_scene(value, backgrounds, *, location):
    """Reuse exact registered names; keep new locations as text for video creation."""
    text = str(value or "").strip()
    if not text:
        return ""
    names = {str(entry.get(key) or "") for entry in backgrounds
             for key in ("id", "display_name", "asset_background_id")}
    names.update(str(alias) for entry in backgrounds for alias in entry.get("aliases") or [])
    return resolve(text, backgrounds, location=location) if text in names else text



def normalize_beat_bindings(beat, *, bible, scene_contexts=None):
    result = copy.deepcopy(beat)
    hints = result.get("model_hints", {}).get(MODEL, {})
    shots = hints.get("shots") or []
    if not shots:
        raise BindingError("H3 缺少可解析的 Shots")
    characters = character_entries(bible)
    registered = {str(entry["id"]) for entry in characters if entry.get("id")}
    backgrounds = bible.get("allowed_backgrounds") or []
    contexts = scene_contexts if scene_contexts is not None else result.get("scene_contexts", [])
    by_context = {}
    for context in contexts:
        key = str(context.get("id") or "")
        if not key or key in by_context:
            raise BindingError("连续场景 id 缺失或重复")
        by_context[key] = resolve_scene(context.get("scene_id"), backgrounds, location=f"场景 {key}")
    visible, offscreen, mentioned, scenes = [], [], [], []
    text_roles = set()

    def resolve_role(value, location):
        if not isinstance(value, str) or not value.strip():
            raise BindingError(f"{location}: 人物必须是非空姓名或角色 ID")
        name = resolve(value, characters, location=location, allow_unregistered_text=True)
        if name not in registered:
            if _REFERENCE_TOKEN.fullmatch(name):
                raise BindingError(f"{location}: 未登记角色 ID 或未绑定素材标签「{name}」")
            text_roles.add(name)
        return name

    for index, shot in enumerate(shots, 1):
        location = f"Beat {beat.get('id', '?')} / Shot {index}"
        if "visible_roles" not in shot or not isinstance(shot["visible_roles"], list):
            raise BindingError(f"{location}: 缺少入画人物列表 visible_roles；空镜请明确填 []")
        shot["visible_roles"] = list(dict.fromkeys(resolve_role(value, location) for value in shot["visible_roles"]))
        if "scene_context_id" in shot and shot["scene_context_id"]:
            key = str(shot["scene_context_id"])
            if key not in by_context:
                raise BindingError(f"{location}: 未知连续场景 {key}")
            scene = by_context[key]
            if shot.get("scene_id") and resolve_scene(shot["scene_id"], backgrounds, location=location) != scene:
                raise BindingError(f"{location}: 场景与连续场景引用冲突")
        elif "scene_id" in shot:
            scene = resolve_scene(shot["scene_id"], backgrounds, location=location)
        else:
            raise BindingError(f"{location}: 缺少场景；无具体场景请明确填 scene_id='' ")
        shot["scene_id"] = scene
        scenes.append(scene)
        visible.extend(shot["visible_roles"])
        for field in ("offscreen_speakers", "mentioned_roles"):
            names = shot.get(field, [])
            if not isinstance(names, list):
                raise BindingError(f"{location}: {field} 必须是人物列表")
            shot[field] = [resolve_role(value, location) for value in names]
        mentioned.extend(shot["mentioned_roles"])
        offscreen.extend(shot["offscreen_speakers"])
        values = shot.get("dialogues", shot.get("dialogue", []))
        for dialogue in values if isinstance(values, list) else [values]:
            if not isinstance(dialogue, dict) or not dialogue:
                continue
            # Narrators may be explicitly independent voices, not registered actors.
            if dialogue.get("scope") == "narration":
                continue
            speaker = resolve_role(dialogue.get("speaker"), location)
            dialogue["speaker"] = speaker
            onscreen = has_onscreen_speech(dialogue)
            if onscreen and speaker not in shot["visible_roles"]:
                raise BindingError(f"{location}: 画内说话人不在入画人物列表中")
            if not onscreen or any(span['visibility'] == 'offscreen' for span in (dialogue.get('delivery_spans') or [])):
                offscreen.append(speaker)
    reference_roles = list(dict.fromkeys(role for role in visible if role in registered))
    identity_constraints = result.get("identity_constraints")
    if not isinstance(identity_constraints, dict):
        identity_constraints = {}
    identity_constraints.update(registered_identity_only=False, locked_roles=reference_roles)
    identity_constraints.pop("forbidden_unregistered_appearance", None)
    result.update(visible_roles=list(dict.fromkeys(visible)),
                  reference_roles=reference_roles, text_roles=sorted(text_roles), identity_constraints=identity_constraints,
                  offscreen_speakers=list(dict.fromkeys(offscreen)),
                  mentioned_roles=list(dict.fromkeys(mentioned)),
                  important_roles=list(dict.fromkeys(visible + offscreen + mentioned)),
                  scene_id=scenes[0] if len(set(scenes)) == 1 else "",
                  scene_contexts=copy.deepcopy(contexts), binding_schema_version=BINDING_SCHEMA_VERSION)
    return result


def binding_coverage_errors(beat, manifest, *, bible):
    errors = []
    metadata = [a.get("metadata") or {} for a in manifest]
    registered = {e["id"]: e for e in character_entries(bible)}
    for role in beat.get("reference_roles", []):
        if role in registered and registered[role].get("need_reference_image", True) and not any(
                m.get("role_id") == role and m.get("asset_kind") != "background" for m in metadata):
            errors.append(f"入画人物缺少参考图：{role}")
    return list(dict.fromkeys(errors))
