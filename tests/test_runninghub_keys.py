import json
from types import SimpleNamespace

import pytest

from services import runninghub_keys as keys, secrets
from workflow import runninghub_asset_cache, runninghub_sync
from workflow import runninghub_client
from workflow.submitters import runninghub_provider


@pytest.fixture
def key_config(monkeypatch):
    settings = {}
    environment = {}
    monkeypatch.setattr(keys, 'CONFIG', settings)
    monkeypatch.setattr(keys, 'secret_value', lambda name, **kwargs: environment.get(name, ''))
    monkeypatch.setattr(keys, '_next_key', 0)
    return settings, environment


def test_fixed_rotation_and_private_config_fallback(key_config):
    settings, environment = key_config
    settings['runninghub_api_keys'] = {'other-pc': 'test-private-config'}
    assert keys.resolve_runninghub_key().alias == 'other-pc'

    environment[keys.RUNNINGHUB_API_KEY_ENV] = 'test-legacy'
    assert keys.resolve_runninghub_key().alias == keys.LEGACY_ALIAS
    environment[keys.RUNNINGHUB_API_KEYS_ENV] = json.dumps({'a': 'test-secret-a', 'b': 'test-secret-b'})

    first, second, third = [keys.resolve_runninghub_key() for _ in range(3)]
    assert [key.alias for key in (first, second, third)] == ['a', 'b', 'a']
    settings[keys.SELECTION_FIELD] = 'b'
    assert keys.resolve_runninghub_key() == second
    assert keys.resolve_runninghub_key(first.id) == first
    assert keys.resolve_runninghub_key('').value == 'test-legacy'
    environment[keys.RUNNINGHUB_API_KEYS_ENV] = json.dumps({'a': 'test-new-account', 'b': 'test-secret-b'})
    with pytest.raises(ValueError, match='恢复原 Key'):
        keys.resolve_runninghub_key(first.id)


def test_each_submission_uses_one_key_and_has_an_isolated_upload_cache(key_config, tmp_path, monkeypatch):
    settings, _ = key_config
    settings[keys.CONFIG_KEYS_FIELD] = {'a': 'test-secret-a', 'b': 'test-secret-b'}
    calls = []

    class Client:
        def __init__(self, *, api_key, workflow_id, **kwargs):
            self.key = api_key
            self.workflow_id = workflow_id

        def upload_media(self, path):
            calls.append(('upload', self.key))
            return f'{self.key}.png'

        def create_task_with_workflow(self, workflow):
            calls.append(('submit', self.key))
            assert workflow['80']['inputs']['image'] == f'{self.key}.png'
            return f'task-{len(calls)}'

    monkeypatch.setattr(runninghub_provider, 'RunningHubClient', Client)
    monkeypatch.setattr(runninghub_provider, 'submit_backend_config', lambda *a, **kw: {'workflow_id': 'test'})
    monkeypatch.setattr(runninghub_asset_cache, 'get_project_dir', lambda: tmp_path)
    asset = tmp_path / 'reference.png'
    asset.write_bytes(b'test reference content')
    job = SimpleNamespace(part_id='part_001', asset_slots=[{
        'node_id': '80', 'input_name': 'file_name', 'path': str(asset),
    }])
    provider = runninghub_provider.RunningHubSubmitProvider()
    results = [provider.submit({'80': {'inputs': {}}}, job, wait=False) for _ in range(3)]
    assert calls == [
        ('upload', 'test-secret-a'), ('submit', 'test-secret-a'),
        ('upload', 'test-secret-b'), ('submit', 'test-secret-b'),
        ('submit', 'test-secret-a'),
    ]
    ids = [item.submit_result['key_id'] for item in results]
    assert ids[0] == ids[2] != ids[1]
    assert 'test-secret-a' not in repr(results)

    for ordinal, item in enumerate(results):
        task_id = item.task_id
        segment = {
            'task_id': task_id,
            'submit_result': item.submit_result,
            'runninghub_attempts': [{'task_id': f'old-{ordinal}', 'key_id': 'historical-key-id'}],
        }
        key_id = runninghub_sync._task_key_id(segment, task_id)
        assert key_id == ids[ordinal]
        assert runninghub_sync._task_key_id(segment, f'old-{ordinal}') == 'historical-key-id'
        poller = runninghub_provider.RunningHubSubmitProvider(key_id=key_id)
        assert poller.client.key == ('test-secret-b' if ordinal == 1 else 'test-secret-a')


def test_runninghub_responses_redact_credentials(monkeypatch):
    key = 'test-private-credential'
    client = runninghub_client.RunningHubClient('https://example.invalid', key, workflow_id='test')
    fake = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {'apiKey': key, 'msg': f'credential {key} returned', 'data': [{'taskId': 'task-1'}]},
    )
    monkeypatch.setattr(runninghub_client.requests, 'post', lambda *args, **kwargs: fake)
    assert key not in json.dumps(client.get_task_status('task-1'))
    assert client.create_task_with_workflow({}) == 'task-1'


def test_sync_keeps_credentials_separate_for_the_same_model(key_config, monkeypatch):
    from workflow import segment_runner

    settings, _ = key_config
    settings[keys.CONFIG_KEYS_FIELD] = {'a': 'test-secret-a', 'b': 'test-secret-b'}
    a, b = keys.resolve_runninghub_key(), keys.resolve_runninghub_key()
    settings[keys.SELECTION_FIELD] = 'b'
    segment_data = [('missing', 'removed-key-id'), ('task-a', a.id), ('task-b', b.id)]
    payload = {'segments': [
        {
            'segment_index': index, 'task_id': task_id, 'model_id': 'same-model',
            'status': 'running', 'backend': 'runninghub', 'submitted': True,
            'submit_result': {'task_id': task_id, 'key_id': key_id},
        }
        for index, (task_id, key_id) in enumerate(segment_data, 1)
    ]}
    calls = []

    def provider(*, model_id, key_id):
        key = keys.resolve_runninghub_key(key_id)

        def status(task_id):
            calls.append((model_id, task_id, key.alias))
            return {'status': 'RUNNING'}

        return SimpleNamespace(client=SimpleNamespace(get_task_status=status, get_task_outputs=lambda _: []))

    monkeypatch.setattr(runninghub_sync, 'resolved_model_id', lambda *args: 'same-model')
    monkeypatch.setattr(runninghub_sync, 'RunningHubSubmitProvider', provider)
    monkeypatch.setattr(runninghub_sync, 'load_video_jobs', lambda: payload)
    monkeypatch.setattr(runninghub_sync, 'save_video_jobs', lambda _: None)
    monkeypatch.setattr(segment_runner, 'dispatch_queued_runninghub_segments', lambda: None)

    runninghub_sync.sync_runninghub_video_jobs()
    assert calls == [('same-model', 'task-a', 'a'), ('same-model', 'task-b', 'b')]
    assert payload['segments'][0]['submitted'] is True


def test_manually_configured_environment_is_read_without_writing(monkeypatch):
    import os

    name = keys.RUNNINGHUB_API_KEYS_ENV
    monkeypatch.delenv(name, raising=False)
    values = {name: '{"manual": "test-manual-key"}'}
    monkeypatch.setattr(secrets, '_windows_user_env_get', lambda key: values.get(key, ''))
    assert keys._environment_keys() == [keys.RunningHubKey('manual', 'test-manual-key')]
    assert name not in os.environ

    values[name] = secrets._encrypt_for_machine(values[name])
    assert keys._environment_keys() == [keys.RunningHubKey('manual', 'test-manual-key')]
    assert name not in os.environ
