import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import web_llm_bridge as bridge
from ui.web_llm_routes import register_web_llm_routes


@pytest.mark.parametrize('provider', ['deepseek', 'doubao', 'chatgpt'])
@pytest.mark.parametrize('kind', ['json', 'text'])
def test_business_format_is_scoped_inside_shared_envelope(env, provider, kind):
    from ui.asset_library_panel import _METADATA_SYSTEM
    task = bridge.create_task(provider, kind, {'system': _METADATA_SYSTEM, 'user': '物流园调度中心'}, entry='other')
    request = task['request']
    assert '仅约束 data 内部的业务结果' in request
    assert '六字段限制仅针对 data 内部' in request
    start = 'AICF_RESULT_' + task['id'] + '_START'
    end = 'AICF_RESULT_' + task['id'] + '_END'
    envelope = json.loads(request.split(start)[1].split(end)[0].strip())
    assert envelope['request_id'] == task['id']
    assert isinstance(envelope['data'], dict if kind == 'json' else str)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(bridge, "BRIDGE_DIR", tmp_path / "bridge")
    monkeypatch.setattr(bridge, "get_project_dir", lambda: root)
    from services import text_jobs
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    import ui.web_llm_routes as routes
    monkeypatch.setattr(routes, "assert_current_runtime_writable", lambda *_: None)
    return root


def seed(root):
    old = root / "old.mp4"
    new = root / "new.mp4"
    old.write_bytes(b"video")
    new.write_bytes(b"video")
    value = {"segments": [{"segment_index": 1, "segment_uid": "segment-a", "job": {"prompt": "saved current"},
        "renders": [
            {"render_id": "old", "created_at": "2026-01-01", "prompt_snapshot": "old prompt", "output": {"video_path": str(old)}},
            {"render_id": "new", "created_at": "2026-02-01", "prompt_snapshot": "video prompt", "output": {"video_path": str(new)}},
            {"render_id": "pending", "created_at": "2026-03-01", "status": "running", "output": {}}
        ]}]}
    (root / "video_jobs.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def test_h3_snapshot_carries_current_save_baseline_after_planning(env):
    from generation.pipelines.minimax_h3.editor_draft import reference_revision
    data = seed(env)
    segment = data['segments'][0]
    segment['model_id'] = 'minimax_h3_local_ref2va'
    segment['generation_config'] = {'prompt_override': 'planned prompt', 'asset_manifest': [
        {'id': 'person', 'type': 'image', 'path': 'person.png', 'role': '人物'}]}
    (env / 'video_jobs.json').write_text(json.dumps(data), encoding='utf-8')
    snapshot = bridge.segment_snapshot(1)
    assert snapshot['reference_revision'] == reference_revision(segment)
    assert snapshot['reference_manifest'] == segment['generation_config']['asset_manifest']
    segment['generation_config']['prompt_override'] = 'concurrent newer prompt'
    (env / 'video_jobs.json').write_text(json.dumps(data), encoding='utf-8')
    assert bridge.segment_snapshot(1)['reference_revision'] != snapshot['reference_revision']


def test_provider_precedence_and_available_services(env):
    assert bridge.route_for("revision") == "deepseek"
    assert bridge.route_for("story") == "deepseek"
    assert bridge.route_for("beats") == "deepseek"
    bridge.save_settings({"default_provider": "chatgpt", "overrides": {"story": "inherit"}})
    assert bridge.route_for("story") == "chatgpt"
    assert bridge.route_for("story", "deepseek") == "deepseek"
    bridge.save_settings({"overrides": {"beats": "chatgpt"}})
    assert bridge.get_settings()["overrides"]["story"] == "inherit"
    assert bridge.route_for("beats") == "chatgpt"  # Explicit per-feature service beats the global default.
    bridge.save_settings({"overrides": {"beats": "inherit"}})
    bridge.save_settings({"default_provider": "doubao"})
    assert bridge.route_for("revision") == "doubao"
    assert bridge.route_for("beats") == "doubao"
    bridge.save_settings({"overrides": {"beats": "api"}})
    assert bridge.route_for("beats") == "api"


def test_multiple_providers_are_saved_and_least_loaded_is_selected(env):
    bridge.save_settings({
        "default_providers": ["deepseek", "chatgpt", "doubao"],
        "overrides": {"prompt": ["chatgpt", "doubao"]},
    })
    assert bridge.routes_for("story") == ["deepseek", "chatgpt", "doubao"]
    assert bridge.routes_for("prompt") == ["chatgpt", "doubao"]
    bridge.create_task("chatgpt", "text", {"user": "queued"}, entry="prompt")
    assert bridge.choose_provider(["chatgpt", "doubao"], "next") == "doubao"
    with pytest.raises(ValueError, match="API 当前只能单独使用"):
        bridge.save_settings({"overrides": {"prompt": ["api", "doubao"]}})


def test_legacy_default_api_overrides_migrate_to_follow_global(env):
    bridge.get_settings()
    with bridge._connect() as db:
        value = json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])
        value["overrides"] = {entry: "api" for entry in bridge.ENTRIES if entry != "revision"}
        value["overrides"]["other"] = "chatgpt"  # A real provider choice is preserved.
        value.pop("routing_defaults_migrated", None)
        db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(value),))
    bridge.save_settings({"default_provider": "doubao"})
    assert bridge.route_for("beats") == "doubao"
    assert bridge.route_for("other") == "chatgpt"


