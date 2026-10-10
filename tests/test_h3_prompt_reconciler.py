from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
import pytest


def _image(asset_id: str, name: str) -> dict:
    return {
        "id": asset_id,
        "type": "image",
        "path": f"{asset_id}.png",
        "role": name,
        "label": f"{name}正面图",
        "metadata": {
            "asset_kind": "character",
            "subject_description": f"{name}的稳定外观描述",
            "aliases": [name],
        },
    }


def _prompt() -> str:
    return """【主体定义】
<Subject 1>：金色熊猫；可见内容来自 <Picture 1>。
<Subject 2>：3号糖宝宝；可见内容来自 <Picture 2>。
<Subject 3>：8号禾宝宝；可见内容来自 <Picture 3>。

【任务概述】
<Subject 2>和8号禾宝宝一起奔跑。

【参考保留关系】
<Subject 2>（出现于 Shot 1）：fully_preserved - 保持人物。

【详细镜头描述】
[Shot 1]（0–5秒）：<Subject 2>拉住<Subject 3>，金色熊猫回头。

【整体声音环境】
风声。

【非叙事性音乐】
轻快配乐。"""


def test_reorder_renumbers_picture_and_subject_by_stable_asset_id():
    old = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫"), _image("sugar", "3号糖宝宝"), _image("grain", "8号禾宝宝")]})
    new = H3GenerationConfig.from_mapping({"asset_manifest": [_image("grain", "8号禾宝宝"), _image("sugar", "3号糖宝宝"), _image("panda", "金色熊猫")]})

    result = H3PromptReconciler().reconcile(_prompt(), old, new)

    assert "<Subject 1>：8号禾宝宝；可见内容来自 <Picture 1>" in result
    assert "<Subject 2>：3号糖宝宝；可见内容来自 <Picture 2>" in result
    assert "<Subject 3>：金色熊猫；可见内容来自 <Picture 3>" in result
    assert "<Subject 2>拉住<Subject 1>，<Subject 3>回头" in result


def test_replacement_keeps_slot_tags_and_screenplay_updates_appearance():
    original = [_image("panda", "金色熊猫"), _image("sugar", "3号糖宝宝"), _image("grain", "8号禾宝宝")]
    replacement = _image("sugar", "3号糖宝宝")
    replacement["path"] = "sugar_new_pose.png"
    replacement["metadata"]["subject_description"] = "新的蓝色外套外观"
    old = H3GenerationConfig.from_mapping({"asset_manifest": original})
    new = H3GenerationConfig.from_mapping({"asset_manifest": [original[0], replacement, original[2]]})
    result = H3PromptReconciler().reconcile(_prompt(), old, new)
    assert "<Subject 2>拉住<Subject 3>，<Subject 1>回头" in result
    assert "<Subject 2>：3号糖宝宝；可见内容来自 <Picture 2>" in result
    assert "新的蓝色外套外观" in result
    assert "3号糖宝宝的稳定外观描述" not in result
    assert "风声。" in result and "轻快配乐。" in result


def test_remove_referenced_asset_blocks_save_without_mutating_prompt():
    old = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫"), _image("sugar", "3号糖宝宝"), _image("grain", "8号禾宝宝")]})
    new = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫"), _image("grain", "8号禾宝宝")]})

    with pytest.raises(ValueError, match="参考素材绑定失效：3号糖宝宝"):
        H3PromptReconciler().reconcile(_prompt(), old, new)


def test_removed_asset_can_be_saved_after_its_prompt_references_are_deleted():
    old = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫"), _image("sugar", "3号糖宝宝"), _image("grain", "8号禾宝宝")]})
    new = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫"), _image("grain", "8号禾宝宝")]})
    prompt = _prompt().replace("<Subject 2>和8号禾宝宝一起奔跑。", "8号禾宝宝独自奔跑。").replace(
        "<Subject 2>拉住<Subject 3>，金色熊猫回头。", "<Subject 3>奔跑，金色熊猫回头。"
    )

    result = H3PromptReconciler().reconcile(prompt, old, new)

    assert "已移除主体" not in result
    assert "<Subject 2>独自奔跑" in result


def test_legacy_removed_marker_is_unwrapped_and_repaired_on_save():
    config = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫")]})
    prompt = _prompt().replace("<Subject 2>和8号禾宝宝一起奔跑。", "[已移除主体：<Subject 1>]挥手。")

    result = H3PromptReconciler().reconcile(prompt, config, config)

    assert "已移除主体" not in result
    assert "<Subject 1>挥手" in result


def test_role_rename_updates_subject_definition_and_existing_mentions():
    old = H3GenerationConfig.from_mapping({"asset_manifest": [_image("panda", "金色熊猫")]})
    renamed = _image("panda", "黄金熊猫")
    new = H3GenerationConfig.from_mapping({"asset_manifest": [renamed]})
    prompt = _prompt().replace("<Subject 2>和8号禾宝宝一起奔跑。", "金色熊猫挥手。").replace(
        "<Subject 2>拉住<Subject 3>，金色熊猫回头。", "金色熊猫回头。"
    )

    result = H3PromptReconciler().reconcile(prompt, old, new)

    assert "<Subject 1>：黄金熊猫；可见内容来自 <Picture 1>" in result
    assert "<Subject 1>挥手" in result
    assert "<Subject 1>回头" in result


def test_fullscreen_save_keeps_subjects_when_same_files_return_with_different_ids():
    old_assets = [
        _image("asset_1", "金色熊猫"),
        _image("asset_2", "3号糖宝宝"),
        _image("asset_3", "8号禾宝宝"),
    ]
    new_assets = [
        _image("project:character:panda", "金色熊猫"),
        _image("project:character:sugar", "3号糖宝宝"),
        _image("project:character:grain", "8号禾宝宝"),
    ]
    for old, new in zip(old_assets, new_assets):
        new["path"] = old["path"]

    result = H3PromptReconciler().reconcile(
        _prompt(),
        H3GenerationConfig.from_mapping({"asset_manifest": old_assets}),
        H3GenerationConfig.from_mapping({"asset_manifest": new_assets}),
    )

    assert "[已移除主体" not in result
    assert "[已移除图片" not in result
    assert "<Subject 2>拉住<Subject 3>，<Subject 1>回头" in result


def test_asset_manifest_normalizer_preserves_persisted_asset_id():
    from generation.service import GenerationService

    normalized = GenerationService._normalise_asset_manifest(
        [{"asset_id": "project:character:sugar", "type": "image", "path": "sugar.png"}]
    )

    assert normalized[0]["id"] == "project:character:sugar"
