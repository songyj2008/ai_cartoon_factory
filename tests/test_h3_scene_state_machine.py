from spatial_test_support import planner_reply, model_reply
import copy
import pytest
from generation.pipelines.minimax_h3.continuity.ledger import state_before,record_from_plan
from generation.pipelines.minimax_h3.continuity.checkpoints import digest


def jobs():
    data= {'h3_scene_continuity':{'contexts':{'room':{'name':'室内','initial_state':{
        'a':{'position':{'value':'桌前'},'posture':{'value':'站着'},'present':{'value':True}},
        'b':{'position':{'value':'侧后方','relative_to_role_id':'a'},'present':{'value':True}}}}}},
        'segments':[{'segment_uid':str(i),'scene_context_id':'room' if i in (1,3,5) else None,
                     'h3_continuity':{'schema_version':3,'shots':[{'shot_uid':'s'+str(i),'visible_roles':['a'],
                     'world_events':[]}]}} for i in range(1,6)]}
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    for row in data['segments']:
        row['generation_config']={'duration_sec':4,'prompt_override':'【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；人物调度：站在桌前；镜头构图：全景；声音：无对白。','asset_manifest':[]}
        row['h3_shot_editor']={'shot_uids':['s'+row['segment_uid']]}
        row['h3_continuity']['source_revision']=revision(row)
        # A confirmed fixture has an evidenced opening. Unanchored historical
        # records are now automatically re-analysed rather than blindly replayed.
        world=copy.deepcopy(data['h3_scene_continuity']['contexts']['room']['initial_state'])
        row['h3_continuity']['input_state']=world
        row['h3_continuity']['previous_end_revision']=digest(world)
        row['h3_continuity']['previous_segment_uid']=str(int(row['segment_uid'])-2) if int(row['segment_uid']) in (3,5) else None
    return data



def ready_scene_fixture(data, uid):
    """Represent the independent join task having already saved valid records."""
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    rows=data['segments'];target=next(s for s in rows if s['segment_uid']==uid)
    for row in rows[:rows.index(target)+1]:
        if row.get('scene_context_id')!=target.get('scene_context_id'):continue
        ctx=state_before(data,row['segment_uid']);draft=snapshot(row)
        ids=draft['shot_uids'] or [s['uid'] for s in parse_prompt(draft['prompt'],row['segment_uid'],draft['asset_manifest'])['shots']]
        row['h3_continuity']=record_from_plan({'shots':[{'shot_uid':x,'world_events':[]} for x in ids]},
            revision(row),ctx['input_revision'],opening_state=ctx['input_state'],segment_uid=row['segment_uid'],previous_segment_uid=ctx['previous_segment_uid'])
        row['h3_continuity']['context_id']=target['scene_context_id']


def test_replay_only_explicit_previous_members_without_llm(monkeypatch):
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('local replay called model'))
    data=jobs();data['segments'][1]['h3_continuity']['shots'][0]['world_events']=[{'role_id':'a','facts':{'position':{'value':'不应读取'}}}]
    data['segments'][2]['h3_continuity']['shots'][0]['world_events']=[{'role_id':'a','facts':{'posture':{'value':'坐着'}}}]
    before=copy.deepcopy(data)
    assert state_before(data,'3')['input_state']['a']['posture']['value']=='站着'
    assert state_before(data,'5')['input_state']['a']['posture']['value']=='坐着'
    assert state_before(data,'5')['input_state']['a']['position']['value']=='桌前'
    assert state_before(data,'5')['input_state']['b']['present']['value'] is True
    assert state_before(data,'2') is None
    assert data==before


def test_input_revision_changes_only_for_relevant_state():
    data=jobs();rev=state_before(data,'3')['input_revision']
    data['segments'][0]['prompt']='修改对白'
    data['segments'][4]['h3_continuity']['shots'][0]['visible_roles']=['b']
    assert state_before(data,'3')['input_revision']==rev
    data['segments'][0]['h3_continuity']['shots'][0]['world_events']=[{'role_id':'a','facts':{'posture':{'value':'坐着'}}}]
    assert state_before(data,'3')['input_revision']!=rev


def test_missing_member_record_reports_instead_of_importing(monkeypatch):
    data=jobs();data['segments'][0]['h3_continuity']={}
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('history model call'))
    with pytest.raises(ValueError,match='尚未确认'):state_before(data,'3')


