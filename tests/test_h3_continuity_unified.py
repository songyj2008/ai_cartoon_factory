import copy
import pytest
from generation.pipelines.minimax_h3.continuity.mapping import normalize_script_entities
from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan


def test_repeated_identical_quote_survives_model_spacing():
    from generation.pipelines.minimax_h3.continuity.evidence import source_quote
    text='<Subject 3>站在办公桌侧前方；再次说明：<Subject 3>站在办公桌侧前方'
    assert source_quote(text,'<Subject 3> 站在办公桌侧前方')=='<Subject 3>站在办公桌侧前方'
    assert source_quote(text,'<Subject 3>站在办公桌后方') is None
    assert source_quote(text,'<Subject 3>站在办公桌侧前方再次说明') is None


def test_repeated_quote_with_different_original_layout_stays_ambiguous():
    from generation.pipelines.minimax_h3.continuity.evidence import source_quote
    assert source_quote('甲站在桌前；甲 站在桌前','甲\n站在桌前') is None


@pytest.mark.parametrize('repair_valid', [True, False])
def test_source_import_repairs_spliced_evidence_once(monkeypatch, repair_valid):
    import json
    import pytest
    from generation.pipelines.minimax_h3.continuity import trace, checkpoints
    from generation.pipelines.minimax_h3.continuity.reducer import reduce_segment
    source={'segment_uid':'old','prompt_hash':'revision','snapshot':{
        'prompt':'【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；<Subject 1>保持原站位，身体略有反应；连续性：<Subject 1>仍在桌前；<Subject 1>抓起文件。',
        'asset_manifest':[{'asset_id':'person','type':'image','role':'人物','order':1,
                           'metadata':{'asset_kind':'character','role_id':'person'}}]}}
    world={'person':{'position':{'value':'桌前','source_quote':'原有依据'}}}
    original=copy.deepcopy(world)
    calls=[]
    def model(system,user,**kwargs):
        req=json.loads(user); calls.append((req,kwargs))
        assert {'source','roles','input_world','scene','shots'}<=set(req)
        assert 'identity_normalized_prompt' not in req
        assert set(req['source'])=={'snapshot'}
        assert all('body' not in shot for shot in req['shots'])
        assert 'source_quote' not in json.dumps(req['input_world'])
        uid=req['shots'][0]['shot_uid']
        bad={'role_id':'person','facts':{'position':{'value':'门旁','source_quote':'person保持原站位；person仍在桌前'}}}
        return {'relation':'immediate','shots':[{'shot_uid':uid,'world_events':[] if len(calls)>1 and repair_valid else [bad]}],
                'events':[{'actor_role_ids':['person'],'object':'文件','phase':'completed','source_quote':'person抓起文件'}]},0
    monkeypatch.setattr('services.llm.call_llm',model)
    monkeypatch.setattr('services.text_jobs.phase',lambda *a:None)
    monkeypatch.setattr(checkpoints,'_path',lambda:None)
    if repair_valid:
        result=trace.extract_segment_trace(source,world,{'tracked_role_ids':['person']},{})
        assert reduce_segment(world,result,source,['person'])==original
        assert result['events'][0]['source_quote']=='<Subject 1>抓起文件'
    else:
        with pytest.raises(ValueError,match='自动修正后仍未通过'):
            trace.extract_segment_trace(source,world,{'tracked_role_ids':['person']},{})
    assert len(calls)==2
    assert 'person.position' in calls[1][0]['validation_error']
    assert calls[1][1]['label'].endswith('_repair1')
    assert world==original


def test_each_source_uses_its_own_identity_map_and_dialogue_is_opaque():
    def image(role,order):return {'asset_id':role,'type':'image','role':role,'order':order,'metadata':{'asset_kind':'character','role_id':role}}
    text='<Subject 1>站在桌后；<Subject 2>在侧前；<d>不要改<Subject 1></d>'
    first,_=normalize_script_entities(text,[image('boss',1),image('liu',2)])
    second,_=normalize_script_entities(text,[image('liu',1),image('boss',2)])
    assert first.startswith('boss站在桌后') and second.startswith('liu站在桌后')
    assert '<d>不要改<Subject 1></d>' in first


