import copy
import json

import pytest

from generation.pipelines.minimax_h3 import planning_checkpoints, prompt_planner
from services import context, text_jobs


LINE = "请把事情说清楚。现在就说。"
UNIT = {"speaker": "甲", "scope": "onscreen", "line": LINE}


def plan(lines):
    return {"shot_mode": "multi_shot" if len(lines) > 1 else "one_take", "shots": [
        {"start_sec": 8 * i / len(lines), "end_sec": 8 * (i + 1) / len(lines),
         "dialogue": {**UNIT, "line": line}} for i, line in enumerate(lines)
    ]}


def model_responses(monkeypatch, responses):
    calls = []

    def model(system, user, **kwargs):
        calls.append({"system": system, "payload": json.loads(user), **kwargs})
        return copy.deepcopy(responses[len(calls) - 1]), 0

    monkeypatch.setattr(prompt_planner, "_call_model", model)
    return calls


def test_duplicate_dialogue_is_repaired_once_with_located_feedback(monkeypatch):
    calls = model_responses(monkeypatch, [plan([LINE, LINE]), plan(["请把事情说清楚。", "现在就说。"])])
    beat = {"estimated_duration_sec": 8, "dialogue_units": [copy.deepcopy(UNIT)]}
    original = copy.deepcopy(beat)

    result = prompt_planner.plan_h3_prompt(beat, 3)

    assert beat == original
    assert ''.join(shot['dialogue']['line'] for shot in result['shots']) == LINE
    assert [call['label'] for call in calls] == ['h3_prompt_plan_003', 'h3_prompt_plan_003_repair1']
    feedback = calls[1]['payload']['validation_feedback']
    assert feedback['code'] == 'DIALOGUE_MISMATCH'
    assert 'Shot 1、2' in feedback['message']
    assert [entry['shot_index'] for entry in feedback['actual']] == [1, 2]
    assert calls[1]['payload']['invalid_plan'] == plan([LINE, LINE])


def test_repair_failure_stops_and_never_checkpoints_invalid_plan(monkeypatch):
    calls = model_responses(monkeypatch, [plan([LINE, LINE]), plan([LINE, LINE])])
    saved = []
    monkeypatch.setattr(planning_checkpoints, 'save_plan', lambda *args: saved.append(args))

    with pytest.raises(prompt_planner.DialogueValidationError, match='自动修正一次后仍未通过.*Shot 1、2'):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'dialogue_units': [UNIT]}, 2)

    assert len(calls) == 2
    assert saved == []


def test_repair_still_has_to_pass_timing_validation(monkeypatch):
    invalid_timing = plan([LINE])
    invalid_timing['shots'][0]['end_sec'] = 9
    calls = model_responses(monkeypatch, [plan([LINE, LINE]), invalid_timing])
    with pytest.raises(ValueError, match='invalid range'):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'dialogue_units': [UNIT]}, 1)
    assert len(calls) == 2


def test_repetition_explicitly_in_source_is_preserved_without_repair(monkeypatch):
    calls = model_responses(monkeypatch, [plan([LINE, LINE])])
    result = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'dialogue_units': [UNIT, UNIT]}, 1)
    assert len(result['shots']) == 2
    assert len(calls) == 1


def test_auto_duration_is_revalidated_after_dialogue_repair(monkeypatch):
    initial = {**plan([LINE, LINE]), 'duration_sec': 8, 'duration_reason': '原时长'}
    repaired = {**plan([LINE]), 'duration_sec': 9, 'duration_reason': '完整对白所需时长'}
    repaired['shots'][0]['end_sec'] = 9
    calls = model_responses(monkeypatch, [initial, repaired])
    result = prompt_planner.plan_h3_prompt(
        {'estimated_duration_sec': 8, 'dialogue_units': [UNIT]}, 1, auto_duration=True,
    )
    assert len(calls) == 2
    assert result['duration_sec'] == result['shots'][-1]['end_sec'] == 9


def test_transport_failure_does_not_trigger_content_repair(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(kwargs['label'])
        raise text_jobs.NeedsAttentionError('原网页请求状态不明')

    monkeypatch.setattr(prompt_planner, '_call_model', unavailable)
    with pytest.raises(text_jobs.NeedsAttentionError):
        prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8, 'dialogue_units': [UNIT]}, 1)
    assert calls == ['h3_prompt_plan_001']


