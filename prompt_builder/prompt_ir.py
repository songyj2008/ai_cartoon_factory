"""Prompt IR compiler for Beat -> LiconMSR director prompt.

This module converts a normalized story beat into a stable intermediate
representation. The LLM should transform this IR into natural director-shot
description, not infer roles, scenes, timing, or dialogue.
"""
from __future__ import annotations

import re
from typing import Any

from services.project_bible import load_project_bible


MIN_SEGMENT_SEC = 15
MAX_SEGMENT_SEC = 30


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_text(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _entry_names(entry: dict[str, Any]) -> set[str]:
    names = {
        _text(entry.get("id")),
        _text(entry.get("display_name")),
        _text(entry.get("name")),
        _text(entry.get("name_cn")),
        _text(entry.get("asset_identity_id")),
        _text(entry.get("asset_background_id")),
    }
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = [aliases]
    names.update(_text(alias) for alias in aliases)
    return {name for name in names if name}


def _bible_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bible = load_project_bible() or {}
    characters = [
        item
        for item in list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
        if isinstance(item, dict)
    ]
    backgrounds = [item for item in bible.get("allowed_backgrounds") or [] if isinstance(item, dict)]
    return characters, backgrounds


def role_display_name(role: str) -> str:
    role_text = _text(role)
    characters, _ = _bible_entries()
    for entry in characters:
        if role_text in _entry_names(entry):
            return _text(entry.get("display_name") or entry.get("name_cn") or entry.get("name") or role_text)
    return role_text


def scene_display_name(scene_id: str) -> str:
    scene_text = _text(scene_id)
    _, backgrounds = _bible_entries()
    for entry in backgrounds:
        if scene_text in _entry_names(entry):
            return _text(entry.get("display_name") or entry.get("name_cn") or entry.get("name") or scene_text)
    return scene_text or "当前场景"


def _duration(beat: dict[str, Any], duration_sec: int | None = None) -> int:
    try:
        value = int(round(float(duration_sec or beat.get("estimated_duration_sec") or beat.get("duration_sec") or MIN_SEGMENT_SEC)))
    except Exception:
        value = MIN_SEGMENT_SEC
    return max(1, value)


def _dialogue_units(beat: dict[str, Any]) -> list[dict[str, Any]]:
    raw = beat.get("dialogue_units") or beat.get("dialogues") or beat.get("dialogue") or []
    if not isinstance(raw, list):
        return []
    units: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        speaker = _text(item.get("speaker_name") or item.get("speaker") or item.get("role") or item.get("name"))
        line = _text(item.get("line") or item.get("text") or item.get("content"))
        if not speaker or not line:
            continue
        units.append(
            {
                "speaker": speaker,
                "speaker_name": role_display_name(speaker),
                "line": line,
                "target_role": _text(item.get("target_role") or item.get("target") or ""),
                "speech_type": _text(item.get("speech_type") or "onscreen"),
                "visible": bool(item.get("visible", True)),
            }
        )
    return units


def _action_units(beat: dict[str, Any]) -> list[dict[str, Any]]:
    raw = beat.get("action_units") or beat.get("actions") or []
    if not isinstance(raw, list):
        return []
    units: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = _text(item.get("role") or item.get("actor") or item.get("name"))
        action = _text(item.get("action") or item.get("description") or item.get("text"))
        if not action:
            continue
        units.append(
            {
                "role": role,
                "role_name": role_display_name(role) if role else "",
                "action": action,
                "target_role": _text(item.get("target_role") or item.get("target") or ""),
                "object": _text(item.get("object") or item.get("prop") or ""),
                "motion_scale": _text(item.get("motion_scale") or "small"),
                "timing": _text(item.get("timing") or "during"),
                "visibility": _text(item.get("visibility") or "onscreen"),
            }
        )
    return units


def _event_units(beat: dict[str, Any]) -> list[dict[str, Any]]:
    raw = beat.get("event_units") or beat.get("event_flow") or []
    if not isinstance(raw, list):
        return []
    units: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        event_type = _text(item.get("type") or "speech_event") or "speech_event"
        actor = _text(item.get("actor") or item.get("role") or "")
        action = _text(item.get("action") or item.get("description") or "")
        dialogue = _text(item.get("dialogue") or item.get("line") or item.get("text") or "")
        if not action:
            continue
        if event_type == "environment_event":
            actor = ""
            dialogue = ""
        units.append(
            {
                "type": event_type,
                "actor": actor,
                "actor_name": role_display_name(actor) if actor else "",
                "action": action,
                "dialogue": dialogue,
                "target_role": _text(item.get("target_role") or item.get("target") or ""),
                "object": _text(item.get("object") or item.get("prop") or ""),
            }
        )
    return units


DIALOGUE_QUOTE_RE = re.compile(r"[“『\"]([^”』\"]+)[”』\"]")
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]")