def test_preview_uses_last_success_and_its_historical_prompt(env):
    seed(env)
    snapshot = bridge.segment_snapshot(1)
    assert snapshot["render_id"] == "new"
    assert snapshot["original_prompt"] == "video prompt"
    assert snapshot["current_prompt"] == "saved current"
    assert snapshot["video_url"].endswith("new.mp4")


def test_revision_snapshots_and_cross_project_guard(env, monkeypatch):
    seed(env)
    snapshot = bridge.segment_snapshot(1)
    payload = {**snapshot, "edited_prompt": "unsaved editor", "issue": "motion issue", "provider": "chatgpt"}
    task = bridge.revision_task(1, payload)
    assert task["input"]["base_prompt"] == "video prompt"
    assert "edited_prompt" not in task["input"]
    assert task["provider"] == "chatgpt"
    with pytest.raises(ValueError, match="未结束"):
        bridge.revision_task(1, payload)
    with pytest.raises(ValueError, match="已变化"):
        bridge.revision_task(1, {**payload, "fingerprint": "stale"})
    other = env.parent / "other"
    other.mkdir()
    monkeypatch.setattr(bridge, "get_project_dir", lambda: other)
    with pytest.raises(ValueError, match="其他项目"):
        bridge.task_status(task["id"], current_runtime=True)


def test_h3_revision_receives_shared_policy_and_other_models_do_not(env, monkeypatch):
    from generation.pipelines.minimax_h3 import rules

    seed(env)
    jobs_path = env / "video_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs["segments"][0]["model_id"] = "minimax_h3_local_ref2va"
    jobs_path.write_text(json.dumps(jobs), encoding="utf-8")
    monkeypatch.setattr(rules, "h3_segment_planning_rules", lambda: "distinct H3 directing policy")

    snapshot = bridge.segment_snapshot(1)
    task = bridge.revision_task(1, {**snapshot, "issue": "speech timing", "provider": "chatgpt"})
    assert task["input"]["model_id"] == "minimax_h3_local_ref2va"
    assert "distinct H3 directing policy" in task["request"]
    assert task["input"]["base_prompt"] == "video prompt"
    other = bridge.create_task("chatgpt", "revision", {"base_prompt": "other model", "model_id": "other"})
    assert "distinct H3 directing policy" not in other["request"]


@pytest.mark.parametrize('scope,source', [
    ('onscreen', ''), ('offscreen', '，画外'),
    ('telephone', '，电话另一端画外声音'), ('narration', '，画外旁白'),
])
def test_h3_revision_sends_existing_silent_shot_and_speech_scope(env, scope, source):
    prompt = ('【详细镜头描述】\n'
              '[Shot 1]（0.00–2.80 秒）：本镜无对白，人物走到桌边，只有脚步声。\n'
              f'[Shot 2] At 00:02.80（2.80–4.00 秒）：<Subject 2>{source}说：'
              '<d>[Chinese]先保下单支付。</d>')
    original_input = {'base_prompt': prompt, 'model_id': 'minimax_h3_local_ref2va',
                      'target_duration_sec': 4}
    task = bridge.create_task('chatgpt', 'revision', original_input)
    assert task['input']['base_prompt'] == prompt
    assert task['input']['shot_speech'] == [
        {'shot': 1, 'start_sec': 0, 'end_sec': 2.8, 'editor_state': '无台词', 'speech_state': 'explicit_silent', 'issues': [], 'dialogues': []},
        {'shot': 2, 'start_sec': 2.8, 'end_sec': 4, 'editor_state': '有台词', 'speech_state': 'tagged', 'issues': [],
         'dialogues': [{'speaker': '<Subject 2>', 'scope': scope, 'line': '先保下单支付。'}]},
    ]
    assert 'shot_speech' not in original_input
    assert '明确写本镜无对白' in task['request']
    assert '不能截断明确的跨镜续说' in task['request']
    assert '保留 Shot 数量、起止时间和 At 时间戳' in task['request']


@pytest.mark.parametrize('use_editor', [False, True])
def test_h3_revision_shot_state_comes_only_from_selected_draft(env, use_editor):
    jobs = seed(env)
    segment = jobs['segments'][0]
    segment['model_id'] = 'minimax_h3_local_ref2va'
    video_prompt = ('【详细镜头描述】\n[Shot 1]（0.00–4.00 秒）：'
                    '<Subject 1>说：<d>[Chinese]原视频台词。</d>')
    editor_prompt = '【详细镜头描述】\n[Shot 1]（0.00–4.00 秒）：本镜无对白，人物闭口，只有脚步声。'
    segment['renders'][1]['prompt_snapshot'] = video_prompt
    segment['job']['duration_sec'] = 4
    (env / 'video_jobs.json').write_text(json.dumps(jobs), encoding='utf-8')
    before = (env / 'video_jobs.json').read_bytes()
    task = bridge.revision_task(1, {**bridge.segment_snapshot(1), 'issue': '按镜头处理声音',
                                  'edited_prompt': editor_prompt, 'use_editor_prompt': use_editor})
    speech = task['input']['shot_speech'][0]
    assert speech['editor_state'] == ('无台词' if use_editor else '有台词')
    assert bool(speech['dialogues']) is not use_editor
    if use_editor:
        assert '原视频台词' not in task['request']
    assert (env / 'video_jobs.json').read_bytes() == before


