import copy

import pytest

from generation.pipelines.minimax_h3.semantic_bindings import (
    BindingError, MODEL, binding_coverage_errors, normalize_beat_bindings,
)


@pytest.fixture
def bible():
    return {'core_characters': [
        {'id': 'a', 'display_name': '甲', 'aliases': ['经理'], 'need_reference_image': True},
        {'id': 'b', 'display_name': '乙', 'aliases': [], 'need_reference_image': True},
    ], 'allowed_backgrounds': [
        {'id': 'office', 'display_name': '办公室', 'asset_background_id': 'office_img'},
        {'id': 'store', 'display_name': '仓库', 'asset_background_id': 'store_img'},
    ]}


def beat(shots):
    return {'id': 1, 'model_hints': {MODEL: {'shots': shots}}}


def shot(**updates):
    return {'visible_roles': ['甲'], 'scene_id': '办公室', **updates}


def test_names_are_resolved_and_mentioned_listener_is_not_added(bible):
    original = beat([shot(dialogue={'speaker': '经理', 'line': '乙说过。', 'scope': 'onscreen'},
                          mentioned_roles=['乙'])])
    snapshot = copy.deepcopy(original)
    result = normalize_beat_bindings(original, bible=bible)
    assert original == snapshot
    assert result['reference_roles'] == ['a']
    assert result['mentioned_roles'] == ['b']
    assert result['model_hints'][MODEL]['shots'][0]['dialogue']['speaker'] == 'a'


def test_same_scene_context_survives_shots_without_repeated_location(bible):
    result = normalize_beat_bindings(beat([
        {'visible_roles': ['甲'], 'scene_context_id': 'c'},
        {'visible_roles': ['乙'], 'scene_context_id': 'c'},
    ]), bible=bible, scene_contexts=[{'id': 'c', 'scene_id': '办公室'}])
    assert result['scene_id'] == 'office'
    assert result['reference_roles'] == ['a', 'b']
    assert [s['scene_id'] for s in result['model_hints'][MODEL]['shots']] == ['office', 'office']


def test_real_transition_preserves_both_backgrounds(bible):
    result = normalize_beat_bindings(beat([shot(), shot(scene_id='仓库', visible_roles=['乙'])]), bible=bible)
    assert result['scene_id'] == ''
    assert [s['scene_id'] for s in result['model_hints'][MODEL]['shots']] == ['office', 'store']


def test_missing_visibility_is_not_an_empty_shot(bible):
    with pytest.raises(BindingError, match='visible_roles'):
        normalize_beat_bindings(beat([{'scene_id': '办公室'}]), bible=bible)
    assert normalize_beat_bindings(beat([shot(visible_roles=[])]), bible=bible)['reference_roles'] == []


def test_ambiguous_alias_requires_resolution(bible):
    bible['core_characters'][1]['aliases'] = ['经理']
    with pytest.raises(BindingError, match='歧义'):
        normalize_beat_bindings(beat([shot(visible_roles=['经理'])]), bible=bible)


def test_story_names_are_not_reference_roles(bible):
    original = beat([shot(visible_roles=['经理', '小林'], mentioned_roles=['门卫'],
        dialogues=[{'speaker': '小林', 'scope': 'onscreen', 'line': '我到了。'},
                   {'speaker': '快递员', 'scope': 'telephone', 'line': '请签收。'}])])
    result = normalize_beat_bindings(original, bible=bible)
    assert result['visible_roles'] == ['a', '小林']
    assert result['offscreen_speakers'] == ['快递员']
    assert result['mentioned_roles'] == ['门卫']
    assert result['reference_roles'] == ['a']
    assert result['text_roles'] == ['小林', '快递员', '门卫']
    assert binding_coverage_errors(result, [], bible=bible) == ['入画人物缺少参考图：a']
    assert result == normalize_beat_bindings(result, bible=bible)


@pytest.mark.parametrize('speaker', ['乙', '小林'])
def test_onscreen_speaker_must_be_visible(bible, speaker):
    with pytest.raises(BindingError, match='说话人'):
        normalize_beat_bindings(beat([shot(dialogue={'speaker': speaker, 'scope': 'onscreen'})]), bible=bible)


