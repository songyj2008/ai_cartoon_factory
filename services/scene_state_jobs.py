"""Independent scene-state tasks. Only derived facts may be written back."""
import copy
from pathlib import Path

from generation.pipelines.minimax_h3.continuity.readiness import source_guard, read_ready_context


def extraction_source_guard(jobs, uid, *, target_source='draft'):
    """Guard the analyzed draft; upstream facts have their own input revision.

    Completed predecessor renders may change provenance while preserving the
    ready incoming state. They are not the screenplay analyzed by this task.
    """
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest
    rows = jobs.get('segments', [])
    target = next((row for row in rows if row['segment_uid'] == uid), None)
    if target is None:
        raise ValueError('分段不存在或已移除')
    prefix = rows[:rows.index(target) + 1]
    isolated = {**jobs, 'segments': [target]}
    return digest({
        'target': source_guard(isolated, uid, target_source=target_source),
        'members': [{'uid': row['segment_uid'], 'state_mode': row.get('h3_state_mode')}
                    for row in prefix if row.get('scene_context_id') == target.get('scene_context_id')],
    })


def request_scene_state_refresh(uid, *, reason='extract', source_kind='draft', providers=None):
    # Only an explicit extraction action may create a model request.
    if reason not in ('extract', 'retry'):
        return None
    from services import text_jobs
    from services.context import get_project_dir
    from workflow.video_merge import load_video_jobs, video_jobs_lock
    from services.context import assert_current_runtime_writable
    assert_current_runtime_writable('提取本段人物状态')
    with video_jobs_lock():
        jobs = load_video_jobs()
        row = next((s for s in jobs.get('segments', []) if s.get('segment_uid') == uid), None)
        if not row or not row.get('scene_context_id'):
            return None
        input_context=extraction_context(jobs, uid, source_kind)
        # Queueing is not confirmation: preserve the last successful state mode.
        args = {'segment_uid': uid, 'context_id': row['scene_context_id'],
                'source_kind': source_kind, 'source_key': extraction_source_guard(jobs, uid, target_source=source_kind),
                'input_revision': input_context['input_revision'], 'manual': True}
        if providers is not None:
            from services.web_llm_bridge import validate_routes
            args['providers'] = validate_routes(providers,inherit=True)
    with text_jobs.database() as db:
        previous = db.execute("SELECT * FROM operations ORDER BY updated DESC").fetchall()
    for row in previous:
        item = text_jobs._decode(row)
        if item['kind'] == 'scene_state' and item['source'] == str(get_project_dir().resolve()) and item['args'][0] == args:
            if item['state'] in ('error', 'cancelled', 'needs_attention'):
                if reason in ('extract', 'retry'):
                    text_jobs.cancel(item['id'])
                else:
                    return {'id': item['id'], 'state': item['state'], 'ready': False}
            break
    task = text_jobs.submit('scene_state', [args],page_providers=providers)
    return {'id': task['id'], 'state': task['state'], 'ready': False}


def extraction_context(jobs, uid, source_kind):
    from generation.pipelines.minimax_h3.continuity.versions import source_segment
    from generation.pipelines.minimax_h3.continuity.preparation import _context
    rows = jobs['segments']; row = next(s for s in rows if s['segment_uid'] == uid)
    key = row.get('scene_context_id')
    scene = (jobs.get('h3_scene_continuity') or {}).get('contexts', {}).get(key)
    if not scene:
        raise ValueError('请先加入场景')
    preceding = [s for s in rows[:rows.index(row)] if s.get('scene_context_id') == key]
    if not preceding and scene.get('initial_pending'):
        view = source_segment(row, prefer_render=source_kind == 'selected')
        return _context(scene['initial_state'], view, scene, None)
    return read_ready_context(jobs, uid, target_source=source_kind)


def active_scene_state_task(uid, *, source_kind='draft'):
    from services import text_jobs
    from services.context import get_project_dir
    source = str(get_project_dir().resolve())
    from workflow.video_merge import load_video_jobs
    try:
        current_key = extraction_source_guard(load_video_jobs(), uid, target_source=source_kind)
    except ValueError:
        return None
    with text_jobs.database() as db:
        rows = db.execute("SELECT * FROM operations WHERE state IN ('preparing','queued','running','waiting_children') ORDER BY created").fetchall()
    for row in rows:
        item = text_jobs._decode(row)
        if (item['kind'] == 'scene_state' and item['source'] == source
                and item['args'][0]['segment_uid'] == uid and item['args'][0]['source_kind'] == source_kind
                and item['args'][0]['source_key'] == current_key):
            return {'id': item['id'], 'state': item['state'], 'ready': False}
    return None


