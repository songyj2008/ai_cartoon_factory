"""Fresh model-backed H3 Shot planning for one edited Beat."""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from generation.pipelines.minimax_h3.contracts import H3_REF2VA_MODEL_ID
from generation.pipelines.minimax_h3.rules import H3_BLOCKING_PROMPT
from services.context import CONFIG
from services.file_utils import save_json
from services.logger import log


_SYSTEM_PROMPT = """
你是 MiniMax H3 Ref2VA 的单 Beat 镜头提示词规划器。
只根据用户提供的“当前 Beat”重新规划这个 Beat 的 H3 镜头，不得使用任何旧提示词或旧 model_hints。
剧情、角色、地点、动作结果和台词必须来自当前 Beat，不得沿用旧版本，不得新增剧情。
把当前 Beat 规划成一个 4-15 秒视频，可包含一个或多个按时间连续的 Shot。
每个 Shot 使用明确的主体、动作、机位、台词、声音和连续性描述。
台词必须逐字采用 current_beat.dialogue_units；如果 plot 中还有明确引号台词，也要保持原文。
如果当前 Beat 修改了人物所在载具、颜色、位置或动作，必须在 Shot 中准确体现。
不要输出 <Subject N>、<Picture N> 等素材编号，它们由本地编译器绑定。

仅输出 JSON object，结构如下：
{
  "core_idea": "当前 Beat 的一句话核心创意",
  "shot_mode": "multi_shot 或 one_take",
  "shots": [
    {
      "start_sec": 0,
      "end_sec": 5,
      "scene_id": "可选，来自当前 Beat",
      "shot_size": "景别",
      "subject": "明确主体",
      "action": "可见动作",
      "blocking": "出镜人物的位置、身体朝向、视线对象、开场动作状态",
      "camera": "机位和运动",
      "dialogue": {"speaker": "说话角色", "scope": "onscreen", "line": "原台词"},
      "audio": "声音",
      "continuity": "连续性"
    }
  ],
  "exclusions": [],
  "non_diegetic_music": "",
  "exact_text": []
}
没有台词的 Shot 省略 dialogue。最后一个 Shot 的 end_sec 必须等于 Beat 时长。
""".strip() + "\n\n" + H3_BLOCKING_PROMPT


def _call_model(system_prompt: str, user_prompt: str, **kwargs: Any):
    # Keep the OpenAI SDK dependency at the actual network boundary so the
    # deterministic planner validation remains importable in lightweight
    # local checks.
    from services.llm import call_llm

    return call_llm(system_prompt, user_prompt, **kwargs)


def _duration(beat: Mapping[str, Any]) -> float:
    try:
        value = float(beat.get("estimated_duration_sec") or beat.get("duration_sec") or 0)
    except (TypeError, ValueError):
        value = 0
    if value < 4 or value > 15 or not value.is_integer():
        raise ValueError("H3 Beat duration must be an integer between 4 and 15 seconds")
    return value