def test_h3_silent_shot_missing_wording_is_sent_for_rule_repair(env):
    task = bridge.create_task('chatgpt', 'revision', {
        'base_prompt': '【详细镜头描述】\n[Shot 1]（0.00–2.00 秒）：人物跑步入画。',
        'model_id': 'minimax_h3_local_ref2va', 'target_duration_sec': 2})
    shot = task['input']['shot_speech'][0]
    assert shot['editor_state'] == '无台词' and shot['dialogues'] == []
    assert shot['issues'] == [] and shot['rule_issues']
    assert '不改变无台词状态' in task['request']
    assert '不得新增台词' in task['request']


def test_missing_snapshot_never_uses_edited_prompt_as_video_prompt(env):
    value = seed(env)
    for render in value["segments"][0]["renders"]:
        render.pop("prompt_snapshot", None)
    (env / "video_jobs.json").write_text(json.dumps(value), encoding="utf-8")
    snapshot = bridge.segment_snapshot(1)
    assert not snapshot["snapshot_known"]
    with pytest.raises(ValueError, match="原提示词"):
        bridge.revision_task(1, {**snapshot, "edited_prompt": "new editor", "issue": "wrong motion"})


def test_qianwen_is_available_for_user_selected_text_tasks(env):
    provider = next(p for p in bridge.PROVIDERS if p['id'] == 'qianwen')
    assert provider['url'] == 'https://www.qianwen.com/'
    assert provider['available'] is True
    assert provider['label'] == '千问'
    assert bridge.validate_route('qianwen') == 'qianwen'
    bridge.save_settings({'default_providers': ['qianwen']})
    assert bridge.route_for('story') == 'qianwen'


def test_qianwen_route_pool_and_result_when_reviewed_in_isolated_fixture(env, monkeypatch):
    bridge.get_settings()  # Exercise existing settings migration too.
    monkeypatch.setattr(bridge, 'PROVIDERS', [
        {**provider, 'available': True} if provider['id'] == 'qianwen' else provider
        for provider in bridge.PROVIDERS
    ])
    bridge.save_settings({'default_providers': ['qianwen'], 'concurrency': {'qianwen': 1}})
    assert bridge.route_for('story') == 'qianwen'
    task = bridge.create_task('qianwen', 'text', {'user': 'write text'}, entry='story')
    claimed = bridge.claim('client', ['qianwen'], tab_id=44, capabilities=[])
    assert claimed['id'] == task['id']
    bridge.report(task['id'], claimed['lease'], 'complete', result={'data': 'finished'})
    assert bridge.task_status(task['id'])['result'] == {'data': 'finished'}


def test_provider_capabilities_are_scoped_and_legacy_heartbeats_still_work(env):
    cap = 'video_learning_attachments_v1'
    bridge.heartbeat('new-client', ['deepseek', 'chatgpt'], [cap],
                     {'deepseek': [cap], 'chatgpt': []})
    assert bridge.supports_capability(cap, ['deepseek'])
    assert not bridge.supports_capability(cap, ['chatgpt'])
    bridge.heartbeat('old-client', ['chatgpt'], [cap])
    assert not bridge.supports_capability(cap, ['chatgpt'])
    bridge.save_settings({'primary_text_client_id': 'old-client'})
    assert bridge.supports_capability(cap, ['chatgpt'])
    with pytest.raises(ValueError, match='能力格式'):
        bridge.heartbeat('bad-client', ['deepseek'], [], {'deepseek': cap})
    with pytest.raises(ValueError, match='能力格式'):
        bridge.heartbeat('bad-client', ['deepseek'], [], {'unknown': []})


def test_attachment_required_tasks_are_not_claimed_by_a_text_only_provider(env, monkeypatch):
    monkeypatch.setattr(bridge, 'PROVIDERS', [
        {**provider, 'available': True} if provider['id'] == 'qianwen' else provider
        for provider in bridge.PROVIDERS
    ])
    cap = 'video_learning_attachments_v1'
    bridge.create_task('qianwen', 'text', {'user': 'video'},
                       metadata={'required_capability': cap})
    text = bridge.create_task('qianwen', 'text', {'user': 'text'})
    assert bridge.claim('client', ['qianwen'], tab_id=44, capabilities=[])['id'] == text['id']


def test_primary_browser_drains_existing_tasks_before_switch(env):
    bridge.heartbeat('chrome', ['deepseek'])
    bridge.heartbeat('edge', ['deepseek'])
    assert bridge.get_settings()['primary_text_client_id'] == 'chrome'
    first = bridge.create_task('deepseek', 'json', {'user': 'first'})
    second = bridge.create_task('deepseek', 'json', {'user': 'second'})
    assert bridge.claim('edge', ['deepseek'], tab_id=1) is None
    claimed = bridge.claim('chrome', ['deepseek'], tab_id=1)
    assert claimed['id'] == first['id']
    bridge.save_settings({'primary_text_client_id': 'edge'})
    assert bridge.claim('chrome', ['deepseek'], tab_id=2) is None
    assert bridge.claim('edge', ['deepseek'], tab_id=2) is None
    bridge.report(first['id'], claimed['lease'], 'error', message='finished')
    assert bridge.claim('edge', ['deepseek'], tab_id=2)['id'] == second['id']


