"""Scene deletion is scoped to an unused registry entry, never its members."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_scene_state_jobs import runtime, ready_sample, save, load
from generation.pipelines.minimax_h3.continuity.calibration import delete_unused_scene, scene_members


def data():
    jobs = ready_sample()
    jobs['h3_scene_continuity']['contexts']['unused'] = {
        'name': '重复的会议室', 'config_revision': 'original',
        'initial_state': {'actor': {'present': {'value': True}}}}
    return jobs


def test_deleting_unused_scene_preserves_all_segments_and_other_scenes(runtime):
    project, calls = runtime
    jobs = data()
    from workflow.video_merge import save_video_jobs
    save_video_jobs(jobs)
    jobs = load(project)
    assert scene_members(jobs)['unused'] == []
    result = delete_unused_scene({'context_id': 'unused', 'scene_initial_revision': 'original'})
    assert result == {'deleted_context_id': 'unused', 'deleted_scene_name': '重复的会议室'}
    current = load(project)
    assert 'unused' not in current['h3_scene_continuity']['contexts']
    assert current['segments'] == jobs['segments']
    assert current['h3_scene_continuity']['contexts']['room'] == jobs['h3_scene_continuity']['contexts']['room']
    assert not calls


@pytest.mark.parametrize('case', ['in_use', 'changed', 'missing', 'empty'])
def test_invalid_or_concurrently_changed_scene_is_not_deleted(runtime, case):
    from generation.pipelines.minimax_h3.editor_draft import EditorConflictError
    project, calls = runtime
    jobs = data()
    save(project, jobs)
    payload = {'context_id': 'unused', 'scene_initial_revision': 'original'}
    error = ValueError
    if case == 'in_use':
        payload = {'context_id': 'room'}
        error = EditorConflictError
    elif case == 'changed':
        payload['scene_initial_revision'] = 'stale'
        error = EditorConflictError
    elif case == 'missing':
        payload['context_id'] = 'gone'
    else:
        payload['context_id'] = ''
    before = (project / 'video_jobs.json').read_bytes()
    with pytest.raises(error):
        delete_unused_scene(payload)
    assert (project / 'video_jobs.json').read_bytes() == before
    assert not calls


def test_read_only_runtime_cannot_delete_scene(runtime, monkeypatch):
    project, _ = runtime
    save(project, data())
    before = (project / 'video_jobs.json').read_bytes()
    def reject(*args):
        raise PermissionError('只读项目')
    monkeypatch.setattr('services.context.assert_current_runtime_writable', reject)
    with pytest.raises(PermissionError):
        delete_unused_scene({'context_id': 'unused'})
    assert (project / 'video_jobs.json').read_bytes() == before


def test_http_snapshot_lists_scene_usage_and_deletion_rechecks_it(runtime, monkeypatch):
    from ui import api_routes
    project, calls = runtime
    jobs = data()
    save(project, jobs)
    (project / 'beats.json').write_text(json.dumps({'beats': []}), encoding='utf-8')
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    client = TestClient(app)
    snapshot = client.post('/api/segment/editor/snapshot', json={'segment_uid': '1'}).json()
    assert snapshot['scene_members']['unused'] == []
    assert [row['segment_uid'] for row in snapshot['scene_members']['room']] == ['1', '2', '3']
    # Another editor joins the scene after the browser read its empty usage.
    jobs['segments'][2]['scene_context_id'] = 'unused'
    save(project, jobs)
    response = client.post('/api/segment/editor/delete-scene', json={
        'context_id': 'unused', 'scene_initial_revision': 'original'})
    assert response.status_code == 409
    assert response.json()['code'] == 'SCENE_IN_USE'
    assert 'part_003' in response.json()['message']
    jobs['segments'][2]['scene_context_id'] = 'room'
    save(project, jobs)
    response = client.post('/api/segment/editor/delete-scene', json={
        'context_id': 'unused', 'scene_initial_revision': 'original'})
    assert response.status_code == 200, response.text
    assert response.json()['deleted_context_id'] == 'unused'
    assert not calls
