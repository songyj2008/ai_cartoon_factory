from spatial_test_support import planner_reply, model_reply
import copy
import json

import pytest

from test_scene_state_jobs import runtime, ready_sample, save, load
from services import scene_video_batches as batches, text_jobs


def prepare(project):
    data = ready_sample()
    data['default_model_id'] = 'minimax_h3_local_ref2va'
    for row in data['segments']:
        row['model_id'] = 'minimax_h3_local_ref2va'
        row['beat_uid'] = 'beat-' + row['segment_uid']
    save(project, data)
    (project/'beats.json').write_text(json.dumps({'beats':[{'beat_uid':r['beat_uid'],'estimated_duration_sec':4} for r in data['segments']]}),encoding='utf-8')
    from workflow.video_merge import load_video_jobs, save_video_jobs
    save_video_jobs(load_video_jobs())  # Persist the app's normal render-history schema before comparisons.
    c = batches.catalog()
    return {'revision':c['revision'],'rows':[{'uid':r['segment_uid'],'scene':'room','mode':'inherit','selected':True} for r in c['rows']],
            'providers':['deepseek'],'timing_api':True,'backend':'runninghub'}


@pytest.mark.parametrize('name,registered,expected', [
    ('scene_011', True, '办公室'),
    ('scene_011', False, '作战室'),
    ('办公室（次日）', True, '办公室（次日）'),
    ('room', True, '办公室'),
])
def test_scene_labels_use_location_names_and_keep_group_ids(monkeypatch, name, registered, expected):
    monkeypatch.setattr('services.project_bible.load_project_bible', lambda: {
        'allowed_backgrounds': [{'id': 'scene_011', 'display_name': '办公室'}] if registered else []})
    data = {'h3_scene_continuity': {'contexts': {'room': {'name': name}}},
            'segments': [{'scene_context_id': 'room', 'scene_id': 'scene_011',
                          'generation_config': {'asset_manifest': [
                              {'label': '作战室', 'metadata': {'scene_id': 'scene_011', 'asset_kind': 'background'}}]}}]}
    original = copy.deepcopy(data)
    scenes = batches._display_scenes(data)
    assert list(scenes) == ['room']
    assert scenes['room']['display_name'] == expected
    assert data == original


def test_grouping_is_explicit_and_uses_saved_draft_source(runtime):
    project,_ = runtime
    plan=prepare(project)
    result=batches.start(plan)
    assert result['state']=='waiting_children'
    current=load(project)
    assert all(r['h3_continuity_source']=='draft' for r in current['segments'])
    with pytest.raises(ValueError):batches.start(plan)


@pytest.mark.parametrize('change',['missing','duplicate','mode','stale','scene','empty'])
def test_invalid_plan_does_not_mutate_project(runtime,change):
    project,_=runtime;plan=prepare(project)
    if change=='missing':plan['rows'].pop()
    if change=='duplicate':plan['rows'][1]=copy.deepcopy(plan['rows'][0])
    if change=='mode':plan['rows'][0]['mode']='automatic'
    if change=='stale':plan['revision']='old'
    if change=='scene':plan['rows'][0]['scene']='gone'
    if change=='empty':
        for r in plan['rows']:r['selected']=False
    before=(project/'video_jobs.json').read_bytes()
    with pytest.raises(ValueError):batches.start(plan)
    assert (project/'video_jobs.json').read_bytes()==before


def test_scheduler_prepares_in_order_and_resumes_failed_step(runtime,monkeypatch):
    project,_=runtime;plan=prepare(project)
    parent=batches.start(plan);identifier=parent['id'];trace=[];failed=False
    original_submit=text_jobs.submit
    # Keep the test focused on scheduling, publication and durable checkpoints.
    def child_submit(kind,args,*a,**kw):
        nonlocal failed
        task=original_submit(kind,args,*a,**kw)
        if kind=='scene_video_batch':return task
        row=load(project)
        stage='timing' if kind=='source_replan' else args[0]['stage']
        uid=str(args[0]) if kind=='source_replan' else args[0]['uid']
        trace.append((uid,stage))
        if uid=='2' and stage=='state' and not failed:
            failed=True;text_jobs.update(task['id'],'error',message='temporary failure')
        else:text_jobs.update(task['id'],'applied',result={'scene_batch_stamp':batches._stamp(row,uid)})
        return text_jobs.get(task['id'])
    monkeypatch.setattr(text_jobs,'submit',child_submit)
    for _ in range(30):batches.reconcile_pending()
    assert batches.status(identifier)['state']=='error'
    assert ('3','optimize') not in trace
    assert ('1','submit') in trace
    completed=list(trace);batches.resume(identifier)
    for _ in range(30):batches.reconcile_pending()
    assert batches.status(identifier)['state']=='applied'
    assert trace[len(completed):]==[('2','state'),('2','submit'),('3','optimize'),('3','timing'),('3','state'),('3','submit')]


