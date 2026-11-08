from spatial_test_support import planner_reply, model_reply
import copy
import pytest
from test_h3_scene_state_machine import jobs, ready_scene_fixture
from generation.pipelines.minimax_h3.continuity.ledger import state_before,record_from_plan
from generation.pipelines.minimax_h3.continuity.revisions import revision


def unchanged_framing(prepared, context):
    return {'shots': [{'shot_uid': prepared['shot_uids'][0], 'visible_roles': [],
                       'edits': [], 'world_events': [], 'role_coverage': [
                           {'role_id': role, 'visibility': 'out_of_frame',
                            'reason': '固定取景只覆盖桌面局部，在场人物位于当前画框外'}
                           for role in context['tracked_role_ids']]}],
            'conflicts': [], 'unresolved': []}


def test_saved_end_state_becomes_next_opening_without_replaying_shots(monkeypatch):
    data=jobs();row=data['segments'][0];context=state_before(data,'1')
    plan={'shots':[{'shot_uid':'s1','world_events':[{'role_id':'a','facts':{'posture':{'value':'坐着'}}}]}]}
    row['h3_continuity']=record_from_plan(plan,revision(row),context['input_revision'],opening_state=context['input_state'],segment_uid='1')
    assert row['h3_continuity']['end_state']['a']['posture']['value']=='坐着'
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.reducer.reduce_plan',lambda *a:pytest.fail('saved snapshot should be read directly'))
    assert state_before(data,'3')['input_state']['a']['posture']['value']=='坐着'
    assert state_before(data,'3')['previous_segment_uid']=='1'


def test_new_role_can_join_without_modifying_scene_initial_roster():
    data=jobs();data['segments'][0]['generation_config']['asset_manifest']=[{'asset_id':'new','type':'image','metadata':{'asset_kind':'character','role_id':'c'}}]
    before=copy.deepcopy(data['h3_scene_continuity'])
    assert 'c' in state_before(data,'1')['tracked_role_ids']
    assert data['h3_scene_continuity']==before


def test_changed_predecessor_invalidates_dependent_snapshot():
    data=jobs()
    for uid in ('1','3'):
        row=next(s for s in data['segments'] if s['segment_uid']==uid);c=state_before(data,uid)
        row['h3_continuity']=record_from_plan({'shots':row['h3_continuity']['shots']},revision(row),c['input_revision'],opening_state=c['input_state'],segment_uid=uid,previous_segment_uid=c['previous_segment_uid'])
    data['h3_scene_continuity']['contexts']['room']['initial_state']['a']['posture']['value']='斜靠'
    with pytest.raises(ValueError,match='前驱人物状态'):state_before(data,'5')


def test_legacy_draft_can_create_state_candidate_without_changing_text(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint
    data=jobs();target=data['segments'][0]
    target['generation_config']['prompt_override']=target['generation_config']['prompt_override'].replace('镜头构图：','')
    draft=snapshot(target);original=copy.deepcopy(draft)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch', planner_reply(unchanged_framing))
    ready_scene_fixture(data,'1')
    result=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,'draft':draft,'segment_uid':'1','base_revision':fingerprint(draft)})
    candidate=result['draft']
    assert not candidate.get('state_only') and candidate['composition_patch']['shots'][0]['edits']==[]
    assert candidate['prompt'].startswith(original['prompt'])
    assert '【场景人物占位】' not in candidate['prompt']
    assert candidate['asset_manifest']==original['asset_manifest']
    assert candidate['scene_patch']['record']['end_state']['a']['posture']['value']=='站着'


