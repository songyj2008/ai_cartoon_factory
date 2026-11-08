"""Creative/validation rules owned exclusively by MiniMax H3 Ref2VA."""
from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

from generation.model_rules import BeatPlanningStrategy, ModelRuleSet, QualityRule


def h3_segment_planning_rules() -> str:
    """Shared directing policy for H3 writing, planning, and revisions."""
    path = Path(__file__).resolve().parents[3] / "profiles" / "h3_segment_planning_rules.md"
    return path.read_text(encoding="utf-8-sig").strip()


def h3_visual_prompt_rules() -> str:
    """Read the shared expression section without timing/output contracts."""
    return '## 镜头表达\n' + h3_segment_planning_rules().split('## 镜头表达\n', 1)[1]


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
- 按连续事件和自然对白确定段长，不为凑时长拆分。
- 允许切镜、站起、坐下、走动、进入、离开和转场，只要动作推动当前事件，不要把无剧情价值的移动单独拆成 Beat。
- 按可见画面写动作和结果，允许用景别变化、运镜、环境声、音效和对白共同推进。
- 台词长度必须能在对应 Shot 时长内自然说完；画内与画外说话人必须明确。
- 旁白使用独立说话人和 scope=narration；电话另一端对白使用明确角色名和 scope=telephone。人物内心独白不得自动改为旁白。声音角色不因发言而进入 visible_roles 或被新增为画内人物；画内人物不代说、不对口型。仅在输入明确要求时安排这些声音，不新增台词。
- 一句台词跨 Shot 时保持说话人连续；切镜后明确新的景别和承接的主体。
- 人物动作和说话归属保持明确。

- 用户要求一镜到底时保持单一连续过程，不要同时规划多 Shot；默认可以切镜。
""".strip()


H3_BEAT_WRITER_PROMPT = """
【人物和场景结构必填】
用户只输入小说，不需要提供内部 ID。根据 project_bible 的姓名、别名或 ID 填写结构。
每个 Shot 必须输出 visible_roles 数组，只含实际入画人物；空镜明确填 []，不要遗漏字段。
每个 Shot 必须输出 scene_id：有明确匹配时复用项目场景名称或 ID，没有匹配时填写剧情地点名称，并在镜头正文交代环境，由视频模型自行构建；不要求先登记或生成背景参考图，不强配已有场景。明确无具体场景时填空字符串。
从全文识别连续场景，后续段落未重复地点不表示换场。小说生成时输出顶层 scene_contexts 数组：
每项包含 id、scene_id、source_excerpt；各 Shot 用 scene_context_id 引用，同一连续场景沿用同一上下文。
已有 scene_contexts 时沿用对应场景，不因切镜、景别变化或说话人变化丢失场景。
人物在场、实际入画、画外发声、台词提及必须区分；不得把站位说明里所有人强加入画。
对白 speaker 使用项目角色姓名或 ID，保留原文和归属。镜头人物可以变化，不要求整段固定入画。

你是短视频叙事规划师，为 MiniMax H3 Ref2VA 生成可直接编译的语义 Beats。

目标：把 story/topic 拆成多个独立、连续、可生成的视频事件。每个 Beat 对应一条 H3 成片，
可以包含一个或多个 Shot，而不是 LTX 的单一稳定镜头状态。

严格要求：
- 顶层 JSON 必须有非空 title、locked_outcome、target_duration_sec、beats。
- 每个 Beat 必须有 id、title、plot、estimated_duration_sec。
- 不因切镜、景别、背景或参考素材变化、问答、短反应或连续因果动作本身拆 Beat；优先把同一事件链写成一个多 Shot Beat。
- 在自然事件边界拆 Beat；超出提交时长时必须拆分，避免无意义碎段。
- 最终每段提交为 4–15 秒整数，按内容自然分配。
- 先规划宏观剧情事件，再按信息、情绪、动作和空间承接需要决定 Shot 数量；不为凑固定镜头数切镜，不把每段文字、每句对白或每次反应映射成独立 Beat。
- 站起、坐下、行走、进入、离开、转场可以作为事件内部动作；不得为凑数量或时长而将无剧情价值的移动、转场或事后反应单独成 Beat。
- plot 只概括事件及必要事实；对白与导演细节由 Shots 保存，不重复整份导演稿。
- 台词长度与 Shot 时长匹配；跨 Shot 台词要明确连续说话，切镜要明确景别和承接主体。
- 使用 visible_roles 明确实际入画人物，文本指代无歧义即可。
- dialogue 使用 speaker、scope、line，跨镜片段标明 continues_previous。
- Beat 阶段只写 Project Bible 中的角色名/角色 id，不自行生成 Subject、Picture、Video、Audio 素材标签；这些编号由后续 H3 AssetBinder 和提示词编译器统一绑定。
- 默认可以切镜；用户要求一镜到底时只规划一个连续 Shot。
- 如果有角色语义，保持 important_roles、visible_roles、reference_roles、offscreen_speakers、mentioned_roles、scene_id 一致。

