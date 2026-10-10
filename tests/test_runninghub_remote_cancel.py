from types import SimpleNamespace
import pytest
from services import context
from workflow import runninghub_sync as sync, segment_runner


@pytest.mark.parametrize("response", [{"code": 807, "msg": "APIKEY_TASK_NOT_FOUND", "data": None}, {"code": 0, "data": {"status": "CANCELLED"}}])
@pytest.mark.parametrize("render_status", ["running", "needs_regenerate"])
def test_remote_terminal_updates_segment_and_matching_render(monkeypatch, tmp_path, response, render_status):
    monkeypatch.setattr(context, "_ACTIVE_RUNTIME", (tmp_path, "test", ""))
    old = {"render_id": "old", "status": "success", "submission": {"task_id": "old"}}
    current = {"render_id": "current", "status": render_status, "submission": {"task_id": "task", "submitted": True}}
    segment = {"segment_index": 10, "part_id": "part_010", "model_id": "minimax_h3_local_ref2va", "status": "running", "submitted": True, "backend": "runninghub", "task_id": "task", "active_render_id": "old", "renders": [old, current]}
    payload = {"segments": [segment]}
    client = SimpleNamespace(get_task_status=lambda _: response, get_task_outputs=lambda _: [])
    monkeypatch.setattr(sync, "RunningHubSubmitProvider", lambda **_: SimpleNamespace(client=client))
    monkeypatch.setattr(sync, "load_video_jobs", lambda: payload)
    saved = []
    monkeypatch.setattr(sync, "save_video_jobs", lambda p: saved.append(p))
    monkeypatch.setattr(sync, "mark_part_failed", lambda *args: None)
    monkeypatch.setattr(segment_runner, "dispatch_queued_runninghub_segments", lambda: None)
    result, changed = sync.sync_runninghub_video_jobs()
    assert changed and saved
    assert segment["status"] == "failed" and not segment["submitted"]
    assert current["status"] == "failed" and not current["submission"]["submitted"]
    assert old["status"] == "success"
    assert not sync._has_terminal_failure_status({"code": 0, "msg": "success", "data": "RUNNING"})
    assert not sync._has_terminal_failure_status({"code": 401, "msg": "UNAUTHORIZED"})


def test_terminal_render_survives_segment_edit_projection():
    from generation.render_history import begin_render, update_render
    segment = {"segment_index": 10}
    render = begin_render(segment, model_id="minimax_h3_local_ref2va", backend="runninghub", status="running")
    update_render(segment, render["render_id"], {"task_id": "remote", "submitted": True}, status="failed")
    update_render(segment, render["render_id"], {"task_id": "remote", "submitted": False, "status": "needs_regenerate"})
    assert render["status"] == "failed"
    assert not render["submission"]["submitted"]
