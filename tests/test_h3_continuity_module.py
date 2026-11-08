"""Continuity boundaries: evidence, frozen sources, editable drafts, standalone output."""
import copy

import pytest

from generation.pipelines.minimax_h3.continuity import resolve_source_snapshot
from generation.pipelines.minimax_h3.continuity.source import validate_source_revision
from generation.pipelines.minimax_h3.continuity.state import (
    validate_state, finalize_constraints, planning_constraints,
)
from generation.pipelines.minimax_h3.editor_draft import fingerprint


MANIFEST=[{'asset_id':'image-a','type':'image','metadata':{'asset_kind':'character','role_id':'role-a'}}]
SOURCE={'snapshot':{'prompt':'甲坐在桌后，抬手将文件掷出。'}}


def state():
    return {'schema_version':1,'characters':[{'role_id':'role-a','facts':{
        'posture':{'value':'坐着','source_quote':'甲坐在桌后'},
        'position':{'value':'桌后','source_quote':'甲坐在桌后'},
        'holding':{'value':None,'source_quote':''}}}]}


def test_validated_evidence_is_copied_and_unknown_remains_unknown():
    raw=state()
    verified=validate_state(raw,SOURCE,MANIFEST)
    assert verified==raw and verified is not raw
    assert verified['characters'][0]['facts']['holding']['value'] is None


@pytest.mark.parametrize('bare', [True, False, None])
def test_presence_bare_value_reports_identity_and_field(bare):
    raw=state()
    raw['characters'][0]['facts']['present']=bare
    with pytest.raises(ValueError,match=r'role-a\.present.*value.*source_quote'):
        validate_state(raw,SOURCE,MANIFEST)


def test_presence_requires_its_own_source_evidence():
    raw=state()
    raw['characters'][0]['facts']['present']={'value':True,'source_quote':'甲坐在桌后'}
    assert validate_state(raw,SOURCE,MANIFEST)==raw
    raw['characters'][0]['facts']['present']['source_quote']=''
    with pytest.raises(ValueError,match='present'):
        validate_state(raw,SOURCE,MANIFEST)


@pytest.mark.parametrize('defect', ['unbound_role','subject_as_identity','duplicate','no_evidence','target_evidence','wrong_scene','old_free_text','malformed'])
def test_invalid_state_never_becomes_confirmed(defect):
    raw=state()
    if defect=='unbound_role':raw['characters'][0]['role_id']='other'
    elif defect=='subject_as_identity':raw['characters'][0]['role_id']='<Subject 1>'
    elif defect=='duplicate':raw['characters'].append(copy.deepcopy(raw['characters'][0]))
    elif defect=='no_evidence':raw['characters'][0]['facts']['posture']['source_quote']=''
    elif defect=='target_evidence':raw['characters'][0]['facts']['posture']={'value':'站立','source_quote':'甲站起身'}
    elif defect=='wrong_scene':raw['scene_id']=['invalid-scene-type']
    elif defect=='old_free_text':raw={'甲':'站立，掷出文件后的姿态'}
    else:raw=[]
    with pytest.raises(ValueError):validate_state(raw,SOURCE,MANIFEST)


def jobs():
    return {'segments':[
        {'segment_uid':'prior','generation_config':{'prompt_override':'甲坐在桌后。','asset_manifest':[]},
         'renders':[{'render_id':'chosen','prompt_snapshot':'甲站在门口。','asset_manifest':[]}]},
        {'segment_uid':'target'}]}


def test_source_edit_blocks_commit_without_overwriting_target():
    data=jobs()
    selected=resolve_source_snapshot(data,'target')
    constraints={'opening_continuity':{'schema_version':2,'enabled':True,'source':selected}}
    validate_source_revision(data,'target',constraints)
    data['segments'][0]['generation_config']['prompt_override']='甲走出房间。'
    before=copy.deepcopy(data)
    with pytest.raises(ValueError,match='来源已变化'):validate_source_revision(data,'target',constraints)
    assert data==before


def test_render_source_does_not_borrow_today_state_and_stays_versioned():
    data=jobs();prior=data['segments'][0]
    prior['h3_editor_constraints']={'opening_continuity':{'state':{'posture':'错误状态'}}}
    selected=resolve_source_snapshot(data,'target','chosen')
    assert selected['snapshot']['editor_constraints']=={}
    constraints={'opening_continuity':{'schema_version':2,'enabled':True,'source':selected}}
    prior['generation_config']['prompt_override']='今天修改的稿件'
    prior['source_excerpt']='今天的背景'
    validate_source_revision(data,'target',constraints)
    prior['renders'][0]['prompt_snapshot']='改变指定历史稿'
    with pytest.raises(ValueError,match='来源已变化'):validate_source_revision(data,'target',constraints)


