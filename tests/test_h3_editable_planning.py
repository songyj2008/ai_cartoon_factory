import copy
import json

import pytest

from generation.pipelines.minimax_h3 import prompt_planner
from generation.pipelines.minimax_h3.planning_drafts import inspect_plan, draft_segment, refresh_saved_prompt
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator


def response(duration=18, refs=None):
    return {'duration_sec': duration, 'duration_reason': '按原文自然表达。', 'shot_mode': 'one_take',
            'shots': [{'start_sec': 0, 'end_sec': duration, 'action': '人物解释。',
                       'dialogue_refs': refs if refs is not None else [1]}]}


@pytest.fixture
def dialogue():
    return [{'speaker': '甲', 'scope': 'telephone', 'line': '请完整保留这句。'},
            {'speaker': '乙', 'scope': 'onscreen', 'line': '最后一句也需要保留。'}]


def test_long_natural_duration_is_preserved_but_strict_submit_still_rejects(monkeypatch, dialogue):
    calls = []
    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        return response(refs=[1, 2]), 0
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    plan = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 10}, 1, source_dialogue=dialogue,
                                        draft_mode=True)
    assert len(calls) == 1 and calls[0]['duration_policy']['mode'] == 'draft_auto'
    assert plan['duration_sec'] == 18
    assert plan['shots'][0]['dialogues'] == dialogue
    assert plan['planning_state'] == 'warning'
    assert any(i['code'] == 'duration_out_of_range' for i in plan['planning_issues'])
    with pytest.raises(ValueError):
        H3Ref2VAValidator().validate(H3GenerationConfig.from_mapping({'duration_sec': 18})).raise_for_errors()


def test_missing_and_repeated_dialogue_are_visible_without_automatic_rewrite(dialogue):
    source = response(10, [1, 1])
    plan = inspect_plan(source, 10, dialogue)
    assert {i['code'] for i in plan['planning_issues']} >= {'missing_dialogue', 'duplicate_dialogue'}
    assert plan['shots'][0]['dialogues'] == [dialogue[0], dialogue[0]]
    assert plan['planning_raw_response'] == source
    assert plan['shots'][0]['source_dialogue_refs'] == [1, 1]


@pytest.mark.parametrize('raw', [None, [], 'unfinished JSON', {'_parse_error': 'bad JSON', '_raw_text': '{"shots":['}])
def test_unusable_json_has_preserved_raw_response(raw, dialogue):
    plan = inspect_plan(raw, 10, dialogue)
    assert plan['planning_state'] == 'invalid'
    assert plan['planning_raw_response'] == raw


def test_failed_retry_preserves_previous_prompt_and_keeps_bad_candidate(dialogue):
    previous = {'segment_index': 1, 'job': {'final_prompt': '原先可编辑的提示词'},
                'generation_config': {'duration_sec': 8}, 'video_path': 'old.mp4'}
    old = copy.deepcopy(previous)
    plan = inspect_plan({'_raw_text': 'broken', '_parse_error': 'bad'}, 8, dialogue)
    result = draft_segment({'title': 'test'}, 1, previous, {'duration_sec': 8}, plan)
    assert result['job'] == previous['job'] and result['video_path'] == 'old.mp4'
    assert result['planning_candidate']['plan']['planning_raw_response']['_raw_text'] == 'broken'
    assert previous == old
    assert not any(i['blocking'] for i in result['planning_issues'])


def test_submit_rejects_overlong_draft_before_contacting_provider(tmp_path):
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    segment = {'planning_state': 'warning', 'generation_config': {'duration_sec': 18},
               'job': {'duration_sec': 18, 'final_prompt': '[Shot 1] (0-18 秒)：人物交流。'}}
    with pytest.raises(ValueError, match='between 4 and 15'):
        MiniMaxH3Ref2VAPipeline()._submit_segment(object(), segment, wait=False)


def test_explicit_edit_rechecks_duration_and_timeline_without_discarding_prompt():
    segment = {'generation_config': {'duration_sec': 18}}
    refresh_saved_prompt(segment, '[Shot 1] (0-18 秒)：人物回应。')
    assert segment['planning_state'] == 'warning'
    segment['generation_config']['duration_sec'] = 12
    refresh_saved_prompt(segment, '[Shot 1] (0-12 秒)：人物回应。')
    assert segment['planning_state'] == 'ready'


def test_api_invalid_json_is_preserved_without_remote_repair(monkeypatch):
    from services import llm, web_llm_bridge
    monkeypatch.setattr(web_llm_bridge, 'routes_for', lambda *_: ['api'])
    monkeypatch.setattr(llm, '_completion', lambda *_: ({}, '{broken', {'finish_reason': 'stop'}))
    monkeypatch.setattr(llm, '_save_bad_response', lambda *_: ('raw.txt', 'response.json'))
    monkeypatch.setattr(llm, 'repair_json', lambda *_a, **_k: pytest.fail('draft diagnostics must not trigger another request'))
    result, _ = llm.call_llm('system', 'user', tolerate_invalid_json=True)
    assert result['_raw_text'] == '{broken' and result['_parse_error']


def test_segment_cards_render_issues_raw_reply_and_retry_for_missing_beats(monkeypatch, tmp_path):
    from services import context
    from ui import group_panel
    monkeypatch.setattr(group_panel, '_display_context', lambda: ({}, {}))
    monkeypatch.setattr(group_panel, 'selected_project_materials', lambda: {})
    monkeypatch.setattr(group_panel, '_current_beats', lambda: [
        {'title': 'first', 'estimated_duration_sec': 10}, {'title': 'second', 'estimated_duration_sec': 8}])
    monkeypatch.setattr(group_panel, '_h3_config_html', lambda *_: '<textarea>可编辑分镜</textarea>')
    with context.pin_runtime((tmp_path, 'test', '')):
        html = group_panel.render_groups_panel({'novel_batch_id': 'batch', 'default_model_id': 'minimax_h3_local_ref2va',
            'segments': [{'segment_index': 1, 'model_id': 'minimax_h3_local_ref2va', 'novel_batch_id': 'batch',
                          'job': {'duration_sec': 18}, 'planning_state': 'warning',
                          'planning_issues': [{'message': '缺少第六句对白'}],
                          'planning_raw_response': '<script>untrusted</script>'}]})
    assert '缺少第六句对白' in html and '分镜有提示，待检查' in html
    assert '预计总时长：26 秒' in html
    assert html.count('重新生成 Segment') == 2
    assert '<script>untrusted</script>' not in html and '&lt;script&gt;' in html


def test_draft_planning_prompt_still_requires_integer_4_to_15_seconds(monkeypatch):
    captured = []
    def model(system, user, **kwargs):
        captured.append(system)
        return {'duration_sec': 5, 'duration_reason': '自然完成', 'shot_mode': 'one_take',
                'shots': [{'start_sec': 0, 'end_sec': 5, 'action': '本镜无对白'}]}, 0
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    prompt_planner.plan_h3_prompt({'plot': '等待', 'estimated_duration_sec': 8}, 998,
                                 draft_mode=True, force_replan=True)
    assert '每段 H3 总时长必须是 4–15 秒的整数' in captured[0]
    assert '可超过15秒' not in captured[0]
    assert '不返回 needs_replan' not in captured[0]
