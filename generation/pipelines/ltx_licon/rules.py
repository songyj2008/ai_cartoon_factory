"""Creative/validation rules owned exclusively by LTX/LiconMSR."""
from __future__ import annotations

from generation.model_rules import ModelRuleSet, QualityRule


LTX_STORY_CONSTRAINTS = """
【LTX/LiconMSR 可生成性约束（最高优先级）】
- 每个剧情状态必须是稳定、完整的事件，不要以人物移动或镜头衔接作为剧情主体。
- 对话场景直接从稳定状态开始，主要人物第一帧已经在当前场景中。
- 推门、进门、出门、走路、转身、穿过走廊、上下楼、落座、起身不得成为独立剧情状态。
- 推动剧情、具有身份或参与对白的角色必须从当前剧情状态开始就在画面内。
- 场景切换直接进入新的稳定状态，不生成门口、走廊、过道等过渡 Beat。
- 每个状态使用一个背景、1–3 个清晰角色；动作保持小而明确，以对白推进为主。
""".strip()


LTX_SPLITTER_PROMPT = """
你是 LTX/LiconMSR Beat Complexity Splitter。只拆分当前复杂 Beat，不重新创作故事。
保持人物、场景、对白含义、剧情方向和结局不变，不新增角色或场景。
每个 replacement beat 只能有一个稳定 focus pair；三人同场时最多一个 onscreen speaker；
同一角色对不同对象说话或发生道具交接时应拆分。输出只能是 JSON object，字段为 replacement_beats。
""".strip()


def build_ltx_rule_set(model_id: str, label: str) -> ModelRuleSet:
    from pipeline.beat_splitter.guard import detect_ltx_beat_risks

    return ModelRuleSet(
        model_id=model_id,
        revision="ltx-rules-v1",
        label=label,
        beat_min_duration_sec=15,
        beat_max_duration_sec=30,
        targeted_min_duration_sec=8,
        max_visible_roles=4,
        max_reference_roles=4,
        accept_unversioned_beats=True,
        capability_summary="LTX/LiconMSR：稳定事件 Prompt + 1–4 张人物/物品参考图 + 1 张背景图。",
        template_fallback_config_key="template_api_path",
        story_constraints=LTX_STORY_CONSTRAINTS,
        beat_splitter_prompt=LTX_SPLITTER_PROMPT,
        beat_risk_detector=detect_ltx_beat_risks,
        quality_rules=(
            QualityRule("ltx_character_enters_door", r"推门而入|推门进入|推开门进入|打开门进入|拉开门进入", "主要人物推门/进门过程，容易生成未注册入画人物"),
            QualityRule("ltx_character_enters_offscreen", r"从门外(?:走|进|进入|出现|传来)|门外(?:走进|进入|出现)", "主要人物从画外/门外进入，第一帧身份不稳定"),
            QualityRule("ltx_character_enters_frame", r"走进(?:办公室|房间|镜头|画面)|进入(?:办公室|房间|镜头|画面)|走入(?:办公室|房间|镜头|画面)|来到(?:办公室|房间|会议室|现场|镜头|画面)", "主要人物中途入画，建议改成第一帧已在场"),
            QualityRule("ltx_character_exits_frame", r"走出(?:办公室|房间|镜头|画面)|离开(?:办公室|房间|镜头|画面)", "主要人物出画/离场过程，容易变成过渡 Beat"),
            QualityRule("ltx_transition_space", r"走向门口|来到门口|站到门口|门口", "门口/过道过渡空间，容易诱发进出门动作"),
            QualityRule("ltx_transition_action", r"转身离开|转身走出|大步走向|穿过走廊|经过走廊|走廊|过道|落座|坐下|站起|起身", "连续移动/过渡动作，不适合作为主要剧情状态"),
            QualityRule("ltx_empty_aftermath", r"已不在画面内|不在画面内|离开后的稳定结果状态|离开后的.*状态|目送.*画面外方向|看向画面外方向|没有人追出门", "离开后的空场/余波 Beat 缺少独立剧情价值，应合并到上一段或直接切到下一场稳定事件"),
        ),
    )