def test_manual_text_permission_uses_primary_browser(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ui.web_llm_routes import register_web_llm_routes
    bridge.heartbeat('chrome', ['deepseek'])
    bridge.heartbeat('edge', ['deepseek'])
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    path = '/api/web-llm/bridge/text-permission'
    assert client.post(path, json={'client_id':'chrome'}).status_code == 403
    headers = {'Authorization':'Bearer ' + bridge.get_settings()['token']}
    assert client.post(path, headers=headers, json={'client_id':'chrome'}).json()['text_enabled']
    assert not client.post(path, headers=headers, json={'client_id':'edge'}).json()['text_enabled']


def test_primary_connection_does_not_use_secondary_heartbeat(env):
    bridge.heartbeat('chrome', ['deepseek'], browser={'name':'Chrome 工作', 'channels':['doubao_web']})
    bridge.heartbeat('edge', ['chatgpt'])
    with bridge._connect() as db:
        db.execute('UPDATE clients SET seen=0 WHERE id=?', ('chrome',))
    assert not bridge.connection_status()['connected']
    assert not bridge.text_owner('edge')
    assert any(row['name'] == 'Chrome 工作' for row in bridge.browser_clients())


def test_atomic_claim_callback_auth_and_idempotency(env):
    task = bridge.create_task("deepseek", "json", {"user": "make beats"}, entry="beats")
    assert bridge.claim("wrong-provider", ["chatgpt"]) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda i: bridge.claim(f"client-{i}", ["deepseek"]), range(2)))
    assert sum(c is not None for c in claims) == 1
    lease = next(c["lease"] for c in claims if c)
    with pytest.raises(PermissionError):
        bridge.report(task["id"], "wrong", "complete", result={"data": {}})
    bridge.report(task["id"], lease, "complete", result={"data": {"beats": [1]}})
    bridge.report(task["id"], lease, "complete", result={"data": {"beats": [2]}})
    assert bridge.task_status(task["id"])["result"]["data"]["beats"] == [1]


def test_phase_two_claim_limits_each_provider_and_binds_tab(env):
    bridge.save_settings({'concurrency':{'deepseek':1}})
    first=bridge.create_task('deepseek','text',{'user':'first'},entry='story')
    second=bridge.create_task('deepseek','text',{'user':'second'},entry='story')
    other=bridge.create_task('chatgpt','text',{'user':'other'},entry='story')

    claimed=bridge.claim('client-a',['deepseek'],tab_id=7)
    assert claimed['id']==first['id']
    assert claimed['claimed_tab_id']==7
    assert claimed['claimed_client_id']=='client-a'
    assert bridge.claim('client-a',['deepseek'],tab_id=8) is None
    assert bridge.claim('client-a',['chatgpt'],tab_id=7) is None
    assert bridge.claim('client-a',['chatgpt'],tab_id=9)['id']==other['id']
    bridge.cancel_task(first['id'],current_runtime=False)
    assert bridge.claim('client-a',['deepseek'],tab_id=7)['id']==second['id']


def test_pool_claims_same_provider_on_exclusive_tabs_with_total_cap(env):
    bridge.save_settings({'concurrency':{'deepseek':2,'total':2}})
    jobs=[bridge.create_task('deepseek','text',{'user':str(i)}) for i in range(3)]
    other=bridge.create_task('chatgpt','text',{'user':'chat'})
    first=bridge.claim('pool-client',['deepseek'],tab_id=11)
    assert bridge.claim('pool-client',['deepseek'],tab_id=11) is None
    second=bridge.claim('pool-client',['deepseek'],tab_id=12)
    assert {first['id'],second['id']}=={jobs[0]['id'],jobs[1]['id']}
    assert bridge.claim('pool-client',['deepseek'],tab_id=13) is None
    assert bridge.claim('pool-client',['chatgpt'],tab_id=14) is None
    bridge.cancel_task(first['id'],current_runtime=False)
    assert bridge.claim('pool-client',['chatgpt'],tab_id=14)['id']==other['id']
    assert bridge.task_status(second['id'])['status']=='claimed'


