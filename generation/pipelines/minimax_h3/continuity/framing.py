"""Complete scene coverage contracts; camera geometry remains model-owned.

This record is deliberately separate from the ending-state ledger. Replacing an
ending-state declaration must never erase the decisions about who is in frame.
"""
from __future__ import annotations

import copy
import json

from .checkpoints import digest
from .state import model_world, character_keys

SCHEMA_VERSION = 1
VISIBILITY = {'visible', 'partial', 'out_of_frame', 'occluded', 'not_present'}
VISIBLE = {'visible', 'partial'}

CONTRACT = '''全员取景合同：每个Shot增加role_coverage数组，逐项覆盖tracked_role_ids中全部人物，不能只列说话人。
每项严格为{"role_id":"稳定ID","visibility":"visible/partial/out_of_frame/occluded/not_present","reason":"具体空间与取景依据"}。
visible/partial对应visible_roles，partial包括前景肩背、虚化、画缘或运镜途中短暂可见。visible_roles是整镜曾可见并集，不是同一帧人数。
out_of_frame明确实际位置与摄影机方向、画框边界的关系；occluded只能引用已有且能完全遮挡此人的设施/人物关系。不能只写未入画、保持画外，也不能先保留旧人数再编造理由。
not_present仅用于整镜确实不在场且没有进场事件的人；镜末离场不代表整镜不在场。按原稿事件顺序推进状态，切镜和摄影机运动不改变present。
reason用于核对，不能替代最终视频正文。必要位置、坐站、摄影机与被取景覆盖的静默人物必须落实到空间edits，不能只存在role_coverage里。摄影方案应围绕事件重新判断，不能只为说话人补椅子或缩窄原画框。
顶层必须含conflicts和unresolved数组；无问题均为[]。确有影响本镜执行的冲突/关键未知时，每项为{"shot_uid":"原UID","role_id":"稳定ID","source_quote":"所属Shot原文","reason":"具体问题"}。只报告当前执行必须解决的问题，未知但无关的字段不阻塞。不编造走位、离场或遮挡消解冲突。'''


class FramingConflict(ValueError):
    """An evidenced creative conflict, rather than malformed model JSON."""

    def __init__(self, message, diagnostics=()):
        super().__init__(message)
        self.diagnostics = copy.deepcopy(list(diagnostics))
        self.only_unresolved = bool(diagnostics) and all(row['kind'] == 'unresolved' for row in diagnostics)


def directing_input(text):
    """Generated usage annotations are output, never a user visibility veto.

    Keep authored Shots, dialogue and exclusions byte-for-byte. Identity and
    reference semantics are supplied separately in the references payload.
    """
    from ..prompt_reconciler import H3PromptReconciler
    return H3PromptReconciler()._without_sections(text, {'主体定义', '参考保留关系', '场景人物占位'})


def role_names(base, context):
    from .mapping import model_references
    names = {role: role for role in context['tracked_role_ids']}
    try:
        from .display import project_role_names
        registered = project_role_names()
        names.update({role: registered[role] for role in names if registered.get(role)})
    except (OSError, ValueError, KeyError):
        pass
    for ref in model_references(base['asset_manifest']):
        if (ref.get('type') == 'image' and ref.get('asset_kind') == 'character'
                and ref.get('role_id') in names and ref.get('name')
                and names[ref['role_id']] == ref['role_id']):
            names[ref['role_id']] = ref['name']
    return names


def _binding_signature(manifest):
    from ..contracts import H3GenerationConfig
    return digest(H3GenerationConfig.from_mapping({'asset_manifest': manifest}).to_dict()['asset_manifest'])


def validate_layout(context):
    layout = context.get('scene_layout') or {}
    if layout.get('description') and layout.get('current') is False:
        raise ValueError('场景布局对应的背景图已变化，请核对并保存场景布局后再准备镜头')


def _shots(base):
    from .composition import _shots as parse
    return parse(base)


