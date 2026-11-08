"""Shared scene calibration data, independent of each member's framing."""
import copy
import uuid

from .state import SCENE_FACTS


def scene_members(jobs):
    contexts = (jobs.get('h3_scene_continuity') or {}).get('contexts', {})
    return {key: [{field: row.get(field) for field in
                   ('segment_uid', 'segment_index', 'part_id', 'title')}
                  for row in jobs.get('segments', []) if row.get('scene_context_id') == key]
            for key in contexts}


def delete_unused_scene(payload):
    """Delete only an unassigned continuity scene, rechecking usage under lock."""
    from services.context import assert_current_runtime_writable
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from ..editor_draft import EditorConflictError
    assert_current_runtime_writable('删除未使用场景')
    key = payload.get('context_id')
    if not isinstance(key, str) or not key:
        raise ValueError('请选择要删除的场景')
    with video_jobs_lock():
        jobs = load_video_jobs()
        contexts = (jobs.get('h3_scene_continuity') or {}).get('contexts', {})
        if key not in contexts:
            raise ValueError('场景已不存在，请刷新场景列表')
        members = scene_members(jobs)[key]
        if members:
            parts = '、'.join(str(row.get('part_id') or row.get('segment_index') or row['segment_uid'])
                             for row in members)
            raise EditorConflictError('场景仍被 '+parts+' 使用，请先将这些分段移到其他场景或选择“不加入”',
                                      'SCENE_IN_USE')
        scene = contexts[key]
        if ('scene_initial_revision' in payload
                and payload['scene_initial_revision'] != scene.get('config_revision')):
            raise EditorConflictError('场景已被其他页面更新，请刷新列表后重新确认删除', 'SCENE_INITIAL_CONFLICT')
        name = scene.get('name') or key
        del contexts[key]
        save_video_jobs(jobs)
    return {'deleted_context_id': key, 'deleted_scene_name': name}


def _calibration_values(world):
    """A missing fact and an explicitly unknown fact mean the same thing."""
    return {role: {field: {
        'value': facts.get(field, {}).get('value'),
        'relative_to_role_id': facts.get(field, {}).get('relative_to_role_id'),
    } for field in SCENE_FACTS} for role, facts in world.items()}


def calibration_snapshot(jobs, target):
    from .display import project_role_names
    from .constraints import scene_layout
    registered_names = project_role_names()
    key = target.get('scene_context_id')
    scene = (jobs.get('h3_scene_continuity') or {}).get('contexts', {}).get(key, {})
    members = [row for row in jobs.get('segments', [])
               if row.get('scene_context_id') == key] if key else [target]
    roles = {}
    for row in members:
        for asset in (row.get('generation_config') or {}).get('asset_manifest', []):
            meta = asset.get('metadata') or {}
            if asset.get('type') != 'image' or meta.get('asset_kind') != 'character':
                continue
            identity = meta.get('role_id') or meta.get('identity_id') or asset.get('asset_id') or asset.get('id')
            if identity:
                identity = str(identity)
                name = registered_names.get(identity) or registered_names.get(str(meta.get('identity_id')))
                roles.setdefault(identity, {'id': identity, 'name': name or asset.get('role') or asset.get('label') or identity})
    initial = scene.get('initial_state') or {}
    for role in initial:
        roles.setdefault(role, {'id': role, 'name': registered_names.get(role) or role})
    return {
        'scene_roles': list(roles.values()),
        'scene_initial_state': copy.deepcopy(initial),
        'scene_initial_revision': scene.get('config_revision'),
        'scene_layout': scene_layout(scene, (target.get('generation_config') or {}).get('asset_manifest', [])),
        'layout_revision': scene.get('layout_revision'),
        'scene_member_parts': [{field: row.get(field) for field in
                                ('segment_uid', 'segment_index', 'part_id', 'title')}
                               for row in members] if key else [],
    }


def save_initial_calibration(scene, state, payload):
    """Accept one shared calibration without expiring unchanged derived records."""
    if ('scene_initial_revision' in payload
            and payload['scene_initial_revision'] != scene.get('config_revision')):
        from ..editor_draft import EditorConflictError
        raise EditorConflictError('场景初态已被其他页面更新，当前校准内容已保留；请重新读取最新初态后合并',
                                  'SCENE_INITIAL_CONFLICT')
    if not isinstance(state, dict) or not state:
        raise ValueError('请选择初始人物')
    from .reducer import reduce_plan
    updated = reduce_plan({role: {} for role in state}, {'shots': [{
        'shot_uid': 'initial', 'world_events': [
            {'role_id': role, 'facts': facts} for role, facts in state.items()]}]}, 'initial', list(state))
    if (not scene.get('initial_pending')
            and _calibration_values(scene.get('initial_state') or {}) == _calibration_values(updated)):
        return False
    scene.update(initial_state=updated, initial_pending=False, config_revision=uuid.uuid4().hex)
    return True