@pytest.mark.parametrize('unsaved_excerpt', [False, True])
def test_state_only_commit_preserves_screenplay_and_video(tmp_path,monkeypatch,unsaved_excerpt):
    import json
    from contextlib import nullcontext
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint,commit
    data=jobs();target=data['segments'][0];target.update(beat_uid='beat1',status='success',video_path='existing.mp4')
    target['generation_config']['prompt_override']=target['generation_config']['prompt_override'].replace('镜头构图：','')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch', planner_reply(unchanged_framing))
    ready_scene_fixture(data,'1')
    # Start from a screenplay with shared constraints already rendered;
    # a genuinely unchanged second review should still preserve the video.
    initial_draft = snapshot(target)
    materialized = build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,
        'draft':initial_draft,'segment_uid':'1','base_revision':fingerprint(initial_draft)})
    target['generation_config']['prompt_override'] = materialized['draft']['prompt']
    ready_scene_fixture(data, '1')
    draft=snapshot(target)
    if unsaved_excerpt:
        draft['source_excerpt'] = '待保存的剧情上下文'
    candidate=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,'draft':draft,'segment_uid':'1','base_revision':fingerprint(draft)})
    assert bool(candidate['draft'].get('state_only')) is (not unsaved_excerpt)
    (tmp_path/'beats.json').write_text(json.dumps({'beats':[{'beat_uid':'beat1'}]}),encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',lambda:data)
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock',nullcontext)
    monkeypatch.setattr('services.context.get_project_dir',lambda:tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable',lambda *a:None)
    writes=[]
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',lambda *a,**k:writes.append(copy.deepcopy(k)))
    monkeypatch.setattr('generation.pipelines.minimax_h3.shot_edit_service._editing_fingerprint',lambda *a:'saved')
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('saving must not call LLM'))
    result=commit({'segment_uid':'1','base_revision':fingerprint(snapshot(target)),'draft':candidate['draft'],'operation_base':candidate['operation_base']})
    assert len(writes)==1 and result['draft']['prompt']==draft['prompt']
    if unsaved_excerpt:
        assert result['draft']['source_excerpt'] == '待保存的剧情上下文'
    else:
        assert target['status']=='success' and target['video_path']=='existing.mp4'
    assert target['h3_continuity']['end_state']['a']['posture']['value']=='站着'


def test_screenplay_fingerprint_does_not_expire_confirmed_legacy_state():
    data=jobs();row=data['segments'][0]
    row['h3_continuity']['text_pending']=True
    row['h3_continuity']['source_revision']='historical-fingerprint'
    row['generation_config']['prompt_override']+='\n\n【备注】\n新排版说明'
    context=state_before(data,'3')
    assert context['input_state']['a']['posture']['value']=='站着'
    assert row['h3_continuity']['source_revision']=='historical-fingerprint'


def test_text_change_keeps_saved_end_revision_and_input_state():
    from generation.pipelines.minimax_h3.continuity.revisions import reconcile_ledger_revisions
    data=jobs();row=data['segments'][0];c=state_before(data,'1')
    row['h3_continuity']=record_from_plan({'shots':row['h3_continuity']['shots']},revision(row),c['input_revision'],opening_state=c['input_state'],segment_uid='1')
    before=state_before(data,'3');saved=copy.deepcopy(row['h3_continuity'])
    row['generation_config']['prompt_override']+='\n\n【备注】\n参考章节整理'
    reconcile_ledger_revisions(data)
    after=state_before(data,'3')
    assert before['input_state']==after['input_state']
    assert before['input_revision']==after['input_revision']
    assert row['h3_continuity']['end_revision']==saved['end_revision']
    assert row['h3_continuity']['text_pending'] is True


def test_empty_state_still_cannot_be_accepted_as_unchanged():
    data=jobs();data['segments'][0]['h3_continuity']['shots']=[]
    with pytest.raises(ValueError,match='完整Shot'):state_before(data,'3')


def test_saved_prompt_preparation_does_not_replace_state_with_empty_cache(monkeypatch):
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    data=jobs();target=data['segments'][0];target.update(segment_index=1,part_id='part_001')
    data['segments']=[target]
    c=state_before(data,'1')
    target['h3_continuity']=record_from_plan({'shots':target['h3_continuity']['shots']},revision(target),c['input_revision'],opening_state=c['input_state'],segment_uid='1')
    original=copy.deepcopy(target['h3_continuity'])
    pipe=MiniMaxH3Ref2VAPipeline()
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',lambda:data)
    writes=[]
    monkeypatch.setattr('workflow.video_merge.save_video_jobs',lambda payload:writes.append(copy.deepcopy(payload)))
    monkeypatch.setattr('workflow.runninghub_queue.has_active_submission',lambda *a:False)
    monkeypatch.setattr(pipe,'_load_template',lambda:{})
    def prepare(*args,**kwargs):
        result=copy.deepcopy(target)
        result['h3_prompt_plan']={'shots':[],'planning_issues':[]}
        return result
    monkeypatch.setattr(pipe,'_prepare_segment',prepare)
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('saved video preparation must not call LLM'))
    result=pipe.generate({'beats':[{'id':1,'estimated_duration_sec':4}]},submit=False,workflow_only=True,segment_indices={1})
    # Automatic metadata preparation may add provenance, but a saved prompt's
    # existing facts and Shot identities must survive an empty plan unchanged.
    for key,value in original.items():
        assert result['segments'][0]['h3_continuity'][key]==value
        assert writes[-1]['segments'][0]['h3_continuity'][key]==value