def _diagnostics(base, context, patch):
    from .evidence import source_quote
    shots = {shot['uid']: shot['body'] for shot in _shots(base)}
    problems = []
    diagnostics = []
    for key in ('conflicts', 'unresolved'):
        rows = patch.get(key)
        if not isinstance(rows, list):
            raise ValueError('全员取景结果必须提供' + key + '数组，无问题填[]')
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'shot_uid', 'role_id', 'source_quote', 'reason'}
                    or row.get('shot_uid') not in shots or row.get('role_id') not in context['tracked_role_ids']
                    or not isinstance(row.get('reason'), str) or not row['reason'].strip()
                    or source_quote(shots[row['shot_uid']], row.get('source_quote')) is None):
                raise ValueError('取景冲突须定位真实Shot、人物、原文依据和具体原因')
            index = list(shots).index(row['shot_uid']) + 1
            problems.append(f"Shot {index} 人物 {row['role_id']}：{row['reason']}")
            diagnostics.append({'kind': key, **copy.deepcopy(row)})
    if problems:
        raise FramingConflict('场景取景存在需处理的问题：' + '；'.join(problems), diagnostics)


def validate_framing_plan(base, context, patch):
    """Check completeness and state/identity facts, never infer camera geometry."""
    from .composition import validate_state_patch
    from .reducer import reduce_plan
    validate_layout(context)
    validate_state_patch(base, patch, context['tracked_role_ids'])
    _diagnostics(base, context, patch)
    roles = set(context['tracked_role_ids'])
    world = copy.deepcopy(patch.get('initial_state', context['input_state']))
    states = []
    for index, row in enumerate(patch['shots'], 1):
        coverage = row.get('role_coverage')
        if not isinstance(coverage, list):
            raise ValueError(f'Shot {index} 缺少全员role_coverage，不能只返回入画名单')
        keys = []
        for item in coverage:
            if (not isinstance(item, dict) or set(item) != {'role_id', 'visibility', 'reason'}
                    or not isinstance(item['role_id'], str) or item['role_id'] not in roles
                    or not isinstance(item['visibility'], str) or item['visibility'] not in VISIBILITY
                    or not isinstance(item['reason'], str) or not item['reason'].strip()):
                raise ValueError(f'Shot {index} 人物取景记录结构无效')
            keys.append(item['role_id'])
        if len(keys) != len(set(keys)) or set(keys) != roles:
            missing = '、'.join(sorted(roles - set(keys)))
            raise ValueError(f'Shot {index} 必须逐人交代全部场景人物；漏报：{missing or "重复人物"}')
        visible = {item['role_id'] for item in coverage if item['visibility'] in VISIBLE}
        if (not isinstance(row.get('visible_roles'), list)
                or any(not isinstance(role, str) for role in row['visible_roles'])
                or len(set(row['visible_roles'])) != len(row['visible_roles'])
                or set(row['visible_roles']) != visible):
            raise ValueError(f'Shot {index} visible_roles与全程取景可见并集不一致')
        if visible - character_keys(base['asset_manifest']):
            raise ValueError(f'Shot {index} 实际可见人物缺少对应图片参考：' + '、'.join(sorted(visible - character_keys(base['asset_manifest']))))
        before = copy.deepcopy(world)
        # Record known presence through EVERY event, including entry and exit in
        # the same Shot. End-state-only comparisons lose such transient people.
        presence = {role: [world.get(role, {}).get('present', {}).get('value')] for role in roles}
        for event in row['world_events']:
            world = reduce_plan(world, {'shots': [{'shot_uid': row['shot_uid'], 'world_events': [event]}]},
                                base['segment_uid'], roles)
            for role in roles:
                presence[role].append(world.get(role, {}).get('present', {}).get('value'))
        for item in coverage:
            values = presence[item['role_id']]
            if item['visibility'] == 'not_present' and any(value is not False for value in values):
                raise ValueError(f"Shot {index} 人物 {item['role_id']} 本镜仍在场或曾进场，不能登记整镜不在场")
            if all(value is False for value in values) and item['visibility'] != 'not_present':
                raise ValueError(f"Shot {index} 人物 {item['role_id']} 整镜不在场且无进场事件，不能安排在场取景")
        states.append({'shot_uid': row['shot_uid'], 'state_before': model_world(before),
                       'state_after': model_world(world)})
    return states


