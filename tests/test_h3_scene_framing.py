from spatial_test_support import planner_reply, model_reply
"""Scene roster coverage is mandatory; all-in-frame is not a quota."""
import copy
import json

import pytest

from generation.pipelines.minimax_h3.continuity.framing import (
    FramingConflict, build_framing_record, validate_framing_plan,
    validate_framing_record, review_existing_framing,
)
from generation.pipelines.minimax_h3.continuity.composition import (
    finalize_composition, plan_composition_patch,
)


def sample():
    manifest = [{'asset_id': role, 'type': 'image', 'path': role + '.png', 'role': name,
                 'order': i, 'metadata': {'asset_kind': 'character', 'role_id': role}}
                for i, (role, name) in enumerate(zip('abcd', ('甲', '乙', '丙', '丁')), 1)]
    manifest.append({'asset_id': 'room', 'type': 'image', 'path': 'room.png', 'role': '会议室',
                     'order': 5, 'metadata': {'asset_kind': 'background', 'scene_id': 'room'}})
    base = {'segment_uid': 'seg', 'shot_uids': ['s1'], 'duration_sec': 4,
            'asset_manifest': manifest,
            'prompt': '【详细镜头描述】\n[Shot 1]（0–4 秒）：场景使用 <Subject 5>；画面人物：<Subject 1>；'
                      '镜头构图：甲正面近景；<Subject 1>整理文件；声音：本镜无对白。'}
    context = {'context_id': 'scene', 'input_revision': 'opening', 'tracked_role_ids': list('abcd'),
               'input_state': {role: {'present': {'value': True}, 'position': {'value': f'{role}座位'},
                                      'posture': {'value': '坐着'}} for role in 'abcd'}}
    patch = {'shots': [{'shot_uid': 's1', 'visible_roles': ['a'], 'edits': [], 'world_events': [],
                        'role_coverage': [{'role_id': role, 'visibility': 'visible' if role == 'a' else 'out_of_frame',
                                           'reason': '摄影机朝主体，其他座位位于镜头后方' if role != 'a' else '正面主体'}
                                          for role in 'abcd']}], 'conflicts': [], 'unresolved': []}
    return base, context, patch


def segment(base, context, record):
    return {'segment_uid': base['segment_uid'], 'scene_context_id': context['context_id'],
            'generation_config': {'prompt_override': record['source_prompt'], 'duration_sec': 4,
                                  'asset_manifest': base['asset_manifest']},
            'h3_shot_editor': {'shot_uids': base['shot_uids']}, 'h3_scene_framing': record}


def test_silent_role_must_be_accounted_for_even_when_never_visible():
    base, context, patch = sample()
    patch['shots'][0]['role_coverage'].pop()
    with pytest.raises(ValueError, match='漏报：d'):
        validate_framing_plan(base, context, patch)


def test_valid_closeup_keeps_world_roster_but_projects_only_needed_pictures():
    base, context, patch = sample()
    base['_framing_context'] = context
    text, manifest = finalize_composition(base, patch)
    assert [a['asset_id'] for a in manifest] == ['a', 'room']
    assert '【场景人物占位】' not in text
    assert '乙座位' not in text and '画面人物：<Subject 1>' in text
    state = validate_framing_plan(base, context, patch)[0]['state_after']
    assert len(state) == 4 and all(s['present']['value'] for s in state.values())
    # Re-running keeps the world roster internal and projects only Shot usage.
    next_base = dict(base, prompt=text, asset_manifest=manifest)
    again, references = finalize_composition(next_base, patch)
    assert references == manifest and '【场景人物占位】' not in again


def test_partial_and_transient_visibility_both_require_reference_and_cast():
    base, context, patch = sample()
    patch['shots'][0]['role_coverage'][1]['visibility'] = 'partial'
    with pytest.raises(ValueError, match='可见并集'):
        validate_framing_plan(base, context, patch)
    patch['shots'][0]['visible_roles'].append('b')
    base['_framing_context'] = context
    text, manifest = finalize_composition(base, patch)
    assert {a['asset_id'] for a in manifest} == {'a', 'b', 'room'}
    assert '画面人物：<Subject 1>、<Subject 2>' in text


