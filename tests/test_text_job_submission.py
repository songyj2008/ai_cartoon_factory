import json
import time
import uuid

from services import text_jobs


def test_revision_wait_task_keeps_target_segment_identity(tmp_path):
    source = tmp_path / 'episode'
    source.mkdir()
    (source / 'video_jobs.json').write_text(json.dumps({
        'segments': [
            {'segment_index': 1, 'segment_uid': 'seg-1', 'beat_uid': 'beat-1'},
            {'segment_index': 2, 'segment_uid': 'seg-2', 'beat_uid': 'beat-2'},
        ]
    }), encoding='utf-8')

    target = text_jobs._target_segment('revision_wait', ['child-task-id', 2], source)

    assert target == {'segment_index': 2, 'segment_uid': 'seg-2', 'beat_uid': 'beat-2'}


def test_interrupted_revision_preparation_without_web_task_needs_attention(tmp_path, monkeypatch):
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    identifier = str(uuid.uuid4())
    item = {'id':identifier,'kind':'revision_wait','args':['',1,{},{}],
            'children':[],'phase':'准备网页任务','history':[],
            'timings':{'state_started_at':time.time()}}
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (identifier,'preparing',time.time(),time.time(),json.dumps(item)))

    text_jobs.recover_interrupted()

    recovered = text_jobs.get(identifier)
    assert recovered['state'] == 'needs_attention'
    assert '未自动重发' in recovered['message']


def test_web_timeout_is_not_reported_as_generic_business_failure(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from services import context, text_job_operations

    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(context, 'pin_runtime', lambda *_: nullcontext())
    def uncertain(*_):
        raise text_jobs.NeedsAttentionError('原网页任务状态不明')
    monkeypatch.setattr(text_job_operations, 'execute', uncertain)
    identifier = str(uuid.uuid4())
    item = {'id':identifier,'kind':'revision_wait','args':[],
            'work':str(tmp_path),'project':'test','episode':'',
            'phase':'等待网页回复','history':[],
            'timings':{'state_started_at':time.time()}}
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (identifier,'running',time.time(),time.time(),json.dumps(item)))

    text_jobs._execute(text_jobs.get(identifier))

    finished = text_jobs.get(identifier)
    assert finished['state'] == 'needs_attention'
    assert finished['attention_reason'] == 'web_timeout'


def test_manual_beats_save_holds_project_lock_through_entire_write(monkeypatch):
    from contextlib import contextmanager
    from ui import project_handlers
    from workflow import video_merge

    held = False
    @contextmanager
    def tracked_lock(*_):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False
    def save(_):
        assert held
        return 'saved'
    monkeypatch.setattr(video_merge, 'video_jobs_lock', tracked_lock)
    monkeypatch.setattr(project_handlers, '_save_beats_edits_locked', save)

    assert project_handlers.save_beats_edits('{}') == 'saved'
    assert not held


def test_pool_scheduler_obeys_service_capacity_and_total_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(text_jobs,'root',lambda:tmp_path/'operations')
    tasks=[('deepseek',1),('deepseek',2),('chatgpt',3)]
    with text_jobs.database() as db:
        for provider,created in tasks:
            identifier=str(uuid.uuid4())
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                       (identifier,'queued',created,created,json.dumps({
                           'id':identifier,'kind':'story','provider':provider,
                           'phase':'已排队','history':[],'timings':{}
                       })))
    with text_jobs._LOCK:
        limits={'total':2,'deepseek':2,'chatgpt':2,'doubao':2}
        first=text_jobs._claim_runnable({},limits)
        second=text_jobs._claim_runnable({'deepseek':1},limits)
        assert text_jobs._claim_runnable({'deepseek':2},limits) is None
        other_service=text_jobs._claim_runnable({'deepseek':1},limits)
    assert first['provider']=='deepseek'
    assert other_service['provider']=='chatgpt'
    assert second['provider']=='deepseek'


def test_pool_scheduler_uses_an_available_mixed_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(text_jobs,'root',lambda:tmp_path/'operations')
    identifier=str(uuid.uuid4())
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (identifier,'queued',1,1,json.dumps({
                       'id':identifier,'kind':'prompts','provider':'deepseek',
                       'provider_candidates':['deepseek','chatgpt'],
                       'phase':'已排队','history':[],'timings':{}
                   })))
    with text_jobs._LOCK:
        claimed=text_jobs._claim_runnable(
            {'deepseek':2},{'total':3,'deepseek':2,'chatgpt':2,'doubao':2})
    assert claimed['dispatch_provider']=='chatgpt'
