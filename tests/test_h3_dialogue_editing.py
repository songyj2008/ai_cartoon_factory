import copy
import json

import pytest

from generation.pipelines.minimax_h3 import prompt_planner
from generation.pipelines.minimax_h3 import shot_edit_service as service
from generation.pipelines.minimax_h3.shot_editor import parse_prompt, render_prompt, dialogue_units
from test_h3_shot_edit_service import make_runtime, request
from test_h3_shot_editor import PROMPT


def two_speakers(tmp_path):
    payload = make_runtime(tmp_path)
    path = tmp_path / 'video_jobs.json'
    jobs = json.loads(path.read_text(encoding='utf-8'))
    for segment in jobs['segments']:
        segment['generation_config']['asset_manifest'].append({
            'asset_id': 'person_b', 'type': 'image', 'path': 'b.png', 'role': '另一角色',
            'metadata': {'identity_id': 'test_person_b', 'role_id': 'character_b'}})
    path.write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
    return service.preview(payload, root=tmp_path)


def test_edit_owner_and_line_save_reopen_and_beat_roundtrip(tmp_path, monkeypatch):
    state = two_speakers(tmp_path)
    shot = state['documents'][0]['shots'][0]
    original_uid = shot['dialogues'][0]['uid']
    shot['dialogues'][0].update(speaker_key='image:test_person_b', line='审批我协调。', scope='telephone')
    preview = service.preview(request(state), root=tmp_path)
    assert '对白已修改' in ' '.join(preview['documents'][0]['warnings'])
    out = service.commit(request(preview), root=tmp_path, check_files=False)
    beat = json.loads(out['beats_text'])['beats'][0]
    assert beat['dialogue_authoritative']
    assert beat['dialogue_units'][0]['speaker'] == 'character_b'
    assert beat['dialogue_units'][0]['scope'] == 'telephone'
    assert beat['dialogue_units'][0]['line'] == '审批我协调。'
    assert '金额从3亿做到30亿。' not in beat['plot']
    assert beat['model_hints'][service.MODEL]['shots'][0]['dialogues'][0]['speaker'] == 'character_b'
    assert '<d>' not in beat['model_hints'][service.MODEL]['shots'][0]['description']
    reopened = service.preview({'segment_index': 1}, root=tmp_path)['documents'][0]['shots'][0]
    assert reopened['dialogues'][0]['uid'] == original_uid
    assert reopened['dialogues'][0]['line'] == '审批我协调。'
    # The ordinary Beat editor saves before regeneration. It must preserve
    # identity, scope and Shot ownership instead of guessing from display names.
    from pipeline.events import ensure_event_fields_on_beats
    normalized = ensure_event_fields_on_beats({'beats': [beat]})['beats'][0]
    assert normalized['dialogue_units'] == beat['dialogue_units']
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    assert jobs['segments'][0]['renders'][0]['prompt_snapshot'] == PROMPT
    assert jobs['segments'][0]['status'] == 'needs_regenerate'


def test_saved_shot_card_is_exact_saved_text_and_reopens_clean(tmp_path, monkeypatch):
    import html
    import re
    from ui.group_panel import _h3_config_html
    from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
    state = two_speakers(tmp_path)
    state['documents'][0]['shots'][0]['dialogues'][0]['line'] = '已保存的当前台词。'
    service.commit(request(state), root=tmp_path, check_files=False)
    segment = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    saved = segment['generation_config']['prompt_override']
    def must_not_rewrite(*args, **kwargs):
        pytest.fail('Displaying a saved Shot must not rewrite its reference sections')
    with monkeypatch.context() as patch:
        patch.setattr(H3PromptReconciler, 'reconcile', must_not_rewrite)
        markup = _h3_config_html(segment)
    shown = html.unescape(re.search(r'<pre[^>]*class="[^"]*h3-card-prompt-source[^"]*"[^>]*>(.*?)</pre>', markup, re.S)[1])
    assert shown == saved
    reopened = service.preview({'segment_index': 1, 'editor_prompt': shown}, root=tmp_path)
    assert not reopened['documents'][0]['editor_changed']
    # Textarea/HTML newline normalization is not an authored change either.
    reopened = service.preview({'segment_index': 1, 'editor_prompt': shown.replace('\n', '\r\n')}, root=tmp_path)
    assert not reopened['documents'][0]['editor_changed']