@pytest.mark.parametrize('timing_only', [False, True])
def test_saved_override_drops_occupancy_before_video_compilation(timing_only):
    from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    base, context, patch = sample()
    original = base['prompt']
    # Even stale tags in a removed table must not become reference dependencies.
    text = original + '\n\n【场景人物占位】\n<Subject 99>：在场；位置：旧座位。'
    config = H3GenerationConfig.from_mapping(dict(base, prompt_override=text))
    bindings = H3AssetBinder().bind(config)
    compiled = H3PromptCompiler().compile({'h3_timing_only': timing_only}, config, bindings,
                                         duration_sec=4, draft=True)
    assert compiled.text == original
    assert '【场景人物占位】' not in compiled.text


def test_occupancy_cleanup_preserves_shots_dialogue_and_following_sections():
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    base, context, patch = sample()
    dialogue = '<d>请读这行：\n【场景人物占位】\n这是台词。</d>'
    shot_text = base['prompt'] + '\n' + dialogue
    tail = '\n\n【整体声音环境】\n空调低鸣。'
    text = shot_text + '\n\n【场景人物占位】\n<Subject 99>：在场。' + tail
    clean = H3PromptCompiler.strip_occupancy_section(text)
    assert clean == shot_text + '\n\n' + tail.lstrip()
    config = H3GenerationConfig.from_mapping(base)
    spoken = '<d>保持原位置，继续讨论。</d>'
    reconciled = H3PromptReconciler().reconcile(text.replace(dialogue, spoken), config, config)
    assert spoken in reconciled
    assert '<Subject 99>' not in reconciled
    assert '空调低鸣。' in reconciled


def test_reoptimization_discards_old_occupancy_but_keeps_internal_roster():
    base, context, patch = sample()
    base['prompt'] += '\n\n【场景人物占位】\n乙：在场；位置：乙座位。'
    base['_framing_context'] = context
    before = copy.deepcopy(context)
    text, manifest = finalize_composition(base, patch)
    assert context == before
    assert '【场景人物占位】' not in text and '乙座位' not in text
    assert [a['asset_id'] for a in manifest] == ['a', 'room']
    record = build_framing_record(base, context, patch, text, manifest)
    assert record['input_state']['b']['position']['value'] == 'b座位'
    assert len(record['shots'][0]['role_coverage']) == 4


def test_whole_shot_absence_cannot_erase_entry_then_exit():
    base, context, patch = sample()
    context['input_state']['d']['present']['value'] = False
    base['prompt'] = base['prompt'].replace('整理文件', '整理文件，丁进门后又离开房间')
    row = patch['shots'][0]
    row['world_events'] = [
        {'role_id': 'd', 'source_quote': '丁进门', 'facts': {'present': {'value': True}}},
        {'role_id': 'd', 'source_quote': '离开房间', 'facts': {'present': {'value': False}, 'position': {'value': None}}},
    ]
    row['role_coverage'][3]['visibility'] = 'not_present'
    with pytest.raises(ValueError, match='曾进场'):
        validate_framing_plan(base, context, patch)
    row['role_coverage'][3]['visibility'] = 'partial'
    row['visible_roles'].append('d')
    states = validate_framing_plan(base, context, patch)
    assert states[-1]['state_after']['d']['present']['value'] is False


def test_known_absent_character_needs_entry_before_visible():
    base, context, patch = sample()
    context['input_state']['a']['present']['value'] = False
    with pytest.raises(ValueError, match='整镜不在场'):
        validate_framing_plan(base, context, patch)


def test_sparse_legacy_model_reply_is_not_blessed_or_mechanically_repaired(monkeypatch):
    base, context, patch = sample()
    patch['shots'][0].pop('role_coverage')
    calls = []
    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        return copy.deepcopy(patch), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    with pytest.raises(ValueError, match='已自动重试一次.*role_coverage'):
        plan_composition_patch(base, context)
    assert len(calls) == 2
    assert len(calls[0]['input_state']) == len(calls[0]['tracked_role_ids']) == 4
    assert 'role_coverage' in calls[0]['output_contract']['required_shot_fields']


def test_evidenced_conflict_is_reported_without_rewriting_or_retry(monkeypatch):
    base, context, patch = sample()
    patch['conflicts'] = [{'shot_uid': 's1', 'role_id': 'a', 'source_quote': '甲正面近景',
                           'reason': '当前作者指定机位与固定座位约束冲突'}]
    calls = []
    monkeypatch.setattr('services.llm.call_llm', model_reply(lambda *args, **kw: (calls.append(1) or copy.deepcopy(patch), 0)))
    with pytest.raises(FramingConflict, match='Shot 1 人物 a'):
        plan_composition_patch(base, context)
    assert calls == [1]


