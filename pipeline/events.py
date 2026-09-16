"""Beat event helpers for the LiconMSR direct workflow."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


OFFSCREEN_SPEECH_TYPES = {
    "offscreen",
    "offscreen_phone",
    "phone",
    "voice_over",
    "intercom",
    "broadcast",
    "outside_door",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_text(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _subset(values: Any, allowed: set[str]) -> list[str]:
    return [item for item in _unique_text(values) if item in allowed]


def _normalise_background_extras(values: Any) -> list[dict[str, Any]]:
    extras: list[dict[str, Any]] = []
    for value in values or []:
        item = value if isinstance(value, dict) else {"description": value}
        description = _text(item.get("description") or item.get("appearance"))
        if not description:
            continue
        extras.append(
            {
                "description": description,
                "clarity": "low",
                "occupancy": "small",
                "speaking": False,
                "interacting": False,
            }
        )
    return extras


def _normalise_dialogue_units(
    values: Any,
    registered_roles: set[str],
    visible_roles: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    units: list[dict[str, Any]] = []
    offscreen_speakers: list[str] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        speaker = _text(value.get("speaker_name") or value.get("speaker"))
        line = _text(value.get("line") or value.get("text"))
        target = _text(value.get("target_role"))
        if not speaker or not line or speaker not in registered_roles:
            continue
        if target and target not in registered_roles:
            target = ""
        speech_type = _text(value.get("speech_type") or "onscreen").lower()
        visible = bool(value.get("visible", speech_type not in OFFSCREEN_SPEECH_TYPES))
        if speech_type in OFFSCREEN_SPEECH_TYPES:
            visible = False
        if visible and speaker not in visible_roles:
            visible = False
            speech_type = "offscreen" if speech_type == "onscreen" else speech_type
        if not visible:
            offscreen_speakers.append(speaker)
        units.append(
            {
                "speaker_name": speaker,
                "line": line,
                "speech_type": "onscreen" if visible else (speech_type or "offscreen"),
                "visible": visible,
                "target_role": target,
            }
        )
    return units, _unique_text(offscreen_speakers)


def _normalise_action_units(values: Any, registered_roles: set[str], visible_roles: set[str]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        role = _text(value.get("role"))
        action = _text(value.get("action"))
        target = _text(value.get("target_role"))
        if not role or not action or role not in registered_roles:
            continue
        visibility = _text(value.get("visibility") or ("onscreen" if role in visible_roles else "offscreen")).lower()
        if visibility != "onscreen" or role not in visible_roles:
            continue
        if target and target not in registered_roles:
            target = ""
        units.append(
            {
                "role": role,
                "action": action,
                "target_role": target,
                "object": _text(value.get("object")),
                "motion_scale": _text(value.get("motion_scale")) or "small",
                "timing": _text(value.get("timing")) or "with_speech",
                "visibility": "onscreen",
            }
        )
    return units


def _normalise_scene_id(beat: dict[str, Any]) -> str:
    return _text(beat.get("scene_id") or beat.get("background_id") or beat.get("scene"))


def ensure_event_fields_on_beats(beats_data: dict[str, Any] | None) -> dict[str, Any]:
    """Return beats with explicit visibility fields for fast local mapping.

    Only reference_roles are allowed to drive LiconMSR character reference images.
    mentioned_roles and offscreen_speakers never get reference slots.
    """
    data = deepcopy(beats_data if isinstance(beats_data, dict) else {})
    from generation.model_rules import rules_for_beats_data

    model_rules = rules_for_beats_data(data)
    reference_role_limit = model_rules.max_reference_roles
    beats = data.get("beats") if isinstance(data.get("beats"), list) else []
    normalised: list[dict[str, Any]] = []

    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        item = dict(beat)
        item["id"] = int(item.get("id") or index)
        item["order"] = int(item.get("order") or item["id"])
        item["node_id"] = _text(item.get("node_id")) or f"beat_{item['id']:03d}"

        important_roles = _unique_text(item.get("important_roles"))
        registered_roles = set(important_roles)
        if "visible_roles" in item:
            visible_source = item.get("visible_roles")
        elif "reference_roles" in item:
            visible_source = item.get("reference_roles")
        else:
            visible_source = important_roles
        visible_roles = _subset(visible_source, registered_roles)
        reference_source = item.get("reference_roles") if "reference_roles" in item else visible_roles
        reference_roles = _subset(reference_source, set(visible_roles))
        mentioned_roles = _subset(item.get("mentioned_roles"), registered_roles)
        declared_offscreen = _subset(item.get("offscreen_speakers"), registered_roles)

        dialogue_units, dialogue_offscreen = _normalise_dialogue_units(
            item.get("dialogue_units"),
            registered_roles,
            set(visible_roles),
        )
        offscreen_speakers = _unique_text(declared_offscreen + dialogue_offscreen)
        action_units = _normalise_action_units(item.get("action_units"), registered_roles, set(visible_roles))

        # Target roles and offscreen speakers are not visible by default.
        visible_roles = [role for role in visible_roles if role not in offscreen_speakers]
        reference_roles = [role for role in reference_roles if role in visible_roles and role not in offscreen_speakers]
        for role in important_roles:
            if role not in visible_roles and role not in offscreen_speakers and role not in mentioned_roles:
                mentioned_roles.append(role)
        mentioned_roles = [role for role in _unique_text(mentioned_roles) if role not in visible_roles]

        item["important_roles"] = important_roles
        item["visible_roles"] = visible_roles
        item["reference_roles"] = reference_roles[:reference_role_limit] if reference_role_limit else reference_roles
        raw_image_ids = item.get("reference_image_ids")
        item["reference_image_ids"] = {
            role: _text(raw_image_ids.get(role))
            for role in item["reference_roles"]
            if isinstance(raw_image_ids, dict) and _text(raw_image_ids.get(role))
        }
        item["offscreen_speakers"] = offscreen_speakers
        item["mentioned_roles"] = mentioned_roles
        item["dialogue_units"] = dialogue_units
        item["action_units"] = action_units
        item["background_extras"] = _normalise_background_extras(item.get("background_extras"))
        item["scene_id"] = _normalise_scene_id(item)
        if "estimated_duration_sec" not in item:
            item["estimated_duration_sec"] = item.get("duration_sec") or 15
        normalised.append(item)

    data["beats"] = normalised
    if "total_duration_sec" not in data:
        total = 0
        for beat in normalised:
            try:
                total += int(round(float(beat.get("estimated_duration_sec") or 0)))
            except Exception:
                pass
        data["total_duration_sec"] = total
    return data
