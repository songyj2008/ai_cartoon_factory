import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import context, text_jobs, web_llm_bridge as bridge
from ui import api_routes


@pytest.fixture
def source(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'original_workflow.json').write_text('{"50":{"inputs":{"totalframe":192}}}', encoding='utf-8')
    (project / 'beats.json').write_text(json.dumps({'beats': [
        {'plot': '人物解释情况。', 'estimated_duration_sec': 8}
    ]}), encoding='utf-8')
    (project / 'video_jobs.json').write_text(json.dumps({'segments': [
        {'segment_index': 1, 'model_id': 'minimax_h3_local_ref2va', 'job': {'duration_sec': 8},
         'generation_config': {'duration_sec': 8}, 'workflow_path': str(project / 'original_workflow.json')}
    ]}), encoding='utf-8')
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr(api_routes, 'get_project_dir', lambda: project)
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *_: None)
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    monkeypatch.setattr('services.project_assets.sync_generation_assets', lambda: None)
    monkeypatch.setattr('services.project_bible.project_bible_path', lambda *args: project / 'bible.json')
    monkeypatch.setattr('services.ui_run_config.ui_run_config_path', lambda: project / 'ui.json')
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'asset_index.json')
    settings = {'default_providers': ['doubao'], 'default_provider': 'doubao',
                'overrides': {'prompt': ['deepseek']}, 'concurrency': {}}
    monkeypatch.setattr(bridge, 'get_settings', lambda: copy.deepcopy(settings))
    with context.pin_runtime((project, 'test', '')):
        yield project, settings


def test_replan_endpoint_captures_unsaved_page_options(source, monkeypatch):
    project, _ = source
    calls = []
    monkeypatch.setattr(text_jobs, 'submit', lambda *args: calls.append(args) or {'id': 'operation'})
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response = client.post('/api/segment/source-excerpt', json={
            'segment_index': 1, 'source_excerpt': '当前背景', 'regenerate_prompt': True,
            'duration_sec': 12, 'providers': ['chatgpt'], 'editor_prompt': '当前编辑稿', 'asset_manifest': [],
        })
    assert response.status_code == 200, response.text
    assert calls[0][0:2] == ('source_replan', [1, '当前背景', {
        'duration_sec': 12, 'providers': ['chatgpt'], 'timing_only': True, 'timing_api': False,
        'editor_prompt': '当前编辑稿', 'asset_manifest': [],
    }])
    assert json.loads((project / 'beats.json').read_text())['beats'][0]['estimated_duration_sec'] == 8


def test_api_only_timing_request_is_separate_from_web_provider_list(source, monkeypatch):
    calls = []
    monkeypatch.setattr(text_jobs, 'submit', lambda *args: calls.append(args) or {'id': 'operation'})
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response = client.post('/api/segment/source-excerpt', json={
            'segment_index': 1, 'regenerate_prompt': True, 'duration_sec': 8,
            'providers': [], 'timing_api': True, 'editor_prompt': '当前稿',
        })
    assert response.status_code == 200, response.text
    assert calls[0][1][2]['providers'] == []
    assert calls[0][1][2]['timing_api'] is True


@pytest.mark.parametrize('options', [{'providers':['api'], 'timing_api':True},
                                     {'providers':['deepseek'], 'timing_api':'true'}])
def test_timing_api_cannot_be_smuggled_into_web_provider_list(source, monkeypatch, options):
    monkeypatch.setattr(text_jobs, 'submit', lambda *a: pytest.fail('invalid options queued'))
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response = client.post('/api/segment/source-excerpt', json={
            'segment_index':1, 'regenerate_prompt':True, 'duration_sec':8, 'editor_prompt':'当前稿', **options})
    assert response.status_code == 400


