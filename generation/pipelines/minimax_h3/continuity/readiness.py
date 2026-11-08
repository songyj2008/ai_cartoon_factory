"""Read versioned scene facts without starting a model or modifying storage."""
import copy

from .checkpoints import digest
from .state import model_world
from .versions import source_segment, source_record
from .revisions import revision


class SceneStateError(ValueError):
    """Identify the exact scene member whose facts block the current action."""

    def __init__(self, row, *, is_target, reason, reason_code):
        self.blocking_segment_uid = row['segment_uid']
        self.blocking_part_id = str(row.get('part_id') or self.blocking_segment_uid)
        self.is_target = is_target
        self.reason = reason
        self.reason_code = reason_code
        if reason_code == 'initial_state_changed':
            message = (f'{self.blocking_part_id}：{reason}，请重新提取本场景首段状态'
                       if is_target else
                       f'前序段 {self.blocking_part_id}：{reason}。请先打开 {self.blocking_part_id}，'
                       '重新提取本场景首段状态；当前段状态操作尚未完成。')
        elif is_target:
            message = f'{self.blocking_part_id}：{reason}，请沿用上段或提取本段'
        else:
            message = (f'前序段 {self.blocking_part_id}：{reason}。'
                       f'请先打开 {self.blocking_part_id}，沿用上段或提取本段状态；'
                       '当前段状态操作尚未完成。')
        super().__init__(message)

    def details(self):
        return {'blocking_segment_uid': self.blocking_segment_uid,
                'blocking_part_id': self.blocking_part_id,
                'is_target': self.is_target, 'reason_code': self.reason_code}


def source_guard(jobs, uid, *, target_source='selected', compare_content=False):
    rows = jobs.get('segments', [])
    target = next((s for s in rows if s['segment_uid'] == uid), None)
    if target is None:
        raise ValueError('分段不存在或已移除')
    key = target.get('scene_context_id')
    scene = (jobs.get('h3_scene_continuity') or {}).get('contexts', {}).get(key, {})
    sources = []
    for row in rows[:rows.index(target) + 1]:
        if row.get('scene_context_id') != key:
            continue
        view = source_segment(row, prefer_render=row is not target or target_source == 'selected')
        if compare_content:
            from ..editor_draft import snapshot
            from ..contracts import H3GenerationConfig
            draft = snapshot(view)
            # Optimization consumes text, bindings and incoming facts. Completing
            # an equivalent render changes provenance, not those dependencies.
            sources.append({'uid': row['segment_uid'], 'prompt': draft['prompt'],
                            'media': H3GenerationConfig.from_mapping(draft).to_dict()['asset_manifest'],
                            'duration_sec': draft['duration_sec'],
                            'state_mode': row.get('h3_state_mode'),
                            'shots': draft['shot_uids'] if row is target else None})
            continue
        sources.append({'uid': row['segment_uid'], 'source': view['_continuity_source'],
                        'state_mode': row.get('h3_state_mode'),
                        'shots': (row.get('h3_shot_editor') or {}).get('shot_uids')
                        if view['_continuity_source']['kind'] == 'draft' else None})
    return digest({'scene': key, 'config_revision': scene.get('config_revision'),
                   'layout_revision': scene.get('layout_revision'), 'scene_layout': scene.get('scene_layout'),
                   'sources': sources, 'target_source': target_source})


def read_ready_context(jobs, uid, *, include_target=False, target_source='selected'):
    from .ledger import validate_record
    from .preparation import _context
    rows = jobs.get('segments', [])
    target = next((s for s in rows if s['segment_uid'] == uid), None)
    if target is None:
        raise ValueError('分段不存在或已移除')
    key = target.get('scene_context_id')
    if not key:
        return None
    scene = (jobs.get('h3_scene_continuity') or {}).get('contexts', {}).get(key, {})
    if scene.get('initial_pending') or not isinstance(scene.get('initial_state'), dict):
        raise ValueError('场景初态尚未建立，请提取本场景首段状态或校准初态')
    world = copy.deepcopy(scene['initial_state'])
    previous_uid = previous_source = None
    diagnostics = []
    for row in rows[:rows.index(target) + 1]:
        if row.get('scene_context_id') != key:
            continue
        is_target = row is target
        def fail(reason, reason_code):
            raise SceneStateError(row, is_target=is_target, reason=reason, reason_code=reason_code)
        try:
            view = source_segment(row, prefer_render=not is_target or target_source == 'selected')
            context = _context(world, view, scene, previous_uid, previous_source)
            context['diagnostics'] = copy.deepcopy(diagnostics)
            if is_target and not include_target:
                return context
            if row.get('h3_state_mode') == 'unset':
                fail('未设置状态', 'unset')
            record = source_record(row, view)
            if not record:
                fail('未设置状态', 'unset')
            validate_record(record, view)
            if record.get('record_kind') == 'inherit':
                if record.get('context_id') != key:
                    fail('人物状态所属场景已变化', 'context_changed')
                if row.get('h3_state_mode') != 'inherit':
                    fail('请沿用同场景上段状态或提取本段状态', 'inheritance_invalid')
                # Empty changes are a valid identity operation, not missing data.
                # Resolve against the current preceding world without an LLM.
                if is_target:
                    return context
                previous_uid = row['segment_uid']; previous_source = view['_continuity_source']
                continue
            if record.get('context_id') != key:
                fail('人物状态所属场景已变化', 'context_changed')
            if record.get('source_revision') != revision(view):
                fail('提示词或参考图来源已变化', 'source_changed')
            if record.get('previous_end_revision') != digest(model_world(world)):
                fail('场景初态已变化' if previous_uid is None else '前驱人物状态已变化',
                     'initial_state_changed' if previous_uid is None else 'upstream_changed')
            if record.get('end_revision') != digest(model_world(record.get('end_state', {}))):
                fail('人物状态末态版本无效', 'end_revision_invalid')
            diagnostics.extend(record.get('diagnostics') or [])
            if is_target:
                context['diagnostics'] = diagnostics
                return context
            world = copy.deepcopy(record['end_state'])
            previous_uid = row['segment_uid']
            previous_source = view['_continuity_source']
        except SceneStateError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise SceneStateError(row, is_target=is_target, reason=str(exc),
                                  reason_code='record_invalid') from exc
    return None


def scene_state_guard(jobs, uid):
    context = read_ready_context(jobs, uid, target_source='draft')
    if context is None:
        raise ValueError('请先加入场景')
    return {'spatial_constraints': context.get('spatial_constraints', ''), 'uid': uid, 'source': source_guard(jobs, uid, target_source='draft', compare_content=True),
            'input_revision': context['input_revision']}


def validate_scene_guard(jobs, guard):
    from ..editor_draft import EditorConflictError
    current = scene_state_guard(jobs, guard['uid']) if guard else {}
    if not guard or current != guard:
        changed = [key for key in ('uid', 'source', 'input_revision') if (guard or {}).get(key) != current.get(key)]
        raise EditorConflictError('优化所读取的人物状态或稿件已变化，请重新优化；当前稿已保留',
                                  'STALE_OPTIMIZATION', {'changed_fields': changed})
