import json
import uuid

import pytest

from services import text_jobs


@pytest.fixture
def operation_store(tmp_path, monkeypatch):
    storage = tmp_path / 'operations'
    monkeypatch.setattr(text_jobs, 'root', lambda: storage)

    def add(state, **fields):
        identifier = str(uuid.uuid4())
        work = storage / identifier / 'work'
        work.mkdir(parents=True)
        (work / 'candidate.json').write_text('{}', encoding='utf-8')
        item = {'id': identifier, 'state': state, 'work': str(work), **fields}
        with text_jobs.database() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                       (identifier, state, 1, 1, json.dumps(item)))
        return identifier, work.parent

    return add


def test_delete_finished_operation_removes_record_and_candidate_files(operation_store):
    identifier, job_dir = operation_store('error')

    result = text_jobs.delete(identifier)

    assert result == {'id': identifier, 'deleted': True}
    assert not job_dir.exists()
    with text_jobs.database() as db:
        assert db.execute('SELECT 1 FROM operations WHERE id=?', (identifier,)).fetchone() is None


@pytest.mark.parametrize('state', ['queued', 'running', 'applying', 'needs_attention'])
def test_delete_rejects_active_or_unresolved_operation(operation_store, state):
    identifier, job_dir = operation_store(state)

    with pytest.raises(ValueError, match='仍在执行或需要处理'):
        text_jobs.delete(identifier)

    assert job_dir.exists()
    with text_jobs.database() as db:
        assert db.execute('SELECT 1 FROM operations WHERE id=?', (identifier,)).fetchone() is not None


def test_clear_finished_removes_candidates_but_preserves_live_and_recovery_tasks(operation_store):
    removable = [operation_store(state) for state in ('ready', 'applied', 'cancelled', 'error')]
    retained = [operation_store(state) for state in ('queued', 'running', 'needs_attention')]

    assert text_jobs.clear_finished() == {'deleted': 4}
    assert all(not directory.exists() for _, directory in removable)
    assert all(directory.exists() for _, directory in retained)
    with text_jobs.database() as db:
        states = {row['state'] for row in db.execute('SELECT state FROM operations')}
    assert states == {'queued', 'running', 'needs_attention'}


def test_clear_completed_only_removes_successful_records(operation_store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import web_video_bridge
    from ui.web_llm_routes import register_web_llm_routes

    _, completed_dir = operation_store('applied')
    retained = [operation_store(state) for state in ('ready', 'running', 'queued', 'applying', 'needs_attention',
                                                    'cancelled', 'error')]
    parent, parent_dir = operation_store('applied', batch_version=1)
    retained.append((parent, parent_dir))
    retained.extend(operation_store(state, batch_id=parent) for state in ('error', 'applied'))
    video_calls = []
    monkeypatch.setattr(web_video_bridge, 'dismiss_completed_tasks', lambda: video_calls.append(True) or 2)
    app = FastAPI()
    register_web_llm_routes(app)
    with TestClient(app) as client:
        response = client.post('/api/web-llm/operations/clear-completed', json={})

    assert response.json() == {'ok': True, 'deleted': 1, 'video_tasks': 2}
    assert video_calls == [True]
    assert not completed_dir.exists()
    assert all((directory / 'work' / 'candidate.json').exists() for _, directory in retained)
    with text_jobs.database() as db:
        assert {row['id'] for row in db.execute('SELECT id FROM operations')} == {identifier for identifier, _ in retained}


def test_history_retention_keeps_unapplied_results_and_recovery_work(operation_store):
    applied, applied_dir = operation_store('applied')
    failed, failed_dir = operation_store('error')
    cancelled, cancelled_dir = operation_store('cancelled')
    retained = [operation_store(state) for state in ('ready', 'needs_attention', 'running', 'queued')]
    assert text_jobs._delete_expired_applied(now=2 + text_jobs.APPLIED_RETENTION_SECONDS) == 1
    assert not applied_dir.exists()
    assert failed_dir.exists() and cancelled_dir.exists()
    assert text_jobs._delete_expired_applied(now=2 + text_jobs.FAILED_RETENTION_SECONDS) == 2
    assert all(directory.exists() for _, directory in retained)
