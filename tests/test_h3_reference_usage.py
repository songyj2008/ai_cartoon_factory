from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
from workflow.asset_matcher import match_beat_assets


H3 = "minimax_h3_local_ref2va"


def test_h3_detailed_shots_use_subject_tags_without_repeating_source_pictures():
    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {
                    "id": "panda",
                    "type": "image",
                    "path": "panda.png",
                    "role": "金色熊猫",
                    "metadata": {
                        "asset_kind": "character",
                        "subject_description": "圆润体型，黑色眼圈。",
                    },
                },
                {
                    "id": "musk",
                    "type": "image",
                    "path": "musk.png",
                    "role": "埃隆·马斯克",
                    "metadata": {
                        "asset_kind": "character",
                        "subject_description": "黑色西装，成年男性。",
                    },
                },
                {
                    "id": "hebaobao",
                    "type": "image",
                    "path": "hebaobao.png",
                    "role": "8号禾宝宝",
                    "metadata": {
                        "asset_kind": "character",
                        "subject_description": "黄色上衣，红色短裤。",
                    },
                },
                {
                    "id": "space_base",
                    "type": "image",
                    "path": "space_base.png",
                    "role": "航天基地",
                    "metadata": {
                        "asset_kind": "background",
                        "shot_indices": [1],
                    },
                },
            ]
        }
    )
    segment = {
        "plot": "航天基地前询问火星之旅。",
        "model_hints": {
            H3: {
                "shot_mode": "multi_shot",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 8,
                        "subject": "禾宝宝、金色熊猫和马斯克",
                        "action": "禾宝宝看向马斯克，金色熊猫在旁等待",
                    }
                ],
            }
        },
    }
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text
    retention = prompt.split("【参考保留关系】", 1)[1].split("【详细镜头描述】", 1)[0]
    detail = prompt.split("【详细镜头描述】", 1)[1]

    assert "<Subject 1>" in detail
    assert "<Subject 2>" in detail
    assert "<Subject 3>" in detail
    assert "<Subject 4>（航天基地）" in detail
    assert "<Picture" not in detail
    assert "<Subject 1>（出现于 Shot 1）：fully_preserved" in retention
    assert "<Subject 4>（出现于 Shot 1）：fully_preserved" in retention
    assert "<Picture" not in retention


def test_h3_keeps_picture_label_only_for_a_concrete_frame_anchor():
    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {
                    "id": "opening",
                    "type": "image",
                    "path": "opening.png",
                    "role": "开场构图",
                    "metadata": {
                        "picture_role": "first_frame",
                        "is_first_frame": True,
                        "shot_indices": [1],
                    },
                }
            ]
        }
    )
    segment = {
        "model_hints": {
            H3: {
                "shots": [{"start_sec": 0, "end_sec": 8, "action": "人物从开场位置向前行走"}],
            }
        }
    }
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text
    retention = prompt.split("【参考保留关系】", 1)[1].split("【详细镜头描述】", 1)[0]
    detail = prompt.split("【详细镜头描述】", 1)[1]

    assert "<Picture 1>（用于 Shot 1）：fully_preserved - 作为首帧锚点使用" in retention
    assert "<Picture 1>（首帧锚点：开场构图）" in detail
    assert "<Subject 1>" not in prompt


def test_h3_summary_group_pronouns_and_string_dialogue_use_subject_contract():
    manifest = []
    for index, name in enumerate(("金色熊猫", "埃隆·马斯克", "8号禾宝宝", "3号糖宝宝"), start=1):
        manifest.append(
            {
                "id": f"person-{index}",
                "type": "image",
                "path": f"person-{index}.png",
                "role": name,
                "metadata": {"asset_kind": "character", "subject_description": f"{name}的固定人物外观。"},
            }
        )
    config = H3GenerationConfig.from_mapping({"asset_manifest": manifest})
    segment = {
        "model_hints": {
            H3: {
                "core_idea": "三个小家伙询问马斯克能否去火星。",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 4,
                        "subject": "金色熊猫、禾宝宝、糖宝宝",
                        "action": "三个小家伙期待地看向马斯克",
                        "dialogue": "禾宝宝：真的可以去火星吗？",
                    },
                    {
                        "start_sec": 4,
                        "end_sec": 8,
                        "subject": "马斯克",
                        "action": "马斯克看看三人，迟疑地点头；糖宝宝抓住禾宝宝，两人转头",
                        "dialogue": "马斯克：理论上可以。",
                    },
                ],
            }
        }
    }
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=8).text
    summary = prompt.split("【任务概述】", 1)[1].split("【参考保留关系】", 1)[0]
    detail = prompt.split("【详细镜头描述】", 1)[1]

    assert summary.startswith("\n[reference generation]")
    assert "三个小家伙" not in summary
    assert "三个小家伙" not in detail
    assert "看看三人" not in detail
    assert "两人" not in detail
    assert "<Subject 4>抓住<Subject 3>，<Subject 4>、<Subject 3>转头" in detail
    assert "<Subject 3> (S1)说：<d>[Chinese]真的可以去火星吗？</d>" in detail
    assert "<Subject 2> (S2)说：<d>[Chinese]理论上可以。</d>" in detail