@pytest.mark.parametrize('options', [
    {'duration_sec': 3, 'providers': ['chatgpt']},
    {'duration_sec': 16, 'providers': ['chatgpt']},
    {'duration_sec': 10.5, 'providers': ['chatgpt']},
    {'duration_sec': True, 'providers': ['chatgpt']},
    {'duration_sec': 12, 'providers': []},
    {'duration_sec': 12},
    {'providers': ['chatgpt']},
    {'duration_sec': 12, 'providers': ['chatgpt']},
])
def test_invalid_page_options_do_not_queue_work(source, monkeypatch, options):
    monkeypatch.setattr(text_jobs, 'submit', lambda *_: pytest.fail('invalid settings queued'))
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response = client.post('/api/segment/source-excerpt', json={
            'segment_index': 1, 'regenerate_prompt': True, **options,
        })
    assert response.status_code == 400
    assert 'must be numeric' not in response.text
    if 'duration_sec' not in options:
        assert 'Ctrl+F5' in response.json()['message']


def test_timing_prefers_chatgpt_after_resolving_global_defaults(source):
    _, settings = source
    settings['default_providers'] = ['doubao', 'chatgpt', 'deepseek']
    item = text_jobs.submit('source_replan', [1, '背景', {
        'duration_sec': 8, 'providers': ['inherit'], 'timing_only': True, 'editor_prompt': '当前稿',
    }], start=False)
    assert item['provider_candidates'] == ['chatgpt']
    assert item['routes']['overrides']['prompt'] == ['doubao', 'chatgpt', 'deepseek']
    settings['default_providers'] = ['doubao']
    token = text_jobs.ACTIVE.set(item)
    try:
        assert bridge.routes_for('prompt') == ['doubao', 'chatgpt', 'deepseek']
    finally:
        text_jobs.ACTIVE.reset(token)


@pytest.mark.parametrize('selected, enabled, first, order', [
    (['chatgpt', 'deepseek'], True, 'chatgpt', ['chatgpt', 'api', 'deepseek']),
    (['deepseek'], True, 'api', ['api', 'deepseek']),
    ([], True, 'api', ['api']),
    (['inherit'], True, 'api', ['api', 'doubao']),
    (['deepseek'], False, 'deepseek', ['deepseek']),
    (['chatgpt', 'deepseek'], False, 'chatgpt', ['chatgpt', 'deepseek']),
])
def test_timing_queue_reserves_first_channel_and_freezes_pro_policy(source, selected, enabled, first, order):
    item = text_jobs.submit('source_replan', [1, '', {
        'duration_sec': 8, 'providers': selected, 'timing_only': True, 'timing_api':enabled, 'editor_prompt': '当前稿',
    }], start=False)
    assert item['provider'] == first
    assert item['provider_candidates'] == [first]
    assert item['timing_policy'] == {'version': 'duration-pro-v2', 'model': 'deepseek-v4-pro', 'order': order}
    assert item['recovery_safe'] is True


def test_api_timing_receipts_survive_backend_recovery_without_duplicate_request(source, monkeypatch):
    from services import duration_planning_llm as timing, llm
    item = text_jobs.submit('source_replan', [1, '', {
        'duration_sec': 8, 'providers': ['deepseek'], 'timing_only': True, 'timing_api':True, 'editor_prompt': '当前稿',
    }], start=False)
    calls = []

    def api(*args):
        calls.append('api')
        return {'duration_sec': 9}, {'prompt_tokens': 100}

    monkeypatch.setattr(timing, '_api', api)
    monkeypatch.setattr(bridge, 'wait_for_llm', lambda *a, **k: pytest.fail('API succeeded'))
    text_jobs.update(item['id'], 'running')
    token = text_jobs.ACTIVE.set(item)
    try:
        assert llm.call_llm('JSON', 'timings', label='h3_prompt_retime_001')[0] == {'duration_sec': 9}
    finally:
        text_jobs.ACTIVE.reset(token)
    text_jobs.recover_interrupted()
    recovered = text_jobs.get(item['id'])
    assert recovered['state'] == 'queued'
    assert recovered['timing_result'] == {'provider': 'api', 'model': 'deepseek-v4-pro'}
    token = text_jobs.ACTIVE.set(recovered)
    try:
        assert llm.call_llm('JSON', 'timings', label='h3_prompt_retime_001')[0] == {'duration_sec': 9}
    finally:
        text_jobs.ACTIVE.reset(token)
    assert calls == ['api']


