"""Creative/validation rules owned exclusively by MiniMax H3 Ref2VA."""
from __future__ import annotations

import copy
import math
from typing import Any

from generation.model_rules import BeatPlanningStrategy, ModelRuleSet, QualityRule


def normalize_h3_hints(raw: dict, model_id: str) -> dict:
    """Preserve a flat model response without inventing any Shot content."""
    if "shots" in raw and model_id in raw:
        raise ValueError("H3 分镜同时出现在两个层级，请只使用 model_hints.minimax_h3_local_ref2va")
    result = {model_id: copy.deepcopy(raw)} if "shots" in raw else copy.deepcopy(raw)
    hints = result.get(model_id)
    if not isinstance(hints, dict) or not isinstance(hints.get("shots"), list) or not hints["shots"]:
        raise ValueError("H3 缺少有效 shots，请生成 model_hints.minimax_h3_local_ref2va.shots")
    if any(not isinstance(shot, dict) for shot in hints["shots"]):
        raise ValueError("H3 shots 中每个分镜必须是对象")
    return result


H3_BLOCKING_PROMPT = """
【按剧情规划人物调度】
- 每个 Shot 必须有 blocking 字段，用自然语言明确实际出镜人物相对场景和彼此的位置、身体朝向、视线对象、开场正在进行的动作；无人物镜头描述主体空间关系即可。
- 第一个 Shot 从剧情正在发生的状态开始；不要为了展示人物参考图而先摆正面合照。剧情需要并排、正面或面向镜头时可以采用。
- camera 与 blocking 必须一致：先确定场景中的人物位置，再决定摄影机从背面、侧面、肩后或其他适合剧情的位置观察。看光幕不等于看镜头。
- continuity 写清本 Shot 结束位置与下一 Shot 的承接；连续场景承接前段结束状态，换场重新建立空间，不凭空改变人物相对位置。
- 人物参考图用于身份外观；图中站姿、手势、朝向和拍摄背景不是剧情表演指令，除非用户明确指定为首帧、姿态或构图锚点。
- 站位根据人物目的、交流关系、动作和场景空间生成，不为所有场景套用固定左右站位、面对面或侧身模板。
""".strip()


def _h3_default_target_count(total_sec: int, min_duration: int, max_duration: int) -> int:
    total = max(1, int(total_sec or 1))
    minimum = max(1, math.ceil(total / max_duration))
    recommended_min = max(min_duration, 8)
    maximum = max(minimum, math.floor(total / recommended_min))
    preferred = max(1, int(round(total / 9.0)))
    return max(minimum, min(maximum, preferred))


def _normalize_h3_timeline(beat: dict[str, Any], old_duration: int, new_duration: int, model_id: str) -> dict[str, Any]:
    item = copy.deepcopy(beat)
    all_hints = item.get("model_hints") if isinstance(item.get("model_hints"), dict) else {}
    hints = all_hints.get(model_id) if isinstance(all_hints, dict) else None
    hints = hints if isinstance(hints, dict) else {}
    shots = hints.get("shots") if isinstance(hints.get("shots"), list) else []
    if str(hints.get("shot_mode") or "").strip().lower() == "one_take" and len(shots) > 1:
        raise ValueError("H3 one_take Beat cannot contain multiple Shots")
    if not shots:
        return item
    old_value = max(1, int(old_duration or new_duration or 1))
    new_value = max(1, int(new_duration or old_value))
    scale = new_value / old_value
    previous_start = -1.0
    normalized_shots: list[Any] = []
    for index, raw_shot in enumerate(shots, start=1):
        if not isinstance(raw_shot, dict):
            raise ValueError(f"H3 Shot {index} must be an object")
        shot = dict(raw_shot)
        start_raw, end_raw = shot.get("start_sec"), shot.get("end_sec")
        if start_raw in (None, "") and end_raw in (None, ""):
            normalized_shots.append(shot)
            continue
        try:
            start, end = float(start_raw), float(end_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"H3 Shot {index} start_sec/end_sec must be numbers") from exc
        if start < 0 or end <= start or start < previous_start:
            raise ValueError(f"H3 Shot {index} has an invalid chronological range")
        if end > old_value + 1e-6:
            raise ValueError(f"H3 Shot {index} ends at {end:g}s beyond its source Beat duration {old_value}s")
        shot["start_sec"] = round(start * scale, 3)
        shot["end_sec"] = round(end * scale, 3)
        previous_start = start
        normalized_shots.append(shot)
    hints = {**hints, "shots": normalized_shots}
    item["model_hints"] = {**all_hints, model_id: hints}
    return item