@pytest.mark.parametrize('change', ['prompt', 'reference', 'input', 'layout', 'cast_record'])
def test_record_is_bound_to_final_content_state_and_layout(change):
    base, context, patch = sample()
    record = build_framing_record(base, context, patch, base['prompt'], base['asset_manifest'])
    saved = segment(base, context, record)
    assert validate_framing_record(saved, context)['status'] == 'ready'
    if change == 'prompt': saved['generation_config']['prompt_override'] += '\n新构图'
    if change == 'reference': saved['generation_config']['asset_manifest'][0]['path'] = 'other.png'
    if change == 'input': context['input_state']['c']['position']['value'] = '门边'
    if change == 'layout': context['scene_layout'] = {'description': '更换了布局'}
    if change == 'cast_record': saved['h3_scene_framing']['shots'][0]['visible_roles'].append('c')
    with pytest.raises(ValueError):
        validate_framing_record(saved, context)


def test_readonly_review_of_saved_draft_does_not_change_prompt_or_select_images(monkeypatch):
    base, context, patch = sample()
    original = copy.deepcopy(base)
    monkeypatch.setattr('services.llm.call_llm', model_reply(lambda *args, **kwargs: (copy.deepcopy(patch), 0)))
    record = review_existing_framing(base, context)
    assert record['source_prompt'] == base['prompt'] and base == original
    assert len(record['shots'][0]['role_coverage']) == 4


def test_stale_layout_is_not_used_for_new_geometry_or_review(monkeypatch):
    base, context, patch = sample()
    context['scene_layout'] = {'description': '旧背景桌椅布局', 'current': False}
    monkeypatch.setattr('services.llm.call_llm', model_reply(lambda *args, **kwargs: pytest.fail('stale geometry called model')))
    for operation in (plan_composition_patch, review_existing_framing):
        with pytest.raises(ValueError, match='背景图已变化'):
            operation(base, context)


def test_ui_can_report_unready_context_instead_of_crashing():
    base, context, patch = sample()
    record = build_framing_record(base, context, patch, base['prompt'], base['asset_manifest'])
    with pytest.raises(ValueError, match='状态尚未就绪'):
        validate_framing_record(segment(base, context, record), None)


def test_readonly_review_cannot_approve_visible_character_without_picture(monkeypatch):
    base, context, patch = sample()
    base['asset_manifest'] = [a for a in base['asset_manifest'] if a['asset_id'] != 'b']
    patch['shots'][0]['role_coverage'][1]['visibility'] = 'partial'
    patch['shots'][0]['visible_roles'].append('b')
    with pytest.raises(ValueError, match='缺少对应图片参考：b'):
        validate_framing_plan(base, context, patch)


def test_voice_reference_label_never_replaces_character_name_in_internal_record():
    from generation.pipelines.minimax_h3.continuity.framing import role_names
    base, context, patch = sample()
    base['asset_manifest'].append({'asset_id': 'voice', 'type': 'audio', 'path': 'voice.mp3',
        'role': '甲参考音色', 'label': '甲参考音色', 'metadata': {'role_id': 'a', 'voice_only': True}})
    names = role_names(base, context)
    assert names['a'] == '甲'
    assert names['a'] != '甲参考音色'


def test_generated_visibility_notes_are_not_author_constraints(monkeypatch):
    base, context, patch = sample()
    authored = base['prompt'] + '\n限制：不得让未发言人物开口；不得新增台词。'
    base['prompt'] = ('【主体定义】\n<Subject 2>：乙；本段未安排入画；在场状态以镜头为准。\n\n'
                      '【参考保留关系】\n<Subject 2>：weak_reference - 本段不安排入画。\n\n' + authored)
    seen = []
    def model(system, user, **kwargs):
        payload = json.loads(user); seen.append(payload)
        assert '本段未安排入画' not in payload['editor_prompt']
        assert 'weak_reference' not in payload['editor_prompt']
        assert authored in payload['editor_prompt']
        assert len(payload['references']) == 5
        assert '不是人物禁止入画要求' in payload['visibility_authority']
        return copy.deepcopy(patch), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    assert plan_composition_patch(base, context) == patch
    assert len(seen) == 1