def _context_action_from_plot(context: str, actor_name: str, target_name: str) -> str:
    clean = re.sub(r"\s+", " ", _text(context)).strip()
    if clean:
        parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(clean) if part.strip()]
        if parts:
            action = parts[-1][-90:].strip()
            if actor_name not in action:
                action = f"{actor_name}{action}"
            return action
    return f"{actor_name}看向{target_name}，短暂停顿"


def _infer_plot_speech_events(plot: str, visible_roles: list[str]) -> list[dict[str, Any]]:
    """Best-effort fallback: convert bare quoted plot dialogue into speech_event items."""
    text = _text(plot)
    roles = [role for role in visible_roles or [] if _text(role)]
    if not text or not roles:
        return []
    role_names = {role: role_display_name(role) for role in roles}
    matches = list(DIALOGUE_QUOTE_RE.finditer(text))
    if not matches:
        return []
    flow: list[dict[str, Any]] = []
    previous_end = 0
    last_actor_index = -1
    for match in matches:
        dialogue = _text(match.group(1))
        if not dialogue:
            previous_end = match.end()
            continue
        context = text[previous_end : match.start()]
        actor = ""
        actor_pos = -1
        for role, name in role_names.items():
            pos = context.rfind(name)
            if pos > actor_pos:
                actor = role
                actor_pos = pos
        if not actor:
            last_actor_index = (last_actor_index + 1) % len(roles)
            actor = roles[last_actor_index]
        else:
            last_actor_index = roles.index(actor) if actor in roles else last_actor_index
        actor_name = role_names.get(actor) or role_display_name(actor)
        other_roles = [role for role in roles if role != actor]
        target_role = other_roles[0] if other_roles else ""
        target_name = role_names.get(target_role) if target_role else "对方"
        flow.append(
            {
                "type": "speech_event",
                "actor": actor,
                "actor_name": actor_name,
                "action": _context_action_from_plot(context, actor_name, target_name or "对方"),
                "dialogue": dialogue,
                "target_role": target_role,
                "object": "",
                "inferred_from_plot": True,
            }
        )
        previous_end = match.end()
    return flow


