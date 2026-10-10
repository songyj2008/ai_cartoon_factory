from generation.video_history import video_attempts
from ui.group_panel import _runninghub_usage_history


def test_browser_video_is_listed_and_playable_without_runninghub_attempts(tmp_path):
    video = tmp_path / 'doubao.mp4'
    video.write_bytes(b'video')
    segment = {'segment_index': 3, 'active_render_id': 'web', 'renders': [{
        'render_id': 'web', 'status': 'success', 'model': {'id': 'minimax_h3_local_ref2va'},
        'submission': {'backend': 'doubao_web', 'task_id': 'browser-task'},
        'output': {'video_path': str(video)}, 'prompt_snapshot': 'video prompt',
    }]}
    html = _runninghub_usage_history(segment)
    assert '豆包网页视频' in html
    assert 'browser-task' in html
    assert '免费渠道' in html
    assert 'video prompt' in html
    assert 'doubao.mp4' in html
    assert 'selectUsageAttemptVideo' in html
    assert 'confirmDeleteTaskVideo' in html
    segment['renders'][0]['output']['video_deleted'] = True
    assert video_attempts(segment)[0]['video_deleted'] is True


def test_mixed_history_deduplicates_task_ids():
    segment = {'runninghub_attempts': [{'task_id': 'rh'}], 'renders': [
        {'submission': {'backend': 'runninghub', 'task_id': 'rh'}},
        {'submission': {'backend': 'doubao_web', 'task_id': 'web'}},
        {'submission': {'backend': 'doubao_web', 'task_id': 'web'}},
    ]}
    assert [item['task_id'] for item in video_attempts(segment)] == ['rh', 'web']
