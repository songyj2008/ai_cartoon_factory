"""Local risk detector for beats that are too complex for stable video generation."""
from __future__ import annotations

from typing import Any


INTERACTION_WORDS = (
    "推", "递", "交", "接", "拿", "放", "塞", "递给", "交给", "推给",
    "钥匙", "文件", "手机", "杯", "包", "合同", "工牌", "货单",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _onscreen_dialogue_units(beat: dict[str, Any]) -> list[dict[str, Any]]:
    visible_roles = set(_text(x) for x in _list(beat.get("visible_roles")) if _text(x))
    units: list[dict[str, Any]] = []
    for item in _list(beat.get("dialogue_units")):
        if not isinstance(item, dict):
            continue
        speaker = _text(item.get("speaker_name") or item.get("speaker"))
        speech_type = _text(item.get("speech_type") or "onscreen").lower()
        visible = bool(item.get("visible", speech_type == "onscreen"))
        if speaker and visible and (not visible_roles or speaker in visible_roles):
            units.append(item)
    return units


def _dialogue_pairs(beat: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in _onscreen_dialogue_units(beat):
        speaker = _text(item.get("speaker_name") or item.get("speaker"))
        target = _text(item.get("target_role"))
        if speaker and target and speaker != target:
            pairs.append((speaker, target))
    return pairs


def _focus_pairs(beat: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = list(_dialogue_pairs(beat))
    for item in _list(beat.get("action_units")):
        if not isinstance(item, dict):
            continue
        actor = _text(item.get("role") or item.get("actor"))
        target = _text(item.get("target_role"))
        if actor and target and actor != target:
            pairs.append((actor, target))
    for item in _list(beat.get("event_units")):
        if not isinstance(item, dict):
            continue
        actor = _text(item.get("actor"))
        target = _text(item.get("target_role"))
        if actor and target and actor != target:
            pairs.append((actor, target))
    return pairs


def _unordered_pair(pair: tuple[str, str]) -> tuple[str, str]:
    left, right = pair
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def _interaction_then_speaker_switch(beat: dict[str, Any]) -> bool:
    last_interaction_actor = ""
    for item in _list(beat.get("event_units")):
        if not isinstance(item, dict):
            continue
        event_type = _text(item.get("type")).lower()
        actor = _text(item.get("actor"))
        action_text = " ".join(
            _text(item.get(key))
            for key in ("action", "object", "dialogue")
        )
        if event_type == "speech_event":
            speaker = actor
            if last_interaction_actor and speaker and speaker != last_interaction_actor:
                return True
            last_interaction_actor = ""
            continue
        if event_type in {"silent_action", "action"} and any(word in action_text for word in INTERACTION_WORDS):
            last_interaction_actor = actor
    return False


def detect_ltx_beat_risks(beat: dict[str, Any], beat_index: int | None = None) -> dict[str, Any]:
    """Return a risk report for one beat without modifying it."""
    index = int(beat_index or beat.get("id") or beat.get("order") or 0)
    visible_roles = _unique([_text(x) for x in _list(beat.get("visible_roles"))])
    onscreen_speakers = _unique([
        _text(item.get("speaker_name") or item.get("speaker"))
        for item in _onscreen_dialogue_units(beat)
    ])
    dialogue_pairs = _dialogue_pairs(beat)
    focus_pairs = _focus_pairs(beat)

    risk_codes: list[str] = []
    details: dict[str, Any] = {
        "visible_roles": visible_roles,
        "onscreen_speakers": onscreen_speakers,
        "dialogue_pairs": dialogue_pairs,
        "focus_pairs": focus_pairs,
    }

    if len(visible_roles) >= 3 and len(onscreen_speakers) >= 2:
        risk_codes.append("THREE_VISIBLE_MULTI_SPEAKER")

    targets_by_speaker: dict[str, list[str]] = {}
    for speaker, target in dialogue_pairs:
        targets_by_speaker.setdefault(speaker, [])
        if target not in targets_by_speaker[speaker]:
            targets_by_speaker[speaker].append(target)
    if any(len(targets) >= 2 for targets in targets_by_speaker.values()):
        risk_codes.append("DIALOGUE_TARGET_SWITCH")
        details["targets_by_speaker"] = targets_by_speaker

    unique_focus_pairs = _unique(["↔".join(_unordered_pair(pair)) for pair in focus_pairs])
    if len(unique_focus_pairs) >= 2:
        risk_codes.append("FOCUS_PAIR_SWITCH")
        details["unique_focus_pairs"] = unique_focus_pairs

    if _interaction_then_speaker_switch(beat):
        risk_codes.append("INTERACTION_THEN_SPEAKER_SWITCH")

    title = _text(beat.get("title")) or f"Beat {index}"
    if risk_codes:
        summary = f"{title} contains complex role focus or dialogue switching."
        suggestion = "Split this beat into smaller beats with one stable focus pair and no more than one speaker per beat when three roles are visible."
    else:
        summary = f"{title} is locally stable by the first-pass complexity rules."
        suggestion = "No split is required by the current detector."

    return {
        "beat_index": index,
        "beat_id": beat.get("id"),
        "title": title,
        "risk_codes": risk_codes,
        "summary": summary,
        "suggestion": suggestion,
        "details": details,
    }


def detect_beat_risks(beat: dict[str, Any], beat_index: int | None = None, model_id: str | None = None) -> dict[str, Any]:
    from generation.model_rules import rules_for_model

    detector = rules_for_model(model_id).beat_risk_detector
    if not callable(detector):
        return {
            "beat_index": int(beat_index or beat.get("id") or 0),
            "beat_id": beat.get("id"),
            "title": _text(beat.get("title")),
            "risk_codes": [],
            "summary": "No model-owned complexity detector is configured.",
            "suggestion": "No split is required.",
            "details": {},
        }
    return detector(beat, beat_index)


def detect_complex_beats(beats_data: dict[str, Any], model_id: str | None = None) -> dict[str, Any]:
    beats = [item for item in _list((beats_data or {}).get("beats")) if isinstance(item, dict)]
    policy = beats_data.get("generation_policy") if isinstance(beats_data.get("generation_policy"), dict) else {}
    resolved_model_id = str(model_id or policy.get("model_id") or "").strip()
    reports = [detect_beat_risks(beat, index, model_id=resolved_model_id) for index, beat in enumerate(beats, start=1)]
    risky = [item for item in reports if item.get("risk_codes")]
    return {
        "beat_count": len(beats),
        "risky_count": len(risky),
        "reports": reports,
        "risky_reports": risky,
    }
