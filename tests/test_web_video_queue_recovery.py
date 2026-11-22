"""Regression tests for web-video queue state reconciliation.

The bridge SQLite database and video_jobs.json are two independent state
machines. A settled task used to leave its segment flagged as ``queued``,
which disabled regeneration forever. These tests cover the three recovery
paths: clearing/dismissing tasks, server-owned reconciliation, and the
explicit reset action.
"""
import json
import time

import pytest

from services import web_video_bridge as bridge


def _write_jobs(tmp_path, segments):
    (tmp_path / "video_jobs.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def _read_jobs(tmp_path):
    return json.loads((tmp_path / "video_jobs.json").read_text(encoding="utf-8-sig"))


def _queued_segment(index=1, task_id="task-1", backend="runninghub_web", **extra):
    segment = {
        "segment_index": index,
        "part_id": f"part_{index:03d}",
        "segment_uid": f"uid-{index}",
        "status": "queued",
        "backend": backend,
        "task_id": task_id,
        "active_render_id": f"render-{index}",
        "video_queue_entered_at": time.time() - 3600,
        "renders": [
            {
                "render_id": f"render-{index}",
                "status": "queued",
                "submission": {"backend": backend, "task_id": task_id},
            }
        ],
    }
    segment.update(extra)
    return segment


def _seed_task(tmp_path, *, task_id="task-1", state="queued", dismissed=0,
               created=None, updated=None, error="", segment_index=1,
               render_id="render-1", channel="runninghub_web"):
    created = time.time() - 3600 if created is None else created
    updated = created if updated is None else updated
    with bridge._connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id,channel,account,quota_day,state,runtime,project,episode,"
            "segment_index,segment_uid,render_id,input_json,created_at,updated_at,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, channel, "default", "2026-01-01", state, str(tmp_path),
             "__pytest__", "", segment_index, f"uid-{segment_index}", render_id,
             "{}", created, updated, error),
        )
        conn.execute("UPDATE tasks SET dismissed=? WHERE id=?", (dismissed, task_id))


def _task(task_id="task-1"):
    with bridge._connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row is not None else None


def _assert_released(tmp_path, index=0):
    segment = _read_jobs(tmp_path)["segments"][index]
    assert segment["status"] == "failed"
    assert segment["submitted"] is False
    assert "video_queue_entered_at" not in segment
    assert str(segment.get("error") or "").strip()


def test_clear_all_tasks_releases_stuck_queued_segment(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="submitted", updated=time.time() - 10)

    assert bridge.clear_all_tasks() == 1

    _assert_released(tmp_path)
    assert _task()["state"] == "uncertain"
    assert _task()["dismissed"] == 1


def test_dismiss_task_releases_segment_and_keeps_other_segments(tmp_path):
    finished = {
        "segment_index": 2,
        "segment_uid": "uid-2",
        "status": "success",
        "backend": "runninghub_web",
        "task_id": "other-task",
        "video_path": "done.mp4",
    }
    _write_jobs(tmp_path, [_queued_segment(), finished])
    _seed_task(tmp_path, state="uncertain")

    assert bridge.dismiss_tasks("task-1") == 1

    jobs = _read_jobs(tmp_path)
    _assert_released(tmp_path)
    assert jobs["segments"][1]["status"] == "success"
    assert jobs["segments"][1]["video_path"] == "done.mp4"


def test_dismiss_completed_tasks_leaves_video_history_and_other_tasks(tmp_path):
    media = tmp_path / "done.mp4"
    media.write_bytes(b"saved video")
    _write_jobs(tmp_path, [_queued_segment(), _queued_segment(
        index=2, task_id="complete", status="success", video_path=str(media))])
    jobs_before = (tmp_path / "video_jobs.json").read_bytes()
    for state in ("complete", "error", "uncertain", "cancelled_unclaimed", "queued", "submitted"):
        _seed_task(tmp_path, task_id=state, state=state)

    assert bridge.dismiss_completed_tasks() == 1
    assert bridge.dismiss_completed_tasks() == 0
    assert _task("complete")["dismissed"] == 1
    assert all(_task(state)["dismissed"] == 0 for state in (
        "error", "uncertain", "cancelled_unclaimed", "queued", "submitted"))
    assert (tmp_path / "video_jobs.json").read_bytes() == jobs_before
    assert media.read_bytes() == b"saved video"


def test_dismiss_rejects_live_task(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="claimed", created=time.time() - 5, updated=time.time() - 5)

    with pytest.raises(ValueError):
        bridge.dismiss_tasks("task-1")
    assert _read_jobs(tmp_path)["segments"][0]["status"] == "queued"


def test_reconcile_releases_settled_task(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="uncertain", dismissed=1, error="网页提交未确认")

    assert bridge.reconcile_queued_segments() == [1]

    _assert_released(tmp_path)
    assert "网页提交未确认" in _read_jobs(tmp_path)["segments"][0]["error"]


def test_reconcile_keeps_live_task(tmp_path):
    recent = time.time() - 60
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="submitted", created=recent, updated=recent)

    assert bridge.reconcile_queued_segments() == []
    assert _read_jobs(tmp_path)["segments"][0]["status"] == "queued"


def test_reconcile_releases_silent_live_task_and_retires_it(tmp_path):
    stale = time.time() - 7 * 3600
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="submitted", created=stale, updated=stale)

    assert bridge.reconcile_queued_segments() == [1]

    _assert_released(tmp_path)
    assert _task()["state"] == "uncertain"
    assert _task()["dismissed"] == 1


