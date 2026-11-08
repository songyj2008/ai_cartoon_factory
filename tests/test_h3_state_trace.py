import copy
import json

import pytest

from generation.pipelines.minimax_h3.continuity.state_trace import extract_state_record, validate_state_result


def inputs():
    segment = {
        'segment_uid': 'part4', 'part_id': 'Part 4', 'scene_context_id': 'room',
        'status': 'success', 'active_render_id': 'good', 'video_path': 'good.mp4',
        'generation_config': {
            'duration_sec': 4,
            'prompt_override': '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；甲站在桌前，随后坐下；本镜无对白。',
            'asset_manifest': [],
        },
        'h3_prompt_plan': {'shots': []}, 'h3_shot_editor': {'shot_uids': ['stable-shot']},
    }
    context = {'context_id': 'room', 'input_revision': 'upstream-version',
               'input_state': {'a': {'posture': {'value': '站着'}, 'present': {'value': True}}},
               'tracked_role_ids': ['a'], 'previous_segment_uid': 'part3', 'initializing': False}
    response = {'shots': [{'shot_uid': 'stable-shot', 'world_events': [
        {'role_id': 'a', 'source_quote': '随后坐下', 'facts': {'posture': {'value': '坐着'}}}]}],
        'opening_requirements': [{'shot_uid': 'stable-shot', 'role_id': 'a', 'field': 'posture',
                                  'value': '站着', 'source_quote': '甲站在桌前'}],
        'conflicts': [], 'unresolved': []}
    return segment, context, response


def stub(monkeypatch, response, calls=None):
    def model(system, prompt, **kwargs):
        assert kwargs['label'].startswith('h3_prompt_state_trace_')
        assert kwargs['max_tokens'] == 6500 and kwargs['tolerate_invalid_json'] is True
        assert '不执行构图优化' in system
        if calls is not None:
            calls.append(json.loads(prompt))
        return copy.deepcopy(response), {}
    monkeypatch.setattr('services.llm.call_llm', model)


def test_opening_present_string_is_corrected_by_model_retry(monkeypatch):
    segment, context, response = inputs()
    response['opening_requirements'] = [{'shot_uid': 'stable-shot', 'role_id': 'a',
        'field': 'present', 'value': 'true', 'source_quote': '甲站在桌前'}]
    invalid = copy.deepcopy(response)
    calls = []
    def model(system, prompt, **kwargs):
        calls.append(json.loads(prompt))
        assert '禁止字符串 "true"' in system
        if len(calls) == 1:
            return copy.deepcopy(invalid), 0
        corrected = copy.deepcopy(invalid)
        corrected['opening_requirements'][0]['value'] = True
        return corrected, 0
    monkeypatch.setattr('services.llm.call_llm', model)
    before = copy.deepcopy((segment, context, response))
    record = extract_state_record(segment, context)
    assert record['opening_requirements'][0]['value'] is True
    assert len(calls) == 2
    assert calls[1]['shots'] == calls[0]['shots']
    assert calls[1]['validation_feedback']['previous_response'] == invalid
    assert '事实值类型无效：present' in calls[1]['validation_feedback']['error']
    assert (segment, context, response) == before


def test_present_string_remains_rejected_after_bounded_retry(monkeypatch):
    segment, context, response = inputs()
    response['opening_requirements'][0].update(field='present', value='true')
    calls = []
    stub(monkeypatch, response, calls)
    with pytest.raises(ValueError, match='已自动重试一次.*事实值类型无效：present'):
        extract_state_record(segment, context)
    assert len(calls) == 2


