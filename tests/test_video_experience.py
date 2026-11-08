import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import context, text_jobs, video_experience as learning, web_llm_bridge as bridge


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'episode'
    root.mkdir()
    video = root / 'approved.mp4'
    video.write_bytes(b'approved-video-bytes')
    prompt = '[Shot 1] (0-8 秒)：<Subject 1>说：<d>[Chinese]原视频的台词。</d>'
    jobs = {'segments':[{'segment_index':1,'segment_uid':'segment-1','model_id':learning.MODEL_ID,
        'video_ok':True,'active_render_id':'render-1', 'job':{'duration_sec':8,'final_prompt':'编辑区后来改过的台词'},
        'generation_config':{'prompt_override':'当前编辑稿，不应分析它'},
        'renders':[{'render_id':'render-1','status':'success','model':{'id':learning.MODEL_ID},
                    'prompt_snapshot':prompt,'asset_manifest':[],
                    'output':{'video_path':str(video),'actual_media_spec':{'duration_sec':8}}}]}]}
    (root / 'video_jobs.json').write_text(json.dumps(jobs),encoding='utf-8')
    monkeypatch.setattr(learning, 'library_path', lambda: tmp_path / 'persistent' / 'experience.sqlite3')
    monkeypatch.setattr(bridge, 'BRIDGE_DIR', tmp_path / 'bridge')
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr('services.project_bible.project_bible_path', lambda *args, **kwargs: root / 'bible.json')
    monkeypatch.setattr('services.ui_run_config.ui_run_config_path', lambda *args, **kwargs: root / 'ui.json')
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'asset_index.json')
    bridge.save_settings({'default_provider':'deepseek','overrides':{'video_learning':['chatgpt']}})
    with context.pin_runtime((root, 'learning-test', '001')):
        yield root, jobs


def analysis(sample):
    return {'sample_id':sample['sample_id'], 'analysis_basis':'user_approved_prompt',
            'timing_analysis':['台词和动作并行，按提示词时间分配。'],
            'transition_analysis':['跨镜续说保持人物归属。'],
            'speech_control_analysis':['明确指定台词和说话人可能有帮助。'],
            'reusable_lessons':['动作辅助对白。'],
            'limitations':['仅分析提示词，没有核验视频或音轨。'], 'summary':'用户认可样本的候选经验。'}


def test_ok_queues_independent_web_route_and_uses_render_snapshot(env):
    root, jobs = env
    first = learning.on_video_ok(1,jobs,True)
    second = learning.on_video_ok(1,jobs,True)
    assert first['operation_id'] == second['operation_id']
    task = text_jobs.get(first['operation_id'])
    assert task['kind'] == task['entry'] == 'video_learning'
    assert task['provider_candidates'] == ['chatgpt']
    row, sample = learning.get_sample(task['args'][1],task['args'][2])
    assert sample['generated_prompt'] == jobs['segments'][0]['renders'][0]['prompt_snapshot']
    assert '当前编辑稿' not in json.dumps(sample,ensure_ascii=False)
    assert sample['actual_duration_sec'] == 8 and sample['render_id'] == 'render-1'
    assert Path(row['source']) == root


def test_analysis_persists_without_modifying_runtime_or_generating_video(env, monkeypatch):
    root, jobs = env
    before = (root / 'video_jobs.json').read_bytes()
    queued = learning.on_video_ok(1,jobs,True)
    item = text_jobs.get(queued['operation_id'])
    def reply(system,user,**kwargs):
        sample = json.loads(user)
        assert '原视频的台词' in user and '编辑区后来' not in user
        assert kwargs['provider'] == ['chatgpt']
        assert 'attachments' not in kwargs.get('metadata', {})
        assert 'generated_prompt' not in sample and 'asset_manifest' not in sample
        assert set(sample) == {'sample_id','project','episode','segment_index','segment_uid','render_id','model_id',
                               'actual_duration_sec','subject_definitions','shot_descriptions',
                               'expected_dialogue','human_review'}
        assert '原视频的台词' in sample['shot_descriptions']
        result = analysis(sample)
        return result,0
    monkeypatch.setattr(bridge,'wait_for_llm',reply)
    text_jobs._execute(item)
    completed = text_jobs.get(item['id'])
    assert completed['state'] == 'applied', completed
    assert (root / 'video_jobs.json').read_bytes() == before
    assert learning.retrieve([]) == []  # No task/owner match: do not transmit a same-project example.
    with learning.database() as db:
        row = db.execute('SELECT analysis FROM samples WHERE active=1').fetchone()
        assert json.loads(row['analysis'])['timing_analysis']
    text_jobs.delete(item['id'])
    with learning.database() as db:
        assert db.execute('SELECT COUNT(*) FROM samples WHERE active=1').fetchone()[0] == 1
    learning.on_video_ok(1,jobs,False)
    assert learning.retrieve([]) == []


