import copy
import pytest
from generation.pipelines.minimax_h3.editor_draft import validate_candidate,fingerprint
from generation.pipelines.minimax_h3.continuity import resolve_source_snapshot
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler


def image(id,name):
    return {'asset_id':id,'type':'image','path':id+'.png','role':name,'metadata':{'asset_kind':'character','identity_id':id,'role_id':id}}


def scene_image(tmp_path):
    path=tmp_path/'scene.png';path.write_bytes(b'scene fixture')
    return {'asset_id':'bg','type':'image','path':str(path),'role':'测试办公室',
            'metadata':{'asset_kind':'background','scene_id':'scene_test','shot_indices':[3]}}


def test_summary_mentions_are_restored_but_dialogue_is_opaque():
    a,b=image('a','甲'),image('b','乙')
    old=H3GenerationConfig.from_mapping({'asset_manifest':[a,b]})
    new=H3GenerationConfig.from_mapping({'asset_manifest':[a]})
    text='【任务概述】\n甲谈论<Subject 2>。\n\n【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；<Subject 1>说：<d>[Chinese]乙正在开会。</d>'
    out=H3PromptReconciler().reconcile(text,old,new)
    assert '甲谈论乙' in out or '<Subject 1>谈论乙' in out
    assert '<d>[Chinese]乙正在开会。</d>' in out
    assert '<Subject 2>' not in out


def test_visual_dependency_is_not_silently_deleted():
    old=H3GenerationConfig.from_mapping({'asset_manifest':[image('a','甲'),image('b','乙')]})
    new=H3GenerationConfig.from_mapping({'asset_manifest':[image('a','甲')]})
    with pytest.raises(ValueError,match='绑定失效'):
        H3PromptReconciler().reconcile('【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 2>；<Subject 2>站在门口。',old,new)


def draft():
    return {'prompt':'【详细镜头描述】\n[Shot 1]（0–4 秒）：甲说：<d>[Chinese]原文。</d>','duration_sec':4,'asset_manifest':[]}


def test_candidate_rejects_dialogue_or_time_changes():
    base=draft()
    for old,new in [('原文','其他'),('0–4','0–3')]:
        candidate=copy.deepcopy(base);candidate['prompt']=candidate['prompt'].replace(old,new)
        with pytest.raises(ValueError):validate_candidate(base,candidate)
    assert validate_candidate(base,copy.deepcopy(base))['shots']


@pytest.mark.parametrize('old,new,field', [
    ('原文','其他','对白原文'),('甲说','乙说','说话人'),
    ('甲说','甲在画外说','画内外发声方式'),
])
def test_candidate_error_identifies_shot_line_and_changed_field(old,new,field):
    base=draft();candidate=copy.deepcopy(base)
    candidate['prompt']=candidate['prompt'].replace(old,new)
    with pytest.raises(ValueError,match=f'Shot 1 第 1 句的{field}发生变化'):
        validate_candidate(base,candidate)


def test_source_selection_uses_explicit_render_not_latest():
    source={'segment_uid':'a','generation_config':{'prompt_override':'保存稿'},'renders':[{'render_id':'old','prompt_snapshot':'指定原稿','asset_manifest':[]},{'render_id':'new','prompt_snapshot':'最新原稿'}]}
    jobs={'segments':[source,{'segment_uid':'b'}]}
    assert resolve_source_snapshot(jobs,'b')['snapshot']['prompt']=='保存稿'
    assert resolve_source_snapshot(jobs,'b','old')['snapshot']['prompt']=='指定原稿'
    with pytest.raises(ValueError):resolve_source_snapshot(jobs,'a')


