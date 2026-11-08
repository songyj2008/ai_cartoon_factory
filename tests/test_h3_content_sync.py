import copy
import json

import pytest

from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
from generation.pipelines.minimax_h3.shot_editor import inspect_prompt
from generation.pipelines.minimax_h3.revision_review import compare_revision


def prompt(body):
    return '【详细镜头描述】\n[Shot 1]（0.00–2.00 秒）：' + body


@pytest.mark.parametrize('owner,speaker,scope', [
    ('电话角色在画外通过电话', '电话角色', 'telephone'),
    ('电话角色 (S2)在画外通过电话', '电话角色', 'telephone'),
    ('<Subject 1>在画外', '<Subject 1>', 'offscreen'),
    ('叙述者以画外旁白', '叙述者', 'narration'),
])
def test_manual_style_delivery_keeps_owner_scope_and_words(owner, speaker, scope):
    from generation.pipelines.minimax_h3.shot_editor import dialogue_units, render_prompt
    text = prompt(owner + '说：<d>[Chinese]原文保持不变。</d>')
    inspected = inspect_prompt(text, 's')
    assert inspected['verified']
    shot = inspected['document']['shots'][0]
    unit = dialogue_units(shot)[0]
    assert (unit['speaker'], unit['scope'], unit['line']) == (speaker, scope, '原文保持不变。')
    shot['dialogues'][0]['line'] = '修改后的台词。'
    revised = inspect_prompt(render_prompt(inspected['document']), 's')
    assert revised['verified']
    new_unit = dialogue_units(revised['document']['shots'][0])[0]
    assert new_unit['speaker'] == speaker and new_unit['scope'] == scope


@pytest.mark.parametrize('scope,delivery', [('telephone', '在画外通过电话'),
                                         ('offscreen', '在画外'), ('narration', '以画外旁白')])
def test_compiled_delivery_roundtrips_without_internal_labels(scope, delivery):
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    from generation.pipelines.minimax_h3.shot_editor import dialogue_units
    body = H3PromptCompiler()._shot_description(
        {'dialogue': {'speaker': '声音角色', 'scope': scope, 'line': '保留原话。'}},
        subject_entries=[], primary_entries=[])
    assert body == '声音角色' + delivery + '说：<d>[Chinese]保留原话。</d>'
    inspected = inspect_prompt(prompt(body), 's')
    assert inspected['verified']
    unit = dialogue_units(inspected['document']['shots'][0])[0]
    assert unit['speaker'] == '声音角色' and unit['scope'] == scope


@pytest.mark.parametrize('owner,scope,line', [
    ('对白标签：telephone·说话人：顾明哲（电话另一端，仅通过手机听筒发声）：', 'telephone', '先保下单支付。'),
    ('对白标签：onscreen·说话人：<Subject 1>（画内现场发声）：', 'onscreen', '有没有应急方案！'),
    ('对白标签：onscreen·说话人：<Subject 1>（S1，画内现场发声）：', 'onscreen', '谁写的？我马上叫过来。'),
])
def test_labeled_speaker_dialogue_from_revision_remains_editable(owner, scope, line):
    from generation.pipelines.minimax_h3.shot_editor import render_prompt
    text = prompt(owner + '<d>[Chinese]' + line + '</d>')
    inspected = inspect_prompt(text, 's')
    assert inspected['verified']
    shot = inspected['document']['shots'][0]
    assert len(shot['dialogues']) == 1
    assert shot['dialogues'][0]['line'] == line
    assert shot['dialogues'][0]['scope'] == scope
    assert render_prompt(inspected['document']).count(line) == 1
    shot['dialogues'][0]['line'] = '修改后的台词。'
    revised = render_prompt(inspected['document'])
    assert line not in revised and revised.count('修改后的台词。') == 1
    assert inspect_prompt(revised, 's')['verified']


def test_silence_state_is_separate_from_missing_prompt_rule():
    unknown = inspect_prompt(prompt('人物走到桌边。'), 's')
    assert unknown['verified']
    assert unknown['document']['shots'][0]['speech_state'] == 'silent'
    assert unknown['document']['shots'][0]['speech_rule_issues']
    silent = inspect_prompt(prompt('本镜无对白，人物走到桌边。'), 's')
    assert silent['verified']
    assert silent['document']['shots'][0]['speech_state'] == 'explicit_silent'