def build_framing_record(base, context, patch, text, manifest):
    states = validate_framing_plan(base, context, patch)
    from .composition import bind_final_state_quotes
    rows = bind_final_state_quotes(base, patch, text, asset_manifest=manifest)
    for row, state in zip(rows, states):
        row.pop('edits', None)
        row.update(state)
    return {'schema_version': SCHEMA_VERSION, 'context_id': context.get('context_id'),
            'scene_context_id': context.get('context_id'), 'segment_uid': base['segment_uid'],
            'tracked_role_ids': list(context['tracked_role_ids']),
            'input_state': model_world(patch.get('initial_state', context['input_state'])),
            'input_revision': context.get('input_revision'),
            'scene_layout': copy.deepcopy(context.get('scene_layout', {})),
            'spatial_constraints': context.get('spatial_constraints', ''),
            'role_names': role_names(base, context), 'source_prompt': text,
            'source_asset_manifest': copy.deepcopy(manifest),
            'source_binding_hash': _binding_signature(manifest),
            'shot_uids': [row['shot_uid'] for row in rows], 'shots': rows,
            'conflicts': [], 'unresolved': [], 'status': 'ready'}


def _reference_graph(manifest):
    """Exact numbered media and semantic owners; presentation metadata excluded."""
    from ..asset_binder import H3AssetBinder
    fields = ('asset_kind', 'role_id', 'identity_id', 'image_id', 'background_id', 'scene_id',
              'voice_only', 'picture_role', 'reference_role', 'usage', 'retention_marker',
              'retention_relationship', 'retention_note')
    return [(tag, kind, asset.path, asset.use_video_audio,
             {key: asset.metadata[key] for key in fields if key in asset.metadata})
            for tag, kind, asset in H3AssetBinder().bind({'asset_manifest': manifest}).prompt_references]


def advance_framing_reference_record(segment, context, *, source_manifest=None):
    """Prove a compiler-only representation change; never bless a creative edit.

    Old records without their original reference snapshot cannot be migrated by
    guessing. A caller may supply independently recovered, hash-matching source
    media. New records retain that source evidence for normal save round-trips.
    """
    from ..editor_draft import snapshot
    from ..contracts import H3GenerationConfig
    from ..prompt_reconciler import H3PromptReconciler
    from ..asset_binder import H3AssetBinder
    from ..prompt_compiler import H3PromptCompiler
    from .evidence import source_quote
    record = segment.get('h3_scene_framing')
    if not isinstance(record, dict) or record.get('schema_version') != SCHEMA_VERSION:
        return False
    original = source_manifest if source_manifest is not None else record.get('source_asset_manifest')
    if not isinstance(original, list) or _binding_signature(original) != record.get('source_binding_hash'):
        return False
    current = snapshot(segment)
    if (record.get('source_prompt') == current['prompt']
            and record.get('source_binding_hash') == _binding_signature(current['asset_manifest'])):
        return False
    try:
        if _reference_graph(original) != _reference_graph(current['asset_manifest']):
            return False
        old_config = H3GenerationConfig.from_mapping({'asset_manifest': original})
        new_config = H3GenerationConfig.from_mapping(current)
        reconciler = H3PromptReconciler()
        if reconciler.reconcile(record['source_prompt'], old_config, new_config) != current['prompt']:
            return False
        updated = copy.deepcopy(record)
        final = _shots(dict(current, shot_uids=current.get('shot_uids') or record['shot_uids']))
        if [shot['uid'] for shot in final] != record['shot_uids']:
            return False
        compiler = H3PromptCompiler()
        entries = compiler._subject_reference_entries(H3AssetBinder().bind(current))
        for row, shot in zip(updated['shots'], final):
            for event in row['world_events']:
                quote = event['source_quote']
                if source_quote(shot['body'], quote) is None:
                    quote = compiler._replace_subject_mentions(quote, entries)
                quote = source_quote(shot['body'], quote)
                if quote is None:
                    return False
                event['source_quote'] = quote
        updated.update(source_prompt=current['prompt'], source_asset_manifest=copy.deepcopy(current['asset_manifest']),
                       source_binding_hash=_binding_signature(current['asset_manifest']),
                       representation_rebind={'kind': 'compiler_reference_render',
                                              'previous_binding_hash': record['source_binding_hash']})
        candidate = dict(segment, h3_scene_framing=updated)
        validate_framing_record(candidate, context)
    except (ValueError, KeyError, TypeError):
        return False
    segment['h3_scene_framing'] = updated
    return True


