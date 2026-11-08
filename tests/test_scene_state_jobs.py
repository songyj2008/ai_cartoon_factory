"""Joining prepares durable facts; optimization never prepares its ancestors."""
import copy
import json
from pathlib import Path

import pytest

from services import context, text_jobs, scene_state_jobs as tasks
from generation.pipelines.minimax_h3.continuity import preparation
from generation.pipelines.minimax_h3.continuity.ledger import record_from_plan
from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
from test_h3_state_preparation import sample, fake_extractor


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    monkeypatch.setattr('services.web_llm_bridge.get_settings', lambda: {
        'default_providers': ['deepseek'], 'overrides': {}, 'concurrency': {'total': 2}})
    monkeypatch.setattr('services.web_llm_bridge.routes_for', lambda *a, **k: ['deepseek'])
    calls = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fake_extractor(calls))
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **k: pytest.fail('unexpected external LLM'))
    with context.pin_runtime((project, 'test', '')):
        yield project, calls


def save(project, jobs):
    (project / 'video_jobs.json').write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')


def load(project):
    return json.loads((project / 'video_jobs.json').read_text(encoding='utf-8'))


def missing():
    jobs = sample()
    for row in jobs['segments']:
        row.pop('h3_continuity')
    return jobs


def execute(task):
    text_jobs._execute(text_jobs.get(task['id']))
    result = text_jobs.get(task['id'])
    assert result['state'] == 'applied', result.get('message')
    return result



def ready_sample():
    jobs=sample()
    preparation.ensure_scene_state(jobs,'3',include_target=True)
    return jobs


