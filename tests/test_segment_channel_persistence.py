from fastapi import FastAPI
from fastapi.testclient import TestClient
from ui import web_video_routes as routes


def test_default_runninghub_and_per_segment_persistence(monkeypatch):
    payload = {'segments': [{'segment_index': 1}, {'segment_index': 2}]}
    monkeypatch.setattr(routes, '_ui_origin', lambda *_: None)
    monkeypatch.setattr(routes, 'load_video_jobs', lambda: payload)
    monkeypatch.setattr(routes, 'options_for', lambda *_: [])
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: payload)
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda *_: None)
    app = FastAPI()
    routes.register_web_video_routes(app)
    client = TestClient(app)
    assert client.get('/api/video-channels/segments/1').json()['selected'] == 'runninghub'
    response = client.post('/api/video-channels/segments/1', json={'channel_id': 'doubao_web'})
    assert response.json()['ok']
    assert client.get('/api/video-channels/segments/1').json()['selected'] == 'doubao_web'
    assert client.get('/api/video-channels/segments/2').json()['selected'] == 'runninghub'
    assert not client.post('/api/video-channels/segments/1', json={'channel_id': 'invalid'}).json()['ok']
    assert payload['segments'][0]['generation_config']['channel_id'] == 'doubao_web'