def validate_framing_record(segment, context):
    from ..editor_draft import snapshot
    if not isinstance(context, dict):
        raise ValueError('场景人物状态尚未就绪，暂不能核对逐镜取景版本')
    base = snapshot(segment)
    record = segment.get('h3_scene_framing')
    if not isinstance(record, dict) or record.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('本段尚未完成全员取景核对，请根据人物状态优化提示词或执行一键准备')
    if (record.get('context_id') != context.get('context_id')
            or record.get('segment_uid') != segment.get('segment_uid')
            or record.get('input_state') != model_world(context['input_state'])
            or set(record.get('tracked_role_ids', [])) != set(context['tracked_role_ids'])
            or record.get('scene_layout', {}) != context.get('scene_layout', {})
            or record.get('spatial_constraints', '') != context.get('spatial_constraints', '')
            or record.get('source_prompt') != base['prompt']
            or record.get('source_binding_hash') != _binding_signature(base['asset_manifest'])):
        raise ValueError('场景、人物状态、构图稿或参考已变化，逐镜取景需重新核对')
    rows = record.get('shots')
    required = {'shot_uid', 'visible_roles', 'role_coverage', 'world_events', 'state_before', 'state_after'}
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not required <= set(row) for row in rows):
        raise ValueError('逐镜取景记录缺少Shot')
    patch = {'shots': [{key: copy.deepcopy(row[key]) for key in
                       ('shot_uid', 'visible_roles', 'role_coverage', 'world_events')} | {'edits': []}
                       for row in rows], 'conflicts': [], 'unresolved': []}
    base['shot_uids'] = base.get('shot_uids') or record.get('shot_uids')
    states = validate_framing_plan(base, context, patch)
    parsed = _shots(base)
    if 'spatial_plan' in record:
        from .spatial_plan import validate
        if record.get('spatial_review') != {'issues': []} or not isinstance(record.get('spatial_sources'), dict):
            raise ValueError('空间计划缺少通过复核的记录或输入依据')
        validate(record['spatial_plan'], patch, context['tracked_role_ids'], record['spatial_sources'])
    for row, state, shot in zip(rows, states, parsed):
        if (row.get('state_before') != state['state_before'] or row.get('state_after') != state['state_after']
                or set(row['visible_roles']) != set(shot['parsed'].get('visible_roles', []))):
            raise ValueError('逐镜取景记录与正文名单或状态推进不一致')
    return copy.deepcopy(record)


def validate_framing_state(segment, context, *, end_state=None):
    """State-only tools cannot silently contradict a CURRENT framing plan."""
    if not segment.get('h3_scene_framing'):
        return
    try:
        record = validate_framing_record(segment, context)
    except ValueError:
        return  # An old framing record cannot dictate an edited screenplay.
    expected = record['shots'][-1]['state_after']
    actual = model_world(context['input_state'] if end_state is None else end_state)
    if expected != actual:
        labels = {'position': '位置', 'posture': '姿势', 'present': '在场状态'}
        differences = []
        for role in sorted(set(expected) | set(actual)):
            for field, label in labels.items():
                before = expected.get(role, {}).get(field, {})
                after = actual.get(role, {}).get(field, {})
                if before != after:
                    name = record.get('role_names', {}).get(role, role)
                    differences.append(f"{name}的{label}：镜头记录={before.get('value')!r}，结束状态={after.get('value')!r}")
        raise ValueError('逐镜取景的状态变化记录与保存的结束状态不一致（视频未提交）：' + '；'.join(differences)
                         + '。请在编辑器核对并保存结束状态；重新生成视频不会自动调用LLM修正。')