def detect_h3_beat_risks(beat: dict, beat_index: int | None = None) -> dict:
    """Detect only H3 structural overload; camera cuts and movement are valid."""
    index = int(beat_index or beat.get("id") or beat.get("order") or 0)
    title = str(beat.get("title") or f"Beat {index}").strip()
    try:
        duration = float(beat.get("estimated_duration_sec") or beat.get("duration_sec") or 0)
    except (TypeError, ValueError):
        duration = 0
    hints = beat.get("model_hints") if isinstance(beat.get("model_hints"), dict) else {}
    h3 = hints.get("minimax_h3_local_ref2va") if isinstance(hints, dict) else {}
    h3 = h3 if isinstance(h3, dict) else {}
    shot_mode = str(h3.get("shot_mode") or "").strip().lower()
    shots = h3.get("shots") if isinstance(h3.get("shots"), list) else []
    risk_codes: list[str] = []
    if duration and not 4 <= duration <= 15:
        risk_codes.append("H3_DURATION_OUT_OF_RANGE")
    if shot_mode == "one_take" and len(shots) > 1:
        risk_codes.append("H3_ONE_TAKE_MULTISHOT_CONFLICT")
    return {
        "beat_index": index,
        "beat_id": beat.get("id"),
        "title": title,
        "risk_codes": risk_codes,
        "summary": f"{title} exceeds H3 structural limits." if risk_codes else f"{title} is valid for H3 multi-shot generation.",
        "suggestion": "Split only at semantic boundaries or resolve the one-take conflict." if risk_codes else "No split is required by H3 rules.",
        "details": {"duration_sec": duration, "shot_mode": shot_mode, "shot_count": len(shots)},
    }


H3_STORY_CONSTRAINTS = """
【MiniMax H3 可生成性约束（最高优先级）】
- 后续每个 Beat 对应一个 4–15 秒的完整 H3 视频事件；一个 Beat 内可以有一个或多个 Shot。
- 默认把连续因果事件合并为 8–12 秒的多 Shot Beat；4–7 秒只保留给独立短笑点或悬念揭示。
- 允许切镜、站起、坐下、走动、进入、离开和转场，只要动作推动当前事件，不要把无剧情价值的移动单独拆成 Beat。
- 按可见画面写动作和结果，允许用景别变化、运镜、环境声、音效和对白共同推进。
- 台词长度必须能在对应 Shot 时长内自然说完；画内与画外说话人必须明确。
- 一句台词跨 Shot 时保持说话人连续；切镜后明确新的景别和承接的主体。
- 每个 Shot 的 subject、action、dialogue、continuity 都要写出实际涉及的角色名；切镜后重新明确角色，不依赖上一镜头的隐含主语。
- 多角色场景不要单独使用“三人、两人、他们、几个小家伙”等可能指向多个组合的泛称；群体动作首次出现及切镜后都列出准确成员。
- 用户要求一镜到底时保持单一连续过程，不要同时规划多 Shot；默认可以切镜。
""".strip()