def _validated_plan(value: Any, duration: float) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("H3 prompt planner returned a non-object result")
    raw = value.get("model_hints") if isinstance(value.get("model_hints"), Mapping) else value
    if isinstance(raw, Mapping) and isinstance(raw.get(H3_REF2VA_MODEL_ID), Mapping):
        raw = raw[H3_REF2VA_MODEL_ID]
    if not isinstance(raw, Mapping):
        raise ValueError("H3 prompt planner result is missing its plan")
    shots = raw.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("H3 prompt planner returned no Shots")
    normalized: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(shots, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"H3 prompt planner Shot {index} is not an object")
        shot = dict(item)
        try:
            start = float(shot.get("start_sec"))
            end = float(shot.get("end_sec"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"H3 prompt planner Shot {index} has invalid timing") from exc
        if start < 0 or end <= start or start + 1e-6 < previous_end or end > duration + 1e-6:
            raise ValueError(f"H3 prompt planner Shot {index} has an invalid range")
        shot["start_sec"] = int(start) if start.is_integer() else start
        shot["end_sec"] = int(end) if end.is_integer() else end
        previous_end = end
        normalized.append(shot)
    if abs(previous_end - duration) > 1e-6:
        raise ValueError("H3 prompt planner's final Shot must end at the Beat duration")
    mode = str(raw.get("shot_mode") or ("one_take" if len(normalized) == 1 else "multi_shot")).strip().lower()
    if mode not in {"one_take", "multi_shot"}:
        raise ValueError("H3 prompt planner returned an invalid shot_mode")
    if mode == "one_take" and len(normalized) != 1:
        raise ValueError("H3 one_take plan must contain exactly one Shot")
    return {
        "core_idea": str(raw.get("core_idea") or "").strip(),
        "shot_mode": mode,
        "shots": normalized,
        "exclusions": list(raw.get("exclusions") or []),
        "non_diegetic_music": str(raw.get("non_diegetic_music") or "").strip(),
        "exact_text": raw.get("exact_text") or [],
    }


def _plan_dialogue_lines(plan: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for shot in plan.get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        raw = shot.get("dialogues") if isinstance(shot.get("dialogues"), list) else [shot.get("dialogue")]
        for dialogue in raw:
            if isinstance(dialogue, Mapping):
                line = str(dialogue.get("line") or dialogue.get("text") or "").strip()
            else:
                line = str(dialogue or "").strip()
                if "：" in line:
                    line = line.split("：", 1)[1].strip()
            if line:
                lines.append(line)
    return lines


def _assert_current_dialogue_preserved(beat: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    expected = [
        str(item.get("line") or item.get("text") or "").strip()
        for item in beat.get("dialogue_units") or []
        if isinstance(item, Mapping) and str(item.get("line") or item.get("text") or "").strip()
    ]
    actual = _plan_dialogue_lines(plan)
    missing = [line for line in expected if line not in actual]
    if missing:
        raise ValueError("H3 prompt planner omitted current Beat dialogue: " + "；".join(missing))


def regenerate_h3_prompt_plan(beats_data: dict[str, Any], segment_index: int) -> dict[str, Any]:
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else None
    index = int(segment_index)
    if not isinstance(beats, list) or index <= 0 or index > len(beats):
        raise ValueError(f"H3 Beat does not exist: {index}")
    beat = beats[index - 1]
    if not isinstance(beat, dict):
        raise ValueError(f"H3 Beat {index} is not an object")
    duration = _duration(beat)

    # Old H3 hints are deliberately excluded from the model request.  This is
    # the cache boundary that makes regeneration reflect the edited Beat.
    current_beat = copy.deepcopy(beat)
    current_beat.pop("model_hints", None)
    current_beat.pop("h3_shots", None)
    current_beat.pop("shots", None)
    previous = beats[index - 2] if index > 1 else None
    previous_context = None
    if isinstance(previous, dict):
        previous_hints = (previous.get("model_hints") or {}).get(H3_REF2VA_MODEL_ID) or {}
        previous_shots = previous_hints.get("shots") or []
        previous_context = {"scene_id": previous.get("scene_id"), "plot": previous.get("plot"),
                            "ending_shot": previous_shots[-1] if previous_shots else None}
    response, _cost = _call_model(
        _SYSTEM_PROMPT,
        json.dumps({"segment_index": index, "duration_sec": duration, "current_beat": current_beat,
                    "previous_segment_context": previous_context,
                    "context_usage": "前段仅用于空间和动作承接，不复制其剧情、对白或覆盖当前 Beat；换场重新建立空间。"}, ensure_ascii=False),
        max_tokens=int(CONFIG.get("h3_prompt_regenerator_max_tokens", 3500) or 3500),
        label=f"h3_prompt_regenerate_{index:03d}",
    )
    plan = _validated_plan(response, duration)
    _assert_current_dialogue_preserved(beat, plan)
    all_hints = dict(beat.get("model_hints") or {})
    all_hints[H3_REF2VA_MODEL_ID] = plan
    beat["model_hints"] = all_hints
    save_json("beats.json", beats_data)
    log(f"[H3][prompt_regen] Beat {index} regenerated with {len(plan['shots'])} fresh Shots", "STEP")
    return plan
