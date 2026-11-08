"""Automatic, version-guarded scene metadata preparation; never edit a screenplay."""
from __future__ import annotations

import copy

from .checkpoints import digest
from .state import character_keys, model_world


_RECORD_FIELDS = ('h3_continuity', 'h3_continuity_versions')


def _prefix(jobs, uid):
    rows = jobs.get('segments', [])
    index = next((i for i, row in enumerate(rows) if row.get('segment_uid') == uid), None)
    if index is None:
        raise ValueError('分段已变化，请重试当前操作')
    return rows[:index + 1]


def preparation_signature(jobs, uid):
    """Guard source versions and metadata, excluding live provider progress."""
    from ..editor_draft import snapshot
    from .versions import source_segment
    return digest({'scenes': jobs.get('h3_scene_continuity'), 'rows': [
        {'uid': row['segment_uid'], 'scene': row.get('scene_context_id'),
         'draft': snapshot(row), 'source': source_segment(row).get('_continuity_source'),
         'selected': row.get('active_render_id'),
         'records': {key: row.get(key) for key in _RECORD_FIELDS}}
        for row in _prefix(jobs, uid)]})


def preparation_updates(before, after, uid):
    """A metadata-only patch whose source snapshot must still match at commit."""
    old = {row['segment_uid']: row for row in _prefix(before, uid)}
    changes = []
    for row in _prefix(after, uid):
        fields = {key: copy.deepcopy(row[key]) for key in _RECORD_FIELDS
                  if key in row and row.get(key) != old[row['segment_uid']].get(key)}
        if fields:
            changes.append({'segment_uid': row['segment_uid'], 'fields': fields})
    scenes = after.get('h3_scene_continuity')
    changed_scenes = scenes != before.get('h3_scene_continuity')
    if not changes and not changed_scenes:
        return None
    patch = {'through_uid': uid, 'signature': preparation_signature(before, uid), 'rows': changes}
    if changed_scenes:
        patch['scenes'] = copy.deepcopy(scenes)
    return patch


def apply_preparation_updates(jobs, patch):
    if not patch:
        return
    if preparation_signature(jobs, patch['through_uid']) != patch['signature']:
        raise ValueError('整理承接信息期间稿件或场景来源已变化，请重试当前操作；已有视频已保留')
    rows = {row['segment_uid']: row for row in jobs['segments']}
    for item in patch['rows']:
        if set(item['fields']) - set(_RECORD_FIELDS):
            raise ValueError('承接更新不能修改正文或视频')
        rows[item['segment_uid']].update(copy.deepcopy(item['fields']))
    if 'scenes' in patch:
        jobs['h3_scene_continuity'] = copy.deepcopy(patch['scenes'])


def _context(world, row, scene, previous_uid, previous_source=None):
    from .constraints import scene_constraints, scene_layout
    initializing = bool(scene.get('initial_pending')) and previous_uid is None
    return {'context_id': row['scene_context_id'], 'input_state': copy.deepcopy(world),
            'spatial_constraints': scene_constraints(scene),
            'scene_layout': scene_layout(scene, (row.get('generation_config') or {}).get('asset_manifest', [])),
            'layout_revision': scene.get('layout_revision'),
            **({'opening_source_excerpt': row.get('source_excerpt') or ''} if initializing else {}),
            'tracked_role_ids': sorted(set(world) | character_keys(
                (row.get('generation_config') or {}).get('asset_manifest', []))),
            'previous_segment_uid': previous_uid, 'previous_source': copy.deepcopy(previous_source),
            'previous_end_revision': digest(model_world(world)), 'initializing': initializing,
            'input_revision': digest({'context_id': row['scene_context_id'],
                'previous_segment_uid': previous_uid, 'state': model_world(world),
                'pending': bool(scene.get('initial_pending'))})}


