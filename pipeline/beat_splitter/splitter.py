"""One-beat LLM repair for complex beat splitting."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline.beat_splitter.guard import detect_beat_risks
from pipeline.beat_splitter.io import beat_at_index, replace_beat, save_json
from pipeline.beat_splitter.repair_prompt import build_messages, build_split_payload
from services.llm import call_deepseek_chat_sdk
from services.project_bible import background_by_id, character_by_id, load_project_bible


def _extract_json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty beat splitter response")
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("beat splitter response does not contain JSON")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("beat splitter response must be a JSON object")
    return data


def _role_info_for_beat(beat: dict[str, Any]) -> list[dict[str, Any]]:
    bible = load_project_bible()
    characters = character_by_id(bible)
    role_ids = []
    for key in ("important_roles", "visible_roles", "reference_roles", "offscreen_speakers", "mentioned_roles"):
        for role in beat.get(key) or []:
            role_text = str(role or "").strip()
            if role_text and role_text not in role_ids:
                role_ids.append(role_text)
    result = []
    for role_id in role_ids:
        entry = characters.get(role_id) or {}
        result.append(
            {
                "id": role_id,
                "display_name": entry.get("display_name") or entry.get("name_cn") or role_id,
                "role": entry.get("role") or "",
                "aliases": entry.get("aliases") or [],
                "asset_identity_id": entry.get("asset_identity_id") or "",
            }
        )
    return result


def _scene_info_for_beat(beat: dict[str, Any]) -> dict[str, Any]:
    scene_id = str(beat.get("scene_id") or "").strip()
    entry = background_by_id(load_project_bible()).get(scene_id) or {}
    return {
        "scene_id": scene_id,
        "display_name": entry.get("display_name") or entry.get("name_cn") or scene_id,
        "aliases": entry.get("aliases") or [],
        "asset_background_id": entry.get("asset_background_id") or "",
    }


def _call_splitter_llm(config: dict[str, Any], messages: list[dict[str, str]], temp_dir: Path) -> dict[str, Any]:
    from services.web_llm_bridge import routes_for, wait_for_llm
    providers = routes_for('beats')
    if providers != ['api']:
        data, _ = wait_for_llm('\n'.join(m['content'] for m in messages if m['role']=='system'),
            '\n'.join(m['content'] for m in messages if m['role']!='system'),
            kind='json',entry='beats',provider=providers if len(providers)>1 else providers[0],call_hint='beat_splitter')
        return data
    temp_dir.mkdir(parents=True, exist_ok=True)
    text, full_data, details = call_deepseek_chat_sdk(
        config,
        messages,
        temperature=0.1,
        max_tokens=8000,
        json_mode=True,
        request_path=temp_dir / "beat_splitter_request.json",
        full_response_path=temp_dir / "beat_splitter_response.json",
        raw_response_path=temp_dir / "beat_splitter_raw.txt",
    )
    finish_reason = str(details.get("finish_reason") or "")
    if finish_reason == "length":
        raise ValueError("Beat splitter response was truncated by max_tokens")
    return _extract_json_object(text)


def split_complex_beat(
    beats_data: dict[str, Any],
    beat_index: int,
    config: dict[str, Any],
    temp_dir: str | Path,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Split one beat and return a new beats payload plus diagnostics."""
    beat = beat_at_index(beats_data, beat_index)
    policy = beats_data.get("generation_policy") if isinstance(beats_data.get("generation_policy"), dict) else {}
    resolved_model_id = str(model_id or policy.get("model_id") or "").strip()
    risk_report = detect_beat_risks(beat, beat_index, model_id=resolved_model_id)
    payload = build_split_payload(
        beat=beat,
        risk_report=risk_report,
        role_info=_role_info_for_beat(beat),
        scene_info=_scene_info_for_beat(beat),
        locked_outcome=str(beats_data.get("locked_outcome") or ""),
    )
    response = _call_splitter_llm(config, build_messages(payload, model_id=resolved_model_id), Path(temp_dir))
    replacements = response.get("replacement_beats")
    if not isinstance(replacements, list) or not replacements:
        raise ValueError("Beat splitter output must contain non-empty replacement_beats")
    replacements = [item for item in replacements if isinstance(item, dict)]
    if not replacements:
        raise ValueError("replacement_beats contains no beat objects")

    if resolved_model_id == 'minimax_h3_local_ref2va':
        from generation.pipelines.minimax_h3.prompt_planner import _validated_plan, _assert_current_dialogue_preserved
        model = 'minimax_h3_local_ref2va'
        merged_shots = []
        for replacement in replacements:
            duration = float(replacement.get('estimated_duration_sec') or 0)
            if not 4 <= duration <= 15 or not duration.is_integer():
                raise ValueError('H3 replacement duration must be a 4-15 second integer')
            plan = _validated_plan((replacement.get('model_hints') or {}).get(model), duration)
            merged_shots.extend(plan['shots'])
        expected = beat.get('dialogue_units')
        if expected is None:
            old_shots = ((beat.get('model_hints') or {}).get(model) or {}).get('shots', [])
            expected = []
            for shot in old_shots:
                values = shot.get('dialogues', shot.get('dialogue', []))
                expected.extend([values] if isinstance(values, dict) else values or [])
        _assert_current_dialogue_preserved({'dialogue_units': expected, 'dialogue_authoritative': True},
                                          {'shots': merged_shots})
        beats_data = {**beats_data, 'generation_policy': {**policy, 'model_id': model}}
    new_beats = replace_beat(beats_data, beat_index, replacements)
    return {
        "beat_index": beat_index,
        "risk_report": risk_report,
        "replacement_count": len(replacements),
        "replacement_beats": replacements,
        "beats": new_beats,
    }


def save_split_result(path: str | Path, result: dict[str, Any]) -> Path:
    return save_json(path, result)