def test_manual_source_selection_is_not_overridden_without_explicit_policy():
    from test_h3_state_versions import example,OLD_PROMPT,NEW_PROMPT
    from generation.pipelines.minimax_h3.continuity.versions import source_segment
    row=example();assert source_segment(row)['generation_config']['prompt_override']==OLD_PROMPT
    row['h3_continuity_source']='draft'
    assert source_segment(row)['generation_config']['prompt_override']==NEW_PROMPT


@pytest.mark.parametrize('timing_api', [False, True])
def test_ungrouped_segment_waits_for_saved_timing_before_submit(runtime, monkeypatch, timing_api):
    project, _ = runtime
    plan = prepare(project)
    plan['timing_api'] = timing_api
    for row in plan['rows']:
        row.update(scene='', selected=row['uid'] == '2')
    trace = []
    original_submit = text_jobs.submit
    def submit(kind, args, *a, **kw):
        task = original_submit(kind, args, *a, **kw)
        if kind != 'scene_video_batch':
            trace.append((kind, args, task['id']))
        return task
    monkeypatch.setattr(text_jobs, 'submit', submit)
    parent = batches.start(plan)
    for _ in range(3): batches.reconcile_pending()
    assert len(trace) == 1 and trace[0][0] == 'source_replan'
    options = trace[0][1][2]
    assert options['timing_only'] is True and options['timing_api'] is timing_api
    assert options['providers'] == plan['providers']
    timing_id = trace[0][2]
    # A ready but unsaved timing candidate cannot authorize video dispatch.
    text_jobs.update(timing_id, 'ready')
    batches.reconcile_pending()
    assert len(trace) == 1
    text_jobs.update(timing_id, 'applied', result={'scene_batch_stamp': batches._stamp(load(project), '2')})
    for _ in range(3): batches.reconcile_pending()
    assert [kind for kind, _, _ in trace] == ['source_replan', 'scene_video_step']
    assert trace[1][1][0]['stage'] == 'submit'
    assert trace[1][1][0]['uid'] == '2'
    assert set(text_jobs.get(parent['id'])['stages']) == {'2:timing', '2:submit'}


def test_ungrouped_timing_failure_does_not_dispatch_video(runtime, monkeypatch):
    project, _ = runtime
    plan = prepare(project)
    for row in plan['rows']: row.update(scene='', selected=row['uid'] == '1')
    parent = batches.start(plan)
    batches.reconcile_pending()
    timing_id = text_jobs.get(parent['id'])['stages']['1:timing']
    text_jobs.update(timing_id, 'error', message='timing failed')
    batches.reconcile_pending()
    assert text_jobs.get(parent['id'])['state'] == 'error'
    assert '1:submit' not in text_jobs.get(parent['id'])['stages']


def test_inherit_rejects_changed_ending_and_submission_requires_valid_state(runtime,monkeypatch):
    project,_=runtime;prepare(project)
    data=load(project);data['segments'][1]['h3_continuity']['end_state']['actor']['posture']['value']='站着'
    save(project,data)
    item={'source':str(project),'project':'test','episode':'','args':[{'uid':'2','stage':'state','mode':'inherit','stamp':batches._stamp(data,'2')}]}
    with pytest.raises(ValueError,match='结束状态与开场不同'):batches.execute_step(item)


