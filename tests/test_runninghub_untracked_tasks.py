from types import SimpleNamespace

import pytest

from workflow import runninghub_sync
from workflow.runninghub_queue import has_active_submission


@pytest.mark.parametrize("remote, expected", [
    ({"msg": "APIKEY_TASK_NOT_FOUND"}, "cancelled"),
    ({"status": "SUCCESS"}, "success"),
    ({"status": "RUNNING"}, "running"),
])
def test_old_duplicate_is_reconciled_without_changing_current_video(monkeypatch, remote, expected):
    attempt = {"task_id": "old", "status": "running"}
    render = {"render_id": "old-render", "status": "running", "submission": {
        "task_id": "old", "backend": "runninghub"}}
    segment = {"task_id": "latest", "status": "needs_regenerate", "video_path": "latest.mp4",
               "runninghub_attempts": [attempt], "renders": [render]}
    queried = []
    def status(task_id):
        queried.append(task_id)
        return remote
    monkeypatch.setattr(runninghub_sync, "RunningHubSubmitProvider", lambda **kw:
                        SimpleNamespace(client=SimpleNamespace(get_task_status=status)))
    monkeypatch.setattr("workflow.runninghub_usage.runninghub_attempt_model_id", lambda *a: "h3")
    monkeypatch.setattr("generation.render_history.find_render", lambda *a, **kw: render)
    monkeypatch.setattr(runninghub_sync, "record_runninghub_attempt",
                        lambda s, task_id, status: attempt.update(status=status))
    changed = runninghub_sync._sync_untracked_runninghub_tasks(segment)
    assert queried == ["old"]
    assert attempt["status"] == render["status"] == expected
    assert changed == (expected != "running")
    assert has_active_submission(segment) == (expected == "running")
    assert segment["task_id"] == "latest"
    assert segment["video_path"] == "latest.mp4"
    assert segment["status"] == "needs_regenerate"


def test_network_error_does_not_unlock_an_unknown_task(monkeypatch):
    segment = {"task_id": "latest", "runninghub_attempts": [{"task_id": "old", "status": "running"}]}
    def status(task_id):
        raise TimeoutError("network timed out")
    monkeypatch.setattr(runninghub_sync, "RunningHubSubmitProvider", lambda **kw:
                        SimpleNamespace(client=SimpleNamespace(get_task_status=status)))
    monkeypatch.setattr("workflow.runninghub_usage.runninghub_attempt_model_id", lambda *a: "h3")
    assert not runninghub_sync._sync_untracked_runninghub_tasks(segment)
    assert has_active_submission(segment)
