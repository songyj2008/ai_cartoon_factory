"""IO and local merge helpers for beat complexity splitting."""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from pipeline.events import ensure_event_fields_on_beats


def ensure_beat_uids(beats_data: dict[str, Any]) -> dict[str, Any]:
    """Give every Beat an immutable identity distinct from its display order."""
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    for beat in beats if isinstance(beats, list) else []:
        if isinstance(beat, dict) and not str(beat.get("beat_uid") or "").strip():
            beat["beat_uid"] = f"beat_{uuid.uuid4().hex}"
    return beats_data


def load_beats(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("beats file must contain a JSON object")
    if not isinstance(data.get("beats"), list):
        raise ValueError("beats file must contain a beats array")
    return data


def save_json(path: str | Path, data: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def beat_at_index(beats_data: dict[str, Any], beat_index: int) -> dict[str, Any]:
    beats = beats_data.get("beats") if isinstance(beats_data.get("beats"), list) else []
    if beat_index < 1 or beat_index > len(beats):
        raise IndexError(f"beat_index out of range: {beat_index}")
    beat = beats[beat_index - 1]
    if not isinstance(beat, dict):
        raise ValueError(f"beat {beat_index} is not an object")
    return beat


def distribute_duration(total_sec: int, count: int) -> list[int]:
    count = max(1, int(count or 1))
    total_sec = max(count, int(round(float(total_sec or count * 10))))
    base = total_sec // count
    remainder = total_sec % count
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _unique_text(values: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _filter_roles(values: Any, allowed: set[str]) -> list[str]:
    return [role for role in _unique_text(values) if role in allowed]


def _filter_dialogue_units(values: Any, allowed: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        speaker = str(value.get("speaker_name") or value.get("speaker") or "").strip()
        target = str(value.get("target_role") or "").strip()
        if speaker not in allowed:
            continue
        item = deepcopy(value)
        item["speaker_name"] = speaker
        if target and target not in allowed:
            item["target_role"] = ""
        result.append(item)
    return result


def _filter_action_units(values: Any, allowed: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        actor = str(value.get("role") or value.get("actor") or "").strip()
        target = str(value.get("target_role") or "").strip()
        if actor not in allowed:
            continue
        item = deepcopy(value)
        item["role"] = actor
        if target and target not in allowed:
            item["target_role"] = ""
        result.append(item)
    return result


def _filter_event_units(values: Any, allowed: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        actor = str(value.get("actor") or "").strip()
        target = str(value.get("target_role") or "").strip()
        if actor and actor not in allowed:
            continue
        item = deepcopy(value)
        if target and target not in allowed:
            item["target_role"] = ""
        result.append(item)
    return result


def replace_beat(beats_data: dict[str, Any], beat_index: int, replacement_beats: list[dict[str, Any]]) -> dict[str, Any]:
    if not replacement_beats:
        raise ValueError("replacement_beats must not be empty")

    data = deepcopy(beats_data)
    ensure_beat_uids(data)
    beats = [item for item in (data.get("beats") or []) if isinstance(item, dict)]
    original = beat_at_index(data, beat_index)
    original_duration = int(round(float(original.get("estimated_duration_sec") or original.get("duration_sec") or 10)))
    durations = distribute_duration(original_duration, len(replacement_beats))
    allowed_roles = set(
        _unique_text(original.get("important_roles"))
        + _unique_text(original.get("visible_roles"))
        + _unique_text(original.get("reference_roles"))
        + _unique_text(original.get("offscreen_speakers"))
        + _unique_text(original.get("mentioned_roles"))
    )
    original_scene_id = str(original.get("scene_id") or "").strip()

    clean_replacements: list[dict[str, Any]] = []
    for offset, (beat, duration) in enumerate(zip(replacement_beats, durations), start=1):
        item = deepcopy(beat)
        if original_scene_id:
            item["scene_id"] = original_scene_id
        else:
            item.setdefault("scene_id", "")
        item["important_roles"] = _filter_roles(item.get("important_roles") or original.get("important_roles"), allowed_roles)
        item["visible_roles"] = _filter_roles(item.get("visible_roles") or original.get("visible_roles"), allowed_roles)
        item["reference_roles"] = _filter_roles(item.get("reference_roles") or original.get("reference_roles"), set(item["visible_roles"]))
        item["offscreen_speakers"] = _filter_roles(item.get("offscreen_speakers") or [], allowed_roles)
        item["mentioned_roles"] = _filter_roles(item.get("mentioned_roles") or [], allowed_roles)
        item["dialogue_units"] = _filter_dialogue_units(item.get("dialogue_units") or [], allowed_roles)
        item["action_units"] = _filter_action_units(item.get("action_units") or [], allowed_roles)
        item["event_units"] = _filter_event_units(item.get("event_units") or [], allowed_roles)
        item.setdefault("identity_constraints", original.get("identity_constraints") or {})
        item.setdefault("background_extras", original.get("background_extras") or [])
        item["estimated_duration_sec"] = duration
        item["split_from_beat_id"] = original.get("id") or beat_index
        item["split_from_beat_uid"] = original.get("beat_uid") or ""
        item["beat_uid"] = f"beat_{uuid.uuid4().hex}"
        item["split_part"] = offset
        item["split_part_count"] = len(replacement_beats)
        clean_replacements.append(item)

    merged = beats[:beat_index - 1] + clean_replacements + beats[beat_index:]
    for index, beat in enumerate(merged, start=1):
        beat["id"] = index
        beat["order"] = index
        beat["node_id"] = f"beat_{index:03d}"

    data["beats"] = merged
    data["total_duration_sec"] = sum(int(round(float(item.get("estimated_duration_sec") or 0))) for item in merged)
    if not data.get("target_duration_sec"):
        data["target_duration_sec"] = data["total_duration_sec"]
    return ensure_event_fields_on_beats(data)
