"""Explicit user declaration: this segment changes no scene facts."""
import copy

from .checkpoints import digest
from .revisions import revision
from .state import model_world


def inherited_record(view, context):
    world = copy.deepcopy(context['input_state'])
    return {'schema_version': 3, 'record_kind': 'inherit', 'state_trace_source': 'user_inherit',
            'user_declared_unchanged': True, 'context_id': context['context_id'],
            'source_revision': revision(view), 'input_revision': context['input_revision'],
            'previous_segment_uid': context['previous_segment_uid'],
            'previous_source': copy.deepcopy(context.get('previous_source')),
            'previous_end_revision': digest(model_world(world)), 'input_state': world,
            'end_state': copy.deepcopy(world), 'end_revision': digest(model_world(world)),
            'shots': [], 'diagnostics': [], 'record_status': 'ready',
            'text_pending': False, 'state_pending': False}


def inherit_segment_state(uid):
    from services.context import assert_current_runtime_writable
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from .readiness import read_ready_context
    from .versions import source_segment, store_source_record
    assert_current_runtime_writable('沿用上段人物状态')
    with video_jobs_lock():
        jobs = load_video_jobs()
        row = next((s for s in jobs['segments'] if s.get('segment_uid') == uid), None)
        if row is None or not row.get('scene_context_id'):
            raise ValueError('请先加入场景')
        context = read_ready_context(jobs, uid)
        view = source_segment(row)
        # The explicit declaration cannot erase current, evidenced changes.
        # A framing record is checked separately and is never replaced here.
        draft = source_segment(row, prefer_render=False)
        draft_context = read_ready_context(jobs, uid, target_source='draft')
        if row.get('h3_scene_framing'):
            from .framing import validate_framing_state
            validate_framing_state(row, draft_context, end_state=draft_context['input_state'])
        current = row.get('h3_continuity') or {}
        if (current.get('record_kind') != 'inherit'
                and current.get('schema_version') == 3
                and current.get('source_revision') == revision(draft)
                and current.get('context_id') == draft_context['context_id']
                and not current.get('state_pending')
                and current.get('previous_end_revision') == digest(model_world(draft_context['input_state']))
                and isinstance(current.get('end_state'), dict)
                and current.get('end_revision') == digest(model_world(current['end_state']))
                and model_world(current['end_state']) != model_world(draft_context['input_state'])):
            raise ValueError('本段已确认位置、姿势或在场状态发生变化，不能登记无变化；请提取结束状态')
        record = inherited_record(view, context)
        row['h3_state_mode'] = 'inherit'
        store_source_record(row, view, record)
        # The declaration applies to the editing segment as well; immutable
        # render snapshots are never edited or selected by this action.
        row['h3_continuity'] = inherited_record(draft, context)
        save_video_jobs(jobs)
        return state_status(jobs, uid)


def state_status(jobs, uid):
    from .readiness import SceneStateError, read_ready_context
    from .versions import source_segment, source_record
    row = next(s for s in jobs['segments'] if s.get('segment_uid') == uid)
    if not row.get('scene_context_id'):
        return {'mode': 'unset', 'ready': False, 'message': '未加入场景'}
    # The editor reports the state of its saved draft, not an older selected video.
    record = source_record(row, source_segment(row, prefer_render=False))
    mode = row.get('h3_state_mode') or ('inherit' if record.get('record_kind') == 'inherit' else 'extract' if record else 'unset')
    try:
        context = read_ready_context(jobs, uid, include_target=True, target_source='draft')
    except SceneStateError as exc:
        message = exc.reason if exc.is_target else f'受前序段 {exc.blocking_part_id} 阻塞'
        return {'mode': mode, 'ready': False, 'message': message,
                'detail': str(exc), **exc.details()}
    except (ValueError, KeyError, TypeError) as exc:
        return {'mode': mode, 'ready': False, 'message': str(exc), 'detail': str(exc)}
    if record.get('record_kind') == 'inherit':
        if context['previous_segment_uid'] is None:
            return {'mode': 'inherit', 'ready': True, 'previous_segment_uid': None,
                    'message': '沿用场景初态（无变化）· 已保存'}
        previous = next(s for s in jobs['segments'] if s['segment_uid'] == context['previous_segment_uid'])
        index = previous.get('segment_index') or jobs['segments'].index(previous) + 1
        return {'mode': 'inherit', 'ready': True, 'previous_segment_uid': previous['segment_uid'],
                'message': f'沿用 Part {index} · 已保存'}
    message = '优化后的本段结束状态 · 已保存' if record.get('record_kind') == 'optimized' else '已提取本段状态 · 已保存'
    return {'mode': 'extract', 'ready': True, 'message': message}