def test_unchanged_imported_position_keeps_original_evidence_but_changes_need_evidence():
    from generation.pipelines.minimax_h3.continuity.reducer import reduce_segment
    world={'person':{'position':{'value':'桌前','source_quote':'早先原文','origin':{'segment_uid':'first'}}}}
    source={'segment_uid':'next','prompt_hash':'revision','snapshot':{'prompt':'人物保持原站位。'}}
    trace={'shots':[{'shot_uid':'s1','world_events':[{'role_id':'person','facts':{
        'position':{'value':'桌前','source_quote':'模型拼接的无效引用'}}}]}]}
    assert reduce_segment(world,trace,source,['person'])==world
    trace['shots'][0]['world_events'][0]['facts']['position']['value']='门边'
    with pytest.raises(ValueError,match='缺少来源原文依据'):
        reduce_segment(world,trace,source,['person'])
    assert world['person']['position']['origin']['segment_uid']=='first'


def test_ledger_reuses_history_on_later_cast_growth_and_invalidates_real_edits(monkeypatch):
    from generation.pipelines.minimax_h3.continuity import ledger
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest
    config={'context_id':'room','start_segment_uid':'first','tracked_role_ids':['a']}
    before={'a':{}}
    end={'a':{'position':{'value':'桌前'}}}
    source={'segment_uid':'first','content_revision':'v1','snapshot':{'asset_manifest':[]}}
    record={'schema_version':2,'status':'current','config':config,'content_revision':'v1',
            'dependencies':[],'input_state_hash':digest(before),'input_state':before,'end_state':end}
    source['record']=copy.deepcopy(record)
    grown={**config,'tracked_role_ids':['a','b']}
    scope={'config':grown,'chain':[source]}
    monkeypatch.setattr('services.text_jobs.current',lambda:None)
    monkeypatch.setattr('services.text_jobs.phase',lambda *a:None)
    monkeypatch.setattr('services.text_jobs.check_cancelled',lambda:None)
    calls=[]
    def build(*args):
        calls.append(args)
        return {**record,'end_state':{**end,'b':{}}}
    monkeypatch.setattr(ledger,'build_record',build)
    world,records,_=ledger.source_world(scope,{})
    assert calls==[] and world=={**end,'b':{}}
    assert records['first']['config']==grown
    assert source['record']==record
    source['content_revision']='v2'
    ledger.source_world(scope,{})
    assert len(calls)==1
    source['content_revision']='v1'
    source['snapshot']['asset_manifest']=[{'type':'image','metadata':{'asset_kind':'character','role_id':'b'}}]
    ledger.source_world(scope,{})
    assert len(calls)==2  # New tracking of a person already in that source needs import.