@pytest.mark.parametrize('index', [0, 1])
def test_real_optimization_step_commits_only_its_segment(runtime,monkeypatch,index):
    project,_=runtime;prepare(project)
    data=load(project)
    for row in data['segments']:row['h3_continuity_source']='draft'
    save(project,data)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
        planner_reply(lambda base,ctx:{'shots':[{'shot_uid':base['shot_uids'][0],'visible_roles':[],
            'role_coverage':[{'role_id':role,'visibility':'out_of_frame',
                              'reason':'固定物件近景，人物所在区域在画框之外'}
                             for role in ctx['tracked_role_ids']],
            'edits':[],'world_events':[]}], 'conflicts':[], 'unresolved':[]}))
    uid = str(index + 1)
    task=text_jobs.submit('scene_video_step',[{'uid':uid,'stage':'optimize','stamp':batches._stamp(data,uid)}],start=False)
    text_jobs._execute(task)
    done=text_jobs.get(task['id'])
    assert done['state']=='applied',done.get('message')
    current = load(project)
    assert done['result']['scene_batch_stamp']==batches._stamp(current,uid)
    for other, original in zip(current['segments'], data['segments']):
        if other['segment_uid'] != uid:
            assert other == original


def test_submit_step_rejects_expired_target_state(runtime,monkeypatch):
    project,_=runtime;prepare(project)
    data=load(project);data['segments'][1]['h3_state_mode']='unset';save(project,data)
    monkeypatch.setattr('generation.service.get_generation_service',lambda:pytest.fail('must not submit'))
    with pytest.raises(ValueError,match='未设置状态'):
        batches.execute_step({'source':str(project),'project':'test','episode':'','args':[
            {'uid':'2','stage':'submit','stamp':batches._stamp(data,'2')}]})


def test_initialization_is_once_per_scene_and_does_not_guess_new_characters(runtime):
    project,_=runtime;plan=prepare(project)
    for row in plan['rows']:row['scene']='new:one'
    plan['new_scenes']={'new:one':'新场景'}
    task=batches.start(plan);data=load(project)
    key=data['segments'][0]['scene_context_id']
    assert all(row['scene_context_id']==key for row in data['segments'])
    assert data['h3_scene_continuity']['contexts'][key]['initial_pending'] is True
    batches.reconcile_pending();parent=text_jobs.get(task['id'])
    assert list(parent['stages'])==['1:initial']
    batches.reconcile_pending()
    assert text_jobs.get(task['id'])['stages']==parent['stages']


def test_other_scene_progresses_after_one_group_fails(runtime,monkeypatch):
    project,_=runtime;plan=prepare(project)
    plan['rows'][1]['scene']=''  # independent video can proceed
    task=batches.start(plan)
    for _ in range(2):batches.reconcile_pending()
    parent=text_jobs.get(task['id'])
    assert '1:optimize' in parent['stages'] and '2:timing' not in parent['stages']
    text_jobs.update(parent['stages']['1:optimize'],'error',message='group failed')
    batches.reconcile_pending()
    parent=text_jobs.get(task['id'])
    assert '2:timing' in parent['stages'] and '2:submit' not in parent['stages']
    text_jobs.update(parent['stages']['2:timing'],'applied',result={'scene_batch_stamp':batches._stamp(load(project),'2')})
    for _ in range(2):batches.reconcile_pending()
    parent=text_jobs.get(task['id'])
    text_jobs.update(parent['stages']['2:submit'],'applied',result={'scene_batch_stamp':batches._stamp(load(project),'2')})
    for _ in range(3):batches.reconcile_pending()
    assert batches.status(task['id'])['state']=='error'
    assert batches.status(task['id'])['progress_rows']['2']['phase']=='提交视频 已完成'


def test_submission_receipt_prevents_duplicate_on_step_replay(runtime,monkeypatch):
    from types import SimpleNamespace
    from workflow.video_merge import load_video_jobs
    project,_=runtime;prepare(project)
    calls=[]
    pipeline=SimpleNamespace(submit_saved=lambda *a,**k:calls.append((a,k)))
    monkeypatch.setattr('generation.service.get_generation_service',lambda:SimpleNamespace(_pipeline_for=lambda *_:pipeline))
    item={'id':'same-child','source':str(project),'project':'test','episode':'','args':[
        {'uid':'1','stage':'submit','stamp':batches._stamp(load_video_jobs(),'1'),'backend':'runninghub'}]}
    batches.execute_step(item);batches.execute_step(item)
    assert len(calls)==1
    assert load(project)['segments'][0]['scene_batch_submission']['state']=='accepted'


