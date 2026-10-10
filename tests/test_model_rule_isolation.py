import pytest
from types import SimpleNamespace

from generation.beat_policy import project_beat_policy
from generation.contracts import ModelCapabilities, ModelSpec
from generation.model_rules import ModelRuleSet, assert_beats_compatible, rules_for_model
from generation.registry import ModelRegistry
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
from pipeline.beat_splitter.guard import detect_beat_risks
from pipeline.quality_gate import validate_story_display
from scripts.story_script.generate_beats import normalize_beats


LTX = "ltx23_licon_msr_v2"
H3 = "minimax_h3_local_ref2va"


def test_ltx_and_h3_own_independent_rule_sets():
    ltx = rules_for_model(LTX)
    h3 = rules_for_model(H3)

    assert ltx.model_id == LTX
    assert h3.model_id == H3
    assert ltx.revision != h3.revision
    assert ltx.quality_rules is not h3.quality_rules
    assert project_beat_policy(LTX).min_duration_sec == 15
    assert project_beat_policy(H3).min_duration_sec == 4


def test_new_registered_model_extends_rule_routing_without_core_branches(monkeypatch):
    import generation.registry as registry_module

    model_id = "future_video_model"
    rules = ModelRuleSet(
        model_id=model_id,
        revision="future-rules-v1",
        label="Future Video",
        beat_min_duration_sec=2,
        beat_max_duration_sec=20,
        targeted_min_duration_sec=2,
    )
    spec = ModelSpec(
        id=model_id,
        display_name="Future Video",
        pipeline_key="future",
        revision="1",
        capabilities=ModelCapabilities(duration_min_sec=2, duration_max_sec=20, default_fps=30),
    )
    registry = ModelRegistry()
    registry.register(SimpleNamespace(spec=spec, rules=rules))
    monkeypatch.setattr(registry_module, "_REGISTRY", registry)

    policy = project_beat_policy(model_id)

    assert policy.model_id == model_id
    assert policy.min_duration_sec == 2
    assert policy.max_duration_sec == 20


def test_standing_up_is_ltx_blocking_but_not_h3_blocking():
    story = "她站起后走到窗边，继续解释自己的决定。"

    _, ltx_issues = validate_story_display(story, model_id=LTX)
    _, h3_issues = validate_story_display(story, model_id=H3)

    assert any(issue.rule_id == "ltx_transition_action" for issue in ltx_issues)
    assert not h3_issues


def test_h3_complexity_detector_allows_movement_and_multishot():
    beat = {
        "id": 1,
        "title": "起身追出",
        "plot": "角色站起，穿过房间追上同伴，切到近景继续说话。",
        "estimated_duration_sec": 12,
        "model_hints": {H3: {"shot_mode": "multi_shot", "shots": [{"time": "0-5秒"}, {"time": "5-12秒"}]}},
    }

    report = detect_beat_risks(beat, 1, model_id=H3)

    assert report["risk_codes"] == []


def test_h3_complexity_detector_rejects_one_take_multishot_conflict():
    beat = {
        "id": 1,
        "estimated_duration_sec": 12,
        "model_hints": {H3: {"shot_mode": "one_take", "shots": [{}, {}]}},
    }

    report = detect_beat_risks(beat, 1, model_id=H3)

    assert "H3_ONE_TAKE_MULTISHOT_CONFLICT" in report["risk_codes"]


def test_h3_normalization_preserves_rich_shot_semantics():
    raw = {
        "title": "测试",
        "locked_outcome": "角色完成决定。",
        "beats": [
            {
                "title": "行动",
                "plot": "角色站起并走向窗边。",
                "important_roles": ["character_a"],
                "visible_roles": ["character_a"],
                "reference_roles": ["character_a"],
                "estimated_duration_sec": 8,
                "model_hints": {
                    H3: {
                        "shot_mode": "multi_shot",
                        "shots": [
                            {
                                "start_sec": 0,
                                "end_sec": 4,
                                "shot_size": "中景",
                                "action": "角色站起",
                                "dialogue": {"speaker": "character_a", "line": "我已经决定了。"},
                            }
                        ],
                        "exact_text": ["PROJECT H3"],
                    }
                },
            }
        ],
    }

    result = normalize_beats(raw, target_duration_sec=8, target_beat_count=1, model_id=H3)
    hints = result["beats"][0]["model_hints"][H3]

    assert hints["shots"][0]["shot_size"] == "中景"
    assert hints["shots"][0]["dialogue"]["line"] == "我已经决定了。"
    assert result["generation_policy"]["rules_revision"] == rules_for_model(H3).revision


