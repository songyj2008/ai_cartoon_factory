import copy,json
import pytest
from generation.pipelines.minimax_h3.continuity import checkpoints
from generation.pipelines.minimax_h3.continuity.extractor import extract_source_end_state,validate_extraction
from generation.pipelines.minimax_h3.continuity.boundary import target_context,apply_plan
from generation.pipelines.minimax_h3.continuity.mapping import reference_map
from generation.pipelines.minimax_h3.continuity.state import planning_constraints,binding_hash
from generation.pipelines.minimax_h3.editor_draft import fingerprint

def actor(role,order):
    return {'asset_id':'asset-'+role,'type':'image','role':role,'order':order,'path':role+'.png',
            'metadata':{'asset_kind':'character','role_id':role,'identity_id':role}}

def source():
    return {'segment_uid':'prior','snapshot':{'prompt':'甲坐在桌后，文件已经落地。','asset_manifest':[actor('a',1)]}}

def extracted():
    return {'state':{'schema_version':1,'characters':[{'role_id':'a','facts':{
        'posture':{'value':'坐姿','source_quote':'甲坐在桌后'}}}]},
        'events':[{'actor_role_ids':['a'],'object':'文件','phase':'completed','source_quote':'文件已经落地'}]}

def test_extraction_cache_resumes_without_target_or_duplicate_source_call(tmp_path,monkeypatch):
    from services import llm
    monkeypatch.setattr(checkpoints,'_path',lambda:tmp_path/'checkpoint.json')
    calls=[]
    def reply(system,user,**kwargs):
        data=json.loads(user);calls.append(data)
        assert set(data)=={'prompt','asset_manifest'}
        assert kwargs['label']=='h3_prompt_continuity_source_v2'
        return extracted(),0
    monkeypatch.setattr(llm,'call_llm',reply)
    first=extract_source_end_state(source(),'inherit')
    assert extract_source_end_state(source(),'inherit')==first and len(calls)==1
    changed=source();changed['snapshot']['prompt']+='窗外下雨。'
    extract_source_end_state(changed,'inherit')
    assert len(calls)==2

def test_corrupt_cache_does_not_become_facts(tmp_path,monkeypatch):
    from services import llm
    path=tmp_path/'checkpoint.json';path.write_text('{bad',encoding='utf-8')
    monkeypatch.setattr(checkpoints,'_path',lambda:path)
    calls=[]
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:(calls.append(k) or extracted(),0))
    assert extract_source_end_state(source(),'inherit')==extracted()
    assert len(calls)==1

@pytest.mark.parametrize('phase',['in_progress','completed','unknown','not_started'])
def test_event_phase_is_preserved_for_target(phase):
    result=extracted();result['events'][0]['phase']=phase
    result=validate_extraction(result,source())
    base={'prompt':'当前稿','asset_manifest':[actor('a',1)]}
    context=target_context(result,base,base)
    assert context['source_end']['events'][0]['phase']==phase
    assert 'source_quote' not in context['source_end']['events'][0]
    assert 'source' not in context

def test_invalid_source_phase_stops_before_target():
    result=extracted();result['events'][0]['phase']='made-up'
    with pytest.raises(ValueError):validate_extraction(result,source())

def test_reordered_people_use_target_compiler_numbers():
    base={'prompt':'当前稿','asset_manifest':[actor('b',1),actor('a',2)]}
    context=target_context(extracted(),base,base)
    assert context['source_end']['facts']['a:posture']['reference']['subject_tag']=='<Subject 2>'
    assert reference_map(base['asset_manifest'])['b']['subject_tag']=='<Subject 1>'

@pytest.mark.parametrize('relation',['time_jump','scene_change','unclear'])
def test_non_immediate_never_applies_source_pose(relation):
    base={'prompt':'当前稿','asset_manifest':[]}
    request=target_context(extracted(),base,base)
    plan={'relation':relation,'shot1_start_ref':'opening','opening_fact_ids':[],'edits':[]}
    assert apply_plan(plan,request)=='当前稿'
    plan['opening_fact_ids']=['a:posture']
    with pytest.raises(ValueError):apply_plan(plan,request)

def test_no_second_independent_shot1_start_state():
    base={'prompt':'当前稿','asset_manifest':[]}
    request=target_context(extracted(),base,base)
    with pytest.raises(ValueError,match='唯一开场'):
        apply_plan({'relation':'immediate','shot1_start_ref':'another-state'},request)

def test_binding_order_change_invalidates_accepted_facts():
    assets=[actor('a',1),actor('b',2)]
    constraint={'opening_continuity':{'schema_version':2,'enabled':True,'target_prompt_hash':fingerprint('稿件'),
                                    'target_binding_hash':binding_hash(assets)}}
    assert planning_constraints(constraint,'稿件',assets)==constraint
    reordered=copy.deepcopy(assets);reordered[0]['order']=2;reordered[1]['order']=1
    assert planning_constraints(constraint,'稿件',reordered)=={}


def test_voice_metadata_does_not_change_spatial_binding():
    assets=[actor('a',1)];changed=copy.deepcopy(assets)
    changed[0]['metadata']['library_voice']={'path':'new-voice.mp3'}
    changed.append({'type':'audio','path':'new-voice.mp3'})
    assert binding_hash(assets)==binding_hash(changed)


def test_target_failure_reuses_source_and_stable_second_input(tmp_path,monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.continuity import build_opening_candidate
    monkeypatch.setattr(checkpoints,'_path',lambda:tmp_path/'checkpoint.json')
    calls=[];inputs=[]
    def reply(system,user,**kwargs):
        calls.append(kwargs['label'])
        if kwargs['label']=='h3_prompt_continuity_source_v2':return {'state':{},'events':[]},0
        inputs.append(user)
        raise RuntimeError('interrupted target')
    monkeypatch.setattr(llm,'call_llm',reply)
    payload={'draft':{'prompt':'【详细镜头描述】\n[Shot 1]（0–4 秒）：甲说：<d>[Chinese]原文。</d>',
                       'asset_manifest':[],'duration_sec':4},'source':source(),'base_revision':'r'}
    before=copy.deepcopy(payload)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='interrupted target'):build_opening_candidate(payload)
    assert calls==['h3_prompt_continuity_source_v2','h3_prompt_continuity_target_v2','h3_prompt_continuity_target_v2']
    assert inputs[0]==inputs[1] and payload==before


def test_invalid_source_never_sends_target(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.continuity import build_opening_candidate
    calls=[]
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:(calls.append(k['label']) or {'bad':'result'},0))
    with pytest.raises(ValueError):build_opening_candidate({'draft':{},'source':source()})
    assert calls==['h3_prompt_continuity_source_v2']