def test_unconfirmed_submission_is_not_automatically_resent(runtime,monkeypatch):
    from workflow.video_merge import load_video_jobs,save_video_jobs
    project,_=runtime;prepare(project)
    data=load_video_jobs();target=data['segments'][0]
    target['scene_batch_submission']={'task':'old','state':'sending','renders':[r.get('render_id') for r in target.get('renders',[])]}
    save_video_jobs(data)
    monkeypatch.setattr('generation.service.get_generation_service',lambda:pytest.fail('must not submit'))
    with pytest.raises(text_jobs.NeedsAttentionError):
        batches.execute_step({'id':'retry','source':str(project),'project':'test','episode':'','args':[
            {'uid':'1','stage':'submit','stamp':batches._stamp(load_video_jobs(),'1'),'backend':'runninghub'}]})


def test_http_plan_pins_runtime_and_restores_saved_modes(runtime,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from ui import api_routes
    project,_=runtime;plan=prepare(project)
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app));client=TestClient(app)
    original=client.post('/api/scene-batches/catalog',json={}).json()
    assert original['ok'] and len(original['rows'])==3
    wrong=client.post('/api/scene-batches/start',json=plan,headers={'X-AICF-Runtime':'F:/wrong-project'})
    assert wrong.status_code==409
    response=client.post('/api/scene-batches/start',json=plan)
    assert response.status_code==200,response.text
    identifier=response.json()['id']
    reopened=client.post('/api/scene-batches/catalog',json={}).json()
    assert reopened['batch_id']==identifier
    assert all(r['planned_mode']=='inherit' for r in reopened['rows'])
    assert client.post('/api/scene-batches/status',json={'id':identifier}).json()['state']=='waiting_children'


def test_ready_segment_submits_while_later_scene_member_is_unprepared(runtime):
    project, _ = runtime
    plan = prepare(project)
    task = batches.start(plan)
    stamp = batches._stamp(load(project), '1')
    # Also covers persisted batches which previously waited at the group barrier.
    text_jobs.update(task['id'], progress_rows={'1': {
        'next': 4, 'stamp': stamp, 'phase': '文字已准备，等待同组分段'}})
    batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert '1:submit' in parent['stages']
    assert '2:state' not in parent['stages'] and '3:optimize' not in parent['stages']
    assert parent['progress_rows']['1']['phase'] == '提交视频 已排队'
    batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert '2:optimize' in parent['stages']
    assert text_jobs.get(parent['stages']['1:submit'])['state'] == 'queued'


@pytest.mark.parametrize('submission_state', ['queued', 'running', 'error'])
def test_video_dispatch_does_not_block_next_member_text_or_duplicate_submission(runtime, submission_state):
    project, _ = runtime
    plan = prepare(project)
    task = batches.start(plan)
    stamp = batches._stamp(load(project), '1')
    text_jobs.update(task['id'], progress_rows={'1': {'next': 4, 'stamp': stamp}})
    batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    submit_id = parent['stages']['1:submit']
    if submission_state != 'queued':
        text_jobs.update(submit_id, submission_state, message='dispatch state')
    for _ in range(3):
        batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert parent['stages']['1:submit'] == submit_id
    assert parent['stage_tasks'].count(submit_id) == 1
    assert '2:optimize' in parent['stages']
    assert '3:optimize' not in parent['stages']  # still needs member 2's ending state
    if submission_state == 'error':
        assert parent['failures']


def test_unselected_predecessors_are_read_only_and_never_scheduled(runtime):
    project, _ = runtime
    plan = prepare(project)
    plan['rows'][0]['selected'] = False
    # Ignore unchecked form edits, including choices which would otherwise fail.
    plan['rows'][0].update(scene='new:ignored', mode='automatic')
    before = copy.deepcopy(load(project)['segments'][0])
    task = batches.start(plan)
    for _ in range(3):
        batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert list(parent['stages']) == ['2:optimize']
    assert load(project)['segments'][0] == before
    assert parent['progress_rows']['1']['timing_state'] == 'skipped'
    assert parent['progress_rows']['1']['started_at'] is None
    assert parent['progress_rows']['1']['elapsed_seconds'] is None
    assert len(load(project)['h3_scene_continuity']['contexts']) == 1
    with pytest.raises(ValueError, match='未勾选'):
        batches._child(parent, parent['args'][0]['rows'][0], 'optimize', load(project))