@pytest.mark.parametrize('name', ['', '<Subject 8>', '<Audio 2>', 'character_999'])
def test_invalid_role_tokens_are_not_text_roles(bible, name):
    with pytest.raises(BindingError):
        normalize_beat_bindings(beat([shot(visible_roles=[name])]), bible=bible)


@pytest.mark.parametrize('include_registered', [False, True])
def test_novel_text_roles_survive_segment_compilation_and_submit_validation(
        bible, tmp_path, monkeypatch, include_registered):
    from generation.pipelines.minimax_h3 import prompt_planner
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from generation.pipelines.minimax_h3.shot_editor import dialogue_units, parse_prompt
    from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
    from scripts.story_script import generate_beats

    bible['core_characters'][0].update(aliases=['小满'], asset_identity_id='identity_a')
    reference_path = tmp_path / 'a.png'
    reference_path.write_bytes(b'reference')
    index = {'characters': {'identities': [{
        'id': 'identity_a', 'name_cn': '小满', 'images': [{'id': 'face', 'path': str(reference_path)}],
    }]}, 'backgrounds': []}
    for module in ('services.project_bible', 'workflow.asset_matcher',
                   'generation.pipelines.minimax_h3.auto_binder', 'generation.pipelines.minimax_h3.prompt_compiler'):
        monkeypatch.setattr(module + '.load_project_bible', lambda: bible)
    for module in ('workflow.asset_matcher', 'generation.pipelines.minimax_h3.auto_binder'):
        monkeypatch.setattr(module + '.load_asset_index', lambda: index)
    monkeypatch.setattr('services.character_voice.attach_character_voices', lambda items, *args: items)

    cast = ['甲', '苏小满'] if include_registered else ['苏小满']
    action = '苏小满把信递给甲。' if include_registered else '苏小满把信放在桌上。'
    excerpt = action + '苏小满说：“请查收。”'
    authored = shot(start_sec=0, end_sec=8, scene_id='驿站', visible_roles=cast,
                    subject='苏小满', action=action, audio='纸张摩擦声。',
                    dialogue={'speaker': '苏小满', 'scope': 'onscreen', 'line': '请查收。'})
    beat_shot = {key: value for key, value in authored.items() if key != 'subject'}
    raw = {'title': '送信', 'locked_outcome': '信件送达。', 'beats': [dict(beat([beat_shot]),
        title='送信', plot=action, source_excerpt=excerpt, estimated_duration_sec=8)]}
    monkeypatch.setattr(generate_beats, '_call_deepseek_messages', lambda *args, **kwargs: copy.deepcopy(raw))
    result = generate_beats.generate_novel_beats(
        {'novel_text': excerpt, 'project_bible': bible, 'target_duration_sec': 8, 'target_beat_count': 1},
        model_id=MODEL, config={}, temp_dir=tmp_path)
    calls = []

    def plan_model(system, payload, **kwargs):
        calls.append(system)
        return {'duration_sec': 8, 'duration_reason': '信件递送和一句对白。', 'core_idea': action,
                'shots': [copy.deepcopy(authored)], 'shot_mode': 'one_take'}, 0

    monkeypatch.setattr(prompt_planner, '_call_model', plan_model)
    pipeline = MiniMaxH3Ref2VAPipeline()
    segment = pipeline._prepare_segment(
        {}, result['beats'][0], 1, {}, check_files=False, planning_only=True)
    assert len(calls) == 1 and '未登记的人物沿用原名' in calls[0]
    assert segment['planning_state'] == 'ready', segment['planning_issues']
    refs = ['a'] if include_registered else []
    manifest = segment['generation_config']['asset_manifest']
    assert segment['visible_roles'] == refs + ['苏小满']
    assert segment['reference_roles'] == [item['metadata']['role_id'] for item in manifest] == refs
    prompt = segment['job']['prompt']
    assert '苏小满 (S1)说：<d>[Chinese]请查收。</d>' in prompt
    assert '苏小满把信' in prompt
    parsed = parse_prompt(prompt, 'saved', manifest)['shots'][0]
    assert parsed['visible_roles'] == refs + ['苏小满']
    assert dialogue_units(parsed)[0]['speaker'] == '苏小满'
    config = H3GenerationConfig.from_mapping(segment['generation_config'])
    assert H3Ref2VAValidator().validate_binding_contract(segment, config, bible=bible).is_valid
    if include_registered:
        regenerated = pipeline._prepare_segment(
            {}, result['beats'][0], 1, dict(segment, h3_force_replan=True, h3_editor_prompt=prompt),
            check_files=False, planning_only=True)
        assert regenerated['planning_state'] == 'ready', regenerated['planning_issues']
        assert '苏小满 (S1)说：<d>[Chinese]请查收。</d>' in regenerated['job']['prompt']
        assert H3Ref2VAValidator().validate_binding_contract(regenerated,
            H3GenerationConfig.from_mapping(regenerated['generation_config']), bible=bible).is_valid