def test_save_syncs_silence_without_erasing_other_contracts_or_history():
    beat = {'dialogue_units': [{'speaker': 'old', 'line': '旧台词'}],
            'visible_roles': ['listener'], 'action_units': [{'action': 'walk'}],
            'model_hints': {'other': {'keep': True}}}
    segment = {'segment_uid': 's', 'duration_sec': 2,
               'renders': [{'task_id': 'historical'}], 'job': {'task_id': 'running'}}
    original = copy.deepcopy(segment)
    updated, saved, report = sync_segment_content(
        beat, segment, prompt=prompt('本镜无对白。'), generation_config={})
    assert report['verified']
    assert updated['dialogue_units'] == []
    assert updated['visible_roles'] == ['listener']
    assert updated['action_units'] == beat['action_units']
    assert updated['model_hints']['other'] == {'keep': True}
    assert saved['job'] == original['job'] and saved['renders'] == original['renders']
    assert segment == original


def test_plain_silent_shot_saves_empty_dialogue_and_llm_repairs_only_wording():
    text = prompt('人物跑步入画。')
    beat, _, report = sync_segment_content(
        {'dialogue_units': [{'speaker': 'old', 'line': '旧台词'}]},
        {'segment_uid': 's'}, prompt=text, generation_config={})
    assert report['verified'] and beat['dialogue_units'] == []
    fixed = prompt('人物跑步入画。本镜无对白，画内人物不做说话口型。')
    review = compare_revision(text, fixed, target_duration_sec=2)
    assert not review['blocking_issues']
    assert not review['changes']


def test_unknown_draft_preserves_last_known_dialogue_but_is_not_authoritative():
    beat = {'dialogue_units': [{'speaker': 'old', 'line': '旧台词'}]}
    text = prompt('人物说：<d>[Chinese]损坏的标签')
    updated, saved, report = sync_segment_content(
        beat, {'segment_uid': 's'}, prompt=text, generation_config={})
    assert not report['verified']
    assert updated['dialogue_units'] == beat['dialogue_units']
    assert not updated['dialogue_authoritative']
    assert saved['h3_editor_prompt'] == text


def test_unparseable_draft_keeps_identity_for_later_text_repair():
    from generation.pipelines.minimax_h3.shot_edit_service import _hash
    original=prompt('本镜无对白。')
    metadata={'schema_version':2,'shot_uids':['stable_shot'],'prompt_hash':_hash(original)}
    segment={'segment_uid':'s','h3_shot_editor':copy.deepcopy(metadata)}
    beat,broken,report=sync_segment_content({},segment,prompt=prompt('甲说：<d>[Chinese]缺少结束标签'),generation_config={})
    assert not report['verified'] and broken['h3_shot_editor']==metadata
    _,repaired,report=sync_segment_content(beat,broken,prompt=prompt('本镜无对白，近景。'),generation_config={})
    assert report['verified'] and repaired['h3_shot_editor']['shot_uids']==['stable_shot']


@pytest.mark.parametrize('source,force',[('manual',False),('manual',True),('web_revision',False)])
def test_text_save_preserves_shot_identity_and_next_scene_state(tmp_path,monkeypatch,source,force):
    from services import context
    from generation.service import GenerationService
    from generation.pipelines.minimax_h3.continuity.ledger import state_before,record_from_plan
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    from generation.pipelines.minimax_h3.shot_edit_service import _hash
    monkeypatch.setattr(context,'_ACTIVE_RUNTIME',(tmp_path,'isolated-tests',''))
    monkeypatch.setattr(context,'PROJECT_DIR',tmp_path)
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('saving must not call model'))
    text=prompt('画面人物：无；镜头构图：全景；本镜无对白。')
    segment={'segment_index':1,'segment_uid':'s','beat_uid':'b','scene_context_id':'room',
        'model_id':'minimax_h3_local_ref2va','generation_config':{'prompt_override':text,'asset_manifest':[],'duration_sec':2},
        'job':{'duration_sec':2},'h3_shot_editor':{'schema_version':2,'prompt_hash':_hash(text),'shot_uids':['stable_shot']}}
    initial={'a':{'position':{'value':'桌前'},'posture':{'value':'站着'},'present':{'value':True}}}
    segment['h3_continuity']=record_from_plan({'shots':[{'shot_uid':'stable_shot','world_events':[]}]},revision(segment),'opening',opening_state=initial,segment_uid='s')
    data={'segments':[segment,{'segment_uid':'next','scene_context_id':'room'}],
          'h3_scene_continuity':{'contexts':{'room':{'initial_state':initial}}}}
    expected=copy.deepcopy(segment['h3_continuity'])
    (tmp_path/'beats.json').write_text(json.dumps({'beats':[{'beat_uid':'b'}]}),encoding='utf-8')
    (tmp_path/'video_jobs.json').write_text(json.dumps(data),encoding='utf-8')
    GenerationService().update_segment_generation_config(1,prompt_override=text.replace('全景','近景'),
        asset_manifest=[],force_save=force,prompt_provenance={'source':source})
    saved=json.loads((tmp_path/'video_jobs.json').read_text(encoding='utf-8'))
    row=saved['segments'][0]
    assert row['h3_shot_editor']['shot_uids']==['stable_shot']
    assert not row['h3_continuity'].get('state_pending')
    assert row['h3_continuity']['end_state']==expected['end_state']
    assert state_before(saved,'next')['input_state']==expected['end_state']


