import copy

import pytest

from pipeline.planning_budget import budget_report


def plan(count=10, seconds=12):
    return {'target_beat_count': 10, 'target_duration_sec': 120,
            'beats': [{'plot': f'事件{i}', 'estimated_duration_sec': seconds} for i in range(count)]}


@pytest.mark.parametrize('count,seconds', [(10, 12), (8, 12), (12, 12)])
def test_budget_allows_small_changes(count, seconds):
    data = plan(count, seconds)
    before = copy.deepcopy(data)
    assert not budget_report(data)['outside_budget']
    assert data == before


@pytest.mark.parametrize('count,seconds', [(22, 5), (7, 15), (10, 15), (10, 9)])
def test_budget_reports_large_deviation_without_rewriting(count, seconds):
    data = plan(count, seconds)
    data['total_duration_sec'] = 120  # Never trust an inaccurate summary.
    before = copy.deepcopy(data)
    assert budget_report(data)['outside_budget']
    assert data == before
    assert budget_report(data)['estimated_duration_sec'] == count * seconds


def test_small_count_has_one_segment_margin_and_unset_count_remains_optional():
    data = plan(2, 12)
    data.update(target_beat_count=3, target_duration_sec=24)
    assert not budget_report(data)['outside_budget']
    data.pop('target_beat_count')
    data['target_duration_sec'] = 120
    budget_report(data)
