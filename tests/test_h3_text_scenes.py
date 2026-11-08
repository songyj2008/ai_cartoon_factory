"""Story locations can be created by the video model without scene assets."""
import copy

import pytest

from generation.pipelines.minimax_h3.semantic_bindings import (
    BindingError, MODEL, binding_coverage_errors, normalize_beat_bindings,
)


def bible():
    return {'core_characters': [{'id': 'a', 'display_name': '甲',
                                 'asset_identity_id': 'identity_a'}],
            'allowed_backgrounds': [{'id': 'office', 'display_name': '办公室',
                                     'asset_background_id': 'office_img'}]}


def shot(scene='西城城楼'):
    return {'start_sec': 0, 'end_sec': 8, 'visible_roles': ['甲'],
            'scene_id': scene, 'subject': '甲', 'action': '甲从容弹琴。',
            'audio': '本镜无对白，仅有琴声。'}


def beat(shots):
    return {'id': 1, 'plot': '甲在西城城楼弹琴。', 'estimated_duration_sec': 8,
            'model_hints': {MODEL: {'shots': shots}}}


def test_new_location_and_context_are_preserved_without_registration():
    original = beat([dict(shot(), scene_context_id='west_city')])
    contexts = [{'id': 'west_city', 'scene_id': '西城城楼'}]
    before = copy.deepcopy((original, contexts, bible()))
    result = normalize_beat_bindings(original, bible=bible(), scene_contexts=contexts)
    assert result['scene_id'] == '西城城楼'
    assert result['model_hints'][MODEL]['shots'][0]['scene_id'] == '西城城楼'
    assert (original, contexts, bible()) == before
    character_only = [{'metadata': {'asset_kind': 'character', 'role_id': 'a'}}]
    assert binding_coverage_errors(result, character_only, bible=bible()) == []
    assert binding_coverage_errors(result, [], bible=bible()) == ['入画人物缺少参考图：a']


def test_registered_scene_without_image_is_allowed_but_roles_remain_strict():
    result = normalize_beat_bindings(beat([shot('办公室')]), bible=bible())
    assert result['scene_id'] == 'office'
    assert binding_coverage_errors(result, [{'metadata': {'role_id': 'a'}}], bible=bible()) == []
    unknown = shot()
    unknown['visible_roles'] = ['新人物']
    with pytest.raises(BindingError):
        normalize_beat_bindings(beat([unknown]), bible=bible())


def test_new_scene_does_not_match_other_scene_mentioned_in_action():
    from workflow.asset_matcher import match_beat_assets
    authored = shot()
    authored['action'] = '甲想起办公室，继续弹琴。'
    resolved = normalize_beat_bindings(beat([authored]), bible=bible())
    matched = match_beat_assets(resolved, bible=bible(), model_id=MODEL,
                               asset_index={'characters': {'identities': []},
                                            'backgrounds': [{'id': 'office_img', 'path': 'office.png'}]})
    assert matched['scene_id'] == '西城城楼'
    assert matched['auto_asset_match']['backgrounds'] == []
    assert not matched['auto_asset_match']['background']['asset_background_id']


def test_novel_to_segment_passes_text_location_to_video_prompt(tmp_path, monkeypatch):
    from scripts.story_script import generate_beats
    from generation.pipelines.minimax_h3 import prompt_planner
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
    current = bible()
    character = tmp_path / 'a.png'
    character.write_bytes(b'reference')
    index = {'characters': {'identities': [
        {'id': 'identity_a', 'name_cn': '甲', 'images': [{'id': 'face', 'path': str(character)}]}]},
        'backgrounds': []}
    for module in ('services.project_bible', 'workflow.asset_matcher',
                   'generation.pipelines.minimax_h3.auto_binder',
                   'generation.pipelines.minimax_h3.prompt_compiler'):
        monkeypatch.setattr(module + '.load_project_bible', lambda: current)
    for module in ('workflow.asset_matcher', 'generation.pipelines.minimax_h3.auto_binder'):
        monkeypatch.setattr(module + '.load_asset_index', lambda: index)
    monkeypatch.setattr('services.character_voice.attach_character_voices', lambda items, *args: items)
    raw = {'title': '守城', 'locked_outcome': '甲守住城池。', 'beats': [beat([shot()])]}
    monkeypatch.setattr(generate_beats, '_call_deepseek_messages', lambda *args, **kwargs: copy.deepcopy(raw))
    result = generate_beats.generate_novel_beats(
        {'novel_text': '甲在西城城楼从容弹琴。', 'project_bible': current,
         'target_duration_sec': 8, 'target_beat_count': 1},
        model_id=MODEL, config={}, temp_dir=tmp_path)
    monkeypatch.setattr(prompt_planner, '_call_model', lambda *args, **kwargs: ({
        'duration_sec': 8, 'duration_reason': '弹琴动作自然完成。',
        'shot_mode': 'one_take', 'shots': [shot()]}, 0))
    segment = MiniMaxH3Ref2VAPipeline()._prepare_segment(
        {}, result['beats'][0], 1, {}, check_files=False, planning_only=True)
    assert segment['planning_state'] == 'ready', segment['planning_issues']
    assert segment['scene_id'] == '西城城楼'
    manifest = segment['generation_config']['asset_manifest']
    assert all(a['metadata'].get('asset_kind') != 'background' for a in manifest)
    assert '场景：西城城楼' in segment['job']['prompt']
    assert H3Ref2VAValidator().validate_binding_contract(
        segment, H3GenerationConfig.from_mapping(segment['generation_config']), bible=current).is_valid


def test_continuity_accepts_text_scene_without_background_image():
    from generation.pipelines.minimax_h3.continuity.state import validate_state
    value = {'schema_version': 1, 'scene_id': '西城城楼', 'characters': []}
    assert validate_state(value, {}, []) == value
    with pytest.raises(ValueError, match='场景身份'):
        validate_state(dict(value, scene_id=['西城城楼']), {}, [])


def test_single_background_is_not_applied_to_a_different_text_scene(tmp_path, monkeypatch):
    from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_compiler.load_project_bible', bible)
    image = tmp_path / 'office.png'
    image.write_bytes(b'reference')
    config = H3GenerationConfig.from_mapping({'asset_manifest': [{
        'asset_id': 'office', 'type': 'image', 'path': str(image), 'role': '办公室',
        'metadata': {'asset_kind': 'background', 'scene_id': 'office'}}]})
    first = dict(shot('office'), visible_roles=[], subject='窗户', action='阳光照进室内。', end_sec=4)
    second = dict(shot(), visible_roles=[], subject='城楼', action='旗帜迎风飘动。', start_sec=4)
    prompt = H3PromptCompiler().compile(
        beat([first, second]), config, H3AssetBinder().bind(config), duration_sec=8, draft=True).text
    shot_lines = [line for line in prompt.splitlines() if line.startswith('[Shot ')]
    assert '场景使用 <Subject' in shot_lines[0]
    assert '场景：西城城楼' in shot_lines[1]
    assert '场景使用 <Subject' not in shot_lines[1]