def build_scene_state_patch(request):
    from services import text_jobs, context
    from workflow.video_merge import load_video_jobs, video_jobs_lock
    from generation.pipelines.minimax_h3.continuity.state_trace import extract_state_record
    from generation.pipelines.minimax_h3.continuity.versions import source_segment, source_record, store_source_record
    from generation.pipelines.minimax_h3.continuity.preparation import _context
    if request.get('manual') is not True:
        raise ValueError('自动提取入口已关闭，请点击“提取本段状态（LLM）”')
    item = text_jobs.current()
    # Rehydrate completed preceding tasks, without replacing frozen sources.
    with context.pin_runtime((Path(item['source']), item['project'], item['episode'])):
        with video_jobs_lock():
            before = copy.deepcopy(load_video_jobs())
    uid, kind = request['segment_uid'], request['source_kind']
    if extraction_source_guard(before, uid, target_source=kind) != request['source_key']:
        raise ValueError('人物状态任务来源已变化，旧任务未修改当前稿')
    after = copy.deepcopy(before)
    row = next(s for s in after['segments'] if s['segment_uid'] == uid)
    view = source_segment(row, prefer_render=kind == 'selected')
    ctx = extraction_context(after, uid, kind)
    if ctx['input_revision'] != request.get('input_revision'):
        raise ValueError('上段状态已变化，请重新点击提取本段状态')
    record = extract_state_record(view, ctx, existing=source_record(row, view) or None)
    scene = after['h3_scene_continuity']['contexts'][row['scene_context_id']]
    if ctx.get('initializing'):
        initial = record.pop('initial_state')
        scene.update(initial_state=copy.deepcopy(initial), initial_pending=False)
        ctx = _context(initial, view, scene, None)
        record.update(input_revision=ctx['input_revision'], previous_end_revision=ctx['previous_end_revision'])
    # This read-only extraction must agree with the current frozen framing
    # plan; neither operation is allowed to silently overwrite the other.
    if view.get('h3_scene_framing'):
        from generation.pipelines.minimax_h3.continuity.framing import validate_framing_state
        validate_framing_state(view, ctx, end_state=record.get('end_state'))
    record.update(context_id=ctx['context_id'], previous_source=copy.deepcopy(ctx.get('previous_source')),record_kind='extracted')
    store_source_record(row, view, record)
    return {'request': request, 'before': before, 'after': after}


def commit_scene_state_patch(item, result):
    from services import text_jobs, context
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    request = result['request']
    uid, kind = request['segment_uid'], request['source_kind']
    before, after = result['before'], result['after']
    with context.pin_runtime((Path(item['source']), item['project'], item['episode'])):
        # Cancellation and write acceptance share the task lock order.
        with text_jobs._LOCK, video_jobs_lock():
            text_jobs.check_cancelled()
            context.assert_current_runtime_writable('保存场景人物状态')
            latest = load_video_jobs()
            if extraction_source_guard(latest, uid, target_source=kind) != request['source_key']:
                raise ValueError('人物状态任务来源已变化，未覆盖当前稿')
            if extraction_context(latest,uid,kind)['input_revision'] != request['input_revision']:
                raise ValueError('提取期间上段状态已变化，未覆盖当前稿')
            # Framing may have been saved while the LLM was running without
            # changing the prompt. Recheck against the live record at commit.
            from generation.pipelines.minimax_h3.continuity.versions import source_segment, source_record
            live_target = next(row for row in latest['segments'] if row['segment_uid'] == uid)
            analyzed_target = next(row for row in after['segments'] if row['segment_uid'] == uid)
            analyzed_view = source_segment(analyzed_target, prefer_render=kind == 'selected')
            extracted = source_record(analyzed_target, analyzed_view) or {}
            live_view = source_segment(live_target, prefer_render=kind == 'selected')
            if live_view.get('h3_scene_framing'):
                from generation.pipelines.minimax_h3.continuity.framing import validate_framing_state
                validate_framing_state(live_view, extraction_context(latest, uid, kind),
                                       end_state=extracted.get('end_state'))
            live = {s['segment_uid']: s for s in latest['segments']}
            old = {s['segment_uid']: s for s in before['segments']}
            changes = []
            for row in after['segments']:
                for field in ('h3_continuity', 'h3_continuity_versions'):
                    if row.get(field) != old[row['segment_uid']].get(field):
                        current = live[row['segment_uid']].get(field)
                        if current != old[row['segment_uid']].get(field) and current != row.get(field):
                            raise ValueError('人物状态已有更新版本，未覆盖')
                        changes.append((live[row['segment_uid']], field, copy.deepcopy(row[field])))
            key = request['context_id']
            original = before['h3_scene_continuity']['contexts'][key]
            updated = after['h3_scene_continuity']['contexts'][key]
            scene = latest['h3_scene_continuity']['contexts'][key]
            for field in ('initial_state', 'initial_pending'):
                if updated.get(field) != original.get(field):
                    if scene.get(field) not in (original.get(field), updated.get(field)):
                        raise ValueError('场景初态已有更新，未覆盖')
            for row, field, value in changes:
                row[field] = value
            for field in ('initial_state', 'initial_pending'):
                if updated.get(field) != original.get(field):
                    scene[field] = copy.deepcopy(updated.get(field))
            # Only a fully accepted extraction replaces the last successful declaration.
            live[uid]['h3_state_mode'] = 'extract'
            save_video_jobs(latest)
            return {'segment_uid': uid, 'source_key': request['source_key'], 'message': '人物状态已独立保存'}