def test_explicit_delivery_visibility_controls_reference_coverage(bible):
    unit = {'speaker': '乙', 'scope': 'onscreen', 'line': '你好',
            'delivery_spans': [{'range': [0, 2], 'visibility': 'offscreen'}]}
    result = normalize_beat_bindings(beat([shot(dialogue=unit)]), bible=bible)
    assert result['reference_roles'] == ['a'] and result['offscreen_speakers'] == ['b']
    unit['scope'] = 'offscreen'
    unit['delivery_spans'][0]['visibility'] = 'onscreen'
    with pytest.raises(BindingError, match='说话人'):
        normalize_beat_bindings(beat([shot(dialogue=unit)]), bible=bible)


def test_offscreen_speaker_can_be_visible_in_a_later_shot(bible):
    result = normalize_beat_bindings(beat([
        shot(dialogue={'speaker': '乙', 'scope': 'telephone', 'line': '你好'}),
        shot(visible_roles=['乙']),
    ]), bible=bible)
    assert result['offscreen_speakers'] == ['b']
    assert result['visible_roles'] == ['a', 'b']


def test_missing_reference_blocks_even_when_background_exists(bible):
    result = normalize_beat_bindings(beat([shot()]), bible=bible)
    manifest = [{'metadata': {'asset_kind': 'background', 'scene_id': 'office'}}]
    assert binding_coverage_errors(result, manifest, bible=bible) == ['入画人物缺少参考图：a']


def test_normalized_cast_drives_real_asset_matching(bible, tmp_path):
    from workflow.asset_matcher import match_beat_assets
    from generation.pipelines.minimax_h3.auto_binder import auto_bind_asset_manifest
    from unittest.mock import patch
    bible['core_characters'][0]['asset_identity_id'] = 'identity_a'
    index = {'characters': {'identities': [{'id': 'identity_a', 'images': [
        {'id': 'face_a', 'path': 'face.png'}]}]},
             'backgrounds': [{'id': 'office_img', 'path': 'office.png'}]}
    for item in [index['characters']['identities'][0]['images'][0], index['backgrounds'][0]]:
        target = tmp_path / item['path']
        target.write_bytes(b'reference')
        item['path'] = str(target)
    resolved = normalize_beat_bindings(beat([shot()]), bible=bible)
    matched = match_beat_assets(resolved, bible=bible, asset_index=index, model_id=MODEL)
    with patch('generation.pipelines.minimax_h3.auto_binder.load_project_bible', return_value=bible), \
         patch('generation.pipelines.minimax_h3.auto_binder.load_asset_index', return_value=index):
        manifest = auto_bind_asset_manifest(matched)
    assert {a['metadata'].get('role_id') for a in manifest if a['metadata'].get('role_id')} == {'a'}
    assert {a['metadata'].get('scene_id') for a in manifest if a['metadata'].get('scene_id')} == {'office'}