def _prepare_record(row, view, record, context):
    if record.get('record_kind') == 'inherit':
        from .inheritance import inherited_record
        if row.get('h3_state_mode') != 'inherit' or record.get('context_id') != context['context_id']:
            raise ValueError('沿用状态归属已变化，请重新选择状态方式')
        return inherited_record(view, context)
    from .ledger import record_from_plan, validate_record
    from .revisions import revision
    from .state_trace import extract_state_record
    from ..editor_draft import snapshot
    from .timing import advance_timing_record
    if record.get('source_revision') != revision(view):
        timing_view=copy.deepcopy(view)
        timing_view['h3_continuity']=copy.deepcopy(record)
        if advance_timing_record(timing_view):
            record=timing_view['h3_continuity']
    source_matches = (record.get('source_revision') == revision(view)
                      and record.get('context_id', context['context_id']) == context['context_id'])
    valid = False
    if record.get('schema_version') == 3 and source_matches:
        try:
            validate_record(record, view)
            if 'end_state' in record and record.get('end_revision') != digest(model_world(record['end_state'])):
                raise ValueError('场景末态版本无效')
            valid = True
        except (ValueError, KeyError, TypeError):
            pass
    old_input = record.get('previous_end_revision')
    if old_input is None and isinstance(record.get('input_state'), dict):
        old_input = digest(model_world(record['input_state']))
    input_matches = old_input == context['previous_end_revision']
    if valid and input_matches and not context['initializing']:
        # Identical facts permit deterministic provenance rebinding. No semantic
        # claim is made by replaying events on a DIFFERENT opening world.
        if (record.get('previous_segment_uid') == context['previous_segment_uid']
                and record.get('previous_source') == context.get('previous_source')
                and 'end_state' in record):
            result = copy.deepcopy(record)
        else:
            result = record_from_plan(record, revision(view), context['input_revision'],
                opening_state=context['input_state'], segment_uid=row['segment_uid'],
                previous_segment_uid=context['previous_segment_uid'])
            for key in ('opening_requirements', 'state_trace_version', 'state_trace_source', 'diagnostics', 'record_status'):
                if key in record:
                    result[key] = copy.deepcopy(record[key])
    else:
        result = extract_state_record(view, context, existing=record or None)
    result.update(context_id=context['context_id'], input_state=copy.deepcopy(
        result.get('initial_state', context['input_state'])),
        previous_source=copy.deepcopy(context.get('previous_source')),
        text_pending=False, state_pending=False)
    result['source_prompt']=snapshot(view)['prompt']
    return result


def ensure_scene_state(jobs, segment_uid, *, include_target=False, target_source='draft'):
    """Prepare only the required chain in memory, with zero screenplay edits.

    Call outside the job-file lock. Persist using a guarded metadata patch or
    the caller's existing content transaction. Render history is immutable.
    """
    from .versions import source_segment, source_record, store_source_record
    rows = _prefix(jobs, segment_uid)
    target = rows[-1]
    key = target.get('scene_context_id')
    if not key:
        return None
    scene = (jobs.get('h3_scene_continuity') or {}).get('contexts', {}).get(key)
    if not scene or not isinstance(scene.get('initial_state'), dict):
        raise ValueError('场景缺少初始人物状态，请检查场景配置')
    world = copy.deepcopy(scene['initial_state'])
    previous_uid, previous_source = None, None
    for row in rows:
        if row.get('scene_context_id') != key:
            continue
        is_target = row['segment_uid'] == segment_uid
        view = source_segment(row, prefer_render=not is_target or target_source == 'selected')
        context = _context(world, view, scene, previous_uid, previous_source)
        if is_target and not include_target:
            return context
        try:
            from services.text_jobs import check_cancelled, current, phase
            check_cancelled()
            if current():
                phase('正在整理承接信息：' + str(row.get('part_id') or row['segment_uid']))
            record = _prepare_record(row, view, source_record(row, view) or {}, context)
        except ValueError as exc:
            raise ValueError(str(row.get('part_id') or row['segment_uid']) + '：' + str(exc)) from exc
        if context['initializing']:
            initial = record.pop('initial_state', None)
            if not isinstance(initial, dict):
                raise ValueError('自动整理缺少场景初态')
            scene.update(initial_state=copy.deepcopy(initial), initial_pending=False)
            context = _context(initial, view, scene, previous_uid, previous_source)
            record['input_revision'] = context['input_revision']
            record['previous_end_revision'] = context['previous_end_revision']
        store_source_record(row, view, record)
        if is_target:
            return context
        world = copy.deepcopy(record['end_state'])
        previous_uid = row['segment_uid']
        previous_source = view['_continuity_source']
    return None


def prepare_runtime_state(segment_uid, *, include_target=False):
    """Lock-free extraction followed by a short compare-and-save transaction."""
    from services.context import pin_runtime
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    with pin_runtime():
        for attempt in range(2):
            with video_jobs_lock():
                before = copy.deepcopy(load_video_jobs())
            prepared = copy.deepcopy(before)
            context = ensure_scene_state(prepared, segment_uid, include_target=include_target)
            patch = preparation_updates(before, prepared, segment_uid)
            if not patch:
                return context
            with video_jobs_lock():
                latest = load_video_jobs()
                try:
                    apply_preparation_updates(latest, patch)
                except ValueError:
                    if attempt == 0:
                        continue
                    raise
                save_video_jobs(latest)
                return context
    raise ValueError('承接信息来源持续变化，请重试当前操作')