@pytest.mark.parametrize('missing', ['record', 'stale', 'initial'])
def test_missing_unselected_state_reports_blocker_without_preparing_it(runtime, missing):
    project, calls = runtime
    plan = prepare(project)
    plan['rows'][0]['selected'] = False
    jobs = load(project)
    if missing == 'record':
        jobs['segments'][0].pop('h3_continuity')
    elif missing == 'stale':
        jobs['segments'][0]['generation_config']['prompt_override'] += ' changed'
    else:
        jobs['h3_scene_continuity']['contexts']['room']['initial_pending'] = True
    save(project, jobs)
    from workflow.video_merge import load_video_jobs, save_video_jobs
    save_video_jobs(load_video_jobs())
    before = copy.deepcopy(load(project)['segments'][0])
    plan['revision'] = batches.catalog()['revision']
    task = batches.start(plan)
    for _ in range(4):
        batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert parent['state'] == 'error'
    assert not parent['stages']
    assert '未勾选' in next(iter(parent['failures'].values()))
    assert load(project)['segments'][0] == before
    assert calls == []


def test_selected_only_batch_completes_and_retry_never_touches_unchecked_rows(runtime, monkeypatch):
    project, _ = runtime
    plan = prepare(project)
    plan['rows'][0]['selected'] = False
    before = copy.deepcopy(load(project)['segments'][0])
    task = batches.start(plan)
    trace = []
    original_submit = text_jobs.submit
    failed = False

    def child_submit(kind, args, *a, **kw):
        nonlocal failed
        child = original_submit(kind, args, *a, **kw)
        stage = 'timing' if kind == 'source_replan' else args[0]['stage']
        uid = str(args[0]) if kind == 'source_replan' else args[0]['uid']
        trace.append((uid, stage))
        if uid == '2' and stage == 'timing' and not failed:
            failed = True
            text_jobs.update(child['id'], 'error', message='temporary failure')
        else:
            text_jobs.update(child['id'], 'applied', result={'scene_batch_stamp': batches._stamp(load(project), uid)})
        return text_jobs.get(child['id'])

    monkeypatch.setattr(text_jobs, 'submit', child_submit)
    for _ in range(20):
        batches.reconcile_pending()
    assert text_jobs.get(task['id'])['state'] == 'error'
    batches.resume(task['id'])
    for _ in range(25):
        batches.reconcile_pending()
    assert text_jobs.get(task['id'])['state'] == 'applied'
    assert {uid for uid, _ in trace} == {'2', '3'}
    assert trace.count(('2', 'optimize')) == 1
    assert trace.count(('2', 'timing')) == 2
    assert ('2', 'submit') in trace and ('3', 'submit') in trace
    assert load(project)['segments'][0] == before


def test_noncontiguous_selection_does_not_prepare_intervening_segment(runtime):
    project, _ = runtime
    plan = prepare(project)
    plan['rows'][1]['selected'] = False
    before = copy.deepcopy(load(project)['segments'][1])
    task = batches.start(plan)
    text_jobs.update(task['id'], progress_rows={'1': {'next': 5}})
    batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert list(parent['stages']) == ['3:optimize']
    assert load(project)['segments'][1] == before


def test_last_four_of_eight_only_create_selected_tasks(runtime, monkeypatch):
    from generation.pipelines.minimax_h3.continuity.preparation import ensure_scene_state
    from workflow.video_merge import load_video_jobs, save_video_jobs
    project, calls = runtime
    prepare(project)
    jobs = load(project)
    for index in range(4, 9):
        row = copy.deepcopy(jobs['segments'][2])
        row.update(segment_uid=str(index), segment_index=index, part_id=f'part_{index:03d}',
                   beat_uid=f'beat-{index}', h3_continuity_source='draft',
                   h3_shot_editor={'shot_uids': [f's{index}']})
        row.pop('h3_continuity', None)
        jobs['segments'].append(row)
    ensure_scene_state(jobs, '8', include_target=True)
    save_video_jobs(jobs)
    jobs = load_video_jobs()
    before = copy.deepcopy(jobs['segments'][:4])
    calls.clear()
    catalog = batches.catalog()
    task = batches.start({'revision': catalog['revision'], 'providers': ['deepseek'], 'timing_api':True, 'backend': 'runninghub',
        'rows': [{'uid': row['segment_uid'], 'scene': 'room', 'mode': 'inherit',
                  'selected': row['segment_index'] >= 5} for row in catalog['rows']]})
    original_submit = text_jobs.submit
    trace = []

    def child_submit(kind, args, *a, **kw):
        child = original_submit(kind, args, *a, **kw)
        uid = str(args[0]) if kind == 'source_replan' else args[0]['uid']
        trace.append(uid)
        text_jobs.update(child['id'], 'applied', result={'scene_batch_stamp': batches._stamp(load_video_jobs(), uid)})
        return text_jobs.get(child['id'])

    monkeypatch.setattr(text_jobs, 'submit', child_submit)
    for _ in range(45):
        batches.reconcile_pending()
    assert text_jobs.get(task['id'])['state'] == 'applied'
    assert set(trace) == {'5', '6', '7', '8'}
    assert len(trace) == 16  # optimize, timing, state, submit for exactly four segments
    assert load(project)['segments'][:4] == before
    assert calls == []