def test_plain_novel_to_final_segment_refreshes_automatic_cast(bible, tmp_path, monkeypatch):
    from scripts.story_script import generate_beats
    from generation.pipelines.minimax_h3 import prompt_planner
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    index = {'characters': {'identities': []}, 'backgrounds': []}
    for role in bible['core_characters']:
        role['asset_identity_id'] = 'identity_' + role['id']
        path = tmp_path / (role['id'] + '.png')
        path.write_bytes(b'reference')
        index['characters']['identities'].append({
            'id': role['asset_identity_id'], 'name_cn': role['display_name'],
            'images': [{'id': 'image_' + role['id'], 'path': str(path)}]})
    background = tmp_path / 'office.png'
    background.write_bytes(b'reference')
    index['backgrounds'] = [{'id': 'office_img', 'path': str(background)}]
    for module in ('services.project_bible', 'workflow.asset_matcher',
                   'generation.pipelines.minimax_h3.auto_binder',
                   'generation.pipelines.minimax_h3.prompt_compiler'):
        monkeypatch.setattr(module + '.load_project_bible', lambda: bible)
    for module in ('workflow.asset_matcher', 'generation.pipelines.minimax_h3.auto_binder'):
        monkeypatch.setattr(module + '.load_asset_index', lambda: index)
    monkeypatch.setattr('services.character_voice.attach_character_voices', lambda items, *args: items)
    raw = {'title': '交谈', 'locked_outcome': '甲说完。', 'scene_contexts': [
        {'id': 'c', 'scene_id': '办公室', 'source_excerpt': '办公室里'}],
        'beats': [{'id': 1, 'title': '交谈', 'plot': '办公室里甲对乙说你好。',
                   'estimated_duration_sec': 8, 'source_excerpt': '办公室里甲对乙说：“你好。”',
                   'model_hints': {MODEL: {'shots': [
                       {'start_sec': 0, 'end_sec': 8, 'visible_roles': ['甲'],
                        'scene_context_id': 'c', 'action': '甲说话。',
                        'dialogue': {'speaker': '甲', 'scope': 'onscreen', 'line': '你好。'}}]}}}]}
    monkeypatch.setattr(generate_beats, '_call_deepseek_messages', lambda *args, **kwargs: copy.deepcopy(raw))
    result = generate_beats.generate_novel_beats(
        {'novel_text': '办公室里甲对乙说：“你好。”', 'project_bible': bible,
         'target_duration_sec': 8, 'target_beat_count': 1}, model_id=MODEL, config={}, temp_dir=tmp_path)
    source = copy.deepcopy(result['beats'][0])
    # The final director chooses a two-person composition. References must follow it.
    monkeypatch.setattr(prompt_planner, '_call_model', lambda *args, **kwargs: ({
        'duration_sec': 8, 'duration_reason': '对白和动作自然完成。', 'shot_mode': 'one_take',
        'shots': [{'start_sec': 0, 'end_sec': 8, 'scene_context_id': 'c',
                   'visible_roles': ['乙', '甲'], 'action': '甲说话，乙沉默听着。',
                   'dialogue_refs': [1]}]}, 0))
    segment = MiniMaxH3Ref2VAPipeline()._prepare_segment(
        {}, source, 1, {}, check_files=False, planning_only=True)
    assert result['beats'][0] == source
    assert segment['planning_state'] == 'ready', segment['planning_issues']
    assert segment['visible_roles'] == ['b', 'a']
    manifest = segment['generation_config']['asset_manifest']
    assert [a['metadata']['role_id'] for a in manifest if a['metadata'].get('role_id')] == ['a', 'b']
    assert '<Subject 1>：甲；可见内容来自 <Picture 1>' in segment['job']['prompt']
    assert {a['metadata'].get('role_id') for a in manifest if a['metadata'].get('role_id')} == {'a', 'b'}
    assert segment['scene_id'] == 'office'
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
    assert H3Ref2VAValidator().validate_binding_contract(
        segment, H3GenerationConfig.from_mapping(segment['generation_config']), bible=bible).is_valid, segment['job']['prompt']
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt
    parsed = parse_prompt(segment['job']['prompt'], 'saved', manifest)
    assert parsed['shots'][0]['scene_id'] == 'office'

    # A deliberate manual removal is preserved and reported, never silently re-added.
    existing = copy.deepcopy(segment)
    existing['generation_config']['asset_manifest'] = [
        a for a in manifest if a['metadata'].get('role_id') != 'b']
    existing['generation_config']['reference_selection_source'] = 'manual'
    existing['generation_config'].pop('prompt_override', None)
    existing['h3_force_replan'] = True
    regenerated = MiniMaxH3Ref2VAPipeline()._prepare_segment(
        {}, source, 1, existing, check_files=False, planning_only=True)
    assert regenerated['planning_state'] == 'warning'
    assert any(i['code'] == 'invalid_binding' and i['blocking'] for i in regenerated['planning_issues'])
    assert not any(a['metadata'].get('role_id') == 'b'
                   for a in regenerated['generation_config']['asset_manifest'])
