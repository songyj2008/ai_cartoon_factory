"""Timing-only changes never need another state-model request."""
import copy
from types import SimpleNamespace

import pytest

from generation.pipelines.minimax_h3.continuity.preparation import ensure_scene_state
from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.continuity.timing import advance_timing_record
from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
from test_h3_state_preparation import sample


def retimed():
    jobs=sample()
    row=jobs['segments'][2]
    old=copy.deepcopy(row)
    row['h3_continuity']['source_prompt']=old['generation_config']['prompt_override']
    row['generation_config']['prompt_override']=old['generation_config']['prompt_override'].replace('0–4 秒','0–6 秒')
    row['generation_config']['duration_sec']=6
    row['h3_prompt_source']='duration_replan'
    row['h3_continuity']['text_pending']=True
    return jobs,row,old


def no_llm(*args,**kwargs):
    pytest.fail('a timing-only change must not request an LLM')


def test_timing_only_pending_record_is_updated_without_model(monkeypatch):
    jobs,row,old=retimed()
    frozen=copy.deepcopy(row['h3_continuity'])
    monkeypatch.setattr('services.llm.call_llm',no_llm)
    ensure_scene_state(jobs,'3',include_target=True)
    record=row['h3_continuity']
    assert record['source_revision']==revision(row)
    assert record['source_prompt']==row['generation_config']['prompt_override']
    assert record['text_pending'] is False
    for key in ('shots','end_state','end_revision','input_state','previous_end_revision'):
        assert record[key]==frozen[key]


def test_original_source_proves_retime_even_with_stale_pending_flag():
    jobs,row,old=retimed()
    row['h3_continuity'].pop('source_prompt')
    assert advance_timing_record(row,before=old)
    assert row['h3_continuity']['source_revision']==revision(row)
    assert not row['h3_continuity']['text_pending']


def test_common_save_preserves_state_when_only_clock_changes(monkeypatch):
    jobs=sample();row=jobs['segments'][2]
    config=copy.deepcopy(row['generation_config'])
    config.update(duration_sec=6,prompt_override=config['prompt_override'].replace('0–4 秒','0–6 秒'))
    monkeypatch.setattr('services.llm.call_llm',no_llm)
    beat,saved,diagnostics=sync_segment_content({'beat_uid':'b'},row,
        prompt=config['prompt_override'],generation_config=config)
    assert diagnostics['verified']
    assert saved['h3_continuity']['source_revision']==revision(saved)
    assert saved['h3_continuity']['end_state']==row['h3_continuity']['end_state']
    assert row['generation_config']['duration_sec']==4


@pytest.mark.parametrize('change',['action','dialogue','binding','shot','unanchored','pending'])
def test_unproved_edits_are_never_blessed_as_timing_only(change):
    jobs,row,old=retimed()
    if change=='action':
        row['generation_config']['prompt_override']=row['generation_config']['prompt_override'].replace('固定近景','人物起身离场')
    elif change=='dialogue':
        row['generation_config']['prompt_override']+='\n对白：<d>再等6秒。</d>'
    elif change=='binding':
        row['generation_config']['asset_manifest']=[{'asset_id':'new','type':'image','path':'new.png'}]
    elif change=='shot':
        row['h3_shot_editor']['shot_uids']=['another']
    elif change=='unanchored':
        row['h3_continuity']['source_revision']='unknown'
    else:
        row['h3_continuity']['state_pending']=True
    before=copy.deepcopy(row)
    assert not advance_timing_record(row,before=old)
    assert row==before


def test_retimed_evidence_quotes_are_rebound_but_facts_and_history_preserved():
    jobs,row,old=retimed()
    source=old['generation_config']['prompt_override'].replace('固定近景','站立2秒后坐下')
    old['generation_config']['prompt_override']=source
    old['h3_continuity']['source_revision']=revision(old)
    record=copy.deepcopy(old['h3_continuity'])
    record.update(source_prompt=source,opening_requirements=[])
    record['shots'][0]['world_events']=[{'role_id':'actor','source_quote':'站立2秒后坐下',
        'facts':{'posture':{'value':'坐着'}}}]
    row['h3_continuity']=record
    row['generation_config']['prompt_override']=source.replace('0–4 秒','0–6 秒').replace('站立2秒','站立3秒')
    row['renders']=[{'metadata':{'h3_continuity_snapshot':copy.deepcopy(record)}}]
    history=copy.deepcopy(row['renders']);end=copy.deepcopy(record['end_state'])
    assert advance_timing_record(row,before=old)
    assert row['h3_continuity']['shots'][0]['world_events'][0]['source_quote']=='站立3秒后坐下'
    assert row['h3_continuity']['end_state']==end and row['renders']==history


def test_saved_video_submission_after_retime_uses_no_llm(monkeypatch):
    from workflow import video_merge
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    jobs,row,old=retimed()
    from generation.pipelines.minimax_h3.continuity.framing import build_framing_record
    from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    for member in jobs['segments']:
        member['h3_state_mode'] = 'extract'
        member['h3_continuity']['context_id'] = 'room'
    incoming = read_ready_context(jobs, '3', target_source='draft')
    base = snapshot(old)
    patch = {'shots': [{'shot_uid': 's3', 'visible_roles': [], 'edits': [], 'world_events': [],
                       'role_coverage': [{'role_id': 'actor', 'visibility': 'out_of_frame',
                                          'reason': '固定近景只覆盖墙面，actor所在桌边在画框外'}]}],
             'conflicts': [], 'unresolved': []}
    row['h3_scene_framing'] = build_framing_record(base, incoming, patch, base['prompt'], base['asset_manifest'])
    assert advance_timing_record(row, before=old)
    row['job'] = {'final_prompt': row['generation_config']['prompt_override'], 'asset_manifest': []}
    pipe=MiniMaxH3Ref2VAPipeline()
    row.update(model_id=pipe.spec.id,workflow_path='fixed.json',workflow_source_revision=revision(row))
    payload=copy.deepcopy(jobs);submitted=[]
    def save(value):
        value=copy.deepcopy(value);payload.clear();payload.update(value)
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:copy.deepcopy(payload))
    monkeypatch.setattr(video_merge,'save_video_jobs',save)
    monkeypatch.setattr('services.llm.call_llm',no_llm)
    monkeypatch.setattr(pipe,'_submit_provider',lambda:SimpleNamespace(name='comfyui'))
    monkeypatch.setattr(pipe,'_submit_segment',lambda provider,s,**kwargs:submitted.append(copy.deepcopy(s)))
    monkeypatch.setattr('workflow.runninghub_usage.refresh_runninghub_usage_summaries',lambda payload:None)
    pipe.submit_saved({3},wait=False)
    assert len(submitted)==1
    assert submitted[0]['h3_continuity']==row['h3_continuity']
    assert submitted[0]['generation_config']['prompt_override']==row['generation_config']['prompt_override']