def test_cancel_approval_disables_pending_sample(env):
    _, jobs = env
    task = text_jobs.get(learning.on_video_ok(1,jobs,True)['operation_id'])
    learning.on_video_ok(1,jobs,False)
    assert text_jobs.get(task['id'])['state'] == 'cancelled'
    with pytest.raises(ValueError,match='取消'):
        learning.get_sample(task['args'][1],task['args'][2])
    next_task = text_jobs.get(learning.on_video_ok(1,jobs,True)['operation_id'])
    assert next_task['id'] != task['id'] and next_task['args'][2] != task['args'][2]


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(sample_id='other'),
    lambda r: r.update(analysis_basis='video_observed'),
    lambda r: r.update(timing_analysis='invalid'),
    lambda r: r.update(limitations=[]),
    lambda r: r.update(summary=''),
])
def test_invalid_text_analysis_is_rejected(env, mutate):
    _, jobs = env
    sample = learning.snapshot(1, jobs)
    result = analysis(sample)
    mutate(result)
    with pytest.raises(ValueError):
        learning.validate_analysis(result, sample)


def test_only_requested_sections_are_extracted():
    prompt = '【主体定义】Alice\n【任务概述】不要提交\n【参考保留关系】不要提交\n【详细镜头描述】[Shot 1] 说台词\n【整体声音环境】不要提交'
    assert learning.prompt_sections(prompt) == {'subject_definitions':'Alice','shot_descriptions':'[Shot 1] 说台词'}


def test_independent_web_only_configuration_and_plain_text_extension(env):
    assert 'video_learning' in bridge.ENTRIES
    with pytest.raises(ValueError,match='网页'):
        bridge.save_settings({'overrides':{'video_learning':['api']}})
    task = bridge.create_task('chatgpt','json',{'system':'s','user':'u'},entry='video_learning')
    assert bridge.claim('plain-text-extension',['chatgpt'],tab_id=1)['id'] == task['id']


def test_retimer_receives_only_scoped_experiences(env,monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_retimer
    example={'sample_id':'historical','facts':['原句没有重复'],'hypotheses':[]}
    monkeypatch.setattr(learning,'retrieve',lambda manifest, **kwargs:[example])
    captured=[]
    def model(system,user,**kwargs):
        payload=json.loads(user);captured.append(payload)
        assert '不能照搬历史剧情' in system
        return {'duration_sec':8,'duration_reason':'参考已核验案例，按当前台词安排。',
                'timings':{slot['id']:slot['seconds'] for slot in payload['time_slots']}},0
    monkeypatch.setattr(prompt_retimer,'_call_model',model)
    prompt='【任务概述】\n8 秒\n【详细镜头描述】\n[Shot 1] (0-8 秒)：<d>[Chinese]当前台词。</d>'
    assert prompt_retimer.retime_h3_prompt(prompt,1,reference_duration_sec=8)['prompt'] == prompt
    assert captured[0]['approved_prompt_experiences'] == [example]


def test_text_learning_uses_normal_queue_and_stays_low_priority(env):
    _, jobs = env
    queued = learning.on_video_ok(1,jobs,True)
    other = text_jobs.submit('feedback',[[],[],'test'],start=False)
    assert text_jobs._claim_runnable({})['id'] == other['id']
    assert text_jobs._claim_runnable({},active_learning=1) is None
    assert text_jobs._claim_runnable({})['id'] == queued['operation_id']


def test_cancelled_approval_cannot_accept_a_late_analysis(env,monkeypatch):
    root,jobs=env
    queued=learning.on_video_ok(1,jobs,True)
    task=text_jobs.get(queued['operation_id'])
    def reply(system,user,**kwargs):
        sample=json.loads(user)
        with context.pin_runtime((root,'learning-test','001')):
            learning.on_video_ok(1,jobs,False)
        return analysis(sample),0
    monkeypatch.setattr(bridge,'wait_for_llm',reply)
    text_jobs._execute(task)
    assert learning.retrieve([]) == []
    with learning.database() as db:
        row=db.execute('SELECT active,analysis FROM samples WHERE id=?',(task['args'][1],)).fetchone()
    assert row['active'] == 0 and row['analysis'] is None


def test_approved_video_endpoint_keeps_ok_when_learning_fails(env,monkeypatch):
    root,jobs=env
    from ui import api_routes
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(api_routes,'ensure_gradio_app_runtime',lambda *_:None)
    monkeypatch.setattr(api_routes,'assert_current_runtime_writable',lambda *_:None)
    monkeypatch.setattr('workflow.segment_runner.set_segment_video_ok',lambda index,approved:calls.append((index,approved)) or jobs)
    monkeypatch.setattr(learning,'on_video_ok',lambda *args:(_ for _ in ()).throw(ValueError('网页不支持附件')))
    app=FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response=client.post('/api/segment/video_ok',json={'segment_index':1,'video_ok':True})
    assert response.status_code == 200, response.text
    assert calls == [(1,True)]
    assert response.json()['learning']['state'] == 'error'
    assert 'OK 标记已保存' in response.json()['learning']['message']


def test_archived_episode_can_approve_learn_and_cancel_but_not_submit_edits(env, monkeypatch):
    _, jobs = env
    from ui import api_routes
    from types import SimpleNamespace

    def readonly(*args):
        raise PermissionError('已归档的集数只能查看')

    monkeypatch.setattr(context, 'assert_current_runtime_writable', readonly)
    monkeypatch.setattr(api_routes, 'assert_current_runtime_writable', readonly)
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *_: None)
    calls = []
    monkeypatch.setattr('workflow.segment_runner.set_segment_video_ok',
                        lambda index, approved: calls.append((index, approved)) or jobs)
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    with TestClient(app) as client:
        response = client.post('/api/segment/video_ok', json={'segment_index': 1, 'video_ok': True})
        assert response.status_code == 200, response.text
        operation_id = response.json()['learning']['operation_id']
        assert text_jobs.get(operation_id)['kind'] == 'video_learning'
        cancelled = client.post('/api/segment/video_ok', json={'segment_index': 1, 'video_ok': False})
        assert cancelled.status_code == 200, cancelled.text
    assert calls == [(1, True), (1, False)]
    assert learning.retrieve([]) == []
    with pytest.raises(PermissionError):
        text_jobs.submit('feedback', [[], [], 'edit'], start=False)
    with pytest.raises(PermissionError):
        text_jobs.submit('source_replan', [1, {}], start=False)


