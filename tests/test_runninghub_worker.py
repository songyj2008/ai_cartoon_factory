from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event
import pytest

from services import context
from services.runninghub_worker import RunningHubWorker
from workflow import runninghub_sync, segment_runner


@pytest.fixture(autouse=True)
def allow_global_runtime_switch(isolated_runtime):
    # Worker ownership tests establish temporary roots and exercise global selection.
    token = context._PINNED_RUNTIME.set(None)
    try:
        yield
    finally:
        context._PINNED_RUNTIME.reset(token)


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
    monkeypatch.setattr(segment_runner, "_video_submit_concurrency_limit", lambda *args: 3)
    dispatched = Event()
    calls = []

    class Service:
        def _submit_saved_direct(self, indices, **kwargs):
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


def test_worker_finishes_original_runtimes_after_project_and_episode_switch(monkeypatch, tmp_path):
    from services import runninghub_worker as worker_module
    from workflow import video_merge
    outputs, episodes = tmp_path / 'outputs', tmp_path / 'episodes'
    monkeypatch.setattr(worker_module, 'OUTPUT_DIR', outputs)
    monkeypatch.setattr(worker_module, 'EPISODES_DIR', episodes)
    roots = [(outputs / 'A', 'A', ''), (outputs / 'B', 'B', ''),
             (episodes / 'A' / 'draft1', 'A', 'draft1'),
             (episodes / 'A' / 'draft2', 'A', 'draft2')]
    for root, project, episode in roots:
        root.mkdir(parents=True)
        (root / 'video_jobs.json').write_text('{}', encoding='utf-8')
        if episode:
            (root / '.draft').touch()
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', roots[-1])
    calls = []
    def sync():
        owner = context.capture_runtime()
        # Switch while the old operation is running; writes must stay pinned.
        monkeypatch.setattr(context, '_ACTIVE_RUNTIME', roots[1])
        assert context.capture_runtime() == owner
        (context.get_project_dir() / 'completed.txt').write_text('/'.join(owner[1:]), encoding='utf-8')
        calls.append(owner)
    monkeypatch.setattr(runninghub_sync, 'sync_runninghub_video_jobs', sync)
    monkeypatch.setattr(video_merge, 'finish_requested_video_merge', lambda: None)
    RunningHubWorker().tick()
    assert set(calls) == set(roots)
    for root, project, episode in roots:
        assert (root / 'completed.txt').read_text(encoding='utf-8') == project + '/' + episode
    assert context.capture_runtime() == roots[1]


def test_two_pinned_background_threads_do_not_share_runtime(monkeypatch, tmp_path):
    roots = [(tmp_path / 'first', 'A', 'draft1'), (tmp_path / 'second', 'B', 'draft2')]
    ready = [Event(), Event()]
    release = Event()
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', roots[0])
    def run(index):
        with context.pin_runtime(roots[index]):
            ready[index].set()
            assert release.wait(5)
            with context.pin_runtime():
                assert context.capture_runtime() == roots[index]
            return context.capture_runtime()
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, i) for i in range(2)]
        try:
            assert all(event.wait(5) for event in ready)
            monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (tmp_path / 'third', 'C', 'draft3'))
        finally:
            release.set()
        assert [future.result() for future in futures] == roots
