"""Scene calibration is shared, including roles first filmed in later parts."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generation.pipelines.minimax_h3.continuity.calibration import calibration_snapshot, save_initial_calibration
from generation.pipelines.minimax_h3.continuity.inheritance import state_status
from generation.pipelines.minimax_h3.editor_draft import EditorConflictError
from test_scene_state_jobs import runtime, ready_sample, save, load


def asset(role, name):
    return {'asset_id': 'image-' + role, 'type': 'image', 'role': name,
            'metadata': {'asset_kind': 'character', 'role_id': role}}


def scene_data():
    jobs = ready_sample()
    jobs['segments'][0]['generation_config']['asset_manifest'] = [asset('actor', '甲')]
    jobs['segments'][1]['generation_config']['asset_manifest'] = [asset('new-role', '乙')]
    jobs['segments'][2]['scene_context_id'] = 'other'
    jobs['segments'][2]['generation_config']['asset_manifest'] = [asset('unrelated', '丙')]
    scene = jobs['h3_scene_continuity']['contexts']['room']
    scene['config_revision'] = 'scene-version'
    scene['initial_state']['stored-only'] = {'present': {'value': False}}
    return jobs


def test_every_member_reads_all_scene_roles_and_stored_only_roles():
    jobs = scene_data()
    original = copy.deepcopy(jobs)
    first, second = [calibration_snapshot(jobs, row) for row in jobs['segments'][:2]]
    assert first == second
    assert first['scene_roles'] == [
        {'id': 'actor', 'name': '甲'}, {'id': 'new-role', 'name': '乙'},
        {'id': 'stored-only', 'name': 'stored-only'}]
    assert first['scene_initial_revision'] == 'scene-version'
    assert [row['segment_uid'] for row in first['scene_member_parts']] == ['1', '2']
    first['scene_initial_state']['stored-only']['present']['value'] = True
    assert jobs == original


def test_unassigned_part_does_not_borrow_other_scenes_roles():
    jobs = scene_data()
    target = jobs['segments'][0]
    target['scene_context_id'] = None
    result = calibration_snapshot(jobs, target)
    assert result['scene_roles'] == [{'id': 'actor', 'name': '甲'}]
    assert result['scene_initial_state'] == {} and result['scene_member_parts'] == []


def test_equivalent_calibration_keeps_revision_and_existing_evidence():
    jobs = ready_sample()
    scene = jobs['h3_scene_continuity']['contexts']['room']
    scene['config_revision'] = 'original'
    scene['initial_state']['actor']['posture']['source_quote'] = '原校准依据'
    original = copy.deepcopy(scene)
    payload = {'scene_initial_revision': 'original'}
    state = {'actor': {'posture': {'value': '坐着'}, 'present': {'value': True}}}
    assert save_initial_calibration(scene, state, payload) is False
    assert scene == original
    assert state_status(jobs, '1')['ready'] is True


def test_stale_calibration_cannot_overwrite_a_shared_update():
    jobs = scene_data()
    scene = jobs['h3_scene_continuity']['contexts']['room']
    original = copy.deepcopy(scene)
    with pytest.raises(EditorConflictError) as error:
        save_initial_calibration(scene, {'actor': {'present': {'value': True}}},
                                 {'scene_initial_revision': 'older-version'})
    assert error.value.code == 'SCENE_INITIAL_CONFLICT'
    assert scene == original


def test_unchanged_form_preserves_unknown_fields_without_expiring_records():
    jobs = ready_sample()
    scene = jobs['h3_scene_continuity']['contexts']['room']
    scene['config_revision'] = 'unchanged'
    before = copy.deepcopy(scene)
    state = copy.deepcopy(scene['initial_state'])
    state['actor']['position'] = {'value': None, 'relative_to_role_id': None}
    assert save_initial_calibration(scene, state, {'scene_initial_revision': 'unchanged'}) is False
    assert scene == before
    assert state_status(jobs, '1')['ready'] is True


def test_opening_snapshot_remains_readable_when_target_end_state_expires():
    from generation.pipelines.minimax_h3.editor_draft import saved_baseline
    jobs = ready_sample()
    scene = jobs['h3_scene_continuity']['contexts']['room']
    scene['initial_state']['added'] = {'present': {'value': True}}
    target = jobs['segments'][0]
    result = saved_baseline(jobs, target)
    assert result['state_context'] is None
    assert result['state_status']['reason_code'] == 'initial_state_changed'
    assert result['state_input_context']['input_state']['added']['present']['value'] is True
    assert 'added' not in target['h3_continuity']['end_state']


def test_changed_initial_state_identifies_scene_first_part_without_rewriting_records():
    jobs = ready_sample()
    original_records = copy.deepcopy([row['h3_continuity'] for row in jobs['segments']])
    scene = jobs['h3_scene_continuity']['contexts']['room']
    state = copy.deepcopy(scene['initial_state'])
    state['new-role'] = {'position': {'value': '桌前'}, 'posture': {'value': '站着'},
                         'present': {'value': True}}
    assert save_initial_calibration(scene, state, {}) is True
    for uid in ('1', '2'):
        status = state_status(jobs, uid)
        assert status['ready'] is False
        assert status['reason_code'] == 'initial_state_changed'
        assert status['blocking_segment_uid'] == '1'
        assert '场景初态已变化' in status['detail']
        assert '重新提取本场景首段状态' in status['detail']
        assert '沿用上段' not in status['detail']
    assert [row['h3_continuity'] for row in jobs['segments']] == original_records


def test_snapshot_and_calibration_http_use_one_shared_contract(runtime, monkeypatch):
    from ui import api_routes
    project, calls = runtime
    jobs = scene_data()
    jobs['h3_scene_continuity']['contexts']['room']['name'] = 'scene_011'
    monkeypatch.setattr('services.project_bible.load_project_bible', lambda: {
        'core_characters': [{'id': 'actor', 'display_name': '项目角色甲'},
                            {'id': 'new-role', 'display_name': '项目角色乙'}],
        'allowed_backgrounds': [{'id': 'scene_011', 'display_name': '会议室'}]})
    save(project, jobs)
    (project / 'beats.json').write_text(json.dumps({'beats': []}), encoding='utf-8')
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    client = TestClient(app)
    first = client.post('/api/segment/editor/snapshot', json={'segment_uid': '1'}).json()
    second = client.post('/api/segment/editor/snapshot', json={'segment_uid': '2'}).json()
    assert first['scene_roles'] == second['scene_roles']
    assert [role['name'] for role in first['scene_roles'][:2]] == ['项目角色甲', '项目角色乙']
    assert [role['id'] for role in first['scene_roles'][:2]] == ['actor', 'new-role']
    assert first['scene_contexts']['room']['name'] == 'scene_011'
    assert first['scene_contexts']['room']['display_name'] == '会议室'
    assert first['suggested_scene_name'] == '会议室'
    assert first['scene_initial_state'] == second['scene_initial_state']
    assert first['scene_initial_revision'] == second['scene_initial_revision'] == 'scene-version'
    before = (project / 'video_jobs.json').read_bytes()
    response = client.post('/api/segment/editor/scene-config', json={
        'segment_uid': '2', 'context_id': 'room', 'scene_initial_revision': 'older-version',
        'initial_state': {'actor': {'present': {'value': True}}}})
    assert response.status_code == 409
    assert response.json()['code'] == 'SCENE_INITIAL_CONFLICT'
    assert (project / 'video_jobs.json').read_bytes() == before
    assert not calls


def test_layout_http_roundtrip_preserves_initial_and_rejects_stale_save(runtime, monkeypatch):
    from ui import api_routes
    project, calls = runtime
    jobs = scene_data()
    target = jobs['segments'][0]
    target['generation_config']['asset_manifest'].append({
        'type': 'image', 'path': 'room-v1.png', 'metadata': {
            'asset_kind': 'background', 'background_id': 'room-background'}})
    initial = copy.deepcopy(jobs['h3_scene_continuity']['contexts']['room']['initial_state'])
    save(project, jobs)
    (project / 'beats.json').write_text(json.dumps({'beats': []}), encoding='utf-8')
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *args: None)
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    client = TestClient(app)
    response = client.post('/api/segment/editor/scene-config', json={
        'segment_uid': '1', 'context_id': 'room', 'scene_layout': '原图近侧有两把椅子，门位于桌子右侧',
        'layout_revision': None})
    assert response.status_code == 200
    result = client.post('/api/segment/editor/snapshot', json={'segment_uid': '1'}).json()
    assert result['scene_layout']['description'] == '原图近侧有两把椅子，门位于桌子右侧'
    assert result['scene_layout']['current'] is True
    assert result['layout_revision']
    assert result['scene_initial_state'] == initial
    before = (project / 'video_jobs.json').read_bytes()
    response = client.post('/api/segment/editor/scene-config', json={
        'segment_uid': '1', 'context_id': 'room', 'scene_layout': '过期页面的新布局', 'layout_revision': None})
    assert response.status_code == 409
    assert response.json()['code'] == 'SCENE_INITIAL_CONFLICT'
    assert (project / 'video_jobs.json').read_bytes() == before
    changed = load(project)
    changed['segments'][0]['generation_config']['asset_manifest'][-1]['path'] = 'room-v2.png'
    save(project, changed)
    result = client.post('/api/segment/editor/snapshot', json={'segment_uid': '1'}).json()
    assert result['scene_layout']['current'] is False
    assert result['scene_layout']['description'] == '原图近侧有两把椅子，门位于桌子右侧'
    assert result['scene_initial_state'] == initial
    assert not calls
