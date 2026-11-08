from spatial_test_support import planner_reply, model_reply
"""Composition projects a scene roster into coherent, independently bound Shots."""
import copy
import json

import pytest

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
from generation.pipelines.minimax_h3.editor_draft import validate_candidate
from generation.pipelines.minimax_h3.shot_editor import parse_prompt, dialogue_units
from generation.pipelines.minimax_h3.continuity.composition import (
    finalize_composition, bind_final_state_quotes, validate_spatial_edit,
)
from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan


def image(role, order, kind='character'):
    return {'asset_id': role, 'type': 'image', 'path': role + '.png', 'role': role,
            'order': order, 'metadata': {'asset_kind': kind,
                'scene_id' if kind == 'background' else 'role_id': role}}


def sample():
    assets = [image('listener', 1), image('lead', 2), image('room', 3, 'background')]
    prompt = ('【主体定义】\n<Subject 1>：listener；<Picture 1>。\n'
              '<Subject 2>：lead；<Picture 2>。\n<Subject 3>：room；<Picture 3>。\n\n'
              '【参考保留关系】\n<Subject 1>：fully_preserved - 人物外观。\n\n'
              '【详细镜头描述】\n'
              '[Shot 1]（0–4 秒）：场景使用 <Subject 3>；画面人物：<Subject 2>；'
              '镜头构图：正面近景；<Subject 2>坐下；声音：本镜无对白。')
    base = {'segment_uid': 'seg', 'shot_uids': ['s1'], 'prompt': prompt,
            'asset_manifest': assets, 'duration_sec': 4}
    patch = {'shots': [{'shot_uid': 's1', 'visible_roles': ['lead'], 'edits': [],
                       'world_events': [{'role_id': 'lead', 'source_quote': '<Subject 2>坐下',
                                         'facts': {'posture': {'value': '坐着'}}}]}]}
    return base, patch


def test_unused_picture_remaps_identity_state_evidence_and_submission_together():
    base, patch = sample()
    original = copy.deepcopy((base, patch))
    text, manifest = finalize_composition(base, patch)
    candidate = dict(base, prompt=text, asset_manifest=manifest, composition_patch=patch)
    doc = validate_candidate(base, candidate, allow_background_visibility=True)
    assert [a['asset_id'] for a in manifest] == ['lead', 'room']
    assert doc['shots'][0]['visible_roles'] == ['lead']
    assert '场景使用 <Subject 2>' in text and '<Subject 1>坐下' in text
    rows = bind_final_state_quotes(base, patch, text, asset_manifest=manifest)
    assert rows[0]['world_events'][0]['source_quote'] == '<Subject 1>坐下'
    world = {'lead': {'posture': {'value': '站着'}, 'present': {'value': True}},
             'listener': {'posture': {'value': '坐着'}, 'present': {'value': True}}}
    end = reduce_plan(world, {'shots': rows}, 'seg', list(world))
    assert end['lead']['posture']['value'] == '坐着'
    assert end['listener'] == world['listener']
    assert H3PromptCompiler.compact_reference_sections(text, H3AssetBinder().bind(candidate)) == text
    assert (base, patch) == original
    # Neither a swapped file nor an unapproved body edit is allowed by reference
    # synchronization, even though that synchronization can change tag numbers.
    tampered = copy.deepcopy(candidate)
    tampered['asset_manifest'][0]['path'] = 'different_person.png'
    with pytest.raises(ValueError, match='受保护正文'):
        validate_spatial_edit(base, tampered)
    tampered = dict(candidate, prompt=text.replace('坐下', '离开'))
    with pytest.raises(ValueError, match='受保护正文'):
        validate_spatial_edit(base, tampered)


@pytest.mark.parametrize('dependency', ['空间关系：<Subject 1>位于主体后方，不入画；',
                                         '空间关系：listener位于主体后方，不入画；',
                                         '空间参考 <Picture 1>；'])
def test_authored_reference_dependency_is_preserved_without_forcing_visibility(dependency):
    base, patch = sample()
    base['prompt'] = base['prompt'].replace('镜头构图：', dependency + '镜头构图：')
    text, manifest = finalize_composition(base, patch)
    assert manifest == base['asset_manifest']
    assert dependency in text
    assert parse_prompt(text, 'seg', manifest)['shots'][0]['visible_roles'] == ['lead']
    assert '<Subject 1>：weak_reference - 仅作身份与声音归属映射' in text
    assert '<Subject 1>：fully_preserved' not in text


def test_offscreen_speech_voice_and_frame_anchor_survive_reference_projection():
    base, patch = sample()
    base['asset_manifest'].extend([
        {'asset_id': 'voice', 'type': 'audio', 'path': 'voice.wav', 'order': 4,
         'metadata': {'role_id': 'listener', 'voice_only': True}},
        {'asset_id': 'frame', 'type': 'image', 'path': 'frame.png', 'order': 5,
         'metadata': {'picture_role': 'first_frame', 'shot_indices': [1]}},
        {'asset_id': 'motion', 'type': 'video', 'path': 'motion.mp4', 'order': 6,
         'use_video_audio': True},
    ])
    # Embedded video audio consumes Audio 1; the standalone voice is Audio 2.
    speech = ('本句音色参考：<Audio 2>，仅用于<Subject 1>的声音；'
              '<Subject 1>在画外说：<d>[Chinese]保持原文<Subject 9>。</d>')
    base['prompt'] = base['prompt'].replace('声音：本镜无对白。', speech)
    text, manifest = finalize_composition(base, patch)
    doc = validate_candidate(base, dict(base, prompt=text, asset_manifest=manifest,
                                       composition_patch=patch), allow_background_visibility=True)
    assert manifest == base['asset_manifest']
    assert doc['shots'][0]['visible_roles'] == ['lead']
    unit = dialogue_units(doc['shots'][0])[0]
    assert unit['speaker'] == 'listener' and unit['scope'] == 'offscreen'
    assert unit['voice_asset_id'] == 'voice'
    assert '<d>[Chinese]保持原文<Subject 9>。</d>' in text
    assert '<Subject 1>：fully_preserved' not in text
    assert '<Picture 4>（用于 Shot 1）' in text


