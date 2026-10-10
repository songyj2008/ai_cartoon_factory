import json
import uuid

import pytest

from services import text_jobs


@pytest.fixture
def operation_store(tmp_path, monkeypatch):
    storage = tmp_path / 'operations'
    monkeypatch.setattr(text_jobs, 'root', lambda: storage)

    def add(state):
        identifier = str(uuid.uuid4())
        work = storage / identifier / 'work'
        work.mkdir(parents=True)
        (work / 'candidate.json').write_text('{}', encoding='utf-8')
        item = {'id': identifier, 'state': state, 'work': str(work)}
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
