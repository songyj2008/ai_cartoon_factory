import copy

import pytest

from generation.pipelines.minimax_h3 import optimization_history as history
from generation.pipelines.minimax_h3.editor_draft import fingerprint, snapshot


@pytest.fixture
def record(monkeypatch):
    segment = {'segment_uid': 's', 'generation_config': {
        'duration_sec': 4, 'prompt_override': '当前提示词', 'asset_manifest': []}}
    jobs = {'segments': [segment]}
    writes = []
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: jobs)
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda data: writes.append(copy.deepcopy(data)))
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *args: None)
    return segment, writes


def test_old_prompt_is_unknown_and_click_does_not_change_edit_revision(record):
    segment, writes = record
    before = fingerprint(snapshot(segment))
    assert history.status(segment)['known'] is False
    clicked = history.event('s', 'clicked')
    assert clicked['last_attempt']['stage'] == 'clicked'
    assert clicked['last_attempt']['clicked_at']
    assert fingerprint(snapshot(segment)) == before
    assert history.status(writes[-1]['segments'][0]) == clicked
    from generation.job_schema import normalize_video_jobs_payload
    normalized = normalize_video_jobs_payload(copy.deepcopy(writes[-1]))
    assert normalized['segments'][0]['h3_state_optimization'] == segment['h3_state_optimization']


def test_generation_failure_application_save_and_later_retime(record, monkeypatch):
    segment, _ = record
    attempt = history.event('s', 'clicked')['last_attempt']['id']
    monkeypatch.setattr('services.text_jobs.get', lambda _: {'state': 'ready'})
    generated = history.event('s', 'submitted', attempt_id=attempt, operation_id='task')
    assert generated['last_attempt']['stage'] == 'generated'
    assert 'last_saved' not in generated
    assert history.event('s', 'applied', attempt_id=attempt)['last_attempt']['stage'] == 'applied'
    history.mark_saved(segment, {'scene_patch': {}, 'optimization_attempt_id': attempt})
    # A plain content save does not claim the optimization was accepted.
    assert 'last_saved' not in history.status(segment)
    history.mark_saved(segment, {'scene_patch': {'record': {}}, 'optimization_attempt_id': attempt})
    saved = history.status(segment)
    assert saved['last_attempt']['stage'] == 'saved'
    assert saved['current_matches_saved'] is True
    assert history.event('s', 'applied', attempt_id=attempt)['last_attempt']['stage'] == 'saved'
    segment['generation_config']['prompt_override'] += '（时长重新规划）'
    retimed = history.status(segment)
    assert retimed['last_saved'] == saved['last_saved']
    assert retimed['current_matches_saved'] is False
    newer = history.event('s', 'clicked')['last_attempt']['id']
    assert newer != attempt
    late = history.event('s', 'failed', attempt_id=attempt, message='旧任务失败')
    assert late['last_attempt']['id'] == newer
    assert late['last_attempt']['stage'] == 'clicked'
    assert late['last_saved'] == saved['last_saved']


def test_failed_or_deleted_task_still_proves_button_was_clicked(record, monkeypatch):
    segment, _ = record
    attempt = history.event('s', 'clicked')['last_attempt']['id']
    monkeypatch.setattr('services.text_jobs.get', lambda _: {'state': 'error', 'message': '生成失败'})
    failed = history.event('s', 'submitted', attempt_id=attempt, operation_id='task')
    assert failed['known'] and failed['last_attempt']['stage'] == 'failed'
    assert 'last_saved' not in failed
    def missing(_):
        raise ValueError('任务不存在')
    monkeypatch.setattr('services.text_jobs.get', missing)
    assert history.status(segment)['last_attempt']['stage'] == 'unknown'