def test_multiple_lines_delete_middle_and_append_without_changing_actions():
    text = PROMPT.replace('；动作在', '；<Subject 2>说：<d>[Chinese]新增的第二句。</d>；动作在', 1)
    doc = parse_prompt(text, 'one')
    shot = doc['shots'][0]
    assert len(shot['dialogues']) == 2
    shot['dialogues'].pop(0)
    row = copy.deepcopy(shot['dialogues'][0])
    row.update(uid='new-line', line='再加一句。')
    shot['dialogues'].append(row)
    rendered = render_prompt(doc)
    assert '金额从3亿做到30亿。' not in rendered
    assert rendered.count('新增的第二句。') == 1
    assert rendered.count('再加一句。') == 1
    assert '动作在 00:01.00 完成' in rendered


def test_dialogue_literal_reference_tags_are_not_rebound():
    doc = parse_prompt(PROMPT, 'one')
    doc['shots'][0]['dialogues'][0]['line'] = '原文提到 <Subject 2> 和三十亿。'
    rendered = render_prompt(doc)
    assert '<d>[Chinese]原文提到 <Subject 2> 和三十亿。</d>' in rendered


def test_named_speaker_with_space_is_preserved():
    doc = parse_prompt(PROMPT.replace('<Subject 1> (S1)说', '同事 B (S1)说'), 'one')
    assert dialogue_units(doc['shots'][0])[0]['speaker'] == '同事 B'


@pytest.mark.parametrize('delete_all', [False, True])
def test_delete_dialogue_commits_explicit_contract(tmp_path, delete_all):
    state = two_speakers(tmp_path)
    doc = state['documents'][0]
    for shot in doc['shots'] if delete_all else doc['shots'][:1]:
        shot['dialogues'] = []
    result = service.commit(request(state), root=tmp_path, check_files=False)
    beat = json.loads(result['beats_text'])['beats'][0]
    assert beat['dialogue_authoritative']
    assert len(beat['dialogue_units']) == (0 if delete_all else 2)
    assert '金额从3亿做到30亿。' not in beat['plot']
    assert len(beat['model_hints'][service.MODEL]['shots']) == 3
    if delete_all:
        assert '<d>' not in beat['plot']
        assert all(not s['dialogues'] for s in beat['model_hints'][service.MODEL]['shots'])


@pytest.mark.parametrize('field,value', [('speaker_key', 'unknown'), ('line', ''), ('line', '<d>注入</d>')])
def test_invalid_dialogue_does_not_write(tmp_path, field, value):
    state = two_speakers(tmp_path)
    before = (tmp_path / 'beats.json').read_bytes()
    state['documents'][0]['shots'][0]['dialogues'][0][field] = value
    with pytest.raises(ValueError):
        service.commit(request(state), root=tmp_path, check_files=False)
    assert (tmp_path / 'beats.json').read_bytes() == before


@pytest.mark.parametrize('draft', [False, True])
@pytest.mark.parametrize('silent', [False, True])
def test_authoritative_contract_checks_owner_and_silence_in_both_modes(monkeypatch, draft, silent):
    contract = [] if silent else [{'speaker': '<Subject 2>', 'scope': 'onscreen', 'line': '新台词。'}]
    response = {'duration_sec': 6, 'duration_reason': '自然对白', 'shot_mode': 'one_take',
                'shots': [{'start_sec': 0, 'end_sec': 6, 'action': '保持站位。',
                           'dialogue': {'speaker': '<Subject 1>', 'line': '新台词。'}}]}
    monkeypatch.setattr(prompt_planner, '_call_model', lambda *a, **k: (copy.deepcopy(response), 0))
    args = dict(editor_prompt='【详细镜头描述】\n当前编辑稿。', dialogue_contract=contract, draft_mode=draft)
    if draft:
        plan = prompt_planner.plan_h3_prompt({'estimated_duration_sec': 6}, 1, **args)
        assert any(i['code'] == 'dialogue_mismatch' and i['blocking'] for i in plan['planning_issues'])
    else:
        with pytest.raises(prompt_planner.DialogueValidationError):
            prompt_planner.plan_h3_prompt({'estimated_duration_sec': 6}, 1, **args)


