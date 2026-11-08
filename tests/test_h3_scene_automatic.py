import copy,json
import pytest
from generation.pipelines.minimax_h3.continuity.automatic import automatic_scope

def test_automatic_labels_use_prompt_provider_and_panel_reattaches():
    from pathlib import Path
    from services.web_llm_bridge import entry_for_label
    from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
    root=Path(__file__).resolve().parents[1]
    for filename in ('automatic.py','trace.py'):
        code=(root/'generation/pipelines/minimax_h3/continuity'/filename).read_text(encoding='utf-8')
        import re
        labels=re.findall(r"label='([^']+)'",code)
        assert labels and all(entry_for_label(label)=='prompt' for label in labels)
    assert 'r.scene_task' in EDITOR_DRAFT_JS
    assert "api(ov,'scene-auto'" in EDITOR_DRAFT_JS

def actor(role):
    return {'asset_id':role,'type':'image','role':role,'metadata':{'asset_kind':'character','role_id':role}}

def row(uid,text,roles):
    return {'segment_uid':uid,'generation_config':{'prompt_override':text,'asset_manifest':[actor(r) for r in roles]}}

def test_automatic_scope_tracks_selected_cast_without_background_heuristic(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    jobs={'segments':[row('one','五人在办公室开会',['a','b','c','d','e']),row('two','陈默特写',['a'])]}
    before=copy.deepcopy(jobs)
    def reply(system,user,**kwargs):
        request=json.loads(user)
        assert request['current_segment_uid']=='two' and request['output_type']=='single_object'
        assert 'data必须是单个对象' in system
        values=request['segments'];assert [r['segment_uid'] for r in values]==['one','two']
        return {'start_segment_uid':'one','source_quote':'五人在办公室开会'},0
    monkeypatch.setattr(llm,'call_llm',reply)
    scope=automatic_scope(jobs,'two',snapshot(jobs['segments'][1]))
    assert set(scope['config']['tracked_role_ids'])==set('abcde')
    assert scope['config']['start_segment_uid']=='one' and len(scope['chain'])==1
    assert jobs==before

def test_same_background_can_start_a_new_time_context(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    jobs={'segments':[row('one','办公室',['a']),row('two','第二天办公室',['a'])]}
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:({'start_segment_uid':'two','source_quote':'第二天'},0))
    scope=automatic_scope(jobs,'two',snapshot(jobs['segments'][1]))
    assert scope['config']['start_segment_uid']=='two' and scope['chain']==[]

def test_manual_calibration_is_retained_without_another_scope_model_call(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    a=row('one','会议',['a']);cfg={'context_id':'corrected','start_segment_uid':'one','tracked_role_ids':['a'],'manual':True}
    a['h3_continuity']={'config':cfg,'context_id':'corrected','status':'needs_analysis'}
    jobs={'segments':[a],'h3_scene_continuity':{'contexts':{'corrected':cfg}}}
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:pytest.fail('manual scope must be preserved'))
    assert automatic_scope(jobs,'one',snapshot(a))['config']['context_id']=='corrected'

def test_invented_scope_is_rejected(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    jobs={'segments':[row('one','会议',['a']),row('two','会议继续',['a'])]}
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:({'start_segment_uid':'missing','source_quote':'会议'},0))
    with pytest.raises(ValueError,match='来源'):automatic_scope(jobs,'two',snapshot(jobs['segments'][1]))

def test_first_segment_starts_scene_without_scope_model(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.editor_draft import snapshot
    jobs={'segments':[row('first','办公室',['a','b'])]}
    monkeypatch.setattr(llm,'call_llm',lambda *a,**k:pytest.fail('first segment needs no scene inference'))
    scope=automatic_scope(jobs,'first',snapshot(jobs['segments'][0]))
    assert scope['config']['start_segment_uid']=='first'
    assert scope['config']['tracked_role_ids']==['a','b']

def test_background_commit_only_updates_metadata_and_rejects_concurrent_edit(monkeypatch):
    from contextlib import nullcontext
    from services import context
    from workflow import video_merge
    from generation.pipelines.minimax_h3.continuity.automatic import commit_automatic
    from generation.pipelines.minimax_h3.continuity.revisions import revision
    a=row('one','会议',['a']);jobs={'segments':[a]};writes=[]
    cfg={'context_id':'ctx','tracked_role_ids':['a'],'start_segment_uid':'one'}
    record={'context_id':'ctx','config':cfg,'content_revision':revision(a),'dependencies':[],
            'status':'current','end_state':{'a':{}}}
    patch={'config':cfg,'base_config':None,'dependencies':[],'records':{'one':record}}
    result={'draft':{'continuity_patch':patch}}
    monkeypatch.setattr(context,'pin_runtime',lambda *a:nullcontext())
    monkeypatch.setattr(context,'assert_current_runtime_writable',lambda *a:None)
    monkeypatch.setattr(video_merge,'video_jobs_lock',lambda *a:nullcontext())
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:copy.deepcopy(jobs))
    monkeypatch.setattr(video_merge,'save_video_jobs',lambda data:writes.append(data))
    item={'source':'F:/test','project':'test','episode':None}
    commit_automatic(item,result)
    assert writes[0]['segments'][0]['generation_config']==a['generation_config']
    assert writes[0]['segments'][0]['h3_continuity']['status']=='current'
    a['generation_config']['prompt_override']='用户的新稿'
    with pytest.raises(ValueError,match='保存正文'):commit_automatic(item,result)
    assert len(writes)==1