每个 Beat 必须输出 model_hints.minimax_h3_local_ref2va（不要把 shots 直接放在 model_hints 下）：
- core_idea：主体 + 地点 + 事件目标 + 题材/风格的一句话核心创意；具体发声安排由 Shots 承载；
- shot_mode：multi_shot 或 one_take；
- shots：非空的时间顺序数组，每项包含 start_sec、end_sec、scene_id、visible_roles、subject、action、audio；字段职责按共同创作合同；shot_size、blocking、camera、continuity、dialogue 按需提供；
- 同一 Beat 的不同 Shot 可以使用不同 scene_id；Project Bible 的场景是可复用素材，不是地点白名单。未登记地点保留文字交给视频模型构建；无法确定场景时留空，不要填默认场景；
- exclusions：明确不希望出现的内容；
- non_diegetic_music：可选背景音乐；
- 对白只放入 dialogue，不要将台词复制到 exact_text 或作为画面文字显示；画面文字仅在用户明确要求时写入对应镜头描述。

这些字段只描述创作意图。
输出必须是 JSON object，不要 Markdown。
""".strip()


H3_STRUCTURE_PROMPT = """
MiniMax H3 Ref2VA 补充规则：
- 不强制要求背景图片、Licon Event Flow、稳定首帧或 50 帧/秒。
""".strip()


H3_SPLITTER_PROMPT = """
你是 MiniMax H3 Beat Splitter。只拆分当前无法在 4–15 秒内清晰完成的 Beat，不重新创作故事。
保留人物、场景、对白原文、说话人、次数、顺序、剧情方向和结局，不新增角色或支线。
保留明确的角色归属，指代无歧义即可。
不要因为切镜、角色站起、走动、进入或离开就拆分；H3 允许一个 Beat 内包含多 Shot 和连续动作。
按自然事件边界拆分，保持连续因果，避免无意义碎段。
不要因为背景图、素材用途、问答双方、短反应或同一因果链中的动作结果不同而拆分。
每个 replacement beat 都必须有完整事件价值和可落地的 4–15 秒时长。输出只能是 JSON object，字段为 replacement_beats。
""".strip()


def build_h3_rule_set(model_id: str, label: str, min_duration: int, max_duration: int) -> ModelRuleSet:
    return ModelRuleSet(
        model_id=model_id,
        revision="h3-planning-2026-10-v7",
        # Performance guidance changes creative output, not the Beat contract.
        compatible_revisions=("h3-performance-2026-10-v4", "h3-manual-2026-08-v3"),
        label=label,
        beat_min_duration_sec=min_duration,
        beat_max_duration_sec=max_duration,
        targeted_min_duration_sec=max(min_duration, 8),
        max_visible_roles=9,
        max_reference_roles=9,
        capability_summary="MiniMax H3 Ref2VA：4–15 秒、24fps；最多 9 张图片、3 个视频、3 个独立音频参考，上传素材总数不超过 12。",
        story_constraints=H3_STORY_CONSTRAINTS,
        story_writer_profile_name="h3_story_writer_profile.md",
        beat_writer_prompt="\n\n".join((H3_BEAT_WRITER_PROMPT, h3_segment_planning_rules())),
        beat_writer_profile_name="h3_beat_writer_profile.md",
        beat_structure_prompt=H3_STRUCTURE_PROMPT,
        beat_writer_note="使用 H3 多 Shot、台词跨镜与素材语义规则；最终素材标签由 H3 编译器生成。",
        beat_splitter_prompt=H3_SPLITTER_PROMPT + "\n每个 replacement 必须有 id、title、plot、estimated_duration_sec 和 model_hints.minimax_h3_local_ref2va；后者含 core_idea、shot_mode、shots。\n" + h3_segment_planning_rules(),
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
