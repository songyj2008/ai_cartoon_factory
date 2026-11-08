import copy
import json
import pytest

from services import speech_timing as timing, video_experience as learning
from test_h3_experience_matching import assets, prompt, put, library


def test_same_project_and_listener_do_not_admit_unrelated_history(library, tmp_path, monkeypatch):
    monkeypatch.setattr(learning.context, 'get_current_project_name', lambda: '__pytest__')
    current = assets(tmp_path)
    put('listener', current, prompt(speaker=2))
    assert learning.retrieve(current, prompt=prompt(speaker=1)) == []
    assert learning.retrieve(current, dialogue=[], purpose='planning') == []


def test_timing_history_requires_owner_and_excludes_other_task_details(library, tmp_path):
    current = assets(tmp_path)
    put('owner', current, prompt(speaker=1))
    put('listener', current, prompt(speaker=2, silent=True), updated=20)
    result = learning.retrieve(current, prompt=prompt(silent=True), purpose='timing')
    assert [r['sample_id'] for r in result] == ['owner']
    item = result[0]
    assert item['timing_analysis'] and item['limitations']
    assert 'original_prompt_evidence' not in item and 'summary' not in item
    assert 'speech_control_analysis' not in item and 'transition_analysis' not in item
    assert 'source' not in item['source']


def test_statistics_only_send_explicit_owners_and_small_evidence(monkeypatch, tmp_path):
    current = assets(tmp_path)
    context = {'population': {'median_chars_per_sec': 4}, 'examples': ['global history'],
               'statistics_version': 'local', 'excluded_records': {'local': 1},
               'speakers': [{'identity_id': 'person-A', 'canonical_identity_id': 'merged-A',
                             'personal': {'line_count': 20}, 'examples': list(range(8))},
                            {'identity_id': 'person-B', 'examples': ['listener history']}]}
    before = copy.deepcopy(context)
    monkeypatch.setattr(timing, 'planning_context', lambda manifest: context)
    result = timing.task_planning_context(current, prompt=prompt(speaker=1))
    assert [s['identity_id'] for s in result['speakers']] == ['person-A']
    assert result['speakers'][0]['examples'] == [0, 1]
    assert result['population'] == context['population']
    assert 'examples' not in result and 'statistics_version' not in result
    assert context == before
    silent = timing.task_planning_context(current, dialogue=[])
    assert silent['speakers'] == [] and silent['population'] == context['population']
    unknown = timing.task_planning_context(current, dialogue=[{'speaker': 'unregistered', 'line': 'Original'}])
    assert unknown['speakers'] == []


def test_multiple_explicit_owners_are_retained(monkeypatch, tmp_path):
    current = assets(tmp_path)
    monkeypatch.setattr(timing, 'planning_context', lambda manifest: {'speakers': [
        {'identity_id': 'person-A', 'examples': []}, {'identity_id': 'person-B', 'examples': []}]})
    result = timing.task_planning_context(current, dialogue=[
        {'speaker': 'role-A', 'line': 'First'}, {'speaker': 'role-B', 'line': 'Second'}])
    assert [s['identity_id'] for s in result['speakers']] == ['person-A', 'person-B']


def test_confirmed_silence_never_retrieves_old_shot_speakers(monkeypatch, tmp_path):
    from generation.pipelines.minimax_h3 import prompt_planner, planning_checkpoints
    current = assets(tmp_path)
    monkeypatch.setattr(timing, 'planning_context', lambda manifest: {'speakers': [
        {'identity_id': 'person-A', 'examples': ['old speech']}]})
    monkeypatch.setattr(planning_checkpoints, 'load_plan', lambda key: None)
    received = []
    monkeypatch.setattr(learning, 'retrieve', lambda manifest, **kwargs: received.append(kwargs) or [])
    class Captured(Exception):
        pass
    def model(system, user, **kwargs):
        assert json.loads(user)['speech_timing_context']['speakers'] == []
        raise Captured()
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    with pytest.raises(Captured):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'dialogue_authoritative': True,
            'dialogue_units': [], 'model_hints': {learning.MODEL_ID: {'shots': [
                {'dialogue': {'speaker': 'role-A', 'line': 'Old dialogue'}}]}}},
            1, asset_manifest=current, force_replan=True)
    assert received == [{'prompt': '', 'dialogue': [], 'purpose': 'planning', 'shots': None}]
