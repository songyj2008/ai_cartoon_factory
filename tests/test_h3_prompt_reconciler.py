from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
import pytest


def test_save_and_reorder_preserve_names_and_groups_inside_dialogue():
    images = [_image("chen", "陈默"), _image("liu", "刘洋")]
    old = H3GenerationConfig.from_mapping({"asset_manifest": images})
    new = H3GenerationConfig.from_mapping({"asset_manifest": list(reversed(images))})
    dialogue = '<d>[Chinese]刘洋，你们两人\n说过“陈默拿五万”，对吗？</d>'
    prompt = '【详细镜头描述】\n[Shot 1]：陈默看向刘洋，陈默说：' + dialogue
    reconciler = H3PromptReconciler()
    for config in (old, new):
        result = reconciler.reconcile(prompt, old, config)
        assert dialogue in result
    assert '<Subject 2>看向<Subject 1>，<Subject 2>说：' in result
    assert dialogue in reconciler.reconcile(result, new, new)


def test_removing_character_only_named_in_dialogue_does_not_block_save():
    images = [_image("chen", "陈默"), _image("liu", "刘洋")]
    old = H3GenerationConfig.from_mapping({"asset_manifest": images})
    new = H3GenerationConfig.from_mapping({"asset_manifest": images[:1]})
    dialogue = '<d>[Chinese]刘洋拿九十五万，我拿五万。</d>'
    result = H3PromptReconciler().reconcile('【详细镜头描述】\n[Shot 1]：陈默说：' + dialogue, old, new)
    assert dialogue in result
    assert '<Subject 1>说：' in result


def test_subject_replacement_preserves_multiple_dialogue_blocks():
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    entries = [{"aliases": ["刘洋"], "subject_tag": "<Subject 3>", "asset_kind": "character"}]
    text = '刘洋说：<d>[Chinese]刘洋</d> 刘洋回头。<D>[Chinese]刘洋\n两人</D>'
    result = H3PromptCompiler()._replace_subject_mentions(text, entries)
    assert result == '<Subject 3>说：<d>[Chinese]刘洋</d> <Subject 3>回头。<D>[Chinese]刘洋\n两人</D>'


def test_subject_names_beside_tags_do_not_duplicate_on_repeated_save():
    config = H3GenerationConfig.from_mapping({"asset_manifest": [_image("a", "甲方"), _image("b", "乙方")]})
    prompt = '【详细镜头描述】\n[Shot 1]：<Subject 1> 甲方看向<Subject 2> 乙方。'
    reconciler = H3PromptReconciler()
    result = reconciler.reconcile(prompt, config, config)
    assert '<Subject 1>看向<Subject 2>。' in result
    assert reconciler.reconcile(result, config, config) == result


def test_collective_people_reference_excludes_background_subject():
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    entries = [
        {"aliases": ["甲方"], "subject_tag": "<Subject 1>", "asset_kind": "character"},
        {"aliases": ["乙方"], "subject_tag": "<Subject 2>", "asset_kind": "character"},
        {"aliases": ["会议室"], "subject_tag": "<Subject 3>", "asset_kind": "background"},
    ]
    compiler = H3PromptCompiler()
    result = compiler._replace_subject_mentions('会议室里乙方提交报告，甲方反对，两人发生争执。', entries)
    assert result == '<Subject 3>里<Subject 2>提交报告，<Subject 1>反对，<Subject 2>、<Subject 1>发生争执。'
    ambiguous = compiler._replace_subject_mentions('会议室里甲方发言，两人发生争执。', entries)
    assert ambiguous == '<Subject 3>里<Subject 1>发言，两人发生争执。'
    dialogue = '<d>[Chinese]<Subject 1> <Subject 1>，会议室里两人说过。</d>'
    assert compiler._replace_subject_mentions(dialogue, entries) == dialogue


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


def test_replacement_keeps_slot_tags_and_appearance_in_reference_metadata():
    original = [_image("panda", "金色熊猫"), _image("sugar", "3号糖宝宝"), _image("grain", "8号禾宝宝")]
    replacement = _image("sugar", "3号糖宝宝")
    replacement["path"] = "sugar_new_pose.png"
    replacement["metadata"]["subject_description"] = "新的蓝色外套外观"
    replacement["metadata"]["appearance_summary"] = "新的蓝色外套外观"
    old = H3GenerationConfig.from_mapping({"asset_manifest": original})
    new = H3GenerationConfig.from_mapping({"asset_manifest": [original[0], replacement, original[2]]})
    result = H3PromptReconciler().reconcile(_prompt(), old, new)
    assert "<Subject 2>拉住<Subject 3>，<Subject 1>回头" in result
    assert "<Subject 2>：3号糖宝宝；可见内容来自 <Picture 2>" in result
    # Appearance belongs to the replacement image; the shared compiler keeps
    # managed definitions compact instead of restating character dossiers.
    assert "新的蓝色外套外观" not in result
    assert new.asset_manifest[1].path == "sugar_new_pose.png"
    assert new.asset_manifest[1].metadata["appearance_summary"] == "新的蓝色外套外观"
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
    prompt = _prompt().replace("<Subject 2>和8号禾宝宝一起奔跑。", "[已移除主体：<Subject 1>]挥手。").replace(
        "<Subject 2>拉住<Subject 3>，金色熊猫回头。", "金色熊猫回头。")

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


@pytest.mark.parametrize('new_role,new_identity,allowed',[
    ('chen','chen',True),('other','chen',False),('chen','new_identity',True),
])
def test_uploaded_voice_replacement_follows_explicit_speaker_not_file_or_number(new_role,new_identity,allowed):
    image=_image('picture','陈默');image['metadata']={'asset_kind':'character','identity_id':'chen','role_id':'chen'}
    old_voice={'id':'old_voice','type':'audio','path':'old.mp3','role':'陈默原音色',
               'metadata':{'voice_only':True,'identity_id':'chen','role_id':'chen'}}
    uploaded={'id':'uploaded_voice','type':'audio','path':'uploaded.mp3','role':'本段上传音色',
              'metadata':{'voice_only':True,'identity_id':new_identity,'role_id':new_role,'binding_source':'segment_character_voice'}}
    other={'id':'other','type':'audio','path':'other.mp3','metadata':{'voice_only':True,'identity_id':'b','role_id':'b'}}
    old=H3GenerationConfig.from_mapping({'asset_manifest':[image,old_voice,other]})
    new=H3GenerationConfig.from_mapping({'asset_manifest':[image,other,uploaded]})
    line='<d>[Chinese]保留 Audio 1 这几个字。</d>'
    prompt='【详细镜头描述】\n[Shot 1]（0–4 秒）：本句音色参考：<Audio 1>，仅用于<Subject 1>的声音；<Subject 1>说：'+line
    if not allowed:
        with pytest.raises(ValueError,match='绑定失效'):
            H3PromptReconciler().reconcile(prompt,old,new)
        return
    result=H3PromptReconciler().reconcile(prompt,old,new)
    assert '本句音色参考：<Audio 2>' in result and line in result
