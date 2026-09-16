"""Compile PromptIR into the per-request LLM instruction."""
from __future__ import annotations

import json
from typing import Any


def compile_ir_to_llm_prompt(prompt_ir: dict[str, Any]) -> str:
    """Compile PromptIR into the current task payload for the LiconMSR prompt LLM."""
    return f"""
[Current Task]
把下面的 PromptIR 编译成适合 LTX2.3 / LiconMSR 的导演分镜正文。
你不是剧情编剧，只是导演分镜文案编译器。必须严格根据 PromptIR 输出，不得自行推断或新增。

[Hard Constraints]
1. 剧情、地点、人物和台词来自 PromptIR，不扩写新的剧情分支。
2. 画面聚焦 PromptIR.characters.visible_names 中的人物，用自然开场说明他们已经在场。
3. reference_names 只用于身份参考，不在正文里逐个介绍人物外貌、年龄、服装、体型或气质。
4. dialogue_units 中的台词逐句写入正文，并自然绑定说话角色、动作和反应。
5. 如果 event_flow 中存在 speech_event，必须逐条使用 speech_event.actor_name、action、dialogue；不要遗漏，不要改台词。
6. 每个 speech_event 必须写成“角色动作 + 停顿/观察 + 角色自然说出对白 + 话音落下后的恢复动作”。
7. 不要输出裸引号对白；不要让对白单独成行。对白必须嵌入说话角色的表演句中，例如“小刘停顿片刻，语气急促却克制地说：‘……’”。
8. silent_names 用倾听、停顿、视线、手部或姿态反应表现，不写“闭口/不说话/动嘴”等控制词。
9. mentioned_roles 只通过台词、文件、手机、电脑屏幕或画外信息被自然提及。
10. offscreen_speakers 只通过电话、广播、门外或画外声音被自然感知。
11. 优先按 PromptIR.events.event_flow 的顺序输出；没有 event_flow 时，才从 plot、action_units、dialogue_units 整理。
12. 用自然电影语言写出 duration_sec 秒连续镜头，没有切换。
13. 结尾写最后几秒的稳定状态，让环境和人物反应自然收束。

[Output]
只输出导演分镜正文。
不要 markdown。
不要 JSON。
不要解释。
不要输出人物参考图身份描述。
不要输出“必须、不得、不允许、保持闭口、不要说话、动嘴、只有某人说话、没有其他人物进入、没有重复人物”等编程式控制句。
不要把对白单独写成一整行引号；要把对白嵌入说话角色的自然动作和语气中。

[PromptIR]
{json.dumps(prompt_ir, ensure_ascii=False, indent=2)}
""".strip()