def test_missing_shot_retry_has_explicit_coverage_feedback(monkeypatch):
    segment, context, response = inputs()
    calls = []

    def model(system, prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append(payload)
        assert payload['shot_coverage_contract']['ordered_shot_uids'] == ['stable-shot']
        if len(calls) == 1:
            return {**copy.deepcopy(response), 'shots': []}, {}
        details = payload['validation_feedback']['details']
        assert details['expected_shot_uids'] == ['stable-shot']
        assert details['received_shot_uids'] == []
        assert details['missing_shot_uids'] == ['stable-shot']
        return copy.deepcopy(response), {}

    monkeypatch.setattr('services.llm.call_llm', model)
    assert extract_state_record(segment, context)['shots'][0]['shot_uid'] == 'stable-shot'
    assert len(calls) == 2


def test_relative_position_correction_uses_character_mapping_and_preserves_tagged_evidence(monkeypatch):
    segment, context, response = inputs()
    quote = '<Subject 2>走到<Subject 1>的工位侧后方'
    segment['generation_config']['prompt_override'] = (
        '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>、<Subject 2>；'
        '<Subject 1>坐在工位前；' + quote + '；本镜无对白。')
    segment['generation_config']['asset_manifest'] = [
        {'asset_id': 'actor-a', 'type': 'image', 'role': '甲',
         'metadata': {'asset_kind': 'character', 'role_id': 'a'}},
        {'asset_id': 'actor-b', 'type': 'image', 'role': '乙',
         'metadata': {'asset_kind': 'character', 'role_id': 'b'}},
        {'asset_id': 'room', 'type': 'image', 'role': '房间',
         'metadata': {'asset_kind': 'background', 'scene_id': 'room'}},
    ]
    context['tracked_role_ids'].append('b')
    context['input_state']['b'] = {'posture': {'value': '站着'}, 'present': {'value': True}}
    valid = {'shots': [{'shot_uid': 'stable-shot', 'world_events': [
        {'role_id': 'b', 'source_quote': quote,
         'facts': {'position': {'value': 'a的工位侧后方', 'relative_to_role_id': 'a'}}}]}],
        'opening_requirements': [], 'conflicts': [], 'unresolved': []}
    invalid = copy.deepcopy(valid)
    invalid['shots'][0]['world_events'][0]['facts']['position']['value'] = '<Subject 1>的工位侧后方'
    before = copy.deepcopy((segment, context, invalid, valid))
    calls = []

    def model(system, prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append((payload, kwargs['label']))
        assert payload['character_bindings'] == [
            {'subject_tag': '<Subject 1>', 'role_id': 'a', 'name': '甲'},
            {'subject_tag': '<Subject 2>', 'role_id': 'b', 'name': '乙'},
        ]
        assert 'source_quote 是原文证据' in system
        assert '即使标签只是位置描述的一部分' in system
        assert '工位等语义' in system
        if len(calls) == 1:
            return copy.deepcopy(invalid), 0
        details = payload['validation_feedback']['details']
        assert details['location'] == {'scope': 'world_events', 'shot_uid': 'stable-shot',
                                       'shot_index': 1, 'role_id': 'b', 'field': 'position'}
        assert details['invalid_value'] == '<Subject 1>的工位侧后方'
        assert details['allowed_role_ids'] == ['a', 'b']
        assert payload['validation_feedback']['previous_response'] == invalid
        return copy.deepcopy(valid), 0

    monkeypatch.setattr('services.llm.call_llm', model)
    record = extract_state_record(segment, context)
    assert record['end_state']['b']['position']['value'] == 'a的工位侧后方'
    assert record['end_state']['b']['position']['relative_to_role_id'] == 'a'
    assert record['shots'][0]['world_events'][0]['source_quote'] == quote
    assert calls[1][1] == calls[0][1] + '_repair1'
    assert (segment, context, invalid, valid) == before


@pytest.mark.parametrize('scope', ['world_events', 'opening_requirements', 'initial_state'])
def test_tagged_fact_error_identifies_scope_person_and_field(scope):
    segment, context, response = inputs()
    if scope == 'world_events':
        response['shots'][0]['world_events'][0]['facts']['posture']['value'] = '<Subject 1>坐着'
    elif scope == 'opening_requirements':
        response['opening_requirements'][0]['value'] = '<Subject 1>站着'
    else:
        context.update(initializing=True, previous_segment_uid=None)
        response['initial_state'] = {'a': {
            'position': {'value': '<Subject 1>桌前', 'source_quote': '甲站在桌前'},
            'posture': {'value': '站着', 'source_quote': '甲站在桌前'},
            'present': {'value': True, 'source_quote': '甲站在桌前'},
        }}
    before = copy.deepcopy((segment, context, response))
    with pytest.raises(ValueError, match='稳定人物身份') as failure:
        validate_state_result(segment, context, response)
    details = failure.value.details
    assert details['location']['scope'] == scope
    assert details['location']['role_id'] == 'a'
    assert details['location']['shot_index'] == 1
    assert 'relative_to_role_id' in str(failure.value)
    assert (segment, context, response) == before


def test_saved_prompt_without_plan_extracts_state_and_keeps_all_inputs(monkeypatch):
    segment, context, response = inputs()
    existing = {'shots': [{'shot_uid': 'old', 'world_events': []}], 'end_state': {'a': {}}}
    original = copy.deepcopy((segment, context, existing))
    calls = []
    stub(monkeypatch, response, calls)
    record = extract_state_record(segment, context, existing=existing)
    assert (segment, context, existing) == original
    assert record['schema_version'] == 3
    assert record['shots'][0]['shot_uid'] == 'stable-shot'
    assert record['end_state']['a']['posture']['value'] == '坐着'
    assert record['previous_segment_uid'] == 'part3'
    assert record['input_revision'] == 'upstream-version'
    assert record['input_state'] == context['input_state']
    assert record['opening_requirements'] == response['opening_requirements']
    assert record['state_trace_version'] == 1
    assert record['state_trace_source'] == 'llm'
    assert record['text_pending'] is False and record['state_pending'] is False
    assert calls[0]['existing_record'] == existing
    record['input_state']['a']['posture']['value'] = 'changed'
    assert context == original[1]


def test_missing_shot_ids_are_deterministic(monkeypatch):
    segment, context, response = inputs()
    segment.pop('h3_shot_editor')
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt
    uid = parse_prompt(segment['generation_config']['prompt_override'], segment['segment_uid'])['shots'][0]['uid']
    response['shots'][0]['shot_uid'] = uid
    response['opening_requirements'][0]['shot_uid'] = uid
    stub(monkeypatch, response)
    assert extract_state_record(segment, context)['shots'][0]['shot_uid'] == uid
    assert 'h3_shot_editor' not in segment


def test_true_conflict_reports_part_shot_role_field_and_evidence(monkeypatch):
    segment, context, response = inputs()
    context['input_state']['a']['posture']['value'] = '坐着'
    response['conflicts'] = [{k: response['opening_requirements'][0][k]
                              for k in ('shot_uid', 'role_id', 'field', 'source_quote')}]
    response['conflicts'][0]['reason'] = '前驱仍坐着，本镜开场要求站着'
    stub(monkeypatch, response)
    record = extract_state_record(segment, context)
    assert record['record_status'] == 'ready_with_issues'
    assert any('Part 4 Shot 1 人物 a 字段 posture' in d and '甲站在桌前' in d for d in record['diagnostics'])


def test_unrelated_unknown_is_preserved_without_blocking(monkeypatch):
    segment, context, response = inputs()
    context['input_state']['a']['position'] = {'value': None}
    response['unresolved'] = [{'shot_uid': 'stable-shot', 'role_id': 'a', 'field': 'position',
                               'source_quote': '甲站在桌前', 'reason': '该字段不影响本镜开场要求'}]
    stub(monkeypatch, response)
    assert extract_state_record(segment, context)['end_state']['a']['position']['value'] is None


def test_explicit_requirement_with_unknown_upstream_does_not_silently_pass(monkeypatch):
    segment, context, response = inputs()
    context['input_state']['a']['posture'] = {'value': None}
    stub(monkeypatch, response)
    record = extract_state_record(segment, context)
    assert record['record_status'] == 'ready_with_issues'
    assert any('关键开场事实未明确' in d for d in record['diagnostics'])
    assert record['input_state']['a']['posture']['value'] is None


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(prompt='偷偷改稿'),
    lambda r: r['shots'][0].update(visible_roles=['a']),
    lambda r: r['shots'][0]['world_events'][0].update(source_quote='不在原文里'),
    lambda r: r['shots'][0]['world_events'][0].update(role_id='unknown'),
    lambda r: r['shots'][0]['world_events'][0]['facts'].update(gaze={'value': '门外'}),
    lambda r: r['shots'][0]['world_events'][0]['facts'].update(present={'value': 'true'}),
    lambda r: r['shots'][0]['world_events'][0]['facts']['posture'].update(relative_to_role_id='unknown'),
    lambda r: r['shots'][0].update(shot_uid='different'),
    lambda r: r.update(initial_state={}),
    lambda r: r['opening_requirements'][0].update(field=['posture']),
])
def test_unauthorized_or_unevidenced_output_is_rejected(monkeypatch, mutate):
    segment, context, response = inputs()
    mutate(response)
    stub(monkeypatch, response)
    with pytest.raises(ValueError, match='人物状态提取'):
        extract_state_record(segment, context)


