"""Prepare a frozen screenplay's scene coverage before video submission.

Video preparation never calls a text model. Creative planning and state
extraction belong to their explicit editor operations, not video retries.
"""
from __future__ import annotations

import copy


def prepare_scene_framing(jobs, segment):
    """Review only this selected target; never prepare or edit predecessors."""
    if not segment.get('scene_context_id'):
        return None
    from ..editor_draft import snapshot
    from .framing import validate_framing_record, validate_framing_state, advance_framing_reference_record
    from .readiness import read_ready_context, scene_state_guard

    uid = segment['segment_uid']
    context = read_ready_context(jobs, uid, include_target=True, target_source='draft')
    guard = scene_state_guard(jobs, uid)
    advance_framing_reference_record(segment, context)
    validate_framing_record(segment, context)
    ledger = segment.get('h3_continuity') or {}
    end_state = (context['input_state'] if ledger.get('record_kind') == 'inherit'
                 else ledger.get('end_state', {}))
    validate_framing_state(segment, context, end_state=end_state)
    return guard


def validate_submission_framing(jobs, segment, *, guard=None):
    """Prove scene coverage and the exact saved job agree, without any LLM."""
    if not segment.get('scene_context_id'):
        return None
    from ..contracts import H3GenerationConfig
    from ..editor_draft import snapshot
    from .framing import validate_framing_record, validate_framing_state
    from .readiness import read_ready_context, validate_scene_guard

    if guard is not None:
        validate_scene_guard(jobs, guard)
    context = read_ready_context(jobs, segment['segment_uid'], include_target=True,
                                 target_source='draft')
    record = validate_framing_record(segment, context)
    ledger = segment.get('h3_continuity') or {}
    end_state = (context['input_state'] if ledger.get('record_kind') == 'inherit'
                 else ledger.get('end_state', {}))
    validate_framing_state(segment, context, end_state=end_state)
    draft = snapshot(segment)
    job = segment.get('job') or {}
    prompt = job.get('final_prompt') or job.get('prompt') or ''
    if prompt != draft['prompt']:
        raise ValueError('视频工作流提示词与已核对的场景稿不同，请重新准备当前稿')
    media = H3GenerationConfig.from_mapping({'asset_manifest': job.get('asset_manifest', [])})
    saved = H3GenerationConfig.from_mapping({'asset_manifest': draft['asset_manifest']})
    if media.to_dict()['asset_manifest'] != saved.to_dict()['asset_manifest']:
        raise ValueError('视频工作流参考与已核对的场景稿不同，请重新准备当前稿')
    return record


def prepare_runtime_scene_framing(segment_index, *, load_jobs=None, save_jobs=None):
    """CAS-install a web channel target's review without locking model calls."""
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from workflow.runninghub_queue import has_active_submission
    from .readiness import validate_scene_guard

    load_jobs = load_jobs or load_video_jobs
    save_jobs = save_jobs or save_video_jobs
    with video_jobs_lock():
        before = copy.deepcopy(load_jobs())
    row = next((s for s in before.get('segments', [])
                if int(s.get('segment_index') or 0) == int(segment_index)), None)
    if row is None:
        raise ValueError('分段已变化，请重新选择视频段')
    if not row.get('scene_context_id') or has_active_submission(row):
        return None
    guard = prepare_scene_framing(before, row)
    with video_jobs_lock():
        latest = load_jobs()
        validate_scene_guard(latest, guard)
        live = next(s for s in latest['segments'] if s.get('segment_uid') == row['segment_uid'])
        if has_active_submission(live):
            return None  # A task accepted during review remains authoritative.
        if live.get('h3_scene_framing') != row.get('h3_scene_framing'):
            live['h3_scene_framing'] = copy.deepcopy(row['h3_scene_framing'])
            save_jobs(latest)
    return guard