def test_commit_preserves_selected_video_and_history(tmp_path,monkeypatch):
    import json
    from generation.pipelines.minimax_h3.editor_draft import snapshot,commit
    from services import context
    from workflow import video_merge
    segment={'segment_uid':'s','segment_index':1,'beat_uid':'b','title':'test',
      'generation_config':{'duration_sec':4,'asset_manifest':[],'prompt_override':draft()['prompt']},
      'job':{'duration_sec':4},'active_render_id':'old','video_path':'old.mp4',
      'renders':[{'render_id':'old','status':'success'}],'runninghub_attempts':[{'task_id':'history'}]}
    jobs={'segments':[segment]};beats={'beats':[{'beat_uid':'b'}]}
    (tmp_path/'video_jobs.json').write_text(json.dumps(jobs),encoding='utf-8')
    (tmp_path/'beats.json').write_text(json.dumps(beats),encoding='utf-8')
    monkeypatch.setattr(context,'get_project_dir',lambda:tmp_path)
    monkeypatch.setattr(context,'assert_current_runtime_writable',lambda *a:None)
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:copy.deepcopy(jobs))
    base=snapshot(segment);candidate=copy.deepcopy(base)
    candidate['prompt']=candidate['prompt'].replace('甲说','中景；甲说')
    candidate['asset_manifest']=[scene_image(tmp_path)]
    candidate['prompt']=H3PromptReconciler().reconcile(candidate['prompt'],
        H3GenerationConfig.from_mapping(base),H3GenerationConfig.from_mapping(candidate))
    candidate['prompt']+='\n\n【人物音色绑定】\n旧候选的重复音色说明。\n【人物音色绑定结束】'
    candidate['editor_constraints']={'opening_continuity':{'enabled':True,'state':{'position':'门边'}}}
    result=commit({'segment_uid':'s','base_revision':fingerprint(base),'draft':candidate})
    saved=json.loads((tmp_path/'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    assert saved['active_render_id']=='old' and saved['video_path']=='old.mp4'
    assert saved['renders']==segment['renders'] and saved['runninghub_attempts']==segment['runninghub_attempts']
    assert saved['status']=='needs_regenerate'
    assert saved['generation_config']['asset_manifest']==candidate['asset_manifest']
    assert saved['job']['asset_manifest']==candidate['asset_manifest']
    assert '【人物音色绑定】' not in saved['generation_config']['prompt_override']
    assert saved['generation_config']['prompt_override']==saved['job']['final_prompt']==saved['compiled_prompt_snapshot']==result['draft']['prompt']
    assert json.loads((tmp_path/'beats.json').read_text(encoding='utf-8'))['beats'][0]['h3_editor_constraints']==candidate['editor_constraints']
    before=(tmp_path/'video_jobs.json').read_bytes()
    with pytest.raises(ValueError,match='后台内容已变化') as conflict:
        commit({'segment_uid':'s','base_revision':'obsolete','draft':candidate})
    assert conflict.value.code == 'CONTENT_CONFLICT'
    assert conflict.value.details['expected_revision'] == 'obsolete'
    assert (tmp_path/'video_jobs.json').read_bytes()==before


def test_current_selected_picture_is_not_replaced_by_previous(tmp_path):
    from generation.pipelines.minimax_h3.continuity.assets import _prepare_character_references
    current=image('current','甲');current['metadata']['identity_id']='same_identity'
    current['order']=1;current['metadata']['custom_selection']='current'
    previous=image('old_picture','甲');previous['metadata']['identity_id']='same_identity'
    previous['metadata']['role_id']=current['metadata']['role_id']
    base=draft();base['asset_manifest']=[current]
    prepared,added=_prepare_character_references(base,{'snapshot':{'asset_manifest':[previous]}})
    assert added==[] and len(prepared['asset_manifest'])==1
    assert prepared['asset_manifest'][0]['asset_id']=='current'
    assert prepared['asset_manifest'][0]['path']==current['path']
    assert prepared['asset_manifest']==base['asset_manifest']


def test_continuity_does_not_assign_shared_library_voice_to_other_role():
    from generation.pipelines.minimax_h3.continuity.assets import _prepare_voice_references
    actor=image('a','甲');actor['metadata']['identity_id']='shared'
    wrong={'asset_id':'wrong','type':'audio','path':'wrong.mp3',
           'metadata':{'identity_id':'shared','role_id':'b','voice_only':True}}
    base=draft();base['asset_manifest']=[actor]
    prepared,changed=_prepare_voice_references(base,{'snapshot':{'asset_manifest':[wrong]}})
    assert not changed and not any(a['type']=='audio' for a in prepared['asset_manifest'])


def test_remove_active_character_voice_cleans_references_and_saves(monkeypatch,tmp_path):
    import json
    from services import context
    from workflow import video_merge
    from generation.pipelines.minimax_h3.editor_draft import snapshot,preview,commit
    from generation.pipelines.minimax_h3.shot_editor import dialogue_units
    from generation.pipelines.minimax_h3.continuity.assets import _prepare_voice_references
    actor=image('a','甲');actor['metadata']['library_voice']={'path':'original_voice.mp3'}
    voice={'asset_id':'voice','type':'audio','path':'original_voice.mp3','role':'甲音色',
           'metadata':{'identity_id':'a','role_id':'a','voice_only':True,'binding_source':'character_voice'}}
    prompt=('【任务概述】\n[reference generation + audio reference] 4 秒。\n\n【详细镜头描述】\n'
            '[Shot 1]（0–4 秒）：画面人物：<Subject 1>；本句音色参考：<Audio 1>，仅用于<Subject 1>的声音；'
            '<Subject 1> (S1)说：<d>[Chinese]原文包括 Audio 1 和音色。</d>；'
            '声音：使用<Audio 1>绑定<Subject 1>音色；<Subject 1>画内对白。')
    segment={'segment_uid':'s','segment_index':1,'beat_uid':'b','title':'test',
             'generation_config':{'duration_sec':4,'asset_manifest':[actor,voice],'prompt_override':prompt},
             'job':{'duration_sec':4},'video_path':'old.mp4','renders':[{'render_id':'old','status':'success'}]}
    jobs={'segments':[segment]}
    (tmp_path/'video_jobs.json').write_text(json.dumps(jobs),encoding='utf-8')
    (tmp_path/'beats.json').write_text(json.dumps({'beats':[{'beat_uid':'b'}]}),encoding='utf-8')
    monkeypatch.setattr(context,'get_project_dir',lambda:tmp_path)
    monkeypatch.setattr(context,'assert_current_runtime_writable',lambda *a:None)
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:copy.deepcopy(jobs))
    base=snapshot(segment)
    out=preview({'segment_uid':'s','draft':base,'operation':'remove_reference','asset_id':'voice'})
    candidate=out['draft']
    assert '<Audio' not in candidate['prompt'] and 'audio reference' not in candidate['prompt']
    assert candidate['asset_manifest'][0]['metadata']['voice_disabled']
    unit=dialogue_units(out['document']['shots'][0])[0]
    assert unit['line']=='原文包括 Audio 1 和音色。' and unit['speaker']=='a' and 'voice_asset_id' not in unit
    commit({'segment_uid':'s','draft':candidate,'operation_base':out['operation_base'],'base_revision':out['base_revision']})
    saved=json.loads((tmp_path/'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    assert saved['video_path']=='old.mp4'
    assert not any(a['type']=='audio' for a in saved['generation_config']['asset_manifest'])
    assert saved['generation_config']['asset_manifest'][0]['metadata']['voice_disabled']
    # Later continuity must respect the user's explicit disabled setting.
    inherited,_=_prepare_voice_references(candidate,{'snapshot':{'asset_manifest':[actor,voice]}})
    assert not any(a['type']=='audio' for a in inherited['asset_manifest'])
    assert not base['asset_manifest'][0]['metadata'].get('voice_disabled')


def test_remove_voice_keeps_unknown_audio_dependencies_blocked(monkeypatch):
    from workflow import video_merge
    from generation.pipelines.minimax_h3.editor_draft import preview
    actor=image('a','甲')
    voice={'asset_id':'voice','type':'audio','path':'voice.mp3',
           'metadata':{'identity_id':'a','role_id':'a','voice_only':True}}
    base=draft();base['asset_manifest']=[actor,voice]
    base['prompt']=base['prompt'].replace('甲说','播放<Audio 1>完整录音；甲说')
    monkeypatch.setattr(video_merge,'load_video_jobs',lambda:{'segments':[{'segment_uid':'s'}]})
    with pytest.raises(ValueError,match='绑定失效'):
        preview({'segment_uid':'s','draft':base,'operation':'remove_reference','asset_id':'voice'})


@pytest.mark.parametrize('state', ['error','needs_attention','cancelled','running','ready'])
def test_continuity_result_exposes_failure_detail(monkeypatch,tmp_path,state):
    import asyncio,json
    from types import SimpleNamespace
    from fastapi import FastAPI
    from starlette.requests import Request
    from ui import api_routes
    from services import text_jobs
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda demo:None)
    monkeypatch.setattr(api_routes,'get_project_dir',lambda:tmp_path)
    task={'kind':'opening_continuity','source':str(tmp_path),'state':state,
          'phase':'失败' if state=='error' else '当前阶段','message':'具体冲突原因'}
    monkeypatch.setattr(text_jobs,'get',lambda identifier:task)
    app=FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    endpoint=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/segment/editor/{action}')
    async def receive():
        return {'type':'http.request','body':b'{"operation_id":"test"}'}
    request=Request({'type':'http','headers':[]},receive)
    response=asyncio.run(endpoint('result',request))
    result=json.loads(response.body)
    assert result['ok'] is True
    assert result['message']==('具体冲突原因' if state in ('error','needs_attention','cancelled') else '当前阶段')


def test_app_exports_complete_editor_bundle():
    from ui.scripts import APP_JS, APP_JS_FUNCTION
    from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
    assert APP_JS_FUNCTION == APP_JS
    assert EDITOR_DRAFT_JS in APP_JS_FUNCTION
    assert 'A.editH3Reference=' in APP_JS_FUNCTION
    assert 'A.saveH3EditorDraft=' in APP_JS_FUNCTION


def test_stopped_continuity_task_rejects_before_any_llm_call(monkeypatch):
    from services import llm
    from generation.pipelines.minimax_h3.continuity import build_opening_candidate
    calls=[]
    def unexpected_call(*args,**kwargs):
        calls.append((args,kwargs))
        raise AssertionError('Stopped tasks must not call the LLM')
    monkeypatch.setattr(llm,'call_llm',unexpected_call)
    payload={'draft':draft(),'source':{'snapshot':{}},'base_revision':'version'}
    before=copy.deepcopy(payload)
    with pytest.raises(ValueError,match='旧承接任务已停用，请使用场景状态机'):
        build_opening_candidate(payload)
    assert not calls
    assert payload==before