H3_BEAT_WRITER_PROMPT = """
你是短视频叙事规划师，为 MiniMax H3 Ref2VA 生成可直接编译的语义 Beats。

目标：把 story/topic 拆成多个独立、连续、可生成的 4–15 秒视频事件。每个 Beat 对应一条 H3 成片，
可以包含一个或多个 Shot，而不是 LTX 的单一稳定镜头状态。

严格要求：
- 顶层 JSON 必须有非空 title、locked_outcome、target_duration_sec、beats。
- 每个 Beat 必须有 id、title、plot、estimated_duration_sec。
- 不因切镜、景别、背景图、问答、短反应或连续因果动作本身拆 Beat；优先把同一事件链写成一个多 Shot Beat。
- 只有合并后超过 15 秒、发生独立时间跳跃，或地点与事件目标同时彻底改变时才拆 Beat。
- 4–15 秒是硬限制，默认应规划为 8–12 秒；4–7 秒只用于无法与相邻事件合并的独立短笑点或悬念揭示。
- 先规划宏观剧情事件，再在每个 Beat 内安排 2–5 个 Shot；不要把每段文字、每句对白或每次反应映射成独立 Beat。
- 站起、坐下、行走、进入、离开、转场可以作为事件内部动作；只有无剧情价值的移动不得单独成 Beat。
- plot 用自然中文描述主体、地点、事件、题材/风格、动作推进、镜头、台词和声音，不写工作流节点或素材标签。
- 台词长度与 Shot 时长匹配；跨 Shot 台词要明确连续说话，切镜要明确景别和承接主体。
- 每个 Shot 的 subject 必须列出实际出镜角色名，action、dialogue、continuity 中也要明确动作或说话归属；不要只写“三人、两人、他们、几个小家伙”等可能有多个解释的泛称。
- 群体动作首次出现以及每次切镜后，都重新列出准确成员；dialogue 优先使用 speaker、scope、line、continues_previous 字段，不用无法确定说话人的裸字符串。
- Beat 阶段只写 Project Bible 中的角色名/角色 id，不自行生成 <Subject N>、<Picture N>；这些编号由后续 H3 AssetBinder 和提示词编译器统一绑定。
- 默认可以切镜；用户要求一镜到底时只规划一个连续 Shot。
- 如果有角色语义，保持 important_roles、visible_roles、reference_roles、offscreen_speakers、mentioned_roles、scene_id 一致。

每个 Beat 必须输出 model_hints.minimax_h3_local_ref2va（不要把 shots 直接放在 model_hints 下）：
- core_idea：主体 + 地点 + 事件 + 题材/风格的一句话核心创意；
- shot_mode：multi_shot 或 one_take；
- shots：非空的时间顺序数组，每项包含 start_sec、end_sec、subject、action、blocking、camera、continuity，可含 scene_id、shot_size、dialogue、audio；
- 同一 Beat 的不同 Shot 可以使用不同 scene_id；scene_id 必须来自 Project Bible。无法确定场景时留空，不要填默认场景；
- exclusions：明确不希望出现的内容；
- non_diegetic_music：可选背景音乐；
- exact_text：必须准确出现的文字、Logo 或标题原文。

这些字段只描述创作意图。图片、视频、音频编号由后续 H3 AssetBinder 决定。
输出必须是 JSON object，不要 Markdown。
""".strip()


H3_STRUCTURE_PROMPT = """
Runtime structure for MiniMax H3 Ref2VA:
- A Beat is one complete 4-15 second H3 clip and may contain multiple chronological shots.
- Do not split for camera cuts, framing, background/reference changes, question-answer pairs, short reactions, or consecutive cause-and-effect actions.
- Split only when the combined event exceeds 15 seconds, an independent time jump occurs, or both location and event goal fundamentally change.
- Treat 4-15 seconds as the hard envelope and normally plan 8-12 seconds with 2-5 chronological Shots per Beat.
- Movement and transitions are valid inside a Beat when they advance the event; do not create empty movement-only Beats.
- Keep shared role and scene fields internally consistent when present.
- Name every participating role explicitly in each Shot's subject/action/dialogue/continuity fields. After a cut, restate the exact members instead of relying on ambiguous collective pronouns such as "they", "the three", or "the two".
- References are semantic identity hints; final Picture/Video/Audio tags are bound later by the H3 compiler.
- Do not require a background image, Licon Event Flow, a stable first frame, or 50fps.
""".strip()