def test_timing_checkbox_never_changes_other_page_operation_routing(source):
    item = text_jobs.submit('source_replan', [1, '背景', {
        'duration_sec':8, 'providers':['deepseek'], 'timing_api':True, 'auto_duration':True,
    }], start=False)
    assert item['timing_policy'] is None
    assert item['provider_candidates'] == ['deepseek']
    with pytest.raises(ValueError, match='仅支持网页'):
        bridge.resolve_page_providers(['api'])


def test_timing_checkbox_is_read_independently_of_web_providers(tmp_path):
    from ui.scripts import APP_JS
    node = shutil.which('node')
    if not node: pytest.skip('Node.js required')
    functions = 'window.AICF.readPageLLMProviders = function(ov) {' + APP_JS.split(
        'window.AICF.readPageLLMProviders = function(ov) {',1)[1].split(
        'window.AICF.savePromptBeforeTask =',1)[0]
    program = r'''
const assert=require('node:assert/strict');
global.window={AICF:{}};
let web=['deepseek'];const api={checked:true,disabled:false};
const ov={querySelector:s=>s==='.review-duration'?{value:'8'}:s==='.review-timing-api'?api:null,
querySelectorAll:s=>{assert.equal(s,'.review-provider-options input:checked');return web.map(value=>({value,disabled:false}));}};
''' + functions + r'''
const A=window.AICF;
assert.deepEqual(A.readPageLLMProviders(ov),['deepseek']);
assert.deepEqual(A.readPromptReplanOptions(ov),{duration_sec:8,providers:['deepseek'],timing_api:true});
api.checked=false;assert.equal(A.readPromptReplanOptions(ov).timing_api,false);
api.checked=true;web=[];
assert.deepEqual(A.readPromptReplanOptions(ov),{duration_sec:8,providers:[],timing_api:true});
assert.throws(()=>A.readPageLLMProviders(ov),/勾选/);
api.checked=false;assert.throws(()=>A.readPromptReplanOptions(ov),/勾选/);
'''
    path = tmp_path/'timing_checkbox.cjs'
    path.write_text(program,encoding='utf-8')
    result=subprocess.run([node,str(path)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.parametrize('selected, expected', [(['chatgpt'], ['chatgpt']),
                                               (['inherit'], ['doubao']),
                                               (['chatgpt', 'deepseek'], ['chatgpt']),
                                               (['doubao', 'chatgpt'], ['chatgpt']),
                                               (['deepseek', 'doubao'], ['deepseek', 'doubao'])])
@pytest.mark.parametrize('saved_job_duration', [8, 12])
def test_page_routing_is_frozen_and_duration_reaches_regeneration(source, monkeypatch, selected, expected, saved_job_duration):
    from services import text_job_operations
    project, settings = source
    jobs_path = project / 'video_jobs.json'
    jobs = json.loads(jobs_path.read_text(encoding='utf-8'))
    jobs['segments'][0]['job']['duration_sec'] = saved_job_duration
    jobs_path.write_text(json.dumps(jobs), encoding='utf-8')
    item = text_jobs.submit('source_replan', [1, '背景', {
        'duration_sec': 12, 'providers': selected, 'auto_duration': True, 'editor_prompt': '编辑区新人物的新台词',
    }], start=False)
    assert item['provider_candidates'] == expected
    assert item['routes']['overrides']['prompt'] == expected
    settings['default_providers'] = ['deepseek']
    settings['overrides']['prompt'] = ['deepseek']
    original_workflow = (project / 'original_workflow.json').read_bytes()
    observed = []

    class Service:
        def update_segment_duration(self, index, duration, **kwargs):
            from generation.service import GenerationService
            return GenerationService.update_segment_duration.__wrapped__(
                GenerationService(), index, duration, **kwargs)

        def regenerate_segment_prompt(self, beats, index):
            observed.append((beats['beats'][index - 1]['estimated_duration_sec'],
                             bridge.routes_for('prompt'), beats['beats'][index - 1]['source_excerpt']))
            path = context.get_project_dir() / 'video_jobs.json'
            jobs = json.loads(path.read_text(encoding='utf-8'))
            segment = jobs['segments'][0]
            assert segment.pop('h3_auto_duration') is True
            assert segment.pop('h3_editor_prompt') == '编辑区新人物的新台词'
            segment['job']['duration_sec'] = 11
            segment['generation_config']['duration_sec'] = 11
            segment['h3_duration_adjustment'] = {'duration_sec': 11, 'reason': '按对白自然完成'}
            path.write_text(json.dumps(jobs), encoding='utf-8')

    monkeypatch.setattr('generation.service.get_generation_service', lambda: Service())
    token = text_jobs.ACTIVE.set(item)
    try:
        with context.pin_runtime((Path(item['work']), item['project'], item['episode'])):
            result = text_job_operations.execute('source_replan', item['args'])
    finally:
        text_jobs.ACTIVE.reset(token)
    assert observed == [(12, expected, '背景')]
    assert json.loads((project / 'beats.json').read_text())['beats'][0]['estimated_duration_sec'] == 8
    candidate_jobs = json.loads((Path(item['work']) / 'video_jobs.json').read_text(encoding='utf-8'))
    assert candidate_jobs['segments'][0]['generation_config']['duration_sec'] == 11
    candidate_beats = json.loads((Path(item['work']) / 'beats.json').read_text(encoding='utf-8'))
    assert candidate_beats['beats'][0]['estimated_duration_sec'] == 11
    assert result['duration_sec'] == 11 and result['duration_reason'] == '按对白自然完成'
    assert (project / 'original_workflow.json').read_bytes() == original_workflow


def test_page_options_are_read_at_click_time_in_javascript():
    from ui.scripts import APP_JS
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    reader = APP_JS.split('window.AICF.readPromptReplanOptions = function(ov)', 1)[1].split(
        'window.AICF.savePromptSourceExcerpt', 1)[0]
    save = APP_JS.split('window.AICF.savePromptSourceExcerpt = async function(replan, submittedOptions)', 1)[1].split(
        'window.AICF.replanPromptModal', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const duration={value:'9'},picker={providers:['chatgpt']},field={value:'背景',dataset:{savedValue:'背景'}};
const ov={dataset:{segmentIndex:'1'},querySelector:selector=>selector==='.review-duration'?duration:field,
querySelectorAll:()=>picker.providers.map(value=>({value,disabled:false})),
_promptReplanOptions:()=>({duration_sec:11,providers:['doubao']})};
const requests=[];
const window={AICF:{archiveReadOnly:false,setPromptModalFeedback(){},segmentCardFromButton(){return null;}}};
const document={getElementById:()=>ov,querySelector:()=>null};
const fetch=async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true,operation_id:'job'})};};
const _h3Manifest=()=>[];
window.AICF.readPromptReplanOptions = function(ov)
''' + reader + r'''
window.AICF.savePromptSourceExcerpt = async function(replan, submittedOptions)
''' + save + r'''
(async()=>{
const first=window.AICF.savePromptSourceExcerpt(true);field.value='提交期间继续编辑';await first;
assert.equal(requests[0].duration_sec,9);assert.deepEqual(requests[0].providers,['chatgpt']);
assert.equal(requests[0].editor_prompt,'背景');assert.deepEqual(requests[0].asset_manifest,[]);
assert.equal(ov._sourceReplanPending.prompt,'背景');
assert.equal(field.dataset.savedValue,'背景');
duration.value='14';picker.providers=['deepseek'];
await window.AICF.savePromptSourceExcerpt(true);
assert.equal(requests[1].duration_sec,14);assert.deepEqual(requests[1].providers,['deepseek']);
assert.equal(field.dataset.savedValue,'背景');
duration.value='20';await window.AICF.savePromptSourceExcerpt(true);
assert.equal(requests[2].duration_sec,20);
duration.value='0';await assert.rejects(window.AICF.savePromptSourceExcerpt(true),/正数/);
assert.equal(requests.length,3);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_auto_duration_result_updates_open_modal_without_overwriting_user_edits():
    from ui.web_llm_ui import WEB_LLM_JS
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    body = WEB_LLM_JS.split('    async function syncSourceReplan()', 1)[1].split('    async function poll()', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const session='1',index=1,valid=()=>true;
let snapshot={runtime:'runtime',segment_uid:'segment'},state='applied',autoApply=true,applyError=null;
const ov={},editor={value:'before'},duration={value:'9'};
const q=()=>duration,saveDraft=()=>{},loadSnapshot=async()=>{};
let synced=0,baselines=0,referenceSignature='saved-refs';
const A={setPromptModalFeedback(){},syncReplannedDuration(ov,value){synced++;assert.equal(value.duration_sec,12);},
 readH3Manifest:()=>referenceSignature,h3ManifestSignature:value=>value,
 async refreshH3EditorBaseline(){baselines++;}};
let api=async path=>path.startsWith('/operations/')?{result:{state,auto_apply_pending:autoApply,apply_error:applyError,candidate:{duration_reason:'对白自然说完'}}}:
{snapshot:{runtime:'runtime',segment_uid:'segment',duration_sec:12,current_prompt:'generated'}};
async function syncSourceReplan()
''' + body + r'''
(async()=>{
ov._sourceReplanPending={id:'job',session:'1',duration:'9',prompt:'before'};
state='ready';await syncSourceReplan();
assert.equal(ov._sourceReplanPending.id,'job');assert.equal(synced,0);
assert.equal(editor.value,'before');assert.equal(duration.value,'9');
state='applied';
await syncSourceReplan();assert.equal(duration.value,12);assert.equal(editor.value,'generated');
assert.equal(synced,1);
assert.equal(baselines,1);
assert.equal(ov._sourceReplanPending,undefined);
ov._sourceReplanPending={id:'job2',session:'1',duration:'9',prompt:'before'};
editor.value='user edits';duration.value='13';await syncSourceReplan();
assert.equal(editor.value,'user edits');assert.equal(duration.value,'13');
ov._sourceReplanPending={id:'new-refs',session:'1',duration:'9',prompt:'before',manifest:'saved-refs'};
editor.value='before';referenceSignature='new-refs';await syncSourceReplan();
assert.equal(editor.value,'before','A planning result cannot overwrite text paired with new references');
editor.value='user edits';
state='error';ov._sourceReplanPending={id:'failed',session:'1'};await syncSourceReplan();
assert.equal(ov._sourceReplanPending,undefined);assert.equal(editor.value,'user edits');
state='ready';autoApply=false;applyError='原稿已变化';
ov._sourceReplanPending={id:'conflict',session:'1'};await syncSourceReplan();
assert.equal(ov._sourceReplanPending,undefined);assert.equal(editor.value,'user edits');
ov._sourceReplanPending={id:'missing',session:'1'};
api=async()=>{throw Object.assign(new Error('任务不存在'),{code:'OPERATION_NOT_FOUND'});};
await syncSourceReplan();assert.equal(ov._sourceReplanPending,undefined);
assert.equal(editor.value,'user edits');assert.equal(duration.value,'13');
ov._sourceReplanPending={id:'network',session:'1'};
api=async()=>{throw new Error('network failed');};
await assert.rejects(syncSourceReplan(),/network failed/);
assert.equal(ov._sourceReplanPending.id,'network');
// A late response for the deleted task must not release a newer operation.
let fail;
api=()=>new Promise((resolve,reject)=>{fail=reject;});
const waiting=syncSourceReplan();
ov._sourceReplanPending={id:'new',session:'1'};
fail(Object.assign(new Error('任务不存在'),{code:'OPERATION_NOT_FOUND'}));
await assert.rejects(waiting);assert.equal(ov._sourceReplanPending.id,'new');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('invalid_result', [False, True])
@pytest.mark.parametrize('planned_duration, shot_boundary', [(11, 4), (6, 2), (5.1, 2), (18, 6)])
@pytest.mark.parametrize('with_voice', [False, True])
def test_timing_only_operation_saves_custom_prompt_and_only_target_beat(
    source, monkeypatch, invalid_result, planned_duration, shot_boundary, with_voice,
):
    from generation.job_schema import normalize_video_jobs_payload
    from generation.pipelines.minimax_h3 import prompt_retimer
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
    from generation.service import GenerationService

    project, settings = source
    original_prompt = ('【任务概述】\n[reference generation] 8 秒，人物克制地解释。\n'
                       '【详细镜头描述】\n[Shot 1] (0-3 秒)：抬眼说话。\n'
                       '<Subject 1>说：<d>[Chinese]编辑区的新台词，再等3秒。</d>\n'
                       '[Shot 2] At 00:03.000 (3-8 秒)：听者点头，原说话人继续。')
    if with_voice:
        # Retiming preserves a saved binding; it must not invent a new voice
        # section that was absent from the authoritative editor screenplay.
        original_prompt='【人物音色绑定】\n<Audio 1> → <Subject 1>：音色参考。\n\n'+original_prompt
    expected_prompt = original_prompt.replace('8 秒，', f'{planned_duration} 秒，').replace(
        '(0-3 秒)', f'(0-{shot_boundary} 秒)').replace(
        'At 00:03.000 (3-8 秒)', f'At 00:0{shot_boundary}.000 ({shot_boundary}-{planned_duration} 秒)')
    if planned_duration == 5.1:
        expected_prompt = expected_prompt.replace('5.1', '5.100')
    beats = {'target_duration_sec': 60, 'beats': [
        {'id': 1, 'beat_uid': 'beat-1', 'plot': '旧人物说已经删掉的台词。',
         'estimated_duration_sec': 8, 'source_excerpt': '原有上下文'},
        {'id': 2, 'beat_uid': 'beat-2', 'plot': '其他段保持原样。', 'estimated_duration_sec': 6},
    ]}
    jobs = json.loads((project / 'video_jobs.json').read_text(encoding='utf-8'))
    jobs['segments'][0].update({'beat_uid': 'beat-1', 'h3_prompt_plan': {'shots': ['旧计划']},
                               'source_excerpt': '原有上下文'})
    jobs['segments'][0]['job']['final_prompt'] = original_prompt
    # The preserved Subject 1 must have an actual saved image binding. The
    # unsaved new image supplied below is intentionally ignored by retiming.
    jobs['segments'][0]['generation_config']['asset_manifest'] = [
        {'asset_id': 'saved-person', 'type': 'image', 'path': 'saved-person.png',
         'role': '人物', 'metadata': {'asset_kind': 'character'}}]
    if with_voice:
        manifest = jobs['segments'][0]['generation_config']['asset_manifest']
        manifest[0]['metadata']['identity_id'] = 'speaker-identity'
        manifest.append({'asset_id': 'voice', 'type': 'audio', 'path': 'voice.wav',
                         'metadata': {'identity_id': 'speaker-identity', 'voice_only': True,
                                      'duration_sec': 3, 'voice_speaker': '人物'}})
    jobs['segments'].append({'segment_index': 2, 'beat_uid': 'beat-2', 'model_id': 'minimax_h3_local_ref2va',
                             'job': {'duration_sec': 5, 'final_prompt': '其他段提示词'},
                             'generation_config': {'duration_sec': 5}})
    jobs = normalize_video_jobs_payload(jobs)
    (project / 'beats.json').write_text(json.dumps(beats), encoding='utf-8')
    (project / 'video_jobs.json').write_text(json.dumps(jobs), encoding='utf-8')
    original_beats = (project / 'beats.json').read_bytes()
    original_jobs = (project / 'video_jobs.json').read_bytes()
    workflow_before = (project / 'original_workflow.json').read_bytes()
    observed = []

    def model(system, user, **kwargs):
        payload = json.loads(user)
        observed.append(bridge.routes_for('prompt'))
        assert '编辑区的新台词' in payload['prompt_with_time_slots']
        assert '旧人物' not in user and '已经删掉' not in user
        if invalid_result:
            return {'status': 'needs_replan', 'reason': '保留全文需要拆段'}, 0
        return {'duration_sec': planned_duration, 'duration_reason': '按新台词自然完成，动作并行。',
                'timings': {slot['id']: {0: 0, 3: shot_boundary, 8: planned_duration}[slot['seconds']]
                            for slot in payload['time_slots']}}, 0

    monkeypatch.setattr(prompt_retimer, '_call_model', model)
    monkeypatch.setattr(GenerationService, 'prepare_segments', lambda *a, **k: pytest.fail('retiming must not build a disposable workflow'))
    monkeypatch.setattr(GenerationService, 'regenerate_segment_prompt', lambda *a, **k: pytest.fail('retiming must not recreate Shots'))
    item = text_jobs.submit('source_replan', [1, '此按钮不保存其他编辑内容', {
        'duration_sec': 12, 'providers': ['chatgpt'], 'timing_only': True,
        'editor_prompt': original_prompt, 'asset_manifest': [{'type': 'image', 'path': '未保存的新素材.png'}],
    }], start=False)
    settings['default_providers'] = ['deepseek']
    settings['overrides']['prompt'] = ['deepseek']
    text_jobs._execute(item)
    operation = text_jobs.get(item['id'])
    assert observed == [['chatgpt']]
    assert (project / 'original_workflow.json').read_bytes() == workflow_before
    if invalid_result:
        assert operation['state'] == 'error'
        assert (project / 'beats.json').read_bytes() == original_beats
        assert (project / 'video_jobs.json').read_bytes() == original_jobs
        return
    assert operation['state'] == 'applied', operation.get('message') or operation
    updated_beats = json.loads((project / 'beats.json').read_text(encoding='utf-8'))
    updated_jobs = json.loads((project / 'video_jobs.json').read_text(encoding='utf-8'))
    target = updated_jobs['segments'][0]
    expected_beat = {**beats['beats'][0], 'estimated_duration_sec': planned_duration,
                     'plot': target['generation_config']['prompt_override'], 'dialogue_authoritative': True}
    assert all(updated_beats['beats'][0][key] == value for key, value in expected_beat.items())
    assert updated_beats['beats'][0]['dialogue_units'][0]['line'] == '编辑区的新台词，再等3秒。'
    assert (target['generation_config']['asset_manifest'][0].get('asset_id') or target['generation_config']['asset_manifest'][0].get('id')) == 'saved-person'
    assert updated_beats['beats'][1] == beats['beats'][1]
    assert updated_jobs['segments'][1] == jobs['segments'][1]
    assert updated_beats['total_duration_sec'] == planned_duration + 6
    assert updated_beats['target_duration_sec'] == 60
    assert updated_jobs['total_duration_sec'] == planned_duration + 5  # Preserve the other segment's existing job.
    assert target['generation_config']['duration_sec'] == target['job']['duration_sec'] == planned_duration
    saved_prompt = target['generation_config']['prompt_override']
    assert saved_prompt == target['job']['final_prompt'] == target['compiled_prompt_snapshot'] == target['h3_editor_prompt']
    if with_voice:
        assert '<Audio 1> → <Subject 1>' in saved_prompt
        assert saved_prompt.count('【人物音色绑定】') == 1
        assert saved_prompt.split('【详细镜头描述】', 1)[1] == expected_prompt.split('【详细镜头描述】', 1)[1]
    else:
        assert saved_prompt == expected_prompt
    assert target['generation_config']['prompt_version_info']['source'] == 'duration_replan'
    assert target['status'] == 'needs_regenerate'
    if planned_duration == 5.1 or planned_duration > 15:
        assert target['planning_state'] == 'warning'
        assert any(i['code'] in {'duration_not_integer', 'duration_out_of_range'}
                   and i['blocking'] for i in target['planning_issues'])
    assert 'workflow_path' not in target and 'h3_prompt_plan' not in target
    assert target['job']['aligned_frame_count'] == MiniMaxH3Ref2VAPipeline._aligned_frame_count(planned_duration)
    assert target['requested_media_spec']['duration_sec'] == planned_duration
    assert not (Path(item['work']) / 'workflows').exists()
    snapshot = bridge.segment_snapshot(1)
    assert snapshot['duration_sec'] == snapshot['beat_duration_sec'] == planned_duration
    assert snapshot['beat_index'] == 1 and snapshot['duration_source'] == 'duration_replan'


def test_retime_button_label_and_loading_state():
    from ui.scripts import APP_JS
    assert '保存上下文并重新规划' not in APP_JS
    assert '>保存并重新规划时长</button>' in APP_JS
    assert "button.textContent='正在保存并规划…'" in APP_JS
    assert "button.textContent='保存并重新规划时长'" in APP_JS


@pytest.mark.parametrize('brackets', [('【', '】'), ('[', ']')])
def test_retime_syncs_visible_beat_card_and_ignores_stale_duration_drafts(brackets):
    from ui.web_llm_ui import WEB_LLM_JS
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    functions = WEB_LLM_JS.split('  A.reviewDraftDuration = function', 1)[1].split(
        '  A.mountPromptRevision', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const brackets=BRACKETS;
let inputs=0,referenceRenders=0;
const beats={value:brackets[0]+'Beat 1'+brackets[1]+' 其他段\r\n时长：14秒\r\n其他台词。\r\n\r\n'+
brackets[0]+'Beat 2'+brackets[1]+' 当前段\r\n时长： 13 秒\r\n保留台词中的13秒，以及未保存的剧情修改。',
dispatchEvent(event){assert.equal(event.type,'input');inputs++;}};
const before=beats.value;
const durationText={textContent:'12 秒'}, otherDuration={textContent:'14 秒'};
const metric={querySelector:s=>s==='span'?{textContent:'时长'}:durationText};
const otherMetric={querySelector:s=>s==='span'?{textContent:'时长'}:otherDuration};
const pill={textContent:'当前页时长：26 秒'};
const prompt={value:'旧提示词'},preview={textContent:'旧提示词'};
const manifest=[{id:'person',type:'image',path:'person.png'}];
const modalList={dataset:{manifest:JSON.stringify(manifest),baseRevision:'before'}};
const cardSource={dataset:{}},templateSource={dataset:{}};
const ov={querySelector:()=>modalList};
const panel={querySelectorAll:s=>s==='.metric-card'?[metric,otherMetric]:[pill]};
const card={querySelectorAll:()=>[metric],closest:()=>panel,
querySelector:s=>s==='.h3-prompt-override'?prompt:s==='.h3-card-config-source'?cardSource:
s==='.h3-fullscreen-config-template'?{content:{querySelector:()=>templateSource}}:preview};
const document={querySelector:()=>beats};
const A={segmentCardFromButton:()=>card,refreshH3AssetCount:root=>{assert.equal(root,ov);referenceRenders++;}};
A.reviewDraftDuration = function
''' + functions + r'''
const fresh={duration_sec:9,beat_duration_sec:9,beat_index:2,segment_index:2,current_prompt:'新9秒提示词',
reference_revision:'planned-version',reference_manifest:manifest,segment_uid:'segment-2'};
A.syncReplannedDuration(ov,fresh);
assert.equal(referenceRenders,1);
assert.equal(beats.value,before.replace('时长： 13 秒','时长： 9 秒'));
assert.equal(durationText.textContent,'9 秒');assert.equal(otherDuration.textContent,'14 秒');
assert.equal(prompt.value,'新9秒提示词');assert.equal(preview.textContent,'新9秒提示词');
assert.equal(pill.textContent,'当前页时长：23 秒');assert.equal(inputs,1);
for(const source of [modalList,cardSource,templateSource]) {
  assert.equal(source.dataset.baseRevision,'planned-version');
  assert.equal(source.dataset.segmentUid,'segment-2');
  assert.deepEqual(JSON.parse(source.dataset.manifest),manifest);
}
A.syncReplannedDuration(ov,fresh);assert.equal(inputs,1);
// Do not advance the modal baseline over locally changed references.
modalList.dataset.manifest=JSON.stringify([{id:'new-local-reference'}]);
A.syncReplannedDuration(ov,{...fresh,reference_revision:'later-version'});
assert.equal(modalList.dataset.baseRevision,'planned-version');
assert.deepEqual(JSON.parse(modalList.dataset.manifest),[{id:'new-local-reference'}]);
assert.equal(cardSource.dataset.baseRevision,'later-version');
assert.equal(A.reviewDraftDuration({duration:'12'},fresh),9);
assert.equal(A.reviewDraftDuration({duration:'12',durationBase:13},fresh),9);
assert.equal(A.reviewDraftDuration({duration:'12',durationBase:9},fresh),'12');
assert.equal(A.reviewDraftDuration(null,fresh),9);
'''
    program = program.replace('BRACKETS', json.dumps(brackets))
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "?? A.reviewDraftDuration(saved,next)" in WEB_LLM_JS
    assert "durationBase:snapshot.duration_sec" in WEB_LLM_JS