def test_segment_timing_uses_durable_stage_clocks_and_excludes_queue_time(runtime, monkeypatch):
    project, _ = runtime
    clock = [1000.0]
    monkeypatch.setattr(text_jobs.time, 'time', lambda: clock[0])
    plan = prepare(project)
    parent = batches.start(plan)
    row = plan['rows'][0]
    def child(stage):
        return batches._child(text_jobs.get(parent['id']), row, stage, load(project))
    first = child('optimize')
    text_jobs.update(first['id'], 'running')
    clock[0] = 1004
    live = batches.status(parent['id'])['progress_rows']
    assert live['1']['started_at'] == 1000 and live['1']['elapsed_seconds'] == 4
    assert live['1']['finished_at'] is None
    assert live['2']['started_at'] is None and live['2']['elapsed_seconds'] is None
    clock[0] = 1008
    text_jobs.update(first['id'], 'applied', result={'scene_batch_stamp': batches._stamp(load(project), '1')})
    clock[0] = 1010
    second = child('timing')
    clock[0] = 1030
    assert batches.status(parent['id'])['progress_rows']['1']['elapsed_seconds'] == 8
    text_jobs.update(second['id'], 'running')
    clock[0] = 1038
    text_jobs.update(second['id'], 'applied')
    clock[0] = 1040
    final = child('submit')
    text_jobs.update(final['id'], 'running')
    clock[0] = 1043
    text_jobs.update(final['id'], 'applied')
    done = batches.status(parent['id'])['progress_rows']['1']
    assert done['started_at'] == 1000 and done['finished_at'] == 1043
    assert done['elapsed_seconds'] == 19 and done['timing_state'] == 'complete'
    # Reconciliation persists the projection; refresh never restarts the timer.
    batches.reconcile_pending()
    saved = text_jobs.get(parent['id'])['progress_rows']['1']
    assert saved['started_at'] == 1000 and saved['finished_at'] == 1043
    clock[0] = 2000
    assert batches.status(parent['id'])['progress_rows']['1']['elapsed_seconds'] == 19


def test_segment_timing_keeps_first_start_and_counts_retry_without_pause(runtime, monkeypatch):
    project, _ = runtime
    clock = [1000.0]
    monkeypatch.setattr(text_jobs.time, 'time', lambda: clock[0])
    plan = prepare(project)
    parent = batches.start(plan)
    first = batches._child(text_jobs.get(parent['id']), plan['rows'][0], 'optimize', load(project))
    text_jobs.update(first['id'], 'running')
    clock[0] = 1008
    text_jobs.update(first['id'], 'error', message='temporary failure')
    failed = batches.status(parent['id'])['progress_rows']['1']
    assert failed['finished_at'] == 1008 and failed['elapsed_seconds'] == 8
    text_jobs.update(parent['id'], 'error')
    clock[0] = 1200
    batches.resume(parent['id'])
    retried = batches._child(text_jobs.get(parent['id']), plan['rows'][0], 'optimize', load(project))
    assert retried['id'] != first['id']
    text_jobs.update(retried['id'], 'running')
    clock[0] = 1205
    live = batches.status(parent['id'])['progress_rows']['1']
    assert live['started_at'] == 1000 and live['finished_at'] is None
    assert live['elapsed_seconds'] == 13
    assert live['timing_state'] == 'running'