def test_checkpoint_reuses_validated_plan_and_invalidates_changed_input(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    (work / '.draft').touch()
    operation = {'work': str(work)}
    monkeypatch.setattr(text_jobs, 'current', lambda: operation)
    calls = model_responses(monkeypatch, [plan([LINE]), plan([LINE])])
    beat = {'estimated_duration_sec': 8, 'dialogue_units': [UNIT], 'plot': '等待回答'}
    with context.pin_runtime((work, 'test', '')):
        first = prompt_planner.plan_h3_prompt(beat, 1)
        assert prompt_planner.plan_h3_prompt(beat, 1) == first
        prompt_planner.plan_h3_prompt({**beat, 'plot': '转身等待回答'}, 1)
    assert len(calls) == 2
    assert not (work / 'video_jobs.json').exists()
    # The same operation context cannot write into the official project.
    project = tmp_path / 'project'
    with context.pin_runtime((project, 'test', '')):
        assert planning_checkpoints.load_plan(first['planning_fingerprint']) is None
        planning_checkpoints.save_plan(first['planning_fingerprint'], first)
    assert not (project / 'temp' / 'h3_planning_checkpoint.json').exists()


def test_novel_stage_reuses_completed_beats_and_keeps_cache_immutable(tmp_path, monkeypatch):
    from pipeline import novel_segments
    from scripts.story_script import generate_beats

    work = tmp_path / 'work'
    work.mkdir()
    (work / '.draft').touch()
    monkeypatch.setattr(text_jobs, 'current', lambda: {'work': str(work)})
    monkeypatch.setattr(novel_segments, 'load_project_bible', lambda: {'title': '项目设定'})
    monkeypatch.setattr(novel_segments, 'match_beats_assets', lambda data: data)
    monkeypatch.setattr(novel_segments, 'ensure_event_fields_on_beats', lambda data: data)
    calls = []

    def generate(payload, **kwargs):
        calls.append(payload)
        return {'beats': [{'id': 'beat-1', 'title': payload['novel_text']}]}

    monkeypatch.setattr(generate_beats, 'generate_novel_beats', generate)
    with context.pin_runtime((work, 'test', '')):
        first = novel_segments.generate_novel_beats('原稿', 60, 6, 'minimax_h3_local_ref2va')
        first['beats'][0]['title'] = '外部修改'
        cached = novel_segments.generate_novel_beats('原稿', 60, 6, 'minimax_h3_local_ref2va')
        changed = novel_segments.generate_novel_beats('新稿', 60, 6, 'minimax_h3_local_ref2va')
    assert len(calls) == 2
    assert cached['beats'][0]['title'] == '原稿'
    assert changed['beats'][0]['title'] == '新稿'


@pytest.mark.parametrize('change', ['none', 'args', 'source', 'routes'])
def test_resubmission_inherits_only_identical_failed_input_drafts(tmp_path, monkeypatch, change):
    from services import project_assets, project_bible, ui_run_config, web_llm_bridge

    source = tmp_path / 'project'
    source.mkdir()
    (source / 'beats.json').write_text('{"beats": []}', encoding='utf-8')
    routes = {'default_providers': ['deepseek'], 'overrides': {}}
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr(project_assets, 'sync_generation_assets', lambda: None)
    monkeypatch.setattr(project_bible, 'project_bible_path', lambda: tmp_path / 'bible.json')
    monkeypatch.setattr(ui_run_config, 'ui_run_config_path', lambda: tmp_path / 'run.json')
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    monkeypatch.setattr(web_llm_bridge, 'get_settings', lambda: routes)
    args = ['original novel', '60s', 60, 6]

    with context.pin_runtime((source, 'test', '')):
        first = text_jobs.submit('prompts', args, start=False)
    token = text_jobs.ACTIVE.set(first)
    try:
        with context.pin_runtime((first['work'], 'test', '')):
            planning_checkpoints.save_plan('input-hash', {'planning_fingerprint': 'input-hash', **plan([LINE])})
            planning_checkpoints.save_novel_beats('novel-hash', {'beats': [{'id': 'beat-1'}]})
    finally:
        text_jobs.ACTIVE.reset(token)
    text_jobs.update(first['id'], 'error', message='later segment failed')

    if change == 'args':
        args = ['changed novel', '60s', 60, 6]
    elif change == 'source':
        (source / 'beats.json').write_text('{"beats": [{"id": "edited"}]}', encoding='utf-8')
    elif change == 'routes':
        routes['default_providers'] = ['chatgpt']
    with context.pin_runtime((source, 'test', '')):
        second = text_jobs.submit('prompts', args, start=False)
    token = text_jobs.ACTIVE.set(second)
    try:
        with context.pin_runtime((second['work'], 'test', '')):
            cached = planning_checkpoints.load_plan('input-hash')
            beats = planning_checkpoints.load_novel_beats('novel-hash')
    finally:
        text_jobs.ACTIVE.reset(token)
    assert bool(cached) == (change == 'none')
    assert bool(beats) == (change == 'none')
    assert second['planning_resumed_from'] == (first['id'] if change == 'none' else None)
    assert second['children'] == [] and second['web_calls'] == {}
    assert not (source / 'video_jobs.json').exists()