def test_old_operation_rejected_before_any_model_call(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('old task called model'))
    with pytest.raises(ValueError,match='旧承接任务'):build_opening_candidate({})


def test_record_is_separate_from_full_plan():
    plan={'shots':[{'shot_uid':'s','visible_roles':['a'],'world_events':[],'action':'动作'}]}
    record=record_from_plan(plan,'text','state')
    plan['shots'][0]['visible_roles'].clear()
    assert 'visible_roles' not in record['shots'][0]
    assert 'action' not in record['shots'][0]


@pytest.mark.parametrize('segment_index',[1,3])
@pytest.mark.parametrize('offscreen_reference',[False,True])
def test_state_candidate_preserves_times_and_only_updates_target(monkeypatch,segment_index,offscreen_reference):
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint
    from generation.pipelines.minimax_h3.continuity.revisions import accept_candidate_state,revision
    from generation.pipelines.minimax_h3 import prompt_planner
    data=jobs();target=data['segments'][segment_index-1]
    prompt='【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；人物调度：站在桌前；镜头构图：全景；<Subject 1>面无表情；声音：无对白。'
    asset={'asset_id':'actor','type':'image','role':'角色甲','order':1,'metadata':{'asset_kind':'character','role_id':'a'}}
    target['generation_config']={'duration_sec':4,'prompt_override':prompt,'asset_manifest':[asset]}
    if offscreen_reference:
        target['generation_config']['asset_manifest'].append(
            {'asset_id':'listener','type':'image','role':'角色乙','order':2,
             'metadata':{'asset_kind':'character','role_id':'b'}})
        target['generation_config']['prompt_override']=(
            '【主体定义】\n<Subject 1>：角色甲；可见内容来自 <Picture 1>。\n'
            '<Subject 2>：角色乙；可见内容来自 <Picture 2>。\n'+prompt.replace('声音：无对白。','<Subject 2>仍在侧后方，不入画；声音：无对白。'))
    from generation.pipelines.minimax_h3.continuity.preparation import ensure_scene_state
    ensure_scene_state(data,str(segment_index))
    draft=snapshot(target);calls=[]
    draft.update(state_only=True,scene_patch={'obsolete':True},state_guard={'obsolete':True},
                 plan={'obsolete':True},optimization_attempt_id='old-attempt')
    def model(base,context):
        calls.append(base)
        return {'shots':[{'shot_uid':base['shot_uids'][0], 'visible_roles':['a'],
                         'edits':[{'source_quote':'全景','replacement':'近景'}],'world_events':[],
                         'role_coverage':[{'role_id':role,'visibility':'visible' if role=='a' else 'out_of_frame',
                                           'reason':'正面近景覆盖桌前主体' if role=='a' else '机位朝向桌前主体，侧后方座位不在本镜画框范围内'}
                                          for role in context['tracked_role_ids']]}],
                'conflicts':[],'unresolved':[]}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',planner_reply(model))
    ready_scene_fixture(data,str(segment_index))
    result=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,'draft':draft,'segment_uid':str(segment_index),'base_revision':fingerprint(draft),'optimization_attempt_id':'new-attempt'})
    assert not result['draft'].get('state_only'), 'A changed candidate must not inherit the old no-op flag'
    assert result['draft']['optimization_attempt_id']=='new-attempt'
    assert 'plan' not in result['draft']
    assert len(calls)==1
    if offscreen_reference:
        assert 'listener' in {a['asset_id'] for a in result['draft']['asset_manifest']}
        assert result['draft']['composition_patch']['shots'][0]['visible_roles']==['a']
        assert result['scene_preview']['input_state']['b']['present']['value'] is True
        assert data['segments'][segment_index-1]['generation_config']['asset_manifest'][-1]['asset_id']=='listener'
    patch=result['draft']['scene_patch'];assert patch['segment_uid']==str(segment_index)
    assert 'records' not in patch
    # Concurrent initial-state change rejects the candidate before acceptance.
    data['h3_scene_continuity']['contexts']['room']['initial_state']['a']['position']['value']='门边'
    with pytest.raises(ValueError,match='已变化'):accept_candidate_state(data,patch)