def test_accepted_state_survives_save_and_next_segment_without_reimport(tmp_path,monkeypatch):
    import json
    from generation.pipelines.minimax_h3.continuity import ledger
    from generation.pipelines.minimax_h3.continuity.source import freeze_segment
    from generation.pipelines.minimax_h3.continuity.revisions import accept_candidate_state
    asset={'asset_id':'a','type':'image','role':'角色甲','order':1,'metadata':{'asset_kind':'character','role_id':'a'}}
    jobs={'segments':[{'segment_uid':uid,'generation_config':{'duration_sec':4,
        'prompt_override':prompt,'asset_manifest':[asset]}} for uid,prompt in
        [('first','角色甲站在桌前。'),('second','角色甲保持原位。'),('third','角色甲继续发言。')]]}
    cfg={'context_id':'room','start_segment_uid':'first','tracked_role_ids':['a']}
    calls=[]
    def imported(source,world,*args):
        calls.append(source['segment_uid'])
        return {'shots':[{'shot_uid':'s1','world_events':[] if world['a'] else [
            {'role_id':'a','facts':{'position':{'value':'桌前','source_quote':source['snapshot']['prompt']}}}]}]}
    monkeypatch.setattr(ledger,'extract_segment_trace',imported)
    monkeypatch.setattr('services.text_jobs.current',lambda:None)
    monkeypatch.setattr('services.text_jobs.phase',lambda *a:None)
    monkeypatch.setattr('services.text_jobs.check_cancelled',lambda:None)
    scope={'config':cfg,'chain':[freeze_segment(s) for s in jobs['segments'][:2]]}
    world,records,deps=ledger.source_world(scope,{})
    accept_candidate_state(jobs,{'config':cfg,'base_config':None,'records':records,'dependencies':deps})
    path=tmp_path/'jobs.json';path.write_text(json.dumps(jobs),encoding='utf-8')
    saved=json.loads(path.read_text(encoding='utf-8'))
    scope['chain']=[freeze_segment(s) for s in saved['segments'][:2]]
    again,_,_=ledger.source_world(scope,{})
    assert again==world and calls==['first','second']
    # Editing history invalidates it and every dependent state, as intended.
    saved['segments'][0]['generation_config']['prompt_override']='角色甲已经移动到桌前。'
    scope['chain']=[freeze_segment(s) for s in saved['segments'][:2]]
    ledger.source_world(scope,{})
    assert calls==['first','second','first','second']


def test_plan_reduction_keeps_offscreen_people_and_does_not_reread_text():
    world={'chen':{'position':{'value':'桌前'}},'role_c':{'position':{'value':'侧后方','relative_to_role_id':'chen'}}}
    plan={'shots':[{'shot_uid':'s1','visible_roles':['chen'],'world_events':[]}]}
    assert reduce_plan(world,plan,'part2',list(world))==world
    plan['shots'][0]['world_events']=[{'role_id':'role_c','facts':{'present':{'value':False}}}]
    end=reduce_plan(world,plan,'part2',list(world))
    assert end['role_c']['present']['value'] is False
    assert end['role_c']['present']['origin']['kind']=='plan'
    assert 'present' not in world['role_c']


