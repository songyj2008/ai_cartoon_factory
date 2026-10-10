from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event
import pytest

from services import context
from services.runninghub_worker import RunningHubWorker
from workflow import runninghub_sync, segment_runner


@pytest.fixture(autouse=True)
def isolated_worker_roots(monkeypatch, tmp_path):
    monkeypatch.setattr('services.runninghub_worker.OUTPUT_DIR', tmp_path / 'outputs')
    monkeypatch.setattr('services.runninghub_worker.EPISODES_DIR', tmp_path / 'episodes')


def test_worker_recovers_and_dispatches_without_browser(monkeypatch, tmp_path):
    (tmp_path / "video_jobs.json").write_text('{}', encoding="utf-8")
    monkeypatch.setattr(context, "_ACTIVE_RUNTIME", (tmp_path, "test", ""))
    monkeypatch.setitem(segment_runner.CONFIG, "video_submit_backend", "runninghub")
    payload = {"segments": [{"segment_index": 4, "status": "queued", "backend": "runninghub"}]}
    monkeypatch.setattr(runninghub_sync, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "_video_submit_concurrency_limit", lambda: 3)
    dispatched = Event()
    calls = []

    class Service:
        def submit_saved_segments(self, indices, **kwargs):
            calls.append(indices)
            payload["segments"][0].update(status="running", task_id="test-task")
            dispatched.set()
            return payload

    monkeypatch.setattr("generation.service.get_generation_service", Service)
    original = runninghub_sync.sync_runninghub_video_jobs
    attempts = []

    def sync():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("temporary network failure")
        if not dispatched.is_set():
            return original()

    monkeypatch.setattr(runninghub_sync, "sync_runninghub_video_jobs", sync)
    worker = RunningHubWorker(interval=0.01)
    try:
        worker.start()
        thread = worker._thread
        worker.start()
        assert worker._thread is thread
        assert dispatched.wait(5)
    finally:
        worker.stop()
    assert not thread.is_alive()
    assert len(attempts) >= 2
    assert calls == [{4}]


def test_runtime_stays_pinned_across_project_switch_and_pool(monkeypatch, tmp_path):
    first = (tmp_path / "first", "first", "")
    second = (tmp_path / "second", "second", "archive")
    monkeypatch.setattr(context, "_ACTIVE_RUNTIME", first)
    with context.pin_runtime():
        monkeypatch.setattr(context, "_ACTIVE_RUNTIME", second)
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(copy_context().run, context.get_project_dir).result() == first[0]
        assert context.get_current_project_name() == "first"
        assert not context.is_archive_episode_active()
    assert context.get_project_dir() == second[0]
    assert context.is_archive_episode_active()


def test_worker_skips_archive_and_missing_job_file(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runninghub_sync, "sync_runninghub_video_jobs", lambda: calls.append(True))
    monkeypatch.setattr(context, "_ACTIVE_RUNTIME", (tmp_path, "test", ""))
    worker = RunningHubWorker()
    worker.tick()
    (tmp_path / "video_jobs.json").write_text('{}', encoding="utf-8")
    monkeypatch.setattr(context, "_ACTIVE_RUNTIME", (tmp_path, "test", "archive"))
    worker.tick()
    assert calls == []