def test_cancel_timeout_and_late_callback(env):
    task = bridge.create_task("deepseek", "text", {})
    claimed = bridge.claim("test", ["deepseek"])
    bridge.cancel_task(task["id"])
    result = bridge.report(task["id"], claimed["lease"], "complete", result={"data": "late"})
    assert result["status"] == "cancelled"
    stale = bridge.create_task("deepseek", "text", {})
    with bridge._connect() as db:
        db.execute("UPDATE tasks SET created=0 WHERE id=?", (stale["id"],))
    expired = bridge.task_status(stale["id"])
    assert expired["status"] == "timeout"
    assert expired['timeout_phase'] == 'queue'
    assert bridge.claim("test", ["deepseek"]) is None
    stale = bridge.create_task("deepseek", "text", {})
    bridge.claim("test", ["deepseek"])
    assert bridge.task_status(stale["id"])["status"] == "claimed"
    with bridge._connect() as db:
        row = db.execute("SELECT payload FROM tasks WHERE id=?", (stale["id"],)).fetchone()
        payload = json.loads(row[0])
        payload['execution_started_at'] = 1
        payload['execution_deadline_at'] = 1 + bridge.TIMEOUT
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload),stale['id']))
    assert bridge.task_status(stale["id"])["status"] == "timeout"
    assert bridge.claim("client", ["deepseek"]) is None


def test_expired_unclaimed_task_releases_revision_slot(env):
    old = bridge.create_task('chatgpt', 'revision', {}, metadata={'segment_index': 1})
    with bridge._connect() as db:
        db.execute('UPDATE tasks SET created=0 WHERE id=?', (old['id'],))
    new = bridge.create_task('chatgpt', 'revision', {}, metadata={'segment_index': 1})
    assert bridge.task_status(old['id'])['status'] == 'timeout'
    assert bridge.claim('client', ['chatgpt'], tab_id=8)['id'] == new['id']


def test_bridge_history_preserves_referenced_results_and_leaves_tombstones(env):
    metadata = {'operation_id': '11111111-1111-4111-8111-111111111111', 'call_key': 'test-call'}
    task = bridge.create_task('deepseek', 'text', {}, metadata=metadata)
    claim = bridge.claim('client', ['deepseek'])
    bridge.report(task['id'], claim['lease'], 'complete', result={'data': 'saved'})
    with bridge._connect() as db:
        db.execute('UPDATE tasks SET updated=1 WHERE id=?', (task['id'],))
    now = bridge.HISTORY_RETENTION + 2
    assert bridge.cleanup_history([task['id']], now=now) == 0
    assert bridge.task_status(task['id'])['result']['data'] == 'saved'
    assert bridge.cleanup_history(now=now) == 1
    with pytest.raises(bridge.TaskGoneError):
        bridge.task_status(task['id'])
    with bridge._connect() as db:
        assert db.execute('SELECT 1 FROM task_tombstones WHERE id=?', (task['id'],)).fetchone()
    with pytest.raises(bridge.TaskGoneError):
        bridge.create_task('deepseek', 'text', {}, metadata=metadata)


def test_missing_extension_task_has_explicit_gone_protocol(env):
    from ui.web_llm_routes import _error
    response = _error(bridge.TaskGoneError('任务已清理'))
    assert response.status_code == 410
    assert json.loads(response.body)['code'] == 'TASK_GONE'
    with pytest.raises(bridge.TaskGoneError):
        bridge.report('missing', 'lease', 'complete', result={'data': 'late'})


def test_wait_for_timed_out_web_task_requires_attention(env, monkeypatch):
    from services.text_jobs import NeedsAttentionError

    monkeypatch.setattr(bridge, 'task_status', lambda identifier: {
        'id':identifier, 'status':'timeout', 'message':'网页任务超时',
    })
    with pytest.raises(NeedsAttentionError, match='不会自动重发'):
        bridge.wait_for_llm('system','user',kind='text',entry='story',provider='deepseek')


def test_missing_operation_has_structured_not_found_response(env, monkeypatch):
    from services import text_jobs
    def missing(identifier):
        raise ValueError('任务不存在')
    monkeypatch.setattr(text_jobs, 'result', missing)
    app = FastAPI()
    register_web_llm_routes(app)
    response = TestClient(app).get('/api/web-llm/operations/deleted-task')
    assert response.status_code == 404
    assert response.json()['code'] == 'OPERATION_NOT_FOUND'


def test_http_pairing_origin_and_revision_roundtrip(env):
    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    assert client.get("/api/web-llm/settings", headers={"origin": "https://evil.test"}).status_code == 403
    token = client.get("/api/web-llm/settings").json()["settings"]["token"]
    assert client.post("/api/web-llm/bridge/claim", json={"client_id": "x", "providers": ["deepseek"]}).status_code == 403
    snap = client.get("/api/web-llm/segments/1").json()["snapshot"]
    response = client.post("/api/web-llm/segments/1/revisions", json={**snap, "edited_prompt": "saved current", "issue": "motion wrong"})
    assert response.status_code == 200
    task = response.json()["task"]
    listed = client.get("/api/web-llm/segments/1").json()["task"]
    assert listed["id"] == task["id"]
    assert listed["business_operation"]["id"] == response.json()["operation_id"]
    auth = {"authorization": "Bearer " + token}
    claimed = client.post("/api/web-llm/bridge/claim", headers=auth, json={"client_id": "extension", "providers": ["deepseek"]}).json()["task"]
    done = client.post(f"/api/web-llm/bridge/tasks/{task['id']}", headers=auth,
                       json={"lease": claimed["lease"], "status": "complete", "result": {"revised_prompt": "fixed", "changes": ["fix action"]},
                             "diagnostics": [{"event": "click_dispatched"}, {"event": "reply_grew", "after": 123}]})
    assert done.status_code == 200
    result = client.get(f"/api/web-llm/tasks/{task['id']}").json()["task"]
    assert result["status"] == "complete"
    assert result["result"]["revised_prompt"] == "fixed"
    assert result["continuation_diagnostics"] == [{"event": "click_dispatched"}, {"event": "reply_grew", "after": 123}]
    # Applying happens in the editor, never silently alters saved job data.
    assert bridge.segment_snapshot(1)["current_prompt"] == "saved current"