def test_clear_all_removes_live_tasks_invalidates_replies_and_keeps_experience(env, monkeypatch, tmp_path):
    from services import web_video_bridge as video
    monkeypatch.setattr(video, '_DB', tmp_path / 'video-queue.sqlite3')
    _, jobs = env
    operation = text_jobs.get(learning.on_video_ok(1, jobs, True)['operation_id'])
    child = bridge.create_task('chatgpt', 'json', {'system':'s','user':'u'}, entry='video_learning')
    text_jobs.attach_child(operation['id'], child['id'])
    claimed = bridge.claim('extension', ['chatgpt'], tab_id=1)
    text_jobs.update(operation['id'], 'running')
    text_jobs.submit('feedback', [[], [], 'queued'], start=False)
    with learning.database() as db:
        sample = dict(db.execute('SELECT * FROM samples').fetchone())
        values = list(sample.values())
        values[0] = 'completed-experience'; values[7] = 'complete'
        db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)', values)
    with video._connect() as db:
        db.execute('INSERT INTO tasks (id,channel,account,quota_day,state,runtime,project,episode,segment_index,segment_uid,render_id,input_json,lease,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('video','doubao_web','account','day','submitted','runtime','project','001',1,'segment','render','{}','lease',1,1))
    cleared = text_jobs.clear_all_tasks()
    assert cleared['deleted'] == 2 and cleared['web_tasks'] == 1 and cleared['video_tasks'] == 1
    assert text_jobs.list_jobs() == []
    assert video.list_tasks() == []
    assert bridge.pool_status()['reset_epoch'] == cleared['reset_epoch']
    with pytest.raises(ValueError):
        bridge.report(child['id'], claimed['lease'], 'complete', result={'data':{}})
    with pytest.raises(ValueError):
        text_jobs.get(operation['id'])
    with learning.database() as db:
        assert db.execute("SELECT status FROM samples WHERE id='completed-experience'").fetchone()[0] == 'complete'
        assert db.execute('SELECT active FROM samples WHERE id=?', (operation['args'][1],)).fetchone()[0] == 0
    with video._connect() as db:
        row = db.execute("SELECT state,lease,dismissed FROM tasks WHERE id='video'").fetchone()
        assert tuple(row) == ('uncertain', '', 1)
    new = bridge.create_task('chatgpt', 'json', {'system':'s','user':'new'}, entry='video_learning')
    assert bridge.claim('extension', ['chatgpt'], tab_id=2)['id'] == new['id']
