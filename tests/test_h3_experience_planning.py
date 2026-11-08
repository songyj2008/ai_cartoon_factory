"""Planning consumes approved examples without changing the current fact source."""
import copy
import json

import pytest

from generation.pipelines.minimax_h3 import prompt_planner, prompt_retimer
from services import speech_timing, video_experience


@pytest.fixture(autouse=True)
def isolated_learning(tmp_path, monkeypatch):
    monkeypatch.setattr(video_experience, 'library_path', lambda: tmp_path / 'missing.sqlite3')
    monkeypatch.setattr(speech_timing, 'planning_context', lambda manifest: {'speakers': []})
    from generation.pipelines.minimax_h3 import planning_checkpoints
    monkeypatch.setattr(planning_checkpoints, 'load_plan', lambda fingerprint: None)
    monkeypatch.setattr(planning_checkpoints, 'save_plan', lambda *args: None)


def _response(dialogue=None):
    shot = {'start_sec': 0, 'end_sec': 8, 'action': '保持原站位。'}
    if dialogue:
        shot['dialogues'] = copy.deepcopy(dialogue)
    else:
        shot['audio'] = '\u672c\u955c\u65e0\u5bf9\u767d\u3002'
    return {'core_idea': '当前场景', 'shot_mode': 'one_take', 'shots': [shot]}


def _model(monkeypatch, dialogue=None):
    calls = []
    def model(system, user, **kwargs):
        calls.append((system, json.loads(user)))
        return _response(dialogue), 0
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    return calls


def test_planner_retrieves_against_current_speaker_and_native_shots(monkeypatch):
    dialogue = [{'speaker': 'current', 'scope': 'onscreen', 'line': '只保留当前台词。'}]
    shots = [{'start_sec': 0, 'end_sec': 1, 'action': '沉默注视。'},
             {'start_sec': 1, 'end_sec': 8, 'dialogue': dialogue[0]}]
    beat = {'estimated_duration_sec': 8, 'dialogue_units': dialogue,
            'model_hints': {'minimax_h3_local_ref2va': {'shots': shots}}}
    assets = [{'id': 'voice', 'type': 'audio', 'metadata': {'identity_id': 'current'}}]
    original = copy.deepcopy((beat, assets))
    received = []
    example = {'sample_id': 'approved', 'summary': '无对白动作接发言的已认可案例'}
    def retrieve(manifest, **kwargs):
        received.append((manifest, kwargs))
        return [example]
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    calls = _model(monkeypatch, dialogue)
    prompt_planner.plan_h3_prompt(beat, 2, asset_manifest=assets, force_replan=True)
    assert received == [(assets, {'prompt': '', 'dialogue': dialogue, 'shots': shots, 'purpose': 'planning'})]
    assert calls[0][1]['approved_prompt_experiences'] == [example]
    assert video_experience.PLANNING_RULES in calls[0][0]
    assert (beat, assets) == original


@pytest.mark.parametrize('with_contract', [False, True])
def test_editor_retrieval_never_uses_old_beat_dialogue_or_shots(monkeypatch, with_contract):
    current = [{'speaker': '<Subject 1>', 'scope': 'onscreen', 'line': '新台词。'}]
    editor = ('【主体定义】\n<Subject 1>：当前人物；<Picture 1>。\n'
              '【详细镜头描述】\n[Shot 1]（0–8 秒）：'
              '<Subject 1> (S1)说：<d>[Chinese]新台词。</d>')
    beat = {'estimated_duration_sec': 8, 'dialogue_units': [{'speaker': '旧人物', 'line': '旧台词。'}],
            'model_hints': {'minimax_h3_local_ref2va': {'shots': [{'action': '过期镜头'}]}}}
    received = []
    def retrieve(manifest, **kwargs):
        received.append(kwargs)
        return []
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    calls = _model(monkeypatch, current)
    kwargs = {'dialogue_contract': current} if with_contract else {}
    prompt_planner.plan_h3_prompt(beat, 2, editor_prompt=editor, **kwargs)
    assert received == [{'prompt': editor, 'dialogue': current if with_contract else [], 'shots': None, 'purpose': 'planning'}]
    assert '旧台词' not in json.dumps(calls[0][1], ensure_ascii=False)
    assert '过期镜头' not in json.dumps(calls[0][1], ensure_ascii=False)


def test_changed_approved_experience_invalidates_planning_cache(monkeypatch):
    examples = [{'sample_id': 'one', 'summary': '原经验'}]
    monkeypatch.setattr(video_experience, 'retrieve', lambda *args, **kwargs: copy.deepcopy(examples))
    calls = _model(monkeypatch)
    beat = {'estimated_duration_sec': 8}
    first = prompt_planner.plan_h3_prompt(beat, 2)
    same = prompt_planner.plan_h3_prompt(beat, 2, cached_plan=first)
    assert same == first and len(calls) == 1
    examples[0]['summary'] = '更新后的适用范围'
    changed = prompt_planner.plan_h3_prompt(beat, 2, cached_plan=first)
    assert len(calls) == 2
    assert changed['planning_fingerprint'] != first['planning_fingerprint']


@pytest.mark.parametrize('unavailable', [False, True])
def test_no_examples_does_not_add_experience_guidance(monkeypatch, unavailable):
    def retrieve(*args, **kwargs):
        if unavailable:
            raise OSError('test unavailable')
        return []
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    calls = _model(monkeypatch)
    prompt_planner.plan_h3_prompt({'estimated_duration_sec': 8}, 2)
    system, payload = calls[0]
    assert 'approved_prompt_experiences' not in payload
    assert video_experience.PLANNING_RULES not in system


def test_retimer_uses_current_prompt_and_keeps_all_non_time_content(monkeypatch):
    editor = ('【主体定义】\n<Subject 1>：当前人物；<Picture 1>。\n'
              '【任务概述】\n4 秒\n【详细镜头描述】\n'
              '[Shot 1]（0–1 秒）：静默对视。声音：无对白，仅有室内底噪。\n'
              '[Shot 2] At 00:01.000（1–4 秒）：保持站位。'
              '<Subject 1> (S1)说：<d>[Chinese]材料有三份，原文不能改。</d>\n'
              '【人物音色绑定】\n<Audio 1> → <Subject 1>：仅参考音色。')
    received = []
    example = {'sample_id': 'approved', 'summary': '历史人物拿手机后说另一句台词'}
    def retrieve(manifest, **kwargs):
        received.append(kwargs)
        return [example]
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    captured = []
    def model(system, user, **kwargs):
        payload = json.loads(user)
        captured.append((system, payload))
        return {'duration_sec': 5, 'duration_reason': '按当前对白预算调整时间',
                'timings': {slot['id']: {0: 0, 1: 1.5, 4: 5}[slot['seconds']]
                            for slot in payload['time_slots']}}, 0
    monkeypatch.setattr(prompt_retimer, '_call_model', model)
    result = prompt_retimer.retime_h3_prompt(editor, 2, reference_duration_sec=4)
    assert received == [{'prompt': editor, 'purpose': 'timing'}]
    assert captured[0][1]['approved_prompt_experiences'] == [example]
    assert video_experience.PLANNING_RULES in captured[0][0]
    assert '本次经验仅辅助原稿的时间分配' in captured[0][0]
    # Strip only recognized mutable time slots; every other character must survive.
    def immutable(text):
        for slot in reversed(prompt_retimer._time_slots(text)):
            text = text[:slot['start']] + 'TIME' + text[slot['end']:]
        return text
    assert immutable(result['prompt']) == immutable(editor)
    assert result['duration_sec'] == 5