def test_revision_rejects_invalid_provider_before_creating_business_task(env):
    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']

    response = client.post('/api/web-llm/segments/1/revisions', json={
        **snapshot, 'provider':'api', 'issue':'fix motion',
    })

    assert response.status_code == 400
    assert client.get('/api/web-llm/operations').json()['tasks'] == []


def test_revision_setup_failure_removes_unlinked_business_task(env):
    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']

    response = client.post('/api/web-llm/segments/1/revisions', json={
        **snapshot, 'provider':'deepseek', 'issue':'',
    })

    assert response.status_code == 400
    assert client.get('/api/web-llm/operations').json()['tasks'] == []


def test_revision_modal_cancel_also_cancels_business_task(env):
    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']
    submitted = client.post('/api/web-llm/segments/1/revisions', json={
        **snapshot, 'provider':'deepseek', 'issue':'fix motion',
    }).json()
    assert submitted['ok']
    operation_id = submitted['operation_id']
    task_id = submitted['task']['id']

    cancelled = client.post(f'/api/web-llm/tasks/{task_id}/cancel').json()['task']

    assert cancelled['status'] == 'cancelled'
    assert cancelled['business_operation']['state'] == 'cancelled'
    assert client.get(f'/api/web-llm/operations/{operation_id}').json()['result']['state'] == 'cancelled'


def test_duplicate_revision_submission_reuses_linked_task(env):
    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']
    payload = {**snapshot, 'provider':'deepseek', 'issue':'fix motion'}

    first = client.post('/api/web-llm/segments/1/revisions',json=payload).json()
    second = client.post('/api/web-llm/segments/1/revisions',json=payload).json()

    assert first['ok'] and second['ok']
    assert second['operation_id'] == first['operation_id']
    assert second['task']['id'] == first['task']['id']


def test_revision_link_failure_cancels_created_web_and_business_tasks(env, monkeypatch):
    from services import text_jobs

    seed(env)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']
    def fail_link(*_):
        raise RuntimeError('link failed')
    monkeypatch.setattr(bridge, 'attach_operation', fail_link)

    response = client.post('/api/web-llm/segments/1/revisions', json={
        **snapshot, 'provider':'deepseek', 'issue':'fix motion',
    })

    assert response.status_code == 400
    tasks = text_jobs.list_jobs()
    assert len(tasks) == 1
    assert tasks[0]['state'] == 'cancelled'
    assert bridge.task_status(tasks[0]['children'][0],current_runtime=False)['status'] == 'cancelled'


def test_existing_llm_calls_dispatch_web_without_api_key(env, monkeypatch):
    from services import llm
    bridge.save_settings({"overrides": {"story": "chatgpt", "prompt": "deepseek"}})
    calls = []
    def fake(system, user, **kwargs):
        calls.append(kwargs)
        return ({"story": "ok"} if kwargs["kind"] == "json" else "prompt"), 1
    monkeypatch.setattr(bridge, "wait_for_llm", fake)
    assert llm.call_llm("sys", "input", label="story")[0] == {"story": "ok"}
    assert llm.call_llm_text("sys", "input", label="licon_prompt_compile_001")[0] == "prompt"
    assert [c["provider"] for c in calls] == ["chatgpt", "deepseek"]


@pytest.mark.parametrize("provider", ["deepseek", "chatgpt"])
def test_asset_completion_inherits_other_route(env, monkeypatch, provider):
    from services import llm
    bridge.save_settings({"default_provider": provider, "overrides": {"other": "inherit"}})
    calls = []

    def fake(system, user, **kwargs):
        calls.append(kwargs)
        return {"description_cn": "完整描述"}, 1

    monkeypatch.setattr(bridge, "wait_for_llm", fake)
    assert llm.call_llm("sys", "input", label="asset_metadata")[0]["description_cn"] == "完整描述"
    assert calls == [{"kind": "json", "entry": "other", "provider": provider,
                      "call_hint": "asset_metadata"}]
    bridge.save_settings({"overrides": {"other": "api"}})
    assert bridge.route_for(bridge.entry_for_label("asset_metadata")) == "api"


@pytest.mark.parametrize('label', ['h3_composition_patch', 'h3_prompt_state_trace_test'])
@pytest.mark.parametrize('other', ['inherit', ['deepseek', 'doubao'], 'api'])
def test_continuity_calls_use_other_route(env, monkeypatch, label, other):
    from services import llm
    bridge.save_settings({'default_providers': ['doubao'],
                         'overrides': {'prompt': ['chatgpt'], 'other': other}})
    calls = []
    monkeypatch.setattr(bridge, 'wait_for_llm',
                        lambda *args, **kwargs: (calls.append(kwargs) or {}, 0))
    assert bridge.entry_for_label(label) == 'other'
    if other == 'api':
        assert bridge.routes_for(bridge.entry_for_label(label)) == ['api']
    else:
        llm.call_llm('system', 'input', label=label)
        assert calls[0]['entry'] == 'other'
        assert calls[0]['provider'] == ('doubao' if other == 'inherit' else other)
    assert bridge.entry_for_label('h3_prompt_retime_001') == 'prompt'