def test_h3_prompt_compiler_owns_local_reference_tags_and_manual_sections():
    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {"id": "person", "type": "image", "path": "person.png", "role": "人物参考（锁脸）"},
                {"id": "motion", "type": "video", "path": "motion.mp4", "role": "动作参考"},
                {"id": "voice", "type": "audio", "path": "voice.mp3", "role": "音色参考"},
            ]
        }
    )
    bindings = H3AssetBinder().bind(config)
    segment = {
        "plot": "角色站起后走向窗边。",
        "model_hints": {
            H3: {
                "core_idea": "角色在办公室作出决定。",
                "shot_mode": "multi_shot",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 4,
                        "shot_size": "中景",
                        "action": "角色站起",
                        "camera": "镜头轻微跟随",
                        "dialogue": {"speaker": "角色", "scope": "onscreen", "line": "我已经决定了。"},
                    }
                ],
                "exact_text": ["PROJECT H3"],
            }
        },
    }

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text

    assert "<Picture 1>" in prompt
    assert "<Video 1>" in prompt
    assert "<Audio 1>" in prompt
    assert "@image" not in prompt
    assert "【任务概述】" in prompt
    assert "【参考保留关系】" in prompt
    assert "【详细镜头描述】" in prompt
    assert "必须准确显示的文字：PROJECT H3" in prompt
    assert "角色 (S1)说：<d>[Chinese]我已经决定了。</d>" in prompt
    assert "【整体声音环境】" in prompt
    assert "【非叙事性音乐】" in prompt


def test_h3_prompt_compiler_uses_current_beat_dialogue_over_cached_shots():
    config = H3GenerationConfig.from_mapping({})
    bindings = H3AssetBinder().bind(config)
    segment = {
        "plot": "角色在镜头前回答问题。",
        "dialogue_units": [
            {"speaker_name": "角色", "line": "这是修改后的台词。", "speech_type": "onscreen"}
        ],
        "model_hints": {
            H3: {
                "core_idea": "角色在镜头前回答问题。",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 8,
                        "subject": "角色",
                        "action": "角色看向镜头",
                        "dialogue": {"speaker": "角色", "line": "这是旧缓存台词。"},
                    }
                ],
            }
        },
    }

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text

    assert "这是修改后的台词。" in prompt
    assert "这是旧缓存台词。" not in prompt


def test_h3_prompt_compiler_keeps_cached_dialogue_for_legacy_beat_without_units():
    config = H3GenerationConfig.from_mapping({})
    bindings = H3AssetBinder().bind(config)
    segment = {
        "plot": "角色在镜头前回答问题。",
        "dialogue_units": [],
        "model_hints": {
            H3: {
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 8,
                        "subject": "角色",
                        "dialogue": {"speaker": "角色", "line": "旧结构里唯一保存的台词。"},
                    }
                ]
            }
        },
    }

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text

    assert "旧结构里唯一保存的台词。" in prompt


def test_h3_prompt_compiler_adds_character_asset_description_to_subject_definition():
    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {
                    "id": "hebaobao",
                    "type": "image",
                    "path": "hebaobao.png",
                    "role": "8号禾宝宝",
                    "metadata": {
                        "asset_kind": "character",
                        "subject_description": "较小圆脸、明显大眼睛，绿色发圈双低马尾，身穿黄衣红裤。",
                    },
                },
                {
                    "id": "space_base",
                    "type": "image",
                    "path": "space_base.png",
                    "role": "航天基地",
                    "metadata": {"asset_kind": "background", "description_cn": "航天基地外景。"},
                },
            ]
        }
    )
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile({"plot": "禾宝宝看向飞船。"}, config, bindings, duration_sec=9).text

    assert "【主体定义】" in prompt
    assert "<Subject 1>：8号禾宝宝；可见内容来自 <Picture 1>。" in prompt
    assert "较小圆脸、明显大眼睛，绿色发圈双低马尾，身穿黄衣红裤。" in prompt
    assert "<Subject 2>：航天基地；可见内容来自 <Picture 2>。" in prompt


def test_h3_prompt_compiler_rejects_shot_past_requested_duration():
    config = H3GenerationConfig.from_mapping({"asset_manifest": []})
    bindings = H3AssetBinder().bind(config)
    segment = {
        "plot": "九秒短片。",
        "model_hints": {H3: {"shots": [{"start_sec": 6, "end_sec": 12, "action": "人物离开"}]}},
    }

    with pytest.raises(ValueError, match="beyond the requested 9s duration"):
        H3PromptCompiler().compile(segment, config, bindings, duration_sec=9)


def test_beats_rule_revision_prevents_cross_model_reuse():
    h3 = rules_for_model(H3)
    beats = {"generation_policy": {"model_id": H3, "rules_revision": h3.revision}, "beats": []}

    assert_beats_compatible(beats, H3)
    with pytest.raises(ValueError, match="重新拆分 Beats"):
        assert_beats_compatible(beats, LTX)
