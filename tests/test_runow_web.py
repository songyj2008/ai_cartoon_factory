from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from generation.channels import get_channel
from services import runow_web as runow, web_video_bridge as bridge


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    from services import web_llm_bridge
    monkeypatch.setattr(web_llm_bridge, 'BRIDGE_DIR', tmp_path / 'clients')
    web_llm_bridge.heartbeat('profile', ['deepseek'], browser={'name':'Edge', 'channels':['runninghub_web', 'runninghub_international_web', 'doubao_web']})
    image = tmp_path / 'actor.png'
    audio = tmp_path / 'voice.mp3'
    image.write_bytes(b'image')
    audio.write_bytes(b'voice')
    segment = {
        'segment_index': 1, 'segment_uid': 'one', 'model_id': runow.MODEL_ID,
        'status': 'workflow_ready',
        'job': {'duration_sec': 10, 'final_prompt': '外观 <Picture 1>，声音 <Audio 1>。<d>说 @图片9 和 <Picture 9></d>'},
        'generation_config': {'aspect_ratio': '1:1', 'runow_resolution': '768p', 'asset_manifest': [
            {'asset_id': 'voice', 'type': 'audio', 'path': str(audio), 'role': '角色声音'},
            {'asset_id': 'actor', 'type': 'image', 'path': str(image), 'role': '角色形象'},
        ]},
    }
    payload = {'default_model_id': runow.MODEL_ID, 'segments': [segment]}
    monkeypatch.setattr(bridge, '_DB', tmp_path / 'queue.sqlite3')
    monkeypatch.setattr(bridge, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(bridge, 'get_current_project_name', lambda: 'test')
    monkeypatch.setattr(bridge, 'get_current_episode_name', lambda: '')
    monkeypatch.setattr(bridge, 'load_video_jobs', lambda: payload)
    monkeypatch.setattr(bridge, 'save_video_jobs', lambda value: (tmp_path / 'video_jobs.json').write_text(json.dumps(value), encoding='utf-8'))
    return segment, payload, image, audio


@pytest.mark.parametrize('channel_id', ['runninghub_web', 'runninghub_international_web'])
def test_runow_channels_claim_only_their_own_queue_and_accept_progress(fixture, channel_id):
    channel = get_channel(channel_id)
    channel.submit(None, 1)
    other = 'runninghub_web' if channel_id == 'runninghub_international_web' else 'runninghub_international_web'
    assert bridge.claim('profile', [other]) is None
    task = bridge.claim('profile', [channel_id])
    assert task['channel'] == channel_id
    assert task['input']['media']
    runow.progress(task['id'], task['lease'])
    with pytest.raises(PermissionError):
        runow.progress(task['id'], 'wrong')


def test_references_are_typed_ordered_and_dialogue_stays_opaque(fixture):
    segment, _, image, audio = fixture
    before = copy.deepcopy(segment)
    request = runow.build_request(segment)
    assert [item['id'] for item in request['media']] == ['actor', 'voice']
    assert [item['path'] for item in request['media']] == [str(image), str(audio)]
    assert request['prompt'] == '外观 @图片1，声音 @音频1。<d>说 @图片9 和 <Picture 9></d>'
    assert [part['reference_id'] for part in request['prompt_parts'] if 'reference_id' in part] == ['actor', 'voice']
    assert segment == before
    segment['job']['final_prompt'] = '使用 <Image 1> 和 @音频1'
    assert runow.build_request(segment)['prompt'] == '使用 @图片1 和 @音频1'
    segment['job']['final_prompt'] = '使用 @Image 1 和 @Audio 1。<d>原话 @Image 9</d>'
    request = runow.build_request(segment)
    assert request['prompt'] == '使用 @图片1 和 @音频1。<d>原话 @Image 9</d>'
    assert [part['reference_id'] for part in request['prompt_parts'] if 'reference_id' in part] == ['actor', 'voice']


def test_queue_leases_media_without_exposing_paths_and_keeps_render_spec(fixture):
    segment, payload, image, audio = fixture
    channel = get_channel('runninghub_web')
    assert channel.unavailable_reason(segment, payload) == ''
    channel.submit(None, 1)
    assert bridge.claim('doubao-extension', ['doubao_web']) is None
    claimed = bridge.claim('profile', ['runninghub_web'])
    assert claimed['input']['aspect_ratio'] == '1:1'
    assert claimed['input']['resolution'] == '768p'
    assert 'path' not in claimed['input']['media'][0]
    assert 'mtime_ns' not in claimed['input']['media'][0]
    assert bridge.asset(claimed['id'], 'actor', claimed['lease']) == image
    assert bridge.asset(claimed['id'], 'voice', claimed['lease']) == audio
    with pytest.raises(PermissionError):
        bridge.asset(claimed['id'], 'voice', 'wrong')
    with pytest.raises(ValueError, match='不在'):
        bridge.asset(claimed['id'], '../private', claimed['lease'])
    render = segment['renders'][-1]
    assert render['prompt_snapshot'] == segment['job']['final_prompt']
    assert len(render['asset_manifest']) == 2
    assert render['requested_media_spec']['aspect_ratio'] == '1:1'


def test_queued_file_change_is_rejected(fixture):
    segment, _, image, _ = fixture
    request = runow.build_request(segment)
    image.write_bytes(b'changed content')
    with pytest.raises(ValueError, match='发生变化'):
        runow.resolve_asset(request, 'actor')


@pytest.mark.parametrize('duration', [0, 3, 16, 5.5, float('nan'), float('inf')])
def test_invalid_duration_is_reported_without_repair(fixture, duration):
    segment, payload, _, _ = fixture
    segment['job']['duration_sec'] = duration
    reason = get_channel('runninghub_web').unavailable_reason(segment, payload)
    assert '时长' in reason


def test_model_mismatch_missing_reference_and_duplicate_filename(fixture, tmp_path):
    segment, payload, image, _ = fixture
    channel = get_channel('runninghub_web')
    segment['model_id'] = 'ltx23_licon_msr_v2'
    assert '仅支持 H3' in channel.unavailable_reason(segment, payload)
    segment['model_id'] = runow.MODEL_ID
    segment['job']['final_prompt'] = '使用 <Picture 2>'
    assert '没有对应素材' in channel.unavailable_reason(segment, payload)
    other = tmp_path / 'other' / image.name
    other.parent.mkdir()
    other.write_bytes(b'other image')
    segment['generation_config']['asset_manifest'].append({'asset_id': 'other', 'type': 'image', 'path': str(other)})
    assert '重复' in channel.unavailable_reason(segment, payload)


def test_type_limits_and_audio_only_are_checked(fixture):
    segment, payload, image, _ = fixture
    channel = get_channel('runninghub_web')
    segment['generation_config']['asset_manifest'] = [{'asset_id': str(i), 'type': 'image', 'path': str(image)} for i in range(10)]
    assert '最多选择 9' in channel.unavailable_reason(segment, payload)
    segment['generation_config']['asset_manifest'] = [{'asset_id': 'voice', 'type': 'audio', 'path': str(fixture[3])}]
    assert '至少需要' in channel.unavailable_reason(segment, payload)


def test_runow_has_no_local_daily_cap_and_preserves_other_channel_quota(fixture):
    segment, payload, _, _ = fixture
    original = copy.deepcopy(segment)
    payload['segments'] = [{**copy.deepcopy(original), 'segment_index': i, 'segment_uid': str(i)} for i in range(1, 5)]
    channel = get_channel('runninghub_web')
    def enqueue(index):
        try:
            channel.submit(None, index)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(1, 5)))
    assert results.count(True) == 4
    assert channel.daily_limit is None
    assert bridge.remaining_quota('doubao_web') == 10
    task = bridge.claim('profile', ['runninghub_web'])
    runow.progress(task['id'], task['lease'])
    with pytest.raises(PermissionError):
        runow.progress(task['id'], 'wrong')
    bridge.report(task['id'], task['lease'], 'error', error='未点击生成')
    assert channel.unavailable_reason(payload['segments'][0], payload) == ''
    with pytest.raises(PermissionError):
        runow.progress(task['id'], task['lease'])