def test_unresolved_framing_gets_one_model_owned_redesign(monkeypatch):
    base, context, valid = sample()
    unresolved = copy.deepcopy(valid)
    unresolved['unresolved'] = [{'shot_uid': 's1', 'role_id': 'b', 'source_quote': '甲正面近景',
                                 'reason': '旧名单未包括乙，不能根据当前机位确定其在画外'}]
    valid['shots'][0]['visible_roles'].append('b')
    valid['shots'][0]['role_coverage'][1].update(visibility='partial', reason='静默听者作为背景局部入画')
    valid['shots'][0]['edits'] = [{'source_quote': '甲正面近景',
                                  'replacement': 'a为中景主体，b在其已知座位作为虚化背景局部入画'}]
    calls = []
    def model(system, user, **kwargs):
        payload = json.loads(user); calls.append(payload)
        if len(calls) == 2:
            assert payload['validation_feedback']['previous_response']['patch'] == unresolved
            assert '原名单不禁止其他在场人物入画' in payload['validation_feedback']['instruction']
        return copy.deepcopy(unresolved if len(calls) == 1 else valid), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    result = plan_composition_patch(base, context)
    assert len(calls) == 2 and result['shots'][0]['visible_roles'] == ['a', 'b']
    assert result['shots'][0]['world_events'] == []


def test_unresolved_real_constraint_is_not_silently_removed_after_retry(monkeypatch):
    base, context, patch = sample()
    patch['unresolved'] = [{'shot_uid': 's1', 'role_id': 'b', 'source_quote': '甲正面近景',
                            'reason': '作者固定独拍与未知座位关系无法通过允许的空间调整同时满足'}]
    calls = []
    monkeypatch.setattr('services.llm.call_llm', model_reply(lambda *args, **kwargs: (calls.append(1) or copy.deepcopy(patch), 0)))
    with pytest.raises(FramingConflict, match='作者固定独拍'):
        plan_composition_patch(base, context)
    assert len(calls) == 2


def reference_roundtrip():
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
    base, context, patch = sample()
    base['asset_manifest'][0]['role'] = '角色甲'
    base['prompt'] = base['prompt'].replace('甲正面近景', '角色甲正面近景').replace('<Subject 1>整理文件', '角色甲从座位起身')
    patch['shots'][0]['world_events'] = [{'role_id': 'a', 'source_quote': '角色甲从座位起身',
                                        'facts': {'posture': {'value': '站着'}}}]
    record = build_framing_record(base, context, patch, base['prompt'], base['asset_manifest'])
    saved = segment(base, context, record)
    saved['generation_config']['asset_manifest'] = copy.deepcopy(base['asset_manifest'])
    saved['generation_config']['asset_manifest'][0]['metadata']['library_voice'] = {'path': 'unused-library.mp3'}
    new_config = H3GenerationConfig.from_mapping(saved['generation_config'])
    saved['generation_config']['prompt_override'] = H3PromptReconciler().reconcile(
        record['source_prompt'], H3GenerationConfig.from_mapping(base), new_config)
    return saved, context, record


def test_reference_roundtrip_rebinds_only_proven_compiler_changes_without_llm(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.framing import advance_framing_reference_record
    saved, context, old = reference_roundtrip()
    monkeypatch.setattr('services.llm.call_llm', model_reply(lambda *a, **k: pytest.fail('reference rebind cannot call LLM')))
    assert advance_framing_reference_record(saved, context)
    record = validate_framing_record(saved, context)
    assert record['shots'][0]['world_events'][0]['source_quote'] == '<Subject 1>从座位起身'
    assert record['shots'][0]['state_after'] == old['shots'][0]['state_after']
    assert record['shots'][0]['role_coverage'] == old['shots'][0]['role_coverage']


@pytest.mark.parametrize('change', ['file', 'camera', 'state', 'missing_evidence'])
def test_reference_rebind_never_blesses_changed_content_or_unknown_old_media(change):
    from generation.pipelines.minimax_h3.continuity.framing import advance_framing_reference_record
    saved, context, old = reference_roundtrip()
    if change == 'file': saved['generation_config']['asset_manifest'][0]['path'] = 'other-person.png'
    if change == 'camera': saved['generation_config']['prompt_override'] = saved['generation_config']['prompt_override'].replace('正面近景', '侧面近景')
    if change == 'state': context['input_state']['a']['position']['value'] = '门口'
    if change == 'missing_evidence': saved['h3_scene_framing'].pop('source_asset_manifest')
    before = copy.deepcopy(saved)
    assert not advance_framing_reference_record(saved, context)
    assert saved == before

from spatial_test_support import plan_patch as plan_composition_patch
