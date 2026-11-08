"""Queue dispatch must leave its job lock before automatic state preparation."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services import context
from workflow import segment_runner


@pytest.fixture
def queue(monkeypatch, tmp_path):
    state = SimpleNamespace(depth=0, reads=0, calls=[], active=1, limit=3,
                            first=(tmp_path / 'first', 'first', ''),
                            second=(tmp_path / 'second', 'second', ''))
    state.payload = {'segments': [
        {'segment_index': 7, 'status': 'queued', 'backend': 'runninghub'},
        {'segment_index': 2, 'status': 'queued', 'backend': 'runninghub'},
        {'segment_index': 4, 'status': 'queued', 'backend': 'runninghub'},
        {'segment_index': 1, 'status': 'queued', 'backend': 'doubao_web'}]}

    @contextmanager
    def tracked_lock():
        state.depth += 1
        try:
            yield
        finally:
            state.depth -= 1

    def load():
        assert state.depth == 1, 'queue selection must read under its job lock'
        assert context.capture_runtime() == state.first
        state.reads += 1
        # A UI project switch must not redirect the service called afterwards.
        monkeypatch.setattr(context, '_ACTIVE_RUNTIME', state.second)
        return state.payload

    def submit(indices, **kwargs):
        assert state.depth == 0, 'state preparation must not hold the queue job lock'
        assert context.capture_runtime() == state.first
        state.calls.append((indices, kwargs))
        return state.payload

    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', state.first)
    runtime_token = context._PINNED_RUNTIME.set(None)
    monkeypatch.setitem(segment_runner.CONFIG, 'video_submit_backend', 'runninghub')
    monkeypatch.setattr(segment_runner, 'video_jobs_lock', tracked_lock)
    monkeypatch.setattr(segment_runner, 'load_video_jobs', load)
    monkeypatch.setattr(segment_runner, '_runninghub_active_task_count', lambda rows: state.active)
    monkeypatch.setattr(segment_runner, '_video_submit_concurrency_limit', lambda *a: state.limit)
    monkeypatch.setattr('generation.service.get_generation_service', lambda: SimpleNamespace(_submit_saved_direct=submit))
    try:
        yield state
    finally:
        context._PINNED_RUNTIME.reset(runtime_token)


def test_queue_releases_lock_before_h3_preparation_and_keeps_runtime_pinned(queue):
    result = segment_runner.dispatch_queued_runninghub_segments()
    assert result is queue.payload
    assert queue.calls == [({2, 4}, {'merge_after': False, 'wait': False,
                                   'backend': 'runninghub', 'expected_backend': 'runninghub'})]
    assert queue.depth == 0 and queue.reads == 1
    assert context.capture_runtime() == queue.second


@pytest.mark.parametrize('reason', ['no_queue', 'full', 'no_slots'])
def test_dispatch_without_work_keeps_existing_noop_behavior(queue, monkeypatch, reason):
    if reason == 'no_queue':
        queue.payload['segments'] = []
    elif reason == 'full':
        queue.active = queue.limit
    elif reason == 'no_slots':
        queue.active = 0
        queue.limit = 0
    assert segment_runner.dispatch_queued_runninghub_segments() is None
    assert queue.calls == [] and queue.depth == 0
    assert queue.reads == 1


def test_explicit_runninghub_queue_dispatches_when_project_default_is_local(queue, monkeypatch):
    monkeypatch.setitem(segment_runner.CONFIG, 'video_submit_backend', 'comfyui')
    assert segment_runner.dispatch_queued_runninghub_segments() is queue.payload
    assert queue.calls[0][0] == {2, 4}


def test_service_failure_does_not_leave_queue_locked_or_runtime_pinned(queue, monkeypatch):
    def fail(*args, **kwargs):
        assert queue.depth == 0
        assert context.capture_runtime() == queue.first
        raise ValueError('人物状态存在明确冲突')
    monkeypatch.setattr('generation.service.get_generation_service',
                        lambda: SimpleNamespace(_submit_saved_direct=fail))
    with pytest.raises(ValueError, match='明确冲突'):
        segment_runner.dispatch_queued_runninghub_segments()
    assert queue.depth == 0
    assert context.capture_runtime() == queue.second
