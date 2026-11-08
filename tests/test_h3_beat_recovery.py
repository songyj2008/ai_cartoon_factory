import copy
import json
import pytest
from scripts.story_script import generate_beats as beats
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig

MODEL = 'minimax_h3_local_ref2va'


def sample(duration=16, scope='shot'):
    return {'title': '测试', 'locked_outcome': '结局不变', 'beats': [
        {'title': '完整反击', 'plot': '原事件', 'estimated_duration_sec': duration,
         'model_hints': {MODEL: {'shot_mode': 'one_take', 'shots': [
             {'start_sec': 0, 'end_sec': duration, 'subject': '甲', 'action': '甲质问',
              'visible_roles': ['甲'], 'scene_id': '',
              'dialogue': {'speaker': '甲', 'scope': scope, 'line': '请完整保留这句。'}}]}}}]}


def test_strict_feedback_names_beat_actual_duration_and_scope():
    with pytest.raises(beats.H3BeatValidationError) as error:
        beats.normalize_beats(sample(), model_id=MODEL)
    assert {i['code'] for i in error.value.issues} == {'duration_out_of_range', 'invalid_dialogue_record'}
    assert all(i['beat_index'] == 1 for i in error.value.issues)
    assert '16' in str(error.value) and 'shot' in str(error.value)


def test_second_invalid_reply_is_saved_as_editable_candidate(monkeypatch, tmp_path):
    raw = sample()
    calls = []
    def call(config, messages, temp_dir, name):
        calls.append(messages)
        return copy.deepcopy(raw)
    monkeypatch.setattr(beats, '_call_deepseek_messages', call)
    result = beats.generate_novel_beats({'generation_policy': {'model_id': MODEL},
                                       'target_duration_sec': 90}, model_id=MODEL, temp_dir=tmp_path)
    assert len(calls) == 2
    feedback = json.loads(calls[1][1]['content'])
    assert feedback['validation_issues'][0]['actual'] == 16
    assert result['planning_state'] == 'warning'
    assert result['beats'][0]['estimated_duration_sec'] == 16
    assert result['planning_raw_response'] == raw
    dialogue = result['beats'][0]['model_hints'][MODEL]['shots'][0]['dialogue']
    assert dialogue['scope'] == 'shot' and dialogue['line'] == '请完整保留这句。'
    with pytest.raises(ValueError):
        H3Ref2VAValidator().validate(H3GenerationConfig.from_mapping({'duration_sec': 16})).raise_for_errors()


def test_successful_repair_returns_ready_data(monkeypatch, tmp_path):
    replies = iter([sample(), sample(14, 'onscreen')])
    monkeypatch.setattr(beats, '_call_deepseek_messages', lambda *a, **k: next(replies))
    result = beats.generate_novel_beats({'generation_policy': {'model_id': MODEL}},
                                      model_id=MODEL, temp_dir=tmp_path)
    assert not result.get('planning_issues')
    assert result['beats'][0]['estimated_duration_sec'] == 14


def test_final_structure_rejects_unknown_scope():
    segment = sample(14)['beats'][0]
    report = H3Ref2VAValidator().validate_segment_structure(segment, duration_sec=14)
    assert any(issue.code == 'invalid_speech_scope' for issue in report.errors)