def test_character_short_name_matches_numbered_project_identity():
    bible = {
        "core_characters": [
            {
                "id": "character_001",
                "display_name": "3号糖宝宝",
                "asset_identity_id": "girl_player_03",
            }
        ],
        "supporting_characters": [],
        "allowed_backgrounds": [],
    }
    asset_index = {
        "characters": {
            "identities": [
                {
                    "id": "girl_player_03",
                    "display_name": "3号糖宝宝",
                    "images": [{"id": "primary", "image_path": "tang.png", "is_primary": True}],
                }
            ]
        },
        "backgrounds": [],
    }

    matched = match_beat_assets(
        {"plot": "糖宝宝兴奋地抓住禾宝宝的胳膊。"},
        bible=bible,
        asset_index=asset_index,
        model_id=H3,
        reference_role_limit=9,
    )

    assert matched["reference_roles"] == ["character_001"]


def test_h3_dialogue_speaker_by_bible_role_id_resolves_to_subject_tag(monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_compiler as pc

    bible = {
        "core_characters": [
            {
                "id": "character_004",
                "display_name": "吴法",
                "asset_identity_id": "wufa",
                "aliases": ["wufa", "吴法"],
            }
        ],
        "supporting_characters": [],
        "allowed_backgrounds": [],
    }
    monkeypatch.setattr(pc, "load_project_bible", lambda: bible)

    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {
                    "id": "char_wufa",
                    "type": "image",
                    "path": "wufa.png",
                    "role": "吴法",
                    "metadata": {
                        "asset_kind": "character",
                        "identity_id": "wufa",
                        "subject_description": "成年男性，教师。",
                    },
                }
            ]
        }
    )
    segment = {
        "model_hints": {
            H3: {
                "shot_mode": "one_take",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 5,
                        "subject": "吴法",
                        "action": "吴法抱住吴天",
                        "dialogue": {"speaker": "character_004", "scope": "onscreen", "line": "住手！"},
                    }
                ],
            }
        }
    }
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=5).text
    detail = prompt.split("【详细镜头描述】", 1)[1]

    assert "<Subject 1> (S1)说：<d>[Chinese]住手！</d>" in detail
    assert "character_004" not in detail


def test_h3_dialogue_speaker_resolves_via_metadata_role_id_without_bible(monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_compiler as pc

    monkeypatch.setattr(pc, "load_project_bible", lambda: {})

    config = H3GenerationConfig.from_mapping(
        {
            "asset_manifest": [
                {
                    "id": "char_wufa",
                    "type": "image",
                    "path": "wufa.png",
                    "role": "吴法",
                    "metadata": {
                        "asset_kind": "character",
                        "role_id": "character_004",
                        "subject_description": "成年男性，教师。",
                    },
                }
            ]
        }
    )
    segment = {
        "model_hints": {
            H3: {
                "shot_mode": "one_take",
                "shots": [
                    {
                        "start_sec": 0,
                        "end_sec": 5,
                        "subject": "吴法",
                        "action": "吴法抱住吴天",
                        "dialogue": {"speaker": "character_004", "scope": "onscreen", "line": "住手！"},
                    }
                ],
            }
        }
    }
    bindings = H3AssetBinder().bind(config)

    prompt = H3PromptCompiler().compile(segment, config, bindings, duration_sec=5).text
    detail = prompt.split("【详细镜头描述】", 1)[1]

    assert "<Subject 1> (S1)说：<d>[Chinese]住手！</d>" in detail
    assert "character_004" not in detail