def _merge_inferred_speech_with_scene_events(
    speech_flow: list[dict[str, Any]],
    scene_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not speech_flow or not scene_events:
        return speech_flow or scene_events
    merged = list(speech_flow)
    for index, event in enumerate(scene_events):
        insert_at = min((index + 1) * 2, len(merged))
        merged.insert(insert_at, event)
    return merged


def _offscreen_action_text(speaker_name: str, speech_type: str) -> str:
    if str(speech_type or "").strip().lower() == "offscreen":
        return f"{speaker_name}的声音从画外传来"
    return f"{speaker_name}看向对方，短暂停顿后开口"


def _build_event_flow(
    event_units: list[dict[str, Any]],
    action_units: list[dict[str, Any]],
    dialogue_units: list[dict[str, Any]],
    plot: str = "",
    visible_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    # dialogue_units 是权威、完整、有序的对白清单；event_units 更丰富（带逐句动作），
    # 但 beat 编写阶段可能漏掉画外说话人，只把画内对白写进 event_units。若直接整段
    # 采用 event_units，会静默丢掉画外对白，从而触发逐字对白校验失败。因此优先从
    # dialogue_units 构建，逐句借用 event_units 里已存在的动作描述。
    if dialogue_units:
        event_by_line = {
            _text(event.get("dialogue")): event
            for event in event_units
            if _text(event.get("dialogue"))
        }
        flow: list[dict[str, Any]] = []
        for index, dialogue in enumerate(dialogue_units):
            line = _text(dialogue.get("line"))
            speaker = _text(dialogue.get("speaker"))
            speaker_name = _text(dialogue.get("speaker_name")) or role_display_name(speaker)
            matched = event_by_line.get(line) if line else None
            if matched:
                actor = _text(matched.get("actor")) or speaker
                actor_name = _text(matched.get("actor_name")) or speaker_name
                action_text = _text(matched.get("action"))
                target_role = _text(matched.get("target_role")) or _text(dialogue.get("target_role") or "")
                object_ = _text(matched.get("object"))
            else:
                actor = speaker
                actor_name = speaker_name
                action_text = ""
                object_ = ""
                target_role = _text(dialogue.get("target_role") or "")
                # action_units 与 dialogue_units 并非按索引一一对齐（画外对白会错位），
                # 只有当动作所属角色与说话人一致时才借用，否则用通用动作兜底。
                action = action_units[index] if index < len(action_units) else {}
                if action and _text(action.get("role")) == speaker:
                    action_text = _text(action.get("action"))
                    object_ = _text(action.get("object"))
            if not action_text:
                action_text = _offscreen_action_text(actor_name, _text(dialogue.get("speech_type")))
            flow.append(
                {
                    "type": "speech_event",
                    "actor": actor,
                    "actor_name": actor_name,
                    "action": action_text,
                    "dialogue": line,
                    "target_role": target_role,
                    "object": object_,
                }
            )
        if flow:
            return flow

    if any(_text(event.get("dialogue")) for event in event_units):
        return event_units

    inferred_flow = _infer_plot_speech_events(plot, visible_roles or [])
    if inferred_flow:
        return _merge_inferred_speech_with_scene_events(inferred_flow, event_units)
    return event_units


def _count_cn(count: int) -> str:
    mapping = {1: "一个人", 2: "两个人", 3: "三个人", 4: "四个人"}
    return mapping.get(int(count or 0), f"{count}个人")


def build_prompt_ir(beat: dict[str, Any], segment_index: int, duration_sec: int | None = None) -> dict[str, Any]:
    """Build deterministic PromptIR from one normalized beat."""
    visible_roles = _unique_text(beat.get("visible_roles") or beat.get("important_roles") or beat.get("reference_roles"))
    reference_roles = _unique_text(beat.get("reference_roles") or visible_roles)
    offscreen_speakers = _unique_text(beat.get("offscreen_speakers"))
    mentioned_roles = _unique_text(beat.get("mentioned_roles"))
    visible_names = [role_display_name(role) for role in visible_roles]
    reference_names = [role_display_name(role) for role in reference_roles]
    dialogue_units = _dialogue_units(beat)
    action_units = _action_units(beat)
    event_units = _event_units(beat)
    plot = _text(beat.get("plot"))
    event_flow = _build_event_flow(event_units, action_units, dialogue_units, plot=plot, visible_roles=visible_roles)
    duration = _duration(beat, duration_sec)
    scene_id = _text(beat.get("scene_id"))
    scene_name = scene_display_name(scene_id)

    speaking_names: list[str] = []
    for item in dialogue_units:
        name = _text(item.get("speaker_name"))
        if name and name not in speaking_names:
            speaking_names.append(name)
    for item in event_flow:
        if not _text(item.get("dialogue")):
            continue
        name = _text(item.get("actor_name")) or role_display_name(_text(item.get("actor")))
        if name and name not in speaking_names:
            speaking_names.append(name)
    silent_names = [name for name in visible_names if name not in speaking_names]
    names_text = "和".join(visible_names) if len(visible_names) <= 2 else "、".join(visible_names)

    return {
        "segment_index": int(segment_index),
        "title": _text(beat.get("title")) or f"剧情段落{segment_index}",
        "duration_sec": duration,
        "scene": {
            "scene_id": scene_id,
            "scene_name": scene_name,
        },
        "characters": {
            "visible_roles": visible_roles,
            "visible_names": visible_names,
            "reference_roles": reference_roles,
            "reference_names": reference_names,
            "offscreen_speakers": offscreen_speakers,
            "mentioned_roles": mentioned_roles,
            "visible_count": len(visible_names),
            "visible_count_cn": _count_cn(len(visible_names)),
            "visible_names_text": names_text,
        },
        "story": {
            "plot": plot,
            "identity_constraints": _text(beat.get("identity_constraints")),
            "background_extras": beat.get("background_extras") or [],
        },
        "events": {
            "dialogue_units": dialogue_units,
            "action_units": action_units,
            "event_units": event_units,
            "event_flow": event_flow,
            "speaking_names": speaking_names,
            "silent_names": silent_names,
        },
        "camera": {
            "shot_type": _text(beat.get("camera") or beat.get("shot_type") or "中景稳定镜头"),
            "style": "写实电影感，现代真实场景，第一帧稳定，一镜到底",
        },
        "constraints": {
            "do_not_change_story": True,
            "no_new_locations": True,
            "no_new_dialogue": True,
            "no_new_clear_characters": True,
            "all_visible_at_first_frame": True,
            "single_take_no_cut": True,
            "mentioned_roles_not_visible": bool(mentioned_roles),
            "offscreen_speakers_not_visible": bool(offscreen_speakers),
            "forbidden_transition_words": [
                "走进",
                "进入",
                "走到",
                "走过来",
                "迎上来",
                "推门",
                "门口",
                "离开",
                "走出",
                "起身",
                "落座",
                "坐下",
                "站起",
                "来到",
            ],
        },
        "output_contract": {
            "format": "plain_text",
            "only_director_prompt": True,
            "no_markdown": True,
            "no_json": True,
            "no_identity_descriptions": True,
        },
    }


def _natural_recovery_for_event(event: dict[str, Any]) -> str:
    actor_name = _text(event.get("actor_name")) or role_display_name(_text(event.get("actor"))) or "说话的人物"
    object_name = _text(event.get("object"))
    target_name = role_display_name(_text(event.get("target_role"))) if event.get("target_role") else "对方"
    if object_name:
        return f"话音落下后，{actor_name}的动作在{object_name}旁自然收住，目光重新回到{target_name}身上。"
    return f"话音落下后，{actor_name}稍微收住动作，继续观察{target_name}的反应。"


def _natural_speech_sentence(event: dict[str, Any]) -> str:
    actor_name = _text(event.get("actor_name")) or role_display_name(_text(event.get("actor"))) or "当前人物"
    action = _text(event.get("action")) or f"{actor_name}看向对方，短暂停顿"
    dialogue = _text(event.get("dialogue"))
    if not dialogue:
        return action
    cleaned_action = action.rstrip("。.!！；;，,")
    return f"{cleaned_action}，自然开口说：“{dialogue}”{_natural_recovery_for_event(event)}"


def fallback_director_prompt(prompt_ir: dict[str, Any]) -> str:
    """Deterministic no-LLM fallback from PromptIR, written as natural direction."""
    scene = prompt_ir.get("scene") or {}
    chars = prompt_ir.get("characters") or {}
    story = prompt_ir.get("story") or {}
    events = prompt_ir.get("events") or {}
    duration = int(prompt_ir.get("duration_sec") or MIN_SEGMENT_SEC)
    scene_name = _text(scene.get("scene_name")) or "当前场景"
    names_text = _text(chars.get("visible_names_text")) or "当前人物"
    plot = _text(story.get("plot"))
    lines = [
        f"{scene_name}里，{names_text}从镜头开始就处在稳定的位置，空间、灯光、道具和背景保持真实自然。",
        f"镜头以连续的{duration}秒完成，没有切换，采用自然的中景电影感观察人物反应。",
    ]
    event_flow = events.get("event_flow") or []
    if event_flow:
        for event in event_flow:
            event_type = _text(event.get("type"))
            action = _text(event.get("action"))
            dialogue = _text(event.get("dialogue"))
            if event_type == "speech_event" and dialogue:
                lines.append(_natural_speech_sentence(event))
            elif action:
                lines.append(action)
    elif plot:
        lines.append(plot)
    else:
        dialogue_units = events.get("dialogue_units") or []
        for item in dialogue_units:
            speaker_name = _text(item.get("speaker_name") or item.get("speaker")) or "当前说话角色"
            target_name = role_display_name(_text(item.get("target_role"))) if item.get("target_role") else "对方"
            line = _text(item.get("line"))
            if line:
                lines.append(f"{speaker_name}看向{target_name}，短暂停顿后自然开口说：“{line}”话音落下后，{speaker_name}稍微收住动作，继续观察{target_name}的反应。")
    lines.append(f"最后几秒，{names_text}仍保持原来的空间关系，镜头缓缓停住，让环境安静下来。")
    return "\n".join(lines).strip()