def test_regenerate_record_preserves_draft_and_save_keeps_video_status(monkeypatch,tmp_path):
    import json
    from generation.pipelines.minimax_h3 import editor_draft
    from generation.pipelines.minimax_h3.continuity import ledger
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest
    prompt='【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；<Subject 1>坐在桌后；声音：无对白。'
    asset={'asset_id':'a','type':'image','role':'角色甲','order':1,'metadata':{'asset_kind':'character','role_id':'a'}}
    segment={'segment_uid':'target','beat_uid':'beat','segment_index':1,'status':'completed','submitted':True,
             'workflow_path':'existing.json','generation_config':{'duration_sec':4,'prompt_override':prompt,'asset_manifest':[asset]}}
    draft=editor_draft.snapshot(segment)
    config={'context_id':'room','start_segment_uid':'target','tracked_role_ids':['a']}
    world={'a':{}}
    monkeypatch.setattr(ledger,'source_world',lambda *a:(copy.deepcopy(world),{},[]))
    def record(source,*args):
        assert source['snapshot']['prompt']==prompt
        return {'schema_version':2,'status':'current','context_id':'room','config':config,
            'content_revision':revision(segment),'dependencies':[],'input_state':world,'input_state_hash':digest(world),
            'end_state':{'a':{'position':{'value':'桌后'},'posture':{'value':'坐着'},'present':{'value':True}}}}
    monkeypatch.setattr(ledger,'build_record',record)
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_planner.plan_h3_prompt',lambda *a,**k:pytest.fail('state refresh must not replan shots'))
    result=build_opening_candidate({'mode':'record_state','segment_uid':'target','draft':draft,
        'base_revision':editor_draft.fingerprint(draft),'source':{},
        'scene_scope':{'config':config,'base_config':None,'chain':[]}})
    assert result['draft']['prompt']==prompt and result['draft']['asset_manifest']==[asset]
    jobs={'segments':[segment],'final_video_path':'final.mp4'}
    beats={'beats':[{'beat_uid':'beat'}]}
    (tmp_path/'beats.json').write_text(json.dumps(beats),encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',lambda:jobs)
    monkeypatch.setattr('services.context.get_project_dir',lambda:tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable',lambda *a:None)
    writes=[]
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',lambda *a,**k:writes.append(k))
    saved=editor_draft.commit({'segment_uid':'target','base_revision':editor_draft.fingerprint(draft),
        'draft':result['draft'],'operation_base':result['operation_base']})
    assert saved['scene_record']['end_state']['a']['posture']['value']=='坐着'
    assert segment['status']=='completed' and segment['submitted'] is True
    assert segment['workflow_path']=='existing.json' and jobs['final_video_path']=='final.mp4'
    assert len(writes)==1


def test_scene_state_drops_performance_details():
    world={'a':{'position':{'value':'桌前'},'posture':{'value':'站着'},'present':{'value':True},
                'gaze':{'value':'瞪着对方'},'facing':{'value':'面向镜头'},'holding':{'value':'文件'}}}
    result=reduce_plan(world,{'shots':[{'world_events':[]}]},'next',['a'])
    assert set(result['a'])=={'position','posture','present'}
    assert 'gaze' in world['a']


def test_one_plan_adds_background_reference_and_compiles_new_prompt(monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_planner
    from generation.pipelines.minimax_h3.continuity import ledger
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    def image(role,order,kind='character'):
        return {'asset_id':role,'type':'image','path':role+'.png','role':{'chen':'陈默','boss':'王振国','role_c':'苏念','office':'办公室'}[role],'order':order,
                'metadata':{'asset_kind':kind,('role_id' if kind=='character' else 'scene_id'):role}}
    a,b,c,bg=image('chen',1),image('boss',2),image('role_c',3),image('office',4,'background')
    prompt='【详细镜头描述】\n[Shot 1]（0–4 秒）：场景使用 <Subject 3>；画面人物：<Subject 1>；<Subject 1>站在桌前；声音：无对白。'
    draft={'prompt':prompt,'asset_manifest':[a,b,{**bg,'order':3}],'duration_sec':4}
    cfg={'context_id':'office1','tracked_role_ids':['chen','boss','role_c'],'start_segment_uid':'prior'}
    world={'chen':{'position':{'value':'桌前'}},'boss':{'position':{'value':'桌后'}},'role_c':{'position':{'value':'侧后方','relative_to_role_id':'chen'}}}
    monkeypatch.setattr(ledger,'source_world',lambda *args:(copy.deepcopy(world),{},[]))
    calls=[]
    def planner(*args,**kwargs):
        calls.append(kwargs)
        assert 'role_c' in kwargs['continuity_context']['input_state']
        return {'scene_relation':'immediate','shot_mode':'one_take','core_idea':'近景',
          'shots':[{'shot_uid':kwargs['fixed_shots'][0]['shot_uid'],'start_sec':0,'end_sec':4,
           'scene_id':'office','visible_roles':['chen','role_c'],'subject':'chen','action':'保持站位',
           'camera':'从桌后朝chen拍近景','blocking':'role_c在chen侧后方虚化入画','world_events':[]}],
          'exclusions':[],'non_diegetic_music':''}
    monkeypatch.setattr(prompt_planner,'plan_h3_prompt',planner)
    source={'snapshot':{'prompt':'','asset_manifest':[a,b,c,bg]}}
    result=build_opening_candidate({'draft':draft,'source':source,'segment_uid':'target','base_revision':'base',
        'scene_scope':{'config':cfg,'base_config':None,'chain':[]}})
    out=result['draft']
    assert len(calls)==1
    assert any((x.get('metadata') or {}).get('role_id')=='role_c' for x in out['asset_manifest'])
    assert '苏念' in out['prompt'] and '虚化' in out['prompt']
    assert out['prompt']!=prompt and out['plan']['shots'][0]['visible_roles']==['chen','role_c']
    assert out['continuity_patch']['records']['target']['end_state']==world
