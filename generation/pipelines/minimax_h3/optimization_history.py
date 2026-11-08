"""Audit optimization separately from editable content and scene facts."""
import copy
import sqlite3
from datetime import datetime, timezone
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def content_revision(segment):
    from .editor_draft import fingerprint, snapshot
    draft = snapshot(segment)
    return fingerprint({'prompt': draft['prompt'], 'asset_manifest': draft['asset_manifest']})


def status(segment):
    record = copy.deepcopy(segment.get('h3_state_optimization') or {})
    attempt = record.get('last_attempt') or {}
    if attempt.get('stage') == 'submitted' and attempt.get('operation_id'):
        from services.text_jobs import get
        try:
            task = get(attempt['operation_id'])
            if task['state'] == 'ready':
                attempt['stage'] = 'generated'
            elif task['state'] in ('error', 'cancelled', 'needs_attention'):
                attempt.update(stage='failed', message=task.get('message') or task['state'])
        except (KeyError, ValueError, sqlite3.Error):
            attempt.update(stage='unknown', message='原任务记录已清理，无法确认生成结果')
    saved = record.get('last_saved')
    return {**record, 'known': bool(record),
            'current_matches_saved': bool(saved and saved['content_revision'] == content_revision(segment))}


def event(uid, stage, *, attempt_id=None, operation_id=None, message=None):
    from services.context import assert_current_runtime_writable
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from .editor_draft import locate
    assert_current_runtime_writable('记录人物状态优化操作')
    if stage not in ('clicked', 'submitted', 'applied', 'undone', 'failed', 'content_saved'):
        raise ValueError('无效的优化记录阶段')
    with video_jobs_lock():
        jobs = load_video_jobs()
        segment = locate(jobs, uid)
        record = segment.setdefault('h3_state_optimization', {})
        if stage == 'clicked':
            record['last_attempt'] = {'id': uuid.uuid4().hex, 'clicked_at': now(), 'stage': stage}
        else:
            attempt = record.get('last_attempt') or {}
            if attempt.get('id') != attempt_id or attempt.get('stage') == 'saved':
                return status(segment)
            attempt.update(stage=stage, updated_at=now())
            if operation_id:
                attempt['operation_id'] = operation_id
            if message:
                attempt['message'] = str(message)[:500]
        save_video_jobs(jobs)
        return status(segment)


def mark_saved(segment, draft):
    """Called only after a candidate has passed commit validation."""
    from .prompt_baseline import retain_baseline
    retain_baseline(segment, draft)
    attempt_id = draft.get('optimization_attempt_id')
    if not attempt_id or not draft.get('scene_patch'):
        return
    record = segment.setdefault('h3_state_optimization', {})
    record['last_saved'] = {'attempt_id': attempt_id, 'saved_at': now(),
                            'content_revision': content_revision(segment)}
    attempt = record.get('last_attempt') or {}
    if attempt.get('id') == attempt_id:
        attempt.update(stage='saved', saved_at=record['last_saved']['saved_at'])
