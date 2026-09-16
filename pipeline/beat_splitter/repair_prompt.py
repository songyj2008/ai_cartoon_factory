"""Prompt builder for one-beat complexity splitting."""
from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """
你是 Beat Complexity Splitter。

你的任务不是重新创作故事，而是把一个复杂 Beat 拆成多个更适合 AI 视频生成的 replacement_beats。

必须遵守：
1. 不修改剧情。
2. 不修改人物。
3. 不新增角色。
4. 不新增场景。
5. 保持对白核心含义。
6. 保持剧情方向和结局。
7. 每个 replacement beat 只能有一个稳定 focus pair。
8. 三人同场时，每个 replacement beat 最多一个 onscreen speaker。
9. 如果同一角色需要对不同对象说话，必须拆成不同 beat。
10. 如果出现道具交接，尽量作为 beat 边界。
11. 输出只能是 JSON object，不要 Markdown。

输出格式：
{
  "replacement_beats": [
    {
      "title": "...",
      "plot": "...",
      "scene_id": "...",
      "important_roles": [],
      "visible_roles": [],
      "reference_roles": [],
      "offscreen_speakers": [],
      "mentioned_roles": [],
      "dialogue_units": [],
      "action_units": [],
      "event_units": [],
      "identity_constraints": {},
      "background_extras": [],
      "estimated_duration_sec": 10
    }
  ]
}
"""


def build_split_payload(
    beat: dict[str, Any],
    risk_report: dict[str, Any],
    role_info: list[dict[str, Any]],
    scene_info: dict[str, Any],
    locked_outcome: str,
) -> dict[str, Any]:
    return {
        "task": "Split only the current complex beat into replacement_beats.",
        "locked_outcome": str(locked_outcome or "").strip(),
        "current_beat": beat,
        "risk_report": risk_report,
        "current_beat_role_info": role_info,
        "current_scene": scene_info,
        "local_constraints": [
            "Only return replacement_beats.",
            "Do not return the full story.",
            "Do not modify any other beat.",
            "Use only roles already present in current_beat important_roles/visible_roles/offscreen_speakers/mentioned_roles.",
            "Use the same scene_id as current_beat unless current_beat scene_id is empty.",
            "Preserve dialogue meaning; shorter wording is allowed only when meaning is unchanged.",
            "Each replacement beat should be directly filmable as one stable video segment.",
        ],
    }


def build_messages(payload: dict[str, Any], model_id: str | None = None) -> list[dict[str, str]]:
    from generation.model_rules import rules_for_model

    model_prompt = rules_for_model(model_id).beat_splitter_prompt or SYSTEM_PROMPT.strip()
    return [
        {"role": "system", "content": model_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]