def test_revision_reports_authorized_changes_and_blocks_unknown_structure():
    original = prompt('本镜无对白。')
    changed = prompt('刘洋说：<d>[Chinese]继续。</d>')
    report = compare_revision(original, changed, target_duration_sec=2)
    assert not report['blocking_issues']
    assert any(row['field'] == 'dialogues' for row in report['changes'])
    wording = compare_revision(original, prompt('人物站着。'))
    assert not wording['blocking_issues'] and wording['rule_issues']
    assert compare_revision(original, changed,
                            fixed_constraints={'unchanged_fields': ['dialogues']})['blocking_issues']


@pytest.mark.parametrize('source,force', [('manual', False), ('manual', True), ('web_revision', False)])
def test_save_entry_updates_only_matching_beat_and_preserves_running_job(tmp_path, monkeypatch, source, force):
    from services import context
    from generation.service import GenerationService
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (tmp_path, 'isolated-tests', ''))
    monkeypatch.setattr(context, 'PROJECT_DIR', tmp_path)
    beat = {'beat_uid': 'b', 'dialogue_units': [{'speaker': 'old', 'line': '旧台词'}]}
    untouched = {'beat_uid': 'other', 'plot': '保持原样'}
    segment = {'segment_index': 1, 'segment_uid': 's', 'beat_uid': 'b',
               'model_id': 'minimax_h3_local_ref2va', 'status': 'running',
               'submitted': True, 'backend': 'runninghub', 'task_id': 'active',
               'generation_config': {}, 'job': {'task_id': 'active', 'duration_sec': 2},
               'renders': [{'task_id': 'historical'}]}
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': [beat, untouched]}), encoding='utf-8')
    (tmp_path / 'video_jobs.json').write_text(json.dumps({'segments': [segment]}), encoding='utf-8')
    GenerationService().update_segment_generation_config(
        1, asset_manifest=[], prompt_override=prompt('本镜无对白。'),
        force_save=force, prompt_provenance={'source': source})
    beats = json.loads((tmp_path / 'beats.json').read_text(encoding='utf-8'))['beats']
    saved = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    assert beats[0]['dialogue_units'] == [] and beats[0]['dialogue_authoritative']
    assert beats[1] == untouched
    assert saved['task_id'] == 'active' and saved['status'] == 'running'
    assert any(row.get('task_id') == 'historical' for row in saved['renders'])


def test_phone_scope_does_not_remove_character_visible_in_other_shots():
    from pipeline.events import ensure_event_fields_on_beats
    result = ensure_event_fields_on_beats({
        'generation_policy': {'model_id': 'minimax_h3_local_ref2va'},
        'beats': [{'important_roles': ['listener'], 'visible_roles': ['actor', 'listener'],
                   'reference_roles': ['actor', 'listener'],
                   'dialogue_units': [{'speaker': 'actor', 'line': '收到。', 'scope': 'phone'}]}]})
    beat = result['beats'][0]
    assert beat['dialogue_units'][0]['scope'] == 'telephone'
    assert 'actor' in beat['visible_roles'] and 'actor' in beat['reference_roles']
    assert 'actor' in beat['offscreen_speakers']


def test_unknown_speech_blocks_submission_before_workflow_rebuild(monkeypatch):
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from workflow import video_merge
    from types import SimpleNamespace
    pipeline = MiniMaxH3Ref2VAPipeline()
    segment = {'segment_index': 1, 'model_id': pipeline.spec.id,
               'planning_issues': [{'code': 'unverified_prompt_structure', 'blocking': True}]}
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: {'segments': [segment]})
    monkeypatch.setattr(video_merge, 'save_video_jobs', lambda _: None)
    monkeypatch.setattr(pipeline, '_submit_provider', lambda: SimpleNamespace(name='comfyui'))
    monkeypatch.setattr(pipeline, '_prepare_segment', lambda *a, **k: pytest.fail('must not rebuild'))
    with pytest.raises(RuntimeError, match='结构或时间线无效'):
        pipeline.submit_saved({1}, wait=False)
