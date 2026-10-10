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


def test_missing_snapshot_never_uses_edited_prompt_as_video_prompt(env):
    value = seed(env)
    for render in value["segments"][0]["renders"]:
        render.pop("prompt_snapshot", None)
    (env / "video_jobs.json").write_text(json.dumps(value), encoding="utf-8")
    snapshot = bridge.segment_snapshot(1)
    assert not snapshot["snapshot_known"]
    with pytest.raises(ValueError, match="原提示词"):
        bridge.revision_task(1, {**snapshot, "edited_prompt": "new editor", "issue": "wrong motion"})


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
    assert bridge.task_status(stale["id"])["status"] == "queued"
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


def test_wait_for_timed_out_web_task_requires_attention(env, monkeypatch):
    from services.text_jobs import NeedsAttentionError

    monkeypatch.setattr(bridge, 'task_status', lambda identifier: {
        'id':identifier, 'status':'timeout', 'message':'网页任务超时',
    })
    with pytest.raises(NeedsAttentionError, match='不会自动重发'):
        bridge.wait_for_llm('system','user',kind='text',entry='story',provider='deepseek')


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


@pytest.mark.parametrize("use_editor,expected", [(True, "unsaved editor"), (False, "video prompt")])
def test_revision_uses_only_selected_base(env, use_editor, expected):
    seed(env)
    snapshot = bridge.segment_snapshot(1)
    task = bridge.revision_task(1, {**snapshot, "edited_prompt": "unsaved editor", "issue": "fix motion", "use_editor_prompt": use_editor})
    assert task["input"]["base_prompt"] == expected
    assert set(task["input"]) == {"base_prompt", "target_duration_sec", "video_issues", "expected_result", "context"}
    assert task["metadata"]["force_new_conversation"] is use_editor
    assert task["metadata"]["editor_prompt"] == "unsaved editor"


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
    assert task["input"]["context"]["historical_video_media_spec"]["duration_sec"] == 4
    assert task["input"]["base_prompt"] == ("8 seconds" if use_editor else "video prompt")
    assert "最后一个 Shot 必须在目标总时长结束" in task["request"]