def test_evidence_must_belong_to_the_referenced_shot(monkeypatch):
    segment, context, response = inputs()
    segment['generation_config']['prompt_override'] += '\n[Shot 2]（4–8 秒）：甲走到门口；本镜无对白。'
    segment['h3_shot_editor']['shot_uids'].append('second-shot')
    response['shots'].append({'shot_uid': 'second-shot', 'world_events': []})
    response['shots'][0]['world_events'][0]['source_quote'] = '甲走到门口'
    stub(monkeypatch, response)
    with pytest.raises(ValueError, match='所属Shot原文依据'):
        extract_state_record(segment, context)


def test_initial_state_is_evidenced_and_separate_from_action_result(monkeypatch):
    segment, context, response = inputs()
    context.update(initializing=True, previous_segment_uid=None, input_state={'a': {}})
    response['initial_state'] = {'a': {
        'position': {'value': '桌前', 'source_quote': '甲站在桌前'},
        'posture': {'value': '站着', 'source_quote': '甲站在桌前'},
        'present': {'value': True, 'source_quote': '甲站在桌前'},
    }}
    stub(monkeypatch, response)
    record = extract_state_record(segment, context)
    assert record['initial_state']['a']['posture']['value'] == '站着'
    assert record['end_state']['a']['posture']['value'] == '坐着'
    assert context['input_state'] == {'a': {}}


def test_reviewed_result_uses_same_validation_without_fake_provider_call(monkeypatch):
    segment, context, response = inputs()
    original = copy.deepcopy((segment, context, response))
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **k: pytest.fail('reviewed recovery cannot call provider'))
    record = validate_state_result(segment, context, response)
    assert record['state_trace_source'] == 'reviewed_recovery'
    assert record['end_state']['a']['posture']['value'] == '坐着'
    assert (segment, context, response) == original
    response['shots'][0]['world_events'][0]['source_quote'] = '伪造原文'
    with pytest.raises(ValueError, match='所属Shot原文依据'):
        validate_state_result(segment, context, response)
