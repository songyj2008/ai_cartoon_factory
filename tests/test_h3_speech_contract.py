import copy

import pytest

from generation.pipelines.minimax_h3.speech_contract import render_delivery, validate_delivery
from generation.pipelines.minimax_h3.shot_editor import inspect_prompt, dialogue_units, render_prompt


def utterance(source='diegetic'):
    return {'speaker': 'B', 'scope': 'offscreen', 'source_type': source,
            'line': '请先坐下，听我把事情说完。',
            'delivery_spans': [
                {'range': [0, 5], 'visibility': 'offscreen'},
                {'range': [5, 13], 'visibility': 'onscreen', 'mouth_mode': 'sync_speech',
                 'transition': '镜头连续横移，将B纳入画面'}]}


@pytest.mark.parametrize('source', ['diegetic', 'telephone', 'narration'])
def test_delivery_render_parse_edit_roundtrip(source):
    unit = utterance(source)
    text = '【详细镜头描述】\n[Shot 1]（0.00–6.00 秒）：' + render_delivery(unit, 'B')
    inspected = inspect_prompt(text, 'segment')
    assert inspected['verified'], inspected['issues']
    doc = inspected['document']
    shot = doc['shots'][0]
    assert len(shot['dialogues']) == 1
    restored = dialogue_units(shot)[0]
    assert restored['line'] == unit['line']
    assert restored['delivery_spans'] == unit['delivery_spans']
    assert restored['source_type'] == source
    assert render_prompt(doc).count('<d>') == 2
    shot['dialogues'][0]['line'] = '请先坐下，听我把问题说完。'
    reread = inspect_prompt(render_prompt(doc), 'segment')
    assert reread['verified'], reread['issues']
    assert dialogue_units(reread['document']['shots'][0])[0]['line'] == shot['dialogues'][0]['line']
    shot['dialogues'][0]['line'] = '短句。'
    with pytest.raises(ValueError, match='阶段'):
        render_prompt(doc)


@pytest.mark.parametrize('bounds', [[0, 4], [6, 13], [5, 14], [True, 13]])
def test_invalid_phase_ranges_are_rejected(bounds):
    unit = utterance()
    unit['delivery_spans'][1]['range'] = bounds
    with pytest.raises(ValueError):
        validate_delivery(unit)


def test_repeated_independent_lines_are_not_merged():
    text = '【详细镜头描述】\n[Shot 1]（0.00–6.00 秒）：B说：<d>同一句。</d>；B说：<d>同一句。</d>'
    assert len(inspect_prompt(text, 's')['document']['shots'][0]['dialogues']) == 2


def test_voice_mention_is_not_visual_presence():
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    entry = {'asset_kind': 'character', 'subject_tag': '<Subject 2>', 'aliases': ['B'], 'metadata': {}}
    assert not H3PromptCompiler._subject_applies_to_shot(entry, {'subject': 'A', 'dialogue': {'speaker': 'B'}}, 1, 0)


def test_phase_reference_preserves_catalog_words():
    from generation.pipelines.minimax_h3.prompt_planner import _resolve_dialogue_refs
    unit = utterance()
    plan = {'shots': [{'dialogue_refs': [{'number': 1, 'delivery_spans': unit['delivery_spans']}]}]}
    expected = [unit]
    before = copy.deepcopy(expected)
    _resolve_dialogue_refs(plan, expected)
    assert expected == before
    assert plan['shots'][0]['dialogues'][0]['delivery_spans'] == unit['delivery_spans']


def test_saved_phase_and_ids_survive_sync_and_move():
    from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
    from generation.pipelines.minimax_h3.shot_editor import restore_dialogue_ids
    text = '【详细镜头描述】\n[Shot 1]（0.00–6.00 秒）：画面人物：无人物；' + render_delivery(utterance(), 'B')
    inspected = inspect_prompt(text, 's')
    shot = inspected['document']['shots'][0]
    restore_dialogue_ids(shot, ['stable_parent'])
    assert all(s['dialogue']['uid'] == 'stable_parent' for s in shot['dialogue_spans'])
    beat, segment, report = sync_segment_content({}, {'segment_uid': 's'}, prompt=text,
                                                 generation_config={}, inspection=inspected)
    assert report['verified']
    assert beat['visible_roles'] == []
    assert beat['dialogue_units'][0]['delivery_spans'] == utterance()['delivery_spans']
    doc = inspected['document']
    doc['shots'][0]['duration_cs'] = 800
    moved = inspect_prompt(render_prompt(doc), 'new_segment')
    assert moved['verified']
    assert dialogue_units(moved['document']['shots'][0])[0]['delivery_spans'] == utterance()['delivery_spans']


def test_explicit_source_change_and_delete_parent():
    from generation.pipelines.minimax_h3.shot_editor import silent_body
    unit = utterance('telephone')
    unit['delivery_spans'][1]['source_type'] = 'diegetic'
    text = '【详细镜头描述】\n[Shot 1]（0.00–6.00 秒）：' + render_delivery(unit, 'B')
    shot = inspect_prompt(text, 's')['document']['shots'][0]
    assert dialogue_units(shot)[0]['delivery_spans'] == unit['delivery_spans']
    assert '<d>' not in silent_body(shot)
    assert '发声阶段（' not in silent_body(shot)