def test_draft_edits_invalidate_facts_and_never_rewrite_prompt():
    raw={'opening_continuity':{'schema_version':2,'enabled':True,'state':state(),
                               'target_prompt_hash':fingerprint('甲坐在桌后。')}}
    before=copy.deepcopy(raw)
    kept=finalize_constraints(raw,'甲坐在桌后。','格式处理后的甲坐在桌后。')
    assert kept['opening_continuity']['target_prompt_hash']==fingerprint('格式处理后的甲坐在桌后。')
    edited=finalize_constraints(raw,'甲站在门口。','甲站在门口。')
    assert not edited['opening_continuity']['enabled']
    assert edited['opening_continuity']['status']=='target_changed'
    assert raw==before


def test_replanning_only_uses_facts_bound_to_current_draft():
    raw={'another_constraint':True,'opening_continuity':{'schema_version':2,'enabled':True,
         'target_prompt_hash':fingerprint('当前稿'),'state':state()}}
    assert planning_constraints(raw,'当前稿')==raw
    assert planning_constraints(raw,'新稿')=={'another_constraint':True}
    raw['opening_continuity'].pop('schema_version')
    assert planning_constraints(raw,'当前稿')=={'another_constraint':True}


def test_stale_source_cannot_write_editor_files(tmp_path,monkeypatch):
    import json
    from services import context
    from workflow import video_merge
    from generation.pipelines.minimax_h3.editor_draft import snapshot,commit
    data=jobs();target=data['segments'][1]
    target.update(beat_uid='target-beat',generation_config={
        'prompt_override':'【详细镜头描述】\n[Shot 1]（0–4 秒）：甲说：<d>[Chinese]原文。</d>',
        'duration_sec':4,'asset_manifest':[]})
    selected=resolve_source_snapshot(data,'target')
    draft=snapshot(target)
    draft['editor_constraints']={'opening_continuity':{'schema_version':2,'enabled':True,'source':selected}}
    data['segments'][0]['generation_config']['prompt_override']='甲离场。'
    for name,value in [('video_jobs.json',data),('beats.json',{'beats':[{'beat_uid':'target-beat'}]})]:
        (tmp_path/name).write_text(json.dumps(value),encoding='utf-8')
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:copy.deepcopy(data))
    monkeypatch.setattr(context,'get_project_dir',lambda:tmp_path)
    monkeypatch.setattr(context,'assert_current_runtime_writable',lambda *_:None)
    with pytest.raises(ValueError,match='来源已变化'):
        commit({'segment_uid':'target','base_revision':fingerprint(snapshot(target)),'draft':draft})
    assert {path.name:path.read_bytes() for path in tmp_path.iterdir()}==before


def test_service_uses_module_rules_and_preserves_the_selected_target(monkeypatch,tmp_path):
    import json
    from services import llm
    from generation.pipelines.minimax_h3.continuity import build_opening_candidate
    path=tmp_path/'office.png';path.write_bytes(b'fixture')
    portrait=tmp_path/'actor.png';portrait.write_bytes(b'portrait fixture')
    actor={'asset_id':'image-a','type':'image','role':'甲','path':str(portrait),
           'metadata':{'asset_kind':'character','role_id':'role-a','identity_id':'person-a'}}
    background={'asset_id':'office','type':'image','path':str(path),'role':'办公室',
                'metadata':{'asset_kind':'background','scene_id':'office'}}
    target={'prompt':'【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；<Subject 1>说：<d>[Chinese]原台词。</d>',
            'duration_sec':4,'asset_manifest':[actor,background]}
    source={'snapshot':{'prompt':SOURCE['snapshot']['prompt'],'asset_manifest':[actor,background]}}
    original=copy.deepcopy((source,target))
    def reply(system,user,**kwargs):
        request=json.loads(user)
        if kwargs['label']=='h3_prompt_continuity_source_v2':
            assert 'target' not in request
            return {'state':state(),'events':[]},0
        assert kwargs['label']=='h3_prompt_continuity_target_v2'
        assert 'source' not in request
        return {'relation':'immediate','retain_character_asset_ids':['image-a'],
                'opening_fact_ids':['role-a:posture'],'shot1_start_ref':'opening',
                'edits':[],'changes':[],'conflicts':[]},0
    monkeypatch.setattr(llm,'call_llm',reply)
    result=build_opening_candidate({'draft':target,'source':source,'base_revision':'revision'})
    assert result['draft']['editor_constraints']['opening_continuity']['source_end']['state']==state()
    assert result['draft']['editor_constraints']['opening_continuity']['evidence_kind']=='screenplay_text'
    assert (source,target)==original