@pytest.mark.parametrize('saved_shot_identity',[True,False])
def test_unlabelled_shot_composes_all_present_roles_and_saves_missing_picture(tmp_path,monkeypatch,saved_shot_identity):
    import json
    from contextlib import nullcontext
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.continuity.composition import _shots
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint,commit
    data=jobs();target=data['segments'][0]
    target.update(beat_uid='beat1',status='success',video_path='existing.mp4')
    if not saved_shot_identity:target.pop('h3_shot_editor')
    actor={'asset_id':'actor','type':'image','role':'甲','order':1,'metadata':{'asset_kind':'character','role_id':'a'}}
    listener={'asset_id':'listener','type':'image','role':'乙','order':1,'metadata':{'asset_kind':'character','role_id':'b'}}
    target['generation_config']={'duration_sec':4,'asset_manifest':[actor],
        'prompt_override':'【主体定义】\n<Subject 1>：甲；可见内容来自 <Picture 1>。\n【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；甲面无表情地站在桌前，正面近景；声音：无对白，室内底噪\n【非叙事性音乐】\n未指定额外配乐。'}
    data['segments'][1]['generation_config']['asset_manifest']=[listener]
    draft=snapshot(target);calls=[]
    def model(prepared,context):
        calls.append(prepared)
        assert context['input_state']['b']['present']['value'] is True
        assert {a['metadata']['role_id'] for a in prepared['asset_manifest']}=={'a','b'}
        return {'shots':[{'shot_uid':_shots(prepared)[0]['uid'],'visible_roles':['a','b'],
            'edits':[{'source_quote':'正面近景','replacement':'甲的近景，b在甲侧后方背景虚化'}],'world_events':[],
            'role_coverage':[{'role_id':'a','visibility':'visible','reason':'近景覆盖桌前主体甲'},
                             {'role_id':'b','visibility':'partial','reason':'乙在甲侧后方背景虚化可见'}]}],
            'conflicts':[],'unresolved':[]}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',planner_reply(model))
    ready_scene_fixture(data,'1')
    result=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,'draft':draft,'segment_uid':'1','base_revision':fingerprint(draft)})
    assert len(calls)==1 and 'state_only' not in result['draft']
    assert '背景虚化' in result['draft']['prompt']
    assert '正面近景' not in result['draft']['prompt']
    assert result['operation_result']['changed_shots']==[1]
    assert result['operation_result']['kind']=='composition'
    assert '面无表情地站在桌前' in result['draft']['prompt']
    assert '声音：无对白，室内底噪' in result['draft']['prompt']
    assert result['draft']['scene_patch']['record']['end_state']['b']['present']['value'] is True
    (tmp_path/'beats.json').write_text(json.dumps({'beats':[{'beat_uid':'beat1'}]}),encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',lambda:data)
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock',nullcontext)
    monkeypatch.setattr('services.context.get_project_dir',lambda:tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable',lambda *a:None)
    writes=[]
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',lambda *a,**k:writes.append(copy.deepcopy(k)))
    monkeypatch.setattr('generation.pipelines.minimax_h3.shot_edit_service._editing_fingerprint',lambda *a:'saved')
    monkeypatch.setattr('services.llm.call_llm',lambda *a,**k:pytest.fail('saving must not call LLM'))
    saved=commit({'segment_uid':'1','base_revision':fingerprint(draft),'draft':result['draft'],'operation_base':result['operation_base']})
    assert len(writes)==1 and len(calls)==1
    assert saved['draft']['prompt']==result['draft']['prompt']
    assert saved['operation_result']==result['operation_result']
    assert target['status']=='needs_regenerate' and target['video_path']=='existing.mp4'
    assert target['h3_shot_editor']['shot_uids']==[s['shot_uid'] for s in result['draft']['composition_patch']['shots']]
    assert state_before(data,'3')['input_state']['b']['present']['value'] is True