def test_compiler_preserves_multi_line_shot_assignment_and_explicit_silence(tmp_path):
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    state = two_speakers(tmp_path)
    shot = state['documents'][0]['shots'][0]
    row = copy.deepcopy(shot['dialogues'][0])
    row.update(uid='added', speaker_key='image:test_person_b', line='这是接话。')
    shot['dialogues'].append(row)
    result = service.commit(request(state), root=tmp_path, check_files=False)
    beat = json.loads(result['beats_text'])['beats'][0]
    shots = H3PromptCompiler()._raw_shots(beat)
    assert len(shots[0]['dialogues']) == 2
    assert shots[0]['dialogues'][1]['speaker'] == 'character_b'
    assert len(shots[1]['dialogues']) == 1
    silent = {'dialogue_authoritative': True, 'dialogue_units': []}
    assert H3PromptCompiler()._shots_with_current_dialogue(silent, [{'dialogue': {'line': '旧台词'}}]) == [{}]
    with pytest.raises(ValueError, match='归属失效'):
        H3PromptCompiler()._shots_with_current_dialogue(beat, [{'shot_uid': 'stale', 'dialogue': {'line': '旧台词'}}])


@pytest.mark.parametrize('ordinary_reset', [False, True])
@pytest.mark.parametrize('silent', [False, True])
def test_saved_dialogue_is_used_by_both_regeneration_paths(tmp_path, monkeypatch, ordinary_reset, silent):
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from generation.pipelines.minimax_h3.prompt_compiler import H3CompiledPrompt
    state = two_speakers(tmp_path)
    shot = state['documents'][0]['shots'][0]
    shot['dialogues'][0].update(speaker_key='image:test_person_b', line='最新台词。')
    if silent:
        for shot in state['documents'][0]['shots']:
            shot['dialogues'] = []
    service.commit(request(state), root=tmp_path, check_files=False)
    beat = json.loads((tmp_path / 'beats.json').read_text(encoding='utf-8'))['beats'][0]
    previous = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    pipeline = MiniMaxH3Ref2VAPipeline()
    if ordinary_reset:
        # A later time-only edit changes the prompt hash but must not remove
        # ownership/silence validation on the next regeneration.
        previous['generation_config']['prompt_override'] = previous['generation_config']['prompt_override'].replace('6.00 秒', '6 秒')
        pipeline.reset_prompt_for_regeneration(previous)
    else:
        previous['h3_force_replan'] = True
    calls = []
    def model(system, user, **kwargs):
        payload = json.loads(user)
        calls.append(payload)
        return {'duration_sec': 6, 'duration_reason': '自然对白', 'shot_mode': 'one_take',
                'shots': [{'start_sec': 0, 'end_sec': 6, 'action': '保持站位。',
                           'dialogue_refs': list(range(1, len(payload['source_dialogue']) + 1))}]}, 0
    monkeypatch.setattr(prompt_planner, '_call_model', model)
    monkeypatch.setattr(pipeline.adapter.prompt_compiler, 'compile',
                        lambda *a, **k: H3CompiledPrompt('新提示词', (), 'compiled'))
    result = pipeline._prepare_segment({}, beat, 1, previous, check_files=False, planning_only=True)
    assert calls[0]['dialogue_authoritative']
    if silent:
        assert calls[0]['source_dialogue'] == []
    else:
        assert calls[0]['source_dialogue'][0]['line'] == '最新台词。'
        # Both regeneration paths consume the selected current draft's bindings.
        assert calls[0]['source_dialogue'][0]['speaker'] == '<Subject 2>'
    assert result['beat_uid'] == beat['beat_uid']
    assert not any(i['code'] == 'dialogue_mismatch' for i in result['planning_issues'])