def advance_framing_timing_record(segment, *, before=None):
    """Rebind only proven timing-only edits, not a new camera or cast."""
    from ..editor_draft import snapshot
    from ..prompt_retimer import validate_timing_edit
    from .timing import _quote_after_retime
    record = segment.get('h3_scene_framing')
    if not isinstance(record, dict) or record.get('schema_version') != SCHEMA_VERSION:
        return False
    current = snapshot(segment)
    if record.get('source_binding_hash') != _binding_signature(current['asset_manifest']):
        return False
    old_text = record.get('source_prompt')
    if not isinstance(old_text, str) or not old_text:
        return False
    try:
        validate_timing_edit(old_text, current['prompt'])
        old = _shots(dict(current, prompt=old_text, shot_uids=record['shot_uids']))
        new = _shots(dict(current, shot_uids=current.get('shot_uids') or record['shot_uids']))
        if [s['uid'] for s in new] != record['shot_uids'] or len(old) != len(new):
            return False
        updated = copy.deepcopy(record)
        for row, a, b in zip(updated['shots'], old, new):
            for event in row['world_events']:
                event['source_quote'] = _quote_after_retime(event['source_quote'], a['body'], b['body'])
    except (ValueError, KeyError, TypeError):
        return False
    updated['source_prompt'] = current['prompt']
    segment['h3_scene_framing'] = updated
    return True


def review_existing_framing(base, context):
    """Read-only migration of an authored scene draft; never rewrite its camera."""
    from services.llm import call_llm
    from .mapping import model_references
    from .composition import validate_state_patch, resolve_span_edits
    validate_layout(context)
    shots = _shots(base)
    payload = {'editor_prompt': directing_input(base['prompt']), 'input_state': model_world(context['input_state']),
               'tracked_role_ids': context['tracked_role_ids'], 'role_names': role_names(base, context),
               'references': model_references(base['asset_manifest']),
               'scene_layout': context.get('scene_layout', {}),
               'spatial_constraints': context.get('spatial_constraints', ''),
               'shots': [{'shot_uid': s['uid'], 'shot_number': i,
                          'body_source': {'field': 'editor_prompt', 'shot_number': i}}
                         for i, s in enumerate(shots, 1)]}
    system = ('只读核对已保存的连续场景镜头，不改稿、构图、台词、时间或素材。逐镜分析全部人物的空间存在，'
              '按原稿明确事件推进状态，再核对当前名单与取景；不要为了保留旧名单编造画外理由。'
              '只返回JSON，顶层shots/conflicts/unresolved；shots每项为shot_uid/visible_roles/role_coverage/edits/world_events。'
              'edits必须[]，visible_roles必须匹配当前正文。若正文遗漏实际取景中的人，报告具体冲突，不改名单假装通过。'
              'world_events需本镜原文source_quote和facts{position/posture/present:{value,...}}。\n' + CONTRACT)
    system += '\neditor_prompt为唯一原稿；shots按shot_number引用其中对应Shot，shot_uid用于返回结果关联。'
    for attempt in range(2):
        result, _ = call_llm(system, json.dumps(payload, ensure_ascii=False),
                            label='h3_framing_review' + ('_repair1' if attempt else ''), tolerate_invalid_json=True)
        try:
            patch = resolve_span_edits(result, shots)
            if not isinstance(patch, dict) or set(patch) != {'shots', 'conflicts', 'unresolved'}:
                raise ValueError('只读取景核对仅允许shots/conflicts/unresolved')
            _diagnostics(base, context, patch)
            validate_state_patch(base, patch, context['tracked_role_ids'])
            for source, row in zip(shots, patch['shots']):
                if row['edits'] or set(row['visible_roles']) != set(source['parsed'].get('visible_roles', [])):
                    raise ValueError('只读取景核对不能改正文名单或返回改稿补丁')
            record = build_framing_record(base, context, patch, base['prompt'], base['asset_manifest'])
            fake = {'segment_uid': base['segment_uid'], 'scene_context_id': context['context_id'],
                    'generation_config': {'prompt_override': base['prompt'], 'asset_manifest': base['asset_manifest'],
                                          'duration_sec': base.get('duration_sec')},
                    'h3_shot_editor': {'shot_uids': record['shot_uids']}, 'h3_scene_framing': record}
            validate_framing_record(fake, context)
            return record
        except FramingConflict:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            if attempt:
                raise ValueError('全员取景核对失败（已自动修正一次）：' + str(exc)) from exc
            payload['validation_feedback'] = {'error': str(exc), 'previous_response': result}