def test_beat_initialization_autoselects_visible_and_physical_speakers_without_guessing(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.state import initial_from_beat
    from generation.pipelines.minimax_h3.contracts import H3_REF2VA_MODEL_ID
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('initialization called LLM'))
    assets=[{'type':'image','asset_id':r,'metadata':{'asset_kind':'character','role_id':r}} for r in ('a','b','c','phone','unused')]
    beat={'visible_roles':['a','b'],'dialogue_units':[{'speaker':'c','scope':'offscreen'},
            {'speaker':'phone','scope':'telephone'}],
          'model_hints':{H3_REF2VA_MODEL_ID:{'shots':[{'visible_roles':['a']}]}},
          'initial_state':{'a':{'posture':{'value':'坐着'},'position':{'value':'桌后'}}}}
    original=copy.deepcopy(beat)
    result=initial_from_beat(beat,{'generation_config':{'asset_manifest':assets}})
    assert set(result)=={'a','b','c'}
    assert result['a']['present']['value'] is True
    assert result['a']['posture']['value']=='坐着'
    assert result['b']['present']['value'] is None  # Later appearance does not prove opening presence.
    assert result['c']['position']['value'] is None
    assert beat==original


def test_first_optimization_saves_opening_separately_from_ending():
    from generation.pipelines.minimax_h3.continuity.revisions import revision,accept_candidate_state
    data=jobs();scene=data['h3_scene_continuity']['contexts']['room'];scene['initial_pending']=True
    scene['initial_state']={r:{'position':{'value':None},'posture':{'value':None},'present':{'value':None}} for r in ('a','b')}
    context=state_before(data,'1');assert context['initializing'] is True
    initial={'a':{'position':{'value':'桌前'},'posture':{'value':'站着'},'present':{'value':True}},
             'b':{'position':{'value':'侧后方'},'posture':{'value':'站着'},'present':{'value':True}}}
    plan={'shots':[{'shot_uid':'s1','visible_roles':['a'],'world_events':[{'role_id':'a','facts':{'posture':{'value':'坐着'}}}]}]}
    patch={'segment_uid':'1','context_id':'room','input_revision':context['input_revision'],
           'initial_state':initial,'record':record_from_plan(plan,revision(data['segments'][0]),context['input_revision'])}
    accept_candidate_state(data,patch)
    assert scene['initial_state']['a']['posture']['value']=='站着'
    assert scene['initial_pending'] is False
    assert state_before(data,'3')['input_state']['a']['posture']['value']=='坐着'
    assert state_before(data,'1')['initializing'] is False


@pytest.mark.parametrize('automatic',[True,False])
def test_scene_config_http_create_and_calibrate_need_no_model(tmp_path,monkeypatch,automatic):
    import asyncio,json
    from types import SimpleNamespace
    from fastapi import FastAPI
    from starlette.requests import Request
    from ui import api_routes
    data={'segments':[{'segment_uid':'first','status':'completed','workflow_path':'existing.json'}]}
    writes=[]; scheduled=[]
    monkeypatch.setattr('services.scene_state_jobs.request_scene_state_refresh', lambda uid, **kw: scheduled.append(uid) or {'id':'state-job','state':'queued'})
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *a:None)
    monkeypatch.setattr(api_routes,'get_project_dir',lambda:tmp_path)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',lambda:data)
    monkeypatch.setattr('workflow.video_merge.save_video_jobs',lambda jobs:writes.append(copy.deepcopy(jobs)))
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('scene-config called LLM'))
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    endpoint=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/segment/editor/{action}')
    def request(payload):
        async def receive():return {'type':'http.request','body':json.dumps(payload).encode()}
        response=asyncio.run(endpoint('scene-config',Request({'type':'http','headers':[]},receive)))
        assert response.status_code==200,response.body.decode()
        return json.loads(response.body)
    initial={'a':{'position':{'value':None},'posture':{'value':None},'present':{'value':True}}}
    created=request({'segment_uid':'first','name':'办公室','initial_state':initial,'automatic':automatic})
    assert created['state_task'] is None
    assert scheduled==[]
    key=created['context_id'];scene=data['h3_scene_continuity']['contexts'][key]
    assert data['segments'][0]['scene_context_id']==key
    assert scene['initial_pending'] is automatic
    assert scene['initial_state']['a']['present']['value'] is True
    initial['a']['posture']['value']='坐着'
    request({'segment_uid':'first','context_id':key,'initial_state':initial})
    assert scene['initial_pending'] is False
    assert scene['initial_state']['a']['posture']['value']=='坐着'
    assert data['segments'][0]['status']=='completed'
    assert data['segments'][0]['workflow_path']=='existing.json'
    assert len(writes)==2
