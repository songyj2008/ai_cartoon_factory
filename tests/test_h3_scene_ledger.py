"""Persistent world facts survive camera cuts; only authored updates change them."""
import copy
import pytest
from generation.pipelines.minimax_h3.continuity.reducer import initial_world,reduce_segment
from generation.pipelines.minimax_h3.continuity.ledger import source_world
from generation.pipelines.minimax_h3.continuity.revisions import revision,reconcile_ledger_revisions,accept_candidate_state

def source(uid='s1'):
    return {'segment_uid':uid,'prompt_hash':'hash-'+uid,'content_revision':'version-'+uid,
            'snapshot':{'prompt':'甲在桌前。乙在门旁。其余三人在室内。','asset_manifest':[]}}

def trace(events):
    return {'relation':'immediate','shots':[{'shot_uid':'shot1','visible_roles':['a'],
            'world_events':events,'framing_events':[]}]}

def fact(value,quote='甲在桌前'):
    return {'value':value,'source_quote':quote}

def test_five_people_survive_ten_segments_and_keep_original_evidence():
    roster=list('abcde');world=initial_world(roster)
    world=reduce_segment(world,trace([{'role_id':r,'facts':{'present':fact(True,'其余三人在室内')}} for r in roster]),source(),roster)
    world=reduce_segment(world,trace([{'role_id':'a','facts':{'position':fact('桌前')}}]),source(),roster)
    original=copy.deepcopy(world)
    for i in range(2,11): world=reduce_segment(world,trace([]),source('s'+str(i)),roster)
    assert world==original and len(world)==5
    assert world['a']['position']['origin']['segment_uid']=='s1'

def test_actual_departure_differs_from_camera_exit():
    world={'a':{'present':fact(True)}}
    camera=trace([]);camera['shots'][0]['framing_events']=[{'role_id':'a','description':'出画'}]
    assert reduce_segment(world,camera,source(),['a'])==world
    moved=trace([{'role_id':'a','facts':{'present':fact(False),'position':fact(None,'')}}])
    result=reduce_segment(world,moved,source(),['a'])
    assert result['a']['present']['value'] is False and result['a']['position']['value'] is None

def test_random_extra_and_bare_boolean_never_enter_ledger():
    with pytest.raises(ValueError,match='跟踪名单'):
        reduce_segment({},trace([{'role_id':'extra','facts':{}}]),source(),['a'])
    with pytest.raises(ValueError,match='对象'):
        reduce_segment({},trace([{'role_id':'a','facts':{'present':True}}]),source(),['a'])

def test_source_rebuild_reuses_checkpoint_without_future_input(monkeypatch):
    from generation.pipelines.minimax_h3.continuity import ledger
    calls=[]
    def extract(src,world,config,providers):
        calls.append(src['segment_uid']);assert 'target' not in src
        return trace([{'role_id':'a','facts':{'position':fact('桌前')}}])
    monkeypatch.setattr(ledger,'extract_segment_trace',extract)
    config={'context_id':'ctx','tracked_role_ids':['a']}
    scope={'config':config,'chain':[source()]}
    world,records,_=source_world(scope,'inherit')
    scope['chain'][0]['record']=records['s1']
    assert source_world(scope,'inherit')[0]==world and calls==['s1']
    scope['config']={**config,'tracked_role_ids':['a','b']}
    source_world(scope,'inherit');assert calls==['s1','s1']

def row(uid,text):
    return {'segment_uid':uid,'generation_config':{'prompt_override':text,'asset_manifest':[]}}

def test_editing_ancestor_invalidates_descendant_but_polling_does_not():
    a,b=row('a','old'),row('b','second');config={'context_id':'ctx'}
    record={'status':'current','config':config,'context_id':'ctx','content_revision':revision(b),
            'dependencies':[{'segment_uid':'a','revision':revision(a)}]}
    b['h3_continuity']=record
    jobs={'segments':[a,b],'h3_scene_continuity':{'contexts':{'ctx':config}}}
    a['status']='running';reconcile_ledger_revisions(jobs);assert record['status']=='current'
    a['generation_config']['prompt_override']='new'
    reconcile_ledger_revisions(jobs);assert record['status']=='needs_analysis'
    assert b['generation_config']['prompt_override']=='second'

def test_candidate_cannot_accept_changed_source():
    a=row('a','old');jobs={'segments':[a]}
    patch={'config':{'context_id':'ctx'},'base_config':None,'records':{},
           'dependencies':[{'segment_uid':'a','revision':revision(a)}]}
    a['generation_config']['prompt_override']='new'
    with pytest.raises(ValueError,match='来源已变化'):accept_candidate_state(jobs,patch)

def test_first_segment_record_mode_is_candidate_only(monkeypatch):
    import json
    from services import llm
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    prompt='【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；甲在桌前；声音：无对白。'
    def reply(system,user,**kwargs):
        data=json.loads(user)
        assert set(data)=={'source','input_world','scene','shots'}
        return {'relation':'initial','shots':[{'shot_uid':data['shots'][0]['shot_uid'],
            'world_events':[{'role_id':'a','facts':{'position':fact('桌前')}}]}]},0
    monkeypatch.setattr(llm,'call_llm',reply)
    config={'context_id':'ctx','tracked_role_ids':['a'],'start_segment_uid':'first'}
    payload={'mode':'record_state','source':{},'segment_uid':'first','base_revision':'base',
        'draft':{'prompt':prompt,'asset_manifest':[],'duration_sec':4},
        'scene_scope':{'config':config,'base_config':None,'chain':[]}}
    before=copy.deepcopy(payload)
    candidate=build_opening_candidate(payload)
    assert payload==before
    patch=candidate['draft']['continuity_patch']
    assert patch['records']['first']['end_state']['a']['position']['value']=='桌前'
    assert patch['dependencies']==[]