def test_inheritance_is_valid_empty_changes_and_no_llm(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    from generation.pipelines.minimax_h3.continuity.ledger import state_before,validate_record
    project,calls=runtime;jobs=ready_sample()
    for row in jobs['segments'][1:]:row.pop('h3_continuity');row['h3_state_mode']='unset'
    save(project,jobs)
    second=inherit_segment_state('2');third=inherit_segment_state('3')
    assert second['message']=='沿用 Part 1 · 已保存'
    assert third['message']=='沿用 Part 2 · 已保存'
    current=load(project)
    for row in current['segments'][1:]:
        record=row['h3_continuity'];assert record['shots']==[]
        assert record['user_declared_unchanged'] is True
        assert record['input_state']==record['end_state']
        validate_record(record,row)
    assert state_before(current,'3')['input_state']==jobs['h3_scene_continuity']['contexts']['room']['initial_state']
    read_ready_context(current,'3',include_target=True)
    preparation.ensure_scene_state(current,'3',include_target=True)
    assert calls==[]


def test_inheritance_identifies_stale_predecessor_without_saving_or_model(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state, state_status
    from generation.pipelines.minimax_h3.continuity.readiness import SceneStateError
    project,calls=runtime;jobs=ready_sample()
    jobs['segments'][1]['part_id']='part_005'
    jobs['segments'][1]['generation_config']['prompt_override']+='新的构图说明。'
    jobs['segments'][2]['h3_state_mode']='unset'
    save(project,jobs);before=(project/'video_jobs.json').read_bytes()
    with pytest.raises(SceneStateError) as failure:
        inherit_segment_state('3')
    error=failure.value
    assert isinstance(error,ValueError)
    assert error.blocking_segment_uid=='2' and error.blocking_part_id=='part_005'
    assert error.is_target is False and error.reason_code=='source_changed'
    assert '请先打开 part_005' in str(error)
    assert '当前段状态操作尚未完成' in str(error)
    status=state_status(jobs,'3')
    assert status['message']=='受前序段 part_005 阻塞'
    assert status['detail']==str(error) and status['ready'] is False
    assert status['blocking_segment_uid']=='2' and status['is_target'] is False
    assert not calls and (project/'video_jobs.json').read_bytes()==before


def test_inheritance_replaces_target_stale_record_without_model(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project,calls=runtime;jobs=ready_sample()
    jobs['segments'][1]['generation_config']['prompt_override']+='新的构图说明。'
    save(project,jobs)
    status=inherit_segment_state('2')
    assert status['ready'] and status['mode']=='inherit'
    current=load(project)
    assert current['segments'][1]['h3_continuity']['source_revision']==revision(current['segments'][1])
    assert current['segments'][1]['h3_continuity']['record_kind']=='inherit'
    read_ready_context(current,'2',include_target=True)
    assert not calls


@pytest.mark.parametrize(('change','reason_code','reason'),[
    ('context','context_changed','人物状态所属场景已变化'),
    ('source','source_changed','提示词或参考图来源已变化'),
    ('upstream','upstream_changed','前驱人物状态已变化'),
    ('end','end_revision_invalid','人物状态末态版本无效'),
    ('record','record_invalid','人物状态记录缺少完整Shot列表'),
])
def test_state_status_preserves_predecessor_validation_reason(runtime,change,reason_code,reason):
    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
    from generation.pipelines.minimax_h3.continuity.readiness import SceneStateError
    _,calls=runtime;jobs=ready_sample();row=jobs['segments'][1];record=row['h3_continuity']
    if change=='context':record['context_id']='other'
    elif change=='source':row['generation_config']['prompt_override']+='新的构图说明。'
    elif change=='upstream':record['previous_end_revision']='stale'
    elif change=='end':record['end_revision']='invalid'
    else:record.update(schema_version=0,shots=None)
    original=copy.deepcopy(jobs)
    with pytest.raises(SceneStateError) as failure:
        read_ready_context(jobs,'3',include_target=True)
    assert failure.value.reason_code==reason_code
    assert reason in str(failure.value)
    status=state_status(jobs,'3')
    assert status['reason_code']==reason_code and status['blocking_segment_uid']=='2'
    assert status['message']=='受前序段 part_002 阻塞'
    assert reason in status['detail'] and status['ready'] is False
    assert jobs==original and not calls


def test_state_status_keeps_target_and_scene_failure_details(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
    _,calls=runtime;jobs=ready_sample()
    jobs['segments'][1]['h3_state_mode']='unset'
    status=state_status(jobs,'2')
    assert status['message']=='未设置状态'
    assert status['detail']=='part_002：未设置状态，请沿用上段或提取本段'
    assert status['is_target'] is True and status['blocking_segment_uid']=='2'
    jobs['h3_scene_continuity']['contexts']['room']['initial_pending']=True
    status=state_status(jobs,'2')
    assert status['message']=='场景初态尚未建立，请提取本场景首段状态或校准初态'
    assert status['detail']==status['message']
    jobs['segments'][1]['scene_context_id']=None
    assert state_status(jobs,'2')=={'mode':'unset','ready':False,'message':'未加入场景'}
    assert not calls


def test_missing_target_record_is_unset_without_claiming_automatic_preparation(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
    _,calls=runtime;jobs=ready_sample();row=jobs['segments'][1]
    row.pop('h3_continuity');row.pop('h3_state_mode',None)
    status=state_status(jobs,'2')
    assert status['mode']=='unset' and status['ready'] is False
    assert status['message']=='未设置状态' and status['reason_code']=='unset'
    assert status['detail']=='part_002：未设置状态，请沿用上段或提取本段'
    assert not calls


def test_inheritance_tracks_upstream_without_treating_empty_as_missing(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest
    from generation.pipelines.minimax_h3.continuity.state import model_world
    project,calls=runtime;save(project,ready_sample());inherit_segment_state('2')
    jobs=load(project);record=jobs['segments'][0]['h3_continuity']
    record['end_state']['actor']['posture']['value']='站着'
    record['end_revision']=digest(model_world(record['end_state']))
    ctx=read_ready_context(jobs,'2',include_target=True)
    assert ctx['input_state']['actor']['posture']['value']=='站着'
    assert jobs['segments'][1]['h3_continuity']['shots']==[] and not calls


def test_inherited_empty_record_survives_saving_new_shot_structure(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
    from generation.pipelines.minimax_h3.continuity.revisions import reconcile_ledger_revisions
    project,calls=runtime;save(project,ready_sample());inherit_segment_state('2')
    jobs=load(project);row=jobs['segments'][1];cfg=copy.deepcopy(row['generation_config'])
    cfg['prompt_override']='【详细镜头描述】\n[Shot 1]（0–2 秒）：固定近景；声音：无对白。\n[Shot 2]（2–4 秒）：侧面近景；声音：无对白。'
    _,updated,inspection=sync_segment_content({'beat_uid':'b2'},row,prompt=cfg['prompt_override'],generation_config=cfg)
    assert inspection['verified']
    jobs['segments'][1]=updated;reconcile_ledger_revisions(jobs)
    record=updated['h3_continuity']
    assert record['shots']==[] and not record['state_pending'] and not record['text_pending']
    read_ready_context(jobs,'2',include_target=True)
    assert not calls


def test_http_inherit_uses_same_scene_predecessor_and_no_model(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    project,calls=runtime;jobs=ready_sample();jobs['segments'][1]['segment_index']=4;save(project,jobs)
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    inherit_segment_state('2')
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    response=TestClient(app).post('/api/segment/editor/inherit-state',json={'segment_uid':'3'})
    assert response.status_code==200,response.text
    assert response.json()['state_status']['message']=='沿用 Part 4 · 已保存'
    assert not calls


def test_first_segment_inherits_initial_state_and_later_segments_can_follow(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    from generation.pipelines.minimax_h3.continuity.ledger import state_before, validate_record
    project,calls=runtime;jobs=ready_sample()
    # The first member of this scene may follow a member of another scene.
    unrelated=copy.deepcopy(jobs['segments'][0])
    unrelated.update(segment_uid='other',scene_context_id='other')
    jobs['segments'].insert(0,unrelated)
    for row in jobs['segments'][1:]:
        row.pop('h3_continuity');row['h3_state_mode']='unset'
    save(project,jobs)
    status=inherit_segment_state('1')
    assert status=={'mode':'inherit','ready':True,'previous_segment_uid':None,
                    'message':'沿用场景初态（无变化）· 已保存'}
    assert inherit_segment_state('2')['message']=='沿用 Part 1 · 已保存'
    current=load(project)
    initial=current['h3_scene_continuity']['contexts']['room']['initial_state']
    record=current['segments'][1]['h3_continuity']
    assert record['previous_segment_uid'] is None
    assert record['input_state']==record['end_state']==initial
    assert record['shots']==[] and record['user_declared_unchanged'] is True
    validate_record(record,current['segments'][1])
    assert state_before(current,'2')['input_state']==initial
    assert read_ready_context(current,'2',include_target=True)['input_state']==initial
    preparation.ensure_scene_state(current,'2',include_target=True)
    # An unchanged declaration continues to resolve against shared calibration.
    initial['actor']['posture']['value']='站着'
    assert read_ready_context(current,'2',include_target=True)['input_state']==initial
    assert not calls


def test_inheritance_still_requires_an_established_initial_state(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project,calls=runtime;jobs=ready_sample()
    jobs['h3_scene_continuity']['contexts']['room']['initial_pending']=True
    save(project,jobs);before=(project/'video_jobs.json').read_bytes()
    with pytest.raises(ValueError,match='场景初态尚未建立'):
        inherit_segment_state('1')
    assert not calls and (project/'video_jobs.json').read_bytes()==before


@pytest.mark.parametrize('presence', [None, 'true', 0, False, True])
def test_manual_calibration_requires_explicit_boolean_presence(runtime,monkeypatch,presence):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    project,calls=runtime;save(project,ready_sample())
    before=(project/'video_jobs.json').read_bytes()
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    response=TestClient(app).post('/api/segment/editor/scene-config',json={
        'segment_uid':'1','context_id':'room','initial_state':{
            'actor':{'position':{'value':None},'posture':{'value':None},'present':{'value':presence}}}})
    if type(presence) is bool:
        assert response.status_code==200,response.text
        assert load(project)['h3_scene_continuity']['contexts']['room']['initial_state']['actor']['present']['value'] is presence
    else:
        assert response.status_code==400
        assert '在场或不在场' in response.json()['message']
        assert (project/'video_jobs.json').read_bytes()==before
    assert not calls


def test_automatic_scene_suggestion_does_not_guess_missing_presence(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    project,calls=runtime;save(project,ready_sample())
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    response=TestClient(app).post('/api/segment/editor/scene-config',json={
        'segment_uid':'1','name':'新场景','automatic':True,'initial_state':{
            'actor':{'position':{'value':None},'posture':{'value':None},'present':{'value':None}}}})
    assert response.status_code==200,response.text
    scene=load(project)['h3_scene_continuity']['contexts'][response.json()['context_id']]
    assert scene['initial_pending'] is True
    assert scene['initial_state']['actor']['present']['value'] is None
    assert not calls


def test_missing_state_task_reports_structured_not_found(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    response=TestClient(app).post('/api/segment/editor/state-result',json={'operation_id':'removed'})
    assert response.status_code==404
    assert response.json()['code']=='OPERATION_NOT_FOUND'
    assert '重新提取' in response.json()['message']


def test_state_progress_includes_latest_llm_task(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    from services import web_llm_bridge
    project,_=runtime;save(project,ready_sample())
    task=tasks.request_scene_state_refresh('2')
    text_jobs.update(task['id'],'running',children=['first-call','repair-call'],message='waiting')
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    seen=[]
    def child(identifier,**kwargs):
        seen.append(identifier)
        return {'provider':'doubao','status':'waiting','message':'等待网页回复','input':{'private':'not exposed'}}
    monkeypatch.setattr(web_llm_bridge,'task_status',child)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    response=TestClient(app).post('/api/segment/editor/state-result',json={'operation_id':task['id']})
    assert response.status_code==200,response.text
    assert seen==['repair-call']
    assert response.json()['web_task']=={'provider':'doubao','status':'waiting','message':'等待网页回复'}


def test_editor_snapshot_identifies_first_member_of_current_scene_without_llm(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    project,calls=runtime;jobs=missing()
    jobs['segments'][0]['scene_context_id']='other-room'
    jobs['h3_scene_continuity']['contexts']['room']['initial_pending']=True
    save(project,jobs)
    (project/'beats.json').write_text('{"beats":[]}',encoding='utf-8')
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    client=TestClient(app)
    for uid,has_previous in [('2',False),('3',True)]:
        response=client.post('/api/segment/editor/snapshot',json={'segment_uid':uid})
        assert response.status_code==200,response.text
        data=response.json()
        assert data['scene_initial_pending'] is True
        assert data['scene_has_predecessor'] is has_previous
        assert data['scene_first_segment']=={'segment_uid':'2','segment_index':2,'part_id':'part_002','title':None}
    assert not calls


@pytest.mark.parametrize('reason',['join','editor_open','editor_save','upstream_changed','membership'])
def test_implicit_entry_points_cannot_enqueue_any_llm(runtime,reason):
    project,calls=runtime;save(project,missing())
    assert tasks.request_scene_state_refresh('3',reason=reason) is None
    with text_jobs.database() as db:assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0
    assert not calls


def test_extract_only_current_segment_and_deduplicate_running_click(runtime):
    project,calls=runtime;jobs=ready_sample();jobs['segments'][2].pop('h3_continuity');save(project,jobs)
    first=tasks.request_scene_state_refresh('3')
    assert tasks.request_scene_state_refresh('3')['id']==first['id']
    execute(first)
    assert [c[0]['segment_uid'] for c in calls]==['3']
    current=load(project);read_ready_context(current,'3',include_target=True)
    for before,after in zip(jobs['segments'][:2],current['segments'][:2]):
        for field in ('h3_continuity','generation_config','video_path'):assert before[field]==after[field]


def test_extract_does_not_fill_missing_predecessors(runtime):
    project,calls=runtime;save(project,missing())
    with pytest.raises(ValueError):tasks.request_scene_state_refresh('3')
    assert not calls
    with text_jobs.database() as db:assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0


def test_changed_upstream_facts_are_rejected_before_model(runtime):
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest
    from generation.pipelines.minimax_h3.continuity.state import model_world
    project,calls=runtime;save(project,ready_sample());task=tasks.request_scene_state_refresh('2')
    jobs=load(project);record=jobs['segments'][0]['h3_continuity']
    record['end_state']['actor']['posture']['value']='站着'
    record['end_revision']=digest(model_world(record['end_state']));save(project,jobs)
    text_jobs._execute(text_jobs.get(task['id']))
    assert text_jobs.get(task['id'])['state']=='error' and not calls


def test_explicit_extract_replaces_inheritance_with_actual_extraction(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project,calls=runtime;save(project,ready_sample());inherit_segment_state('2')
    execute(tasks.request_scene_state_refresh('2'))
    row=load(project)['segments'][1]
    assert row['h3_state_mode']=='extract'
    assert row['h3_continuity'].get('record_kind')!='inherit'
    assert row['h3_continuity']['shots'] and len(calls)==1


def test_predecessor_render_completion_during_extraction_keeps_ready_state(runtime, monkeypatch):
    project, calls = runtime
    save(project, ready_sample())
    task = tasks.request_scene_state_refresh('2')
    from generation.pipelines.minimax_h3.continuity import state_trace
    extract = state_trace.extract_state_record

    def complete_render(row, ctx, **kwargs):
        result = extract(row, ctx, **kwargs)
        current = load(project)
        previous = current['segments'][0]
        cfg = previous['generation_config']
        previous.update(active_render_id='completed', renders=[{
            'render_id': 'completed', 'status': 'success',
            'prompt_snapshot': cfg['prompt_override'],
            'asset_manifest': cfg.get('asset_manifest', []),
            'requested_media_spec': {'duration_sec': cfg['duration_sec']},
        }])
        save(project, current)
        return result

    monkeypatch.setattr(state_trace, 'extract_state_record', complete_render)
    execute(task)
    current = load(project)
    assert current['segments'][0]['active_render_id'] == 'completed'
    read_ready_context(current, '2', include_target=True)
    assert len(calls) == 1


@pytest.mark.parametrize('change',['prompt','binding','leave','cancel','inherit','upstream_prompt','upstream_state'])
def test_old_or_cancelled_results_cannot_write(runtime,monkeypatch,change):
    project,calls=runtime;save(project,ready_sample())
    task=tasks.request_scene_state_refresh('2')
    from generation.pipelines.minimax_h3.continuity import state_trace
    extract=state_trace.extract_state_record
    def changed(row,ctx,**kwargs):
        result=extract(row,ctx,**kwargs)
        current=load(project)
        if change=='prompt':current['segments'][1]['generation_config']['prompt_override']+=' new draft'
        elif change=='binding':current['segments'][1]['generation_config']['asset_manifest']=[{'id':'new','type':'image','path':'new.png'}]
        elif change=='leave':current['segments'][1]['scene_context_id']=None
        elif change=='cancel':text_jobs.cancel(task['id'])
        elif change=='upstream_prompt':current['segments'][0]['generation_config']['prompt_override']+=' new draft'
        elif change=='upstream_state':
            from generation.pipelines.minimax_h3.continuity.checkpoints import digest
            from generation.pipelines.minimax_h3.continuity.state import model_world
            record=current['segments'][0]['h3_continuity']
            record['end_state']['actor']['posture']['value']='站着'
            record['end_revision']=digest(model_world(record['end_state']))
        if change!='inherit':save(project,current)
        else:
            from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
            with context.pin_runtime((project,'test','')):inherit_segment_state('2')
        return result
    monkeypatch.setattr(state_trace,'extract_state_record',changed)
    text_jobs._execute(text_jobs.get(task['id']))
    assert text_jobs.get(task['id'])['state'] in ('error','cancelled')
    if change=='inherit':assert load(project)['segments'][1]['h3_continuity']['record_kind']=='inherit'


def test_selected_render_and_draft_remain_separate(runtime):
    project,calls=runtime;jobs=ready_sample();row=jobs['segments'][0]
    draft=row['generation_config']['prompt_override']
    row.update(active_render_id='good',renders=[{'render_id':'good','status':'success',
        'prompt_snapshot':draft.replace('固定近景','旧稿全景'),'asset_manifest':[],'requested_media_spec':{'duration_sec':4}}])
    from generation.job_schema import normalize_video_jobs_payload
    jobs=normalize_video_jobs_payload(jobs);row=jobs['segments'][0];save(project,jobs)
    execute(tasks.request_scene_state_refresh('1',source_kind='selected'))
    selected=load(project)['segments'][0]
    assert selected['renders']==row['renders'] and selected['active_render_id']=='good'
    assert '旧稿全景' in calls[0][0]['generation_config']['prompt_override']
    execute(tasks.request_scene_state_refresh('1'))
    current=load(project)['segments'][0]
    assert current['h3_continuity_versions']==selected['h3_continuity_versions']
    assert current['h3_continuity']['source_revision']==revision(current)


    assert calls[-1][0]['generation_config']['prompt_override']==draft
    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
    assert state_status(load(project),'1')['ready'] is True

def test_legacy_queued_automatic_job_is_rejected_before_model(runtime):
    project,calls=runtime;save(project,ready_sample());task=tasks.request_scene_state_refresh('1')
    item=text_jobs.get(task['id']);item['args'][0].pop('manual');text_jobs.update(task['id'],args=item['args'])
    text_jobs._execute(text_jobs.get(task['id']))
    assert text_jobs.get(task['id'])['state']=='error' and not calls


def test_old_automatic_attention_task_cannot_block_explicit_extraction(runtime):
    project,_=runtime;save(project,ready_sample());old=tasks.request_scene_state_refresh('1')
    item=text_jobs.get(old['id']);item['args'][0].pop('manual')
    text_jobs.update(old['id'],'needs_attention',args=item['args'])
    current=tasks.request_scene_state_refresh('2')
    assert text_jobs._claim_runnable({}, {'total':2})['id']==current['id']


def test_initialization_happens_in_one_current_segment_call(runtime,monkeypatch):
    project,_=runtime;jobs=missing();scene=jobs['h3_scene_continuity']['contexts']['room'];scene['initial_pending']=True
    initial=copy.deepcopy(scene['initial_state']);calls=[]
    def extract(row,ctx,**kwargs):
        calls.append(row['segment_uid'])
        record=record_from_plan({'shots':[{'shot_uid':'s1','world_events':[]}]},revision(row),ctx['input_revision'],opening_state=initial,segment_uid='1')
        record['initial_state']=initial;return record
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record',extract)
    save(project,jobs);execute(tasks.request_scene_state_refresh('1'))
    assert calls==['1'] and load(project)['h3_scene_continuity']['contexts']['room']['initial_pending'] is False


def test_optimization_saves_its_own_state_instead_of_inherited_declaration(runtime,monkeypatch):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.continuity.revisions import accept_candidate_state
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint
    project,calls=runtime;save(project,ready_sample());inherit_segment_state('3')
    jobs=load(project);draft=snapshot(jobs['segments'][2]);optimizations=[]
    def model(*a,**kwargs):
        optimizations.append(kwargs['label'])
        return {'shots':[{'shot_uid':'s3','visible_roles':[],'edits':[],'world_events':[],
                          'role_coverage':[{'role_id':'actor','visibility':'out_of_frame',
                                            'reason':'固定近景朝向墙面，actor所在桌边位于画框外'}]}],
                'conflicts':[],'unresolved':[]},0
    from spatial_test_support import model_reply
    monkeypatch.setattr('services.llm.call_llm',model_reply(model))
    result=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':jobs,'segment_uid':'3','draft':draft,'base_revision':fingerprint(draft)})
    assert optimizations==['h3_composition_patch'] and not calls
    assert result['draft']['scene_patch']['record']['shots']==[{'shot_uid':'s3','world_events':[]}]
    # The state candidate is bound to the final saved prose, including the
    # independently executable scene occupancy section.
    jobs['segments'][2]['generation_config'].update(
        prompt_override=result['draft']['prompt'], asset_manifest=result['draft']['asset_manifest'])
    accept_candidate_state(jobs,result['draft']['scene_patch'])
    assert jobs['segments'][2]['h3_continuity']['record_kind']=='optimized'
    assert jobs['segments'][2]['h3_state_mode']=='extract'
    read_ready_context(jobs,'3',include_target=True)



def test_failed_extraction_keeps_last_successful_inheritance(runtime, monkeypatch):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state, state_status
    from generation.pipelines.minimax_h3.continuity import state_trace
    project, _ = runtime
    save(project, ready_sample())
    inherit_segment_state('2')
    confirmed = copy.deepcopy(load(project)['segments'][1])
    task = tasks.request_scene_state_refresh('2')
    queued = load(project)['segments'][1]
    assert queued['h3_state_mode'] == 'inherit'
    assert queued['h3_continuity'] == confirmed['h3_continuity']
    assert tasks.active_scene_state_task('2')['id'] == task['id']
    def failed(*args, **kwargs):
        raise ValueError('model unavailable')
    monkeypatch.setattr(state_trace, 'extract_state_record', failed)
    text_jobs._execute(text_jobs.get(task['id']))
    assert text_jobs.get(task['id'])['state'] == 'error'
    current = load(project)
    assert current['segments'][1]['h3_continuity'] == confirmed['h3_continuity']
    assert current['segments'][1]['h3_state_mode'] == 'inherit'
    assert state_status(current, '2')['ready'] is True


def test_last_successful_button_replaces_state_without_editing_content(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state, state_status
    project, calls = runtime
    save(project, ready_sample())
    inherit_segment_state('2')
    current = load(project)
    row = current['segments'][1]
    row['generation_config']['prompt_override'] += '最新保存的站位说明。'
    row['generation_config']['duration_sec'] = 7
    content = copy.deepcopy(row['generation_config'])
    save(project, current)
    execute(tasks.request_scene_state_refresh('2'))
    extracted = load(project)
    row = extracted['segments'][1]
    assert calls[-1][0]['generation_config'] == content
    assert row['h3_state_mode'] == 'extract'
    assert row['h3_continuity']['record_kind'] == 'extracted'
    assert row['h3_continuity']['source_revision'] == revision(row)
    assert row['generation_config'] == content
    assert state_status(extracted, '2')['ready'] is True
    inherit_segment_state('2')
    inherited = load(project)['segments'][1]
    assert inherited['h3_state_mode'] == 'inherit'
    assert inherited['h3_continuity']['record_kind'] == 'inherit'
    assert inherited['generation_config'] == content
    execute(tasks.request_scene_state_refresh('2'))
    final = load(project)['segments'][1]
    assert final['h3_state_mode'] == 'extract'
    assert final['h3_continuity']['record_kind'] == 'extracted'
    assert final['generation_config'] == content