def test_completion_preserves_prompt_history_and_returns_preview(fixture, monkeypatch, tmp_path):
    segment, _, _, _ = fixture
    from generation.contracts import MediaSpec
    from generation.video_history import video_attempts
    from ui.group_panel import _runninghub_usage_history
    channel = get_channel('runninghub_web')
    channel.submit(None, 1)
    task = bridge.claim('profile', ['runninghub_web'])
    receipt = {'taskId': 'remote-parent', 'source': 'runninghub_commit', 'projectId': '123'}
    bridge.report(task['id'], task['lease'], 'submitted', receipt=receipt)
    with pytest.raises(ValueError, match='不能释放额度'):
        bridge.report(task['id'], task['lease'], 'error')
    monkeypatch.setattr('generation.media.inspect_media', lambda _: MediaSpec(duration_sec=10, width=768, height=768))
    video = tmp_path / 'download.mp4'
    video.write_bytes(b'video')
    result = bridge.report(task['id'], task['lease'], 'complete', output_path=str(video),
                          receipt={**receipt, 'resultTaskId': 'remote-child', 'resultUrl': 'https://example.com/video.mp4'})
    assert result['receipt']['taskId'] == 'remote-parent'
    assert result['preview_ready']
    assert segment['status'] == 'success'
    assert video_attempts(segment)[0]['backend'] == 'runninghub_web'
    history = _runninghub_usage_history(segment)
    assert 'RunningHub 网页视频' in history
    assert '网站结算' in history
    assert '免费渠道' not in history


