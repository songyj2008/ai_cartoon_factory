"""Behavioral regression checks for the shared H3 planning contract."""
import copy
import pytest
from generation.pipelines.minimax_h3 import prompt_planner as planner
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator


def shot(start, end, **extra):
    return dict(start_sec=start, end_sec=end, scene_id='', visible_roles=[], subject='', action='等待', **extra)


def test_cross_shot_dialogue_can_change_visibility_without_changing_text(monkeypatch):
    source = {'speaker': 'role_a', 'scope': 'onscreen', 'line': '你听好了，我不会走。'}
    response = {'shot_mode': 'multi_shot', 'shots': [
        shot(0, 2, dialogue=dict(source, line='你听好了，')),
        shot(2, 5, dialogue=dict(source, scope='offscreen', line='我不会走。', continues_previous=True))]}
    monkeypatch.setattr(planner, '_call_model', lambda *a, **k: (copy.deepcopy(response), 0))
    plan = planner.plan_h3_prompt({'plot': '对话', 'dialogue_units': [source]}, 991,
                                  duration_sec=5, force_replan=True)
    assert ''.join(s['dialogue']['line'] for s in plan['shots']) == source['line']
    assert plan['shots'][1]['dialogue']['scope'] == 'offscreen'


def test_initial_authored_shots_do_not_need_another_creative_call(monkeypatch):
    native = {'shot_mode': 'one_take', 'shots': [shot(0, 5)]}
    monkeypatch.setattr(planner, '_call_model', lambda *a, **k: pytest.fail('unexpected second planning'))
    result = planner.plan_h3_prompt({'plot': '等待', 'model_hints': {'minimax_h3_local_ref2va': native}},
                                     992, duration_sec=5)
    assert result['shots'] == native['shots']


@pytest.mark.parametrize('ranges', [[(0, 2), (3, 5)], [(0, 3), (2, 5)], [(0, 4)]])
def test_final_structure_rejects_gaps_overlaps_and_incomplete_coverage(ranges):
    segment = {'shots': [shot(a, b) for a, b in ranges]}
    report = H3Ref2VAValidator().validate_segment_structure(segment, duration_sec=5)
    assert any(i.code == 'shot_coverage_invalid' for i in report.errors)


def test_split_preserves_h3_authored_durations_and_scene():
    from pipeline.beat_splitter.io import replace_beat
    data = {'generation_policy': {'model_id': 'minimax_h3_local_ref2va'},
            'beats': [{'id': 1, 'plot': '事件', 'scene_id': 'scene_a', 'estimated_duration_sec': 12}]}
    result = replace_beat(data, 1, [
        {'plot': '前半', 'scene_id': 'scene_a', 'estimated_duration_sec': 5},
        {'plot': '后半', 'scene_id': 'scene_b', 'estimated_duration_sec': 9}])
    assert [b['estimated_duration_sec'] for b in result['beats']] == [5, 9]
    assert result['beats'][1]['scene_id'] == 'scene_b'