@pytest.mark.parametrize('kind,label', [
    ('opening_continuity', 'h3_composition_patch'),
    ('scene_state', 'h3_prompt_state_trace_test'),
])
def test_continuity_queue_and_call_share_frozen_other_route(env, tmp_path, monkeypatch, kind, label):
    from services import context, llm, text_jobs
    seed(env)
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    bridge.save_settings({'overrides': {'prompt': ['chatgpt'], 'other': ['deepseek', 'doubao']}})
    with context.pin_runtime((env, 'test', '')):
        task = text_jobs.submit(kind, [{'segment_uid': 'segment-a'}], start=False, hold=True)
        assert task['provider_candidates'] == ['deepseek', 'doubao']
        # A queued task keeps its submitted route even if settings change later.
        bridge.save_settings({'overrides': {'other': ['chatgpt']}})
        calls = []
        monkeypatch.setattr(bridge, 'wait_for_llm',
                            lambda *args, **kwargs: (calls.append(kwargs) or {}, 0))
        token = text_jobs.ACTIVE.set(task)
        try:
            llm.call_llm('system', 'input', label=label)
        finally:
            text_jobs.ACTIVE.reset(token)
        assert calls[0]['entry'] == 'other'
        assert calls[0]['provider'] == task['provider_candidates']


@pytest.mark.parametrize("use_editor,expected", [(True, "unsaved editor"), (False, "video prompt")])
def test_revision_uses_only_selected_base(env, use_editor, expected):
    seed(env)
    snapshot = bridge.segment_snapshot(1)
    task = bridge.revision_task(1, {**snapshot, "edited_prompt": "unsaved editor", "issue": "fix motion", "use_editor_prompt": use_editor})
    assert task["input"]["base_prompt"] == expected
    assert set(task["input"]) == {"base_prompt", "target_duration_sec", "video_issues", "expected_result", "context"}
    assert task["metadata"]["force_new_conversation"] is use_editor
    assert task["metadata"]["editor_prompt"] == "unsaved editor"


@pytest.mark.parametrize('kind', ['scene_state', 'opening_continuity', 'source_replan', 'revision_wait'])
@pytest.mark.parametrize('selected,expected', [(['chatgpt'], ['chatgpt']),
    (['inherit'], ['doubao']), (['chatgpt', 'deepseek'], ['chatgpt', 'deepseek']),
    (['doubao', 'chatgpt'], ['doubao', 'chatgpt']), (['deepseek', 'doubao'], ['deepseek', 'doubao'])])
def test_page_selection_overrides_feature_routes_and_is_frozen(env, tmp_path, monkeypatch, kind, selected, expected):
    from services import context, llm, text_jobs
    seed(env)
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    monkeypatch.setattr('services.project_assets.sync_generation_assets', lambda: None)
    settings = bridge.save_settings({'default_providers': ['doubao'],
        'overrides': {'prompt': ['deepseek'], 'other': ['deepseek'], 'revision': ['deepseek']}})
    args = [None, 1, {'providers': selected}, {}] if kind == 'revision_wait' else (
        [1, '', {'providers': selected}] if kind == 'source_replan' else [{'segment_uid': 'segment-a'}])
    if kind == 'source_replan' and 'chatgpt' in expected:
        expected = ['chatgpt']
    with context.pin_runtime((env, 'test', '')):
        task = text_jobs.submit(kind, args, start=False, hold=True, page_providers=selected)
        assert task['provider_candidates'] == expected
        assert task['routes']['overrides']['other'] == expected
        assert task['routes']['overrides']['prompt'] == expected
        assert task['routes']['overrides']['revision'] == expected
        assert bridge.get_settings()['overrides'] == settings['overrides']
        bridge.save_settings({'default_providers': ['deepseek'],
            'overrides': {'other': ['doubao'], 'prompt': ['doubao']}})
        calls, api_calls = [], []
        from services import duration_planning_llm
        monkeypatch.setattr(duration_planning_llm, '_api',
                            lambda *a: (api_calls.append('deepseek-v4-pro') or {}, {}))
        monkeypatch.setattr(bridge, 'wait_for_llm', lambda *a, **k: (calls.append(k) or {}, 0))
        token = text_jobs.ACTIVE.set(task)
        try:
            for label in ['h3_composition_patch', 'h3_prompt_state_trace_test',
                          'h3_prompt_state_trace_test_repair1']:
                llm.call_llm('system', 'input', label=label)
            assert all(call['provider'] == (expected[0] if len(expected) == 1 else expected) for call in calls)
            calls.clear()
            llm.call_llm('system', 'input', label='h3_prompt_retime_001')
        finally:
            text_jobs.ACTIVE.reset(token)
        timing_provider = 'chatgpt' if 'chatgpt' in expected else 'deepseek' if 'deepseek' in expected else expected[0]
        assert [call['provider'] for call in calls] == [timing_provider]
        assert api_calls == []  # No API checkbox/explicit API route was selected.