def test_job_manifest_fallback_is_saved(fixture):
    segment, _, _, _ = fixture
    segment['job']['asset_manifest'] = segment['generation_config'].pop('asset_manifest')
    get_channel('runninghub_web').submit(None, 1)
    assert len(segment['renders'][-1]['asset_manifest']) == 2


def test_http_media_and_heartbeat_require_pairing_and_task_lease(fixture, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import web_llm_bridge
    from ui.web_video_routes import register_web_video_routes

    monkeypatch.setattr(web_llm_bridge, 'get_settings', lambda: {'token': 'paired', 'primary_text_client_id':'profile'})
    get_channel('runninghub_web').submit(None, 1)
    task = bridge.claim('profile', ['runninghub_web'])
    app = FastAPI()
    register_web_video_routes(app)
    client = TestClient(app)
    endpoint = f"/api/web-video/bridge/tasks/{task['id']}/assets/voice/local-path"
    assert client.get(endpoint, params={'lease': task['lease']}).status_code == 403
    headers = {'Authorization': 'Bearer paired'}
    assert client.get(endpoint, params={'lease': 'bad'}, headers=headers).status_code == 403
    response = client.get(endpoint, params={'lease': task['lease']}, headers=headers)
    assert response.status_code == 200
    assert response.json()['path'] == str(fixture[3])
    endpoint = f"/api/runow-web/tasks/{task['id']}/progress"
    assert client.post(endpoint, json={'lease': task['lease']}).status_code == 403
    assert client.post(endpoint, json={'lease': 'bad'}, headers=headers).status_code == 403
    assert client.post(endpoint, json={'lease': task['lease']}, headers=headers).json() == {'ok': True}

def test_runow_result_receipt_cannot_change_remote_task(fixture, tmp_path):
    get_channel('runninghub_web').submit(None, 1)
    task = bridge.claim('profile', ['runninghub_web'])
    receipt = {'taskId': 'own', 'source': 'runninghub_commit'}
    bridge.report(task['id'], task['lease'], 'submitted', receipt=receipt)
    with pytest.raises(ValueError, match='不一致'):
        bridge.report(task['id'], task['lease'], 'complete', receipt={**receipt, 'taskId': 'other'})
    assert bridge.task_status(task['id'])['receipt']['taskId'] == 'own'


def test_legacy_runow_task_requires_bound_result_before_import(fixture):
    get_channel('runninghub_web').submit(None, 1)
    task = bridge.claim('profile', ['runninghub_web'])
    bridge.report(task['id'], task['lease'], 'submitted')
    with pytest.raises(ValueError, match='结果回执'):
        bridge.report(task['id'], task['lease'], 'complete')