def test_segment_apply_failure_has_persisted_finish_time(runtime, monkeypatch):
    project, _ = runtime
    clock = [1000.0]
    monkeypatch.setattr(text_jobs.time, 'time', lambda: clock[0])
    plan = prepare(project)
    parent = batches.start(plan)
    child = batches._child(text_jobs.get(parent['id']), plan['rows'][0], 'timing', load(project))
    text_jobs.update(child['id'], 'running')
    clock[0] = 1008
    text_jobs.update(child['id'], 'ready')
    clock[0] = 1010
    text_jobs.record_apply_error(child['id'], 'conflicting duration')
    for now in (1020, 2000):
        clock[0] = now
        failed = batches.status(parent['id'])['progress_rows']['1']
        assert failed['timing_state'] == 'error'
        assert failed['started_at'] == 1000 and failed['finished_at'] == 1010
        assert failed['elapsed_seconds'] == 8


def test_independent_rows_prepare_serially_and_submit_without_waiting_for_render(runtime):
    project, _ = runtime
    plan = prepare(project)
    for row in plan['rows']:
        row['scene'] = ''
    task = batches.start(plan)
    for _ in range(3):
        batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert list(parent['stages']) == ['1:timing']
    first_id = parent['stages']['1:timing']
    text_jobs.update(first_id, 'running')
    for _ in range(3):
        batches.reconcile_pending()
    assert list(text_jobs.get(task['id'])['stages']) == ['1:timing']
    text_jobs.update(first_id, 'applied', result={'scene_batch_stamp': batches._stamp(load(project), '1')})
    for _ in range(2):
        batches.reconcile_pending()
    parent = text_jobs.get(task['id'])
    assert set(parent['stages']) == {'1:timing', '1:submit', '2:timing'}
    assert text_jobs.get(parent['stages']['1:submit'])['state'] == 'queued'
    # Dispatch remains independent of the following segment's preparation.
    text_jobs.update(parent['stages']['1:submit'], 'error', message='dispatch failed')
    batches.reconcile_pending()
    assert '3:timing' not in text_jobs.get(task['id'])['stages']


def test_dispatch_serializes_preexisting_scene_batch_queue_across_providers(runtime):
    from concurrent.futures import ThreadPoolExecutor
    project, _ = runtime
    plan = prepare(project)
    for row in plan['rows']:
        row['scene'] = ''
    parent = batches.start(plan)
    children = []
    for row, provider in zip(plan['rows'], ['deepseek', 'chatgpt', 'doubao']):
        child = batches._child(text_jobs.get(parent['id']), row, 'timing', load(project))
        text_jobs.update(child['id'], provider=provider, provider_candidates=[provider])
        children.append(child)
    limits = {'total': 6, 'deepseek': 2, 'chatgpt': 2, 'doubao': 2}
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: text_jobs._claim_runnable({}, limits), range(2)))
    running = [item for item in claimed if item]
    assert len(running) == 1
    assert running[0]['id'] == children[0]['id']
    text_jobs.update(children[0]['id'], 'applied')
    assert text_jobs._claim_runnable({}, limits)['id'] == children[1]['id']
    assert text_jobs._claim_runnable({}, limits) is None


def test_serial_preparation_keeps_saved_order_across_interleaved_scenes(runtime):
    project, _ = runtime
    plan = prepare(project)
    plan['rows'][1]['scene'] = ''
    task = batches.start(plan)
    text_jobs.update(task['id'], progress_rows={'1': {'next': 5}})
    for _ in range(3):
        batches.reconcile_pending()
    assert list(text_jobs.get(task['id'])['stages']) == ['2:timing']


def test_existing_parallel_preparations_drain_before_claiming_another(runtime):
    project, _ = runtime
    plan = prepare(project)
    for row in plan['rows']:
        row['scene'] = ''
    parent = batches.start(plan)
    children = [batches._child(text_jobs.get(parent['id']), row, 'timing', load(project))
                for row in plan['rows']]
    for child in children[:2]:
        text_jobs.update(child['id'], 'running')
    limits = {'total': 6, 'deepseek': 2}
    assert text_jobs._claim_runnable({}, limits) is None
    text_jobs.update(children[0]['id'], 'applied')
    assert text_jobs._claim_runnable({}, limits) is None
    text_jobs.update(children[1]['id'], 'applied')
    assert text_jobs._claim_runnable({}, limits)['id'] == children[2]['id']