def test_reconcile_releases_orphan_segment(tmp_path):
    _write_jobs(tmp_path, [_queued_segment(task_id="missing-task")])

    assert bridge.reconcile_queued_segments() == [1]
    _assert_released(tmp_path)


def test_reconcile_ignores_local_runninghub_slot_queue(tmp_path):
    _write_jobs(tmp_path, [_queued_segment(backend="runninghub")])
    _seed_task(tmp_path, state="uncertain", dismissed=1, channel="runninghub")

    assert bridge.reconcile_queued_segments() == []
    assert _read_jobs(tmp_path)["segments"][0]["status"] == "queued"


def test_reconcile_releases_failed_retry_and_preserves_prior_video(tmp_path):
    _write_jobs(tmp_path, [_queued_segment(video_path="already.mp4")])
    _seed_task(tmp_path, state="uncertain", dismissed=1)

    assert bridge.reconcile_queued_segments() == [1]
    _assert_released(tmp_path)
    assert _read_jobs(tmp_path)['segments'][0]['video_path'] == 'already.mp4'


def test_reset_queued_segment_cancels_task_and_unlocks_card(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state="queued")

    result = bridge.reset_queued_segment(1, "uid-1")

    assert result["segments"][0]["status"] == "failed"
    _assert_released(tmp_path)
    assert _task()["state"] == "cancelled_unclaimed"
    assert _task()["dismissed"] == 1
    assert _task()["lease"] == ""


def test_reset_rejects_non_web_and_non_queued_segments(tmp_path):
    _write_jobs(tmp_path, [_queued_segment(backend="runninghub")])
    with pytest.raises(ValueError):
        bridge.reset_queued_segment(1, "uid-1")

    _write_jobs(tmp_path, [_queued_segment(status="success")])
    with pytest.raises(ValueError):
        bridge.reset_queued_segment(1, "uid-1")


def test_reset_rejects_stale_segment_uid(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    with pytest.raises(ValueError):
        bridge.reset_queued_segment(1, "uid-from-another-page")


def test_worker_runs_web_reconcile_with_throttling(monkeypatch, tmp_path):
    from services.runninghub_worker import RunningHubWorker

    (tmp_path / "video_jobs.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("workflow.runninghub_sync.sync_runninghub_video_jobs", lambda: (None, False))
    monkeypatch.setattr("workflow.video_merge.finish_requested_video_merge", lambda: None)
    calls = []
    monkeypatch.setattr(bridge, "reconcile_queued_segments", lambda **kwargs: calls.append(kwargs) or [])

    worker = RunningHubWorker()
    worker.tick()
    worker.tick()

    assert len(calls) == 1


def test_group_panel_shows_reset_button_only_for_web_queued_segment():
    from ui import group_panel

    web_html = group_panel.render_groups_panel({"segments": [_queued_segment()]})
    assert "重置排队" in web_html
    assert "resetQueuedSegment" in web_html

    local_html = group_panel.render_groups_panel(
        {"segments": [_queued_segment(backend="runninghub")]}
    )
    assert "重置排队" not in local_html

    done_html = group_panel.render_groups_panel(
        {"segments": [_queued_segment(status="success")]}
    )
    assert "重置排队" not in done_html


def test_reconcile_releases_running_segment_after_task_clear(tmp_path):
    _write_jobs(tmp_path, [_queued_segment(status='running')])
    _seed_task(tmp_path, state='uncertain', dismissed=1)
    assert bridge.reconcile_queued_segments() == [1]
    _assert_released(tmp_path)


def test_reconcile_expires_superseded_claim_even_without_current_segment(tmp_path):
    _write_jobs(tmp_path, [])
    _seed_task(tmp_path, state='claimed', updated=time.time() - 301)
    assert bridge.reconcile_queued_segments() == []
    assert _task()['state'] == 'uncertain'


def test_reconcile_keeps_preparation_with_recent_heartbeat(tmp_path):
    _write_jobs(tmp_path, [_queued_segment()])
    _seed_task(tmp_path, state='claimed', updated=time.time() - 10)
    assert bridge.reconcile_queued_segments() == []
    assert _task()['state'] == 'claimed'


def test_execution_status_checks_browser_owner_even_after_lease_is_cleared(tmp_path):
    _seed_task(tmp_path, state='uncertain', dismissed=1)
    with bridge._connect() as conn:
        conn.execute("UPDATE tasks SET client_id='owner',lease='' WHERE id='task-1'")
    assert bridge.execution_status('task-1', 'owner')['state'] == 'uncertain'
    with pytest.raises(PermissionError):
        bridge.execution_status('task-1', 'other')
    assert bridge.execution_status('missing', 'owner') is None


def test_missing_task_recovery_preserves_previously_selected_successful_render(tmp_path):
    segment = _queued_segment(task_id='missing', active_render_id='previous', video_path='previous.mp4')
    previous = {'render_id':'previous', 'status':'success',
                'submission':{'backend':'runninghub_web','task_id':'previous-task'},
                'output':{'video_path':'previous.mp4'}}
    segment['renders'].append(previous)
    _write_jobs(tmp_path, [segment])
    assert bridge.reconcile_queued_segments() == [1]
    saved = _read_jobs(tmp_path)['segments'][0]
    assert next(render for render in saved['renders'] if render['render_id']=='previous')['status']=='success'
    assert saved['video_path']=='previous.mp4'