def test_state_extraction_from_page_carries_selected_providers(env, tmp_path, monkeypatch):
    from services import context, scene_state_jobs, text_jobs
    from test_h3_state_preparation import sample
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    (env / 'video_jobs.json').write_text(json.dumps(sample()), encoding='utf-8')
    bridge.save_settings({'overrides': {'other': ['doubao']}})
    with context.pin_runtime((env, 'test', '')):
        pending = scene_state_jobs.request_scene_state_refresh('1', providers=['chatgpt'])
        task = text_jobs.get(pending['id'])
        assert task['args'][0]['providers'] == ['chatgpt']
        assert task['provider_candidates'] == ['chatgpt']


@pytest.mark.parametrize('action', ['continuity', 'extract-state'])
def test_editor_llm_endpoints_use_page_providers(env, tmp_path, monkeypatch, action):
    from types import SimpleNamespace
    from services import context, text_jobs
    from ui import api_routes
    from test_h3_state_preparation import sample
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    (env / 'video_jobs.json').write_text(json.dumps(sample()), encoding='utf-8')
    bridge.save_settings({'overrides': {'other': ['doubao']}})
    app = FastAPI();api_routes.register_api_routes(SimpleNamespace(app=app))
    with context.pin_runtime((env, 'test', '')):
        response = TestClient(app).post('/api/segment/editor/' + action, json={
            'operation_contract': 'scene_state_v3', 'segment_uid': '1', 'providers': ['chatgpt']})
        assert response.status_code == 200, response.text
        data = response.json()
        identifier = data['operation_id'] if action == 'continuity' else data['state_task']['id']
        assert text_jobs.get(identifier)['provider_candidates'] == ['chatgpt']


def test_revision_page_inherit_uses_global_default_instead_of_revision_override(env):
    from services import text_jobs
    seed(env)
    bridge.save_settings({'default_providers': ['chatgpt'], 'overrides': {'revision': ['doubao']}})
    app = FastAPI();register_web_llm_routes(app);client = TestClient(app)
    snapshot = client.get('/api/web-llm/segments/1').json()['snapshot']
    response = client.post('/api/web-llm/segments/1/revisions', json={
        **snapshot, 'providers': ['inherit'], 'issue': 'fix motion'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['task']['provider'] == 'chatgpt'
    assert text_jobs.get(data['operation_id'])['routes']['overrides']['revision'] == ['chatgpt']


def test_editor_source_does_not_require_historical_snapshot(env):
    value = seed(env)
    for render in value["segments"][0]["renders"]:
        render.pop("prompt_snapshot", None)
    (env / "video_jobs.json").write_text(json.dumps(value), encoding="utf-8")
    task = bridge.revision_task(1, {**bridge.segment_snapshot(1), "edited_prompt": "editor", "issue": "fix", "use_editor_prompt": True})
    assert task["input"]["base_prompt"] == "editor"
    assert "original_prompt" not in task["input"]


@pytest.mark.parametrize("use_editor", [False, True])
def test_revision_target_duration_overrides_historical_video(env, use_editor):
    value = seed(env)
    segment = value["segments"][0]
    segment["job"].update(duration_sec=6, fps=50)
    segment["requested_media_spec"] = {"duration_sec": 6, "fps": 50}
    segment["renders"][1]["requested_media_spec"] = {"duration_sec": 4, "fps": 50}
    (env / "video_jobs.json").write_text(json.dumps(value), encoding="utf-8")
    task = bridge.revision_task(1, {**bridge.segment_snapshot(1), "edited_prompt": "8 seconds", "issue": "fix pacing", "use_editor_prompt": use_editor})
    assert task["input"]["target_duration_sec"] == 6
    assert task["input"]["context"]["requested_media_spec"]["duration_sec"] == 6
    if use_editor:
        assert set(task["input"]["context"]) == {"requested_media_spec"}
    else:
        assert task["input"]["context"]["historical_video_media_spec"]["duration_sec"] == 4
    assert task["input"]["base_prompt"] == ("8 seconds" if use_editor else "video prompt")
    assert "最后一个 Shot 必须在目标总时长结束" in task["request"]


def test_editor_revision_does_not_send_historical_dialogue_or_asset_descriptions(env):
    seed(env)
    snapshot = bridge.segment_snapshot(1)
    snapshot["context"].update(source_excerpt="旧台词不可恢复标记", plot="旧剧情不可恢复标记",
        asset_manifest=[{"metadata": {"subject_description": "旧素材描述不可恢复标记"}}])
    prompt = "当前编辑版本，仅两人发言。"
    task = bridge.revision_task(1, {**snapshot, "edited_prompt": prompt, "issue": "重排时间",
        "use_editor_prompt": True}, snapshot_override=snapshot)
    assert task["input"]["base_prompt"] == prompt
    assert set(task["input"]["context"]) == {"requested_media_spec"}
    for marker in ("旧台词不可恢复标记", "旧剧情不可恢复标记", "旧素材描述不可恢复标记"):
        assert marker not in task["request"]
    assert "不得从其中恢复底稿已删除的台词" in task["request"]