H3_SPLITTER_PROMPT = """
你是 MiniMax H3 Beat Splitter。只拆分当前无法在 4–15 秒内清晰完成的 Beat，不重新创作故事。
保留人物、场景、对白含义、剧情方向和结局，不新增角色或支线。
保留并明确每个 Shot 的角色归属；切镜后重新列出准确角色名，不把明确角色改写成“三人、两人、他们、几个小家伙”等可能歧义的泛称。
不要因为切镜、角色站起、走动、进入或离开就拆分；H3 允许一个 Beat 内包含多 Shot 和连续动作。
仅在合并后超过 15 秒、发生独立时间跳跃，或地点与事件目标同时彻底改变时拆分。
不要因为背景图、素材用途、问答双方、短反应或同一因果链中的动作结果不同而拆分。
每个 replacement beat 都必须有完整事件价值和可落地的 4–15 秒时长。输出只能是 JSON object，字段为 replacement_beats。
""".strip()


def build_h3_rule_set(model_id: str, label: str, min_duration: int, max_duration: int) -> ModelRuleSet:
    return ModelRuleSet(
        model_id=model_id,
        revision="h3-manual-2026-08-v3",
        label=label,
        beat_min_duration_sec=min_duration,
        beat_max_duration_sec=max_duration,
        targeted_min_duration_sec=max(min_duration, 8),
        max_visible_roles=9,
        max_reference_roles=9,
        capability_summary="MiniMax H3 Ref2VA：4–15 秒、24fps；最多 9 张图片、3 个视频、3 个独立音频参考，上传素材总数不超过 12。",
        story_constraints=H3_STORY_CONSTRAINTS,
        story_writer_profile_name="h3_story_writer_profile.md",
        beat_writer_prompt=H3_BEAT_WRITER_PROMPT + "\n\n" + H3_BLOCKING_PROMPT,
        beat_writer_profile_name="h3_beat_writer_profile.md",
        beat_structure_prompt=H3_STRUCTURE_PROMPT,
        beat_writer_note="使用 H3 多 Shot、台词跨镜与素材语义规则；最终素材标签由 H3 编译器生成。",
        beat_splitter_prompt=H3_SPLITTER_PROMPT + "\n\n" + H3_BLOCKING_PROMPT,
        beat_planning=BeatPlanningStrategy(
            recommended_min_duration_sec=max(min_duration, 8),
            recommended_max_duration_sec=min(max_duration, 12),
            default_count_resolver=lambda total: _h3_default_target_count(total, min_duration, max_duration),
            timeline_normalizer=lambda beat, old, new: _normalize_h3_timeline(beat, old, new, model_id),
        ),
        beat_risk_detector=detect_h3_beat_risks,
        quality_rules=(
            QualityRule(
                "h3_one_take_multishot_conflict",
                r"一镜到底[\s\S]{0,80}(?:Shot\s*[2-9]|镜头\s*[2-9]|多镜头|频繁切镜)|(?:Shot\s*[2-9]|镜头\s*[2-9]|多镜头|频繁切镜)[\s\S]{0,80}一镜到底",
                "一镜到底与多 Shot/切镜要求冲突，请保留一种镜头模式",
            ),
            QualityRule(
                "h3_music_conflict",
                r"(?:不要|不需要|禁止)(?:额外)?(?:背景音乐|BGM)[\s\S]{0,100}(?:背景音乐|BGM|配乐).{0,30}(?:响起|进入|使用|播放)|(?:背景音乐|BGM|配乐).{0,30}(?:响起|进入|使用|播放)[\s\S]{0,100}(?:不要|不需要|禁止)(?:额外)?(?:背景音乐|BGM)",
                "背景音乐要求互相冲突，请明确是否需要配乐",
            ),
        ),
    )
