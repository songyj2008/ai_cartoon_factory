import json
from services.web_video_bridge import _public


def test_preview_ready_requires_matching_render_and_valid_media(tmp_path):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'video')
    render = {'render_id': 'render', 'status': 'success', 'output': {
        'video_path': str(video), 'actual_media_spec': {'duration_sec': 5, 'width': 1280}}}
    payload = {'segments': [{'segment_index': 1, 'segment_uid': 'segment', 'renders': [render]}]}
    jobs = tmp_path / 'video_jobs.json'
    jobs.write_text(json.dumps(payload))
    row = {'state': 'complete', 'output_path': str(video), 'runtime': str(tmp_path),
           'input_json': '{}', 'lease': 'secret', 'segment_index': 1, 'segment_uid': 'segment', 'render_id': 'render'}
    assert _public(row)['preview_ready']
    render['output']['video_path'] = str(tmp_path / 'old.mp4')
    jobs.write_text(json.dumps(payload))
    assert not _public(row)['preview_ready']
    render['output']['video_path'] = str(video)
    render['output']['actual_media_spec']['duration_sec'] = 0
    jobs.write_text(json.dumps(payload))
    assert not _public(row)['preview_ready']
