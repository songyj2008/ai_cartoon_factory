import json

import pytest

from services import extension_updates as updates, web_llm_bridge as bridge


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'BRIDGE_DIR', tmp_path / 'state')
    monkeypatch.setattr(updates, 'BASE_DIR', tmp_path / '应用 程序')
    directory = updates.extension_directory()
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text(json.dumps({'version': '1.2.3'}))
    for name in ('worker.js', 'panel.html', 'extension_update.js', 'extension_build.js'):
        (directory / name).write_text('initial')
    bridge.heartbeat('chrome', ['deepseek'])
    bridge.heartbeat('edge', ['deepseek'])
    return directory


def test_update_waits_for_receipt_and_verifies_loaded_build(setup):
    result = updates.request_update()
    target = result['source']
    assert target['path'] == str(setup.resolve())
    assert updates.source_info()['build'] == target['build']
    command = updates.heartbeat('chrome', {'update_capable': True}, {})
    assert command['phase'] == 'requested'
    assert updates.heartbeat('chrome', {'update_capable': True}, {'id': 'wrong', 'phase': 'reloaded'})
    assert updates.request_update()['browsers'][0]['update']['id'] == command['id']
    task = bridge.create_task('deepseek', 'json', {'user': 'test'})
    assert bridge.claim('chrome', ['deepseek']) is None
    assert not bridge.text_owner('chrome')
    from services.web_video_bridge import claim
    assert claim('chrome', ['doubao_web']) is None
    loaded = {'update_capable': True, 'version': target['version'], 'build': target['build']}
    assert updates.heartbeat('chrome', loaded, {'id': command['id'], 'phase': 'reloaded'}) is None
    assert bridge.claim('chrome', ['deepseek'])['id'] == task['id']
    command = updates.heartbeat('edge', {'update_capable': True}, {})
    updates.heartbeat('edge', {**loaded, 'build': 'old'}, {'id': command['id'], 'phase': 'reloaded'})
    assert next(row for row in updates.status()['browsers'] if row['id'] == 'edge')['update']['phase'] == 'failed'


def test_legacy_browser_reports_manual_bootstrap_and_code_changes_have_distinct_builds(setup):
    updates.request_update()
    assert updates.heartbeat('chrome', {}, {}) is None
    assert updates.status()['browsers'][0]['update']['phase'] == 'failed'
    before = updates.source_info()['build']
    (setup / 'worker.js').write_text('changed code, same version')
    assert updates.source_info()['build'] != before


def test_missing_extension_files_are_reported(setup):
    (setup / 'worker.js').unlink()
    with pytest.raises(ValueError, match='worker.js'):
        updates.request_update()


def test_reload_without_confirmation_times_out_and_allows_retry(setup, monkeypatch):
    updates.request_update()
    command = updates.heartbeat('chrome', {'update_capable': True}, {})
    updates.heartbeat('chrome', {'update_capable': True}, {'id': command['id'], 'phase': 'reloading'})
    monkeypatch.setattr(updates.time, 'time', lambda: 9999999999)
    assert updates.status()['browsers'][0]['update']['phase'] == 'failed'
    assert updates.request_update()['browsers'][0]['update']['id'] != command['id']


def test_update_http_requires_local_origin_and_extension_pairing(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import text_jobs
    from ui.web_llm_routes import register_web_llm_routes
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    app = FastAPI()
    register_web_llm_routes(app)
    client = TestClient(app)
    path = '/api/web-llm/extension-update'
    assert client.post(path, headers={'Origin': 'https://example.com'}).status_code == 403
    result = client.post(path).json()
    assert result['ok']
    ping = '/api/web-llm/bridge/ping'
    payload = {'client_id': 'chrome', 'providers': ['deepseek'], 'browser': {'update_capable': True}}
    assert client.post(ping, json=payload).status_code == 403
    headers = {'Authorization': 'Bearer ' + bridge.get_settings()['token']}
    pending = client.post(ping, json=payload, headers=headers).json()
    assert pending['extension_update']['phase'] == 'requested'
    payload['browser'].update(version=result['source']['version'], build=result['source']['build'])
    payload['extension_update'] = {'id': pending['extension_update']['id'], 'phase': 'reloaded'}
    assert client.post(ping, json=payload, headers=headers).json()['extension_update'] is None