@pytest.mark.parametrize('metadata', [
    {'picture_role': 'composition_anchor'},
    {'retention_marker': 'attribute_transfer', 'retention_note': '仅参考光照'},
])
def test_explicit_reference_purpose_is_not_discarded_as_unused_cast(metadata):
    base, patch = sample()
    base['asset_manifest'][0]['metadata'].update(metadata)
    text, manifest = finalize_composition(base, patch)
    assert manifest == base['asset_manifest']
    if 'retention_note' in metadata:
        assert 'attribute_transfer' in text and '仅参考光照' in text


@pytest.mark.parametrize('camera,cast', [
    ('侧向双人镜，lead为清晰主体，listener在后景座位上', ['lead', 'listener']),
    ('从lead近景横移至listener，完整呈现运动中先后可见的两人', ['lead', 'listener']),
    ('lead侧面近景，取景朝向侧墙，将listener所在区域留在画外', ['lead']),
])
def test_camera_choices_project_visibility_without_manufacturing_scene_events(camera, cast):
    base, patch = sample()
    patch['shots'][0].update(visible_roles=cast, world_events=[],
        edits=[{'source_quote': '正面近景', 'replacement': camera}])
    text, manifest = finalize_composition(base, patch)
    doc = validate_candidate(base, dict(base, prompt=text, asset_manifest=manifest,
                                       composition_patch=patch), allow_background_visibility=True)
    assert doc['shots'][0]['visible_roles'] == cast
    world = {role: {'present': {'value': True}} for role in ('lead', 'listener')}
    assert reduce_plan(world, patch, 'seg', list(world)) == world
    if 'listener' in cast:
        assert '<Subject 1>（出现于 Shot 1）：fully_preserved' in text
    else:
        # Mentioning a real role as a spatial exclusion remains a dependency.
        assert '<Subject 1>：weak_reference - 仅作身份与声音归属映射' in text


def test_projected_candidate_commits_and_reopens_with_matching_state(tmp_path, monkeypatch):
    from test_h3_scene_state_machine import jobs, ready_scene_fixture
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint, commit
    from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context

    data = jobs()
    row = data['segments'][0]
    base, patch = sample()
    row.update(beat_uid='beat', generation_config={'duration_sec': 4,
               'prompt_override': base['prompt'], 'asset_manifest': base['asset_manifest']},
               h3_shot_editor={'shot_uids': ['s1']})
    data['h3_scene_continuity']['contexts']['room']['initial_state'].update({
        'lead': {'posture': {'value': '站着'}, 'present': {'value': True}},
        'listener': {'posture': {'value': '坐着'}, 'present': {'value': True}}})
    ready_scene_fixture(data, '1')
    def model(prepared, context):
        result = copy.deepcopy(patch)
        result.update(conflicts=[], unresolved=[])
        result['shots'][0]['role_coverage'] = [
            {'role_id': role, 'visibility': 'visible' if role == 'lead' else 'out_of_frame',
             'reason': '正面近景覆盖lead上半身' if role == 'lead'
                       else '摄影机正对lead上半身，该人物所在桌侧区域不在画框内'}
            for role in context['tracked_role_ids']]
        return result
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
                        planner_reply(model))
    draft = snapshot(row)
    result = build_opening_candidate({'operation_contract': 'scene_state_v3', 'jobs': data,
             'segment_uid': '1', 'draft': draft, 'base_revision': fingerprint(draft)})
    assert [a['asset_id'] for a in result['draft']['asset_manifest']] == ['lead', 'room']
    assert not result['draft'].get('state_only')
    assert data['segments'][0]['generation_config']['asset_manifest'] == base['asset_manifest']
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': [{'beat_uid': 'beat'}]}), encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: data)
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *args: None)
    writes = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',
                        lambda *args, **kwargs: writes.append(copy.deepcopy(kwargs)))
    monkeypatch.setattr('generation.pipelines.minimax_h3.shot_edit_service._editing_fingerprint', lambda *args: 'saved')
    saved = commit({'segment_uid': '1', 'base_revision': fingerprint(draft),
                    'draft': result['draft'], 'operation_base': result['operation_base']})
    assert len(writes) == 1
    assert saved['draft']['prompt'] == result['draft']['prompt']
    assert row['h3_continuity']['shots'][0]['world_events'][0]['source_quote'] == '<Subject 1>坐下'
    next_world = read_ready_context(data, '3')['input_state']
    assert next_world['lead']['posture']['value'] == '坐着'
    assert next_world['listener']['present']['value'] is True
    again = H3PromptCompiler.compact_reference_sections(saved['draft']['prompt'],
                                                       H3AssetBinder().bind(saved['draft']))
    assert again == saved['draft']['prompt']
