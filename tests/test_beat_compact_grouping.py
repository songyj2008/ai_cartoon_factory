import copy
import json
import pytest

from scripts.story_script import generate_beats as writer

H3 = 'minimax_h3_local_ref2va'
LINES = [('actor_a', '准备好了吗？'), ('actor_b', '好了。'),
         ('actor_a', '那就开始。'), ('actor_b', '明白。')]


def beat(units):
    return {'title': '准备工作', 'plot': '双方确认准备完毕，开始工作。',
            'source_excerpt': ''.join(speaker + '：' + line for speaker, line in units),
            'estimated_duration_sec': 4,
            'model_hints': {H3: {'shot_mode': 'multi_shot', 'shots': [
                {'start_sec': i * 4 / len(units), 'end_sec': (i + 1) * 4 / len(units),
                 'action': '确认后开始工作。',
                 'dialogue': {'speaker': speaker, 'scope': 'onscreen', 'line': line}}
                for i, (speaker, line) in enumerate(units)]}}}


def raw(groups):
    return {'title': '准备工作', 'locked_outcome': '开始工作。', 'beats': [beat(g) for g in groups]}


def generate(monkeypatch, tmp_path, responses, *, count=2, seconds=8):
    calls = []
    def model(config, messages, temp_dir, **kwargs):
        calls.append({'system': messages[0]['content'], 'input': json.loads(messages[-1]['content']),
                      'name': kwargs.get('name')})
        assert len(calls) <= len(responses), 'unbounded grouping retries'
        return copy.deepcopy(responses[len(calls) - 1])
    monkeypatch.setattr(writer, '_call_deepseek_messages', model)
    result = writer.generate_novel_beats({'novel_text': '完整原文', 'target_duration_sec': seconds,
        'target_beat_count': count}, config={}, model_id=H3, temp_dir=tmp_path)
    return result, calls


def test_short_question_answers_are_grouped_by_the_model_once(monkeypatch, tmp_path):
    original = raw([[unit] for unit in LINES])
    revised = raw([LINES[:2], LINES[2:]])
    result, calls = generate(monkeypatch, tmp_path, [original, revised])
    assert len(calls) == 2 and calls[1]['name'] == 'beats_novel_grouping_review'
    assert '换机位和换说话人' in calls[1]['system']
    assert '短问答' in calls[0]['system'] and '不为每句对白前后各加' in calls[0]['system']
    assert len(result['beats']) == 2 and result['total_duration_sec'] == 8
    assert result['grouping_review']['status'] == 'regrouped'
    assert result['beat_grouping_revision'] == writer.BEAT_GROUPING_REVISION
    assert original == raw([[unit] for unit in LINES])


@pytest.mark.parametrize('damage', ['delete_line', 'change_speaker', 'reorder', 'drop_excerpt', 'too_long'])
def test_invalid_compaction_keeps_the_complete_original(monkeypatch, tmp_path, damage):
    original = raw([[unit] for unit in LINES])
    revised = raw([LINES[:2], LINES[2:]])
    shots = revised['beats'][0]['model_hints'][H3]['shots']
    if damage == 'delete_line':
        shots[1]['dialogue']['line'] = '好。'
    elif damage == 'change_speaker':
        shots[1]['dialogue']['speaker'] = 'actor_a'
    elif damage == 'reorder':
        shots[0]['dialogue'], shots[1]['dialogue'] = shots[1]['dialogue'], shots[0]['dialogue']
    elif damage == 'drop_excerpt':
        revised['beats'][0]['source_excerpt'] = '删掉原文'
    else:
        revised['beats'][0]['estimated_duration_sec'] = 20
    result, calls = generate(monkeypatch, tmp_path, [original, revised])
    assert len(calls) == 2
    assert len(result['beats']) == 4
    assert result['grouping_review']['status'] == 'retained'
    assert result['grouping_review']['reason']


def test_no_extra_call_for_moderate_reference_difference(monkeypatch, tmp_path):
    result, calls = generate(monkeypatch, tmp_path, [raw([LINES[:2], LINES[2:]])], count=3)
    assert len(calls) == 1 and len(result['beats']) == 2
    assert 'grouping_review' not in result


def test_no_padding_or_forced_merge_when_model_cannot_reduce_the_plan(monkeypatch, tmp_path):
    original = raw([[unit] for unit in LINES])
    revised = copy.deepcopy(original)
    revised['grouping_reason'] = '独立事件边界和原文自然发声时间需要保留。'
    result, calls = generate(monkeypatch, tmp_path, [original, revised])
    assert len(calls) == 2
    assert len(result['beats']) == 4
    assert result['grouping_review']['reason'] == revised['grouping_reason']
