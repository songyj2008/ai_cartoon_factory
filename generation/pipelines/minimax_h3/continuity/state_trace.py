"""Extract evidenced scene facts from a frozen draft without editing its content."""
from __future__ import annotations

import copy
import json
import re

from ..editor_draft import snapshot
from ..shot_editor import parse_prompt
from .evidence import source_quote
from .ledger import record_from_plan
from .mapping import model_references
from .revisions import revision
from .state import SCENE_FACTS, model_world


STATE_TRACE_VERSION = 1
SYSTEM = '''你只读分析固定稿件，建立供后段承接的人物状态记录，不执行构图优化或改稿。
initializing=true 且提供 scene_opening_context 时，初态证据也可引用该开场上下文；它只解释首镜动作前的状态，不得把后续动作结果提前。world_events 和 opening_requirements 的证据仍必须来自所属 Shot。
原稿是剧情权威。references仅映射稳定role_id，不能修改正文、机位、景别、visible_roles、台词、素材、Subject/Picture编号或时长。没有入画不代表离场，不为连续性编造移动。
状态值只描述动作后已经成立的静态事实，例如“站着”“近侧右位椅子前方”；“起身并向前迈步”是动作过程，不能作为持续姿势或位置。尚未完成的动作不得提前登记完成后的落点，不足以确定则保留未知。已有字段无变化不输出，保留 input_state 的原值、关联人物和依据，不作同义改写。
区分取景出画、场内移动和真正离场：切镜、遮挡、摄影机移动或走出画框本身不改变present；仍在场所内的实际移动只更新有依据的位置和姿势。确实跨出当前场所边界才记录present=false，并同时把该场所position置null，旧位置不是当前占位。重新进场记录present=true，位置和基本姿势采用本次原文依据，未明确的字段置null，不自动恢复离场前的座位或姿势；不要编造走位来补足未知。
同镜world_events按实际事件先后排列；跨镜接着前镜事件结果分析，不每镜重置input_state。visible_roles表示整镜曾经可见的人，不是首帧或末帧在场名单：中途进入、离开前及运动中短暂可见的人都可以列出，名单本身不证明开场在场或离场。后续镜头在场若由本段先前进场事件建立，不列为本段动作前present=true的opening_requirements；明确“尚在场外，随后进门”时，动作前要求应为present=false，而非用动作后的true倒推。
只返回JSON：{"shots":[{"shot_uid":"输入UID","world_events":[{"role_id":"稳定ID","source_quote":"本镜原文","facts":{"position":{"value":"位置","relative_to_role_id":"可选关联人物"},"posture":{"value":"姿势"},"present":{"value":true}}}]}],"opening_requirements":[{"shot_uid":"输入UID","role_id":"稳定ID","field":"position/posture/present","value":"本段明确依赖的开场值","source_quote":"该镜原文","relative_to_role_id":"可选关联人物"}],"conflicts":[{"shot_uid":"输入UID","role_id":"稳定ID","field":"事实字段","source_quote":"对应开场要求原文","reason":"具体冲突"}],"unresolved":[{"shot_uid":"输入UID","role_id":"稳定ID","field":"事实字段","source_quote":"对应开场要求原文","reason":"必须核对但信息不足的原因"}]}。
shots按顺序逐项覆盖输入，每镜只提取原稿明确的位置、基本姿势、在场变化；无变化world_events=[]。只允许position/posture/present；position/posture值为非空字符串或null，present为布尔值或null。source_quote必须逐字来自所属Shot，不引用别镜。facts每项只含value、可选relative_to_role_id；不能用素材编号作为人物或事实值。
opening_requirements只记录在本段动作执行前就必须成立、且原稿有明确依据的要求；后续动作造成的新状态放world_events，不能误列成开场要求。兼容性需根据原稿动作语义与input_state核对，不能仅把旧事件在新前驱上重放。语义兼容时复用input_state已有事实的原措辞及relative_to_role_id，避免同义改写导致版本漂移。existing_record只是历史提取依据，不代表与新前驱兼容。
conflicts/unresolved仅能引用opening_requirements中同镜、同人物、同字段、有原文依据的关键要求。无关字段未知正常保留，不填入unresolved，不阻塞；不得猜测未知值。真实冲突列conflicts；关键开场要求依赖的上游事实未知，列unresolved。
仅initializing=true时，另返回initial_state：按tracked_role_ids完整列出所有人物，每人含position/posture/present三项，每项为{"value":... ,"source_quote":"首镜原文" ,"relative_to_role_id":"可选"}；未知value=null、source_quote=""。它表示首镜动作执行前状态，不把首镜动作结果提前。已知值必须有首镜原文依据；初始化可用该初态核对开场要求。initializing=false严禁返回initial_state。
【原文证据与结构化状态分别填写】
source_quote 是原文证据，逐字保留其中的 <Subject n>、<Picture n>、<Audio n>，不能为了身份转换改写引用。
facts 中的 value、opening_requirements 中的 value 和 initial_state 中的 value 是结构化事实，不是原文引用。它们不得含任何 Subject/Picture/Audio 标签，即使标签只是位置描述的一部分，也不得照抄进去。
按 character_bindings 将原稿人物标签映射为稳定 role_id；所有人物身份键及 relative_to_role_id 都从 tracked_role_ids 取值。相对另一人物或其工位的位置，在 value 中描述空间关系，使用 relative_to_role_id 指向该人物；如需保留人物所属工位等语义，可在 value 使用该人物的稳定 role_id 或姓名，不能丢失原有空间信息。
背景图片和音色编号不是人物身份。只有原稿明确与某人物相关时才填写 relative_to_role_id，不根据映射表编造关系。
这属于原文到结构化事实的抽取，不是修改提示词。遵守身份映射与保留 source_quote 原文同时成立；不能用删除事件、改动原文或把已知事实置空来避开校验。'''


def _fail(message, *, details=None):
    error = ValueError('人物状态提取：' + message)
    if details:
        error.details = details
    raise error


def _fact(field, value, roles, *, allow_quote=False, location=None):
    allowed = {'value', 'relative_to_role_id'} | ({'source_quote'} if allow_quote else set())
    if not isinstance(value, dict) or 'value' not in value or set(value) - allowed:
        _fail('事实须提供value及可选关联人物')
    item = copy.deepcopy(value)
    val = item['value']
    if val is not None:
        valid = type(val) is bool if field == 'present' else isinstance(val, str) and bool(val.strip())
        if not valid:
            _fail('事实值类型无效：' + field)
        if isinstance(val, str) and re.search(r'<\s*(?:Subject|Picture|Audio)\s+\d+', val, re.I):
            location = {**(location or {}), 'field': field}
            target = (' Shot ' + str(location['shot_index']) if location.get('shot_index') else '')
            target += (' 人物 ' + location['role_id'] if location.get('role_id') else '')
            _fail('状态事实须使用稳定人物身份，不使用素材编号：' + target.strip()
                  + ' 字段 ' + field + '，收到 ' + repr(val)
                  + '；请按 character_bindings 在状态中使用稳定人物身份，relative_to_role_id 填关联人物ID；source_quote 保留原文',
                  details={'location': location, 'invalid_value': val, 'allowed_role_ids': list(roles),
                           'instruction': '修正该结构化状态值中的素材标签；保留空间含义和原文证据，不删除事件、不编造关系。'})
    relative = item.get('relative_to_role_id')
    if relative is not None and (not isinstance(relative, str) or relative not in roles):
        _fail('关联人物身份无效')
    return item


def _quote(body, quote):
    recovered = source_quote(body, quote)
    if recovered is None:
        _fail('状态变化或开场要求缺少所属Shot原文依据')
    return recovered


def _identity(row, by_uid, roles):
    uid, role, field = row.get('shot_uid'), row.get('role_id'), row.get('field')
    if not isinstance(uid, str) or uid not in by_uid:
        _fail('Shot身份无效')
    if not isinstance(role, str) or role not in roles:
        _fail('人物身份无效')
    if not isinstance(field, str) or field not in SCENE_FACTS:
        _fail('状态字段无效')
    return uid, role, field


def _validate_initial(raw, roles, first_body):
    if not isinstance(raw, dict) or set(raw) != set(roles):
        _fail('初始化须完整覆盖场次人物')
    result = {}
    for role in roles:
        facts = raw[role]
        if not isinstance(facts, dict) or set(facts) != SCENE_FACTS:
            _fail('初始化须提供position/posture/present')
        result[role] = {}
        for field, fact in facts.items():
            item = _fact(field, fact, roles, allow_quote=True,
                         location={'scope': 'initial_state', 'shot_index': 1, 'role_id': role})
            quote = item.get('source_quote', '')
            if item['value'] is None:
                if not isinstance(quote, str) or quote:
                    _fail('未知初态不得附带确认依据')
                item['source_quote'] = ''
            else:
                item['source_quote'] = _quote(first_body, quote)
            result[role][field] = item
    return result


def _diagnostic_label(segment, by_uid, row):
    index = list(by_uid).index(row['shot_uid']) + 1
    part = str(segment.get('part_id') or segment.get('title') or segment['segment_uid'])
    return f"{part} Shot {index} 人物 {row['role_id']} 字段 {row['field']}"


def _prepare_inputs(segment, context):
    if not isinstance(context, dict):
        _fail('缺少场景输入状态')
    frozen = copy.deepcopy(segment)
    ctx = copy.deepcopy(context)
    draft = snapshot(frozen)
    if not isinstance(draft['prompt'], str) or not draft['prompt'].strip():
        _fail('当前稿为空')
    doc = parse_prompt(draft['prompt'], draft['segment_uid'], draft['asset_manifest'])
    parsed = doc.get('shots') or []
    uids = draft.get('shot_uids') or [s['uid'] for s in parsed]
    if (not parsed or len(uids) != len(parsed) or any(not isinstance(uid, str) or not uid for uid in uids)
            or len(set(uids)) != len(uids)):
        _fail('Shot身份或数量与固定稿不一致')
    by_uid = {uid: shot['body'] for uid, shot in zip(uids, parsed)}
    roles = ctx.get('tracked_role_ids')
    if (not isinstance(roles, list) or any(not isinstance(role, str) or not role for role in roles)
            or len(set(roles)) != len(roles)):
        _fail('场次人物名单无效')
    world = ctx.get('input_state')
    if not isinstance(world, dict) or set(world) - set(roles):
        _fail('场景输入人物无效')
    initializing = bool(ctx.get('initializing'))
    return frozen, ctx, draft, by_uid, roles, world, initializing


def extract_state_record(segment, context, *, existing=None):
    """Validate model evidence, allowing one correction of malformed output."""
    frozen, ctx, draft, by_uid, roles, world, initializing = _prepare_inputs(segment, context)
    payload = {
        'segment_uid': draft['segment_uid'], 'part_id': frozen.get('part_id'),
        'shots': [{'shot_uid': uid, 'body': body} for uid, body in by_uid.items()],
        'references': model_references(draft['asset_manifest']),
        'input_state': model_world(world), 'tracked_role_ids': roles,
        'initializing': initializing,
        'existing_record': copy.deepcopy(existing) if existing else None,
    }
    payload['shot_coverage_contract'] = {
        'expected_count': len(by_uid), 'ordered_shot_uids': list(by_uid),
        'instruction': '返回的shots必须按此顺序逐项覆盖所有UID；即使某镜没有状态变化，也返回该镜及world_events=[]。初始化首镜初态时仍须分析本段全部镜头，不能只返回首镜。',
    }
    if initializing and ctx.get('opening_source_excerpt'):
        payload['scene_opening_context'] = ctx['opening_source_excerpt']
    payload['character_bindings'] = [
        {key: ref[key] for key in ('subject_tag', 'role_id', 'name') if key in ref}
        for ref in payload['references'] if ref.get('type') == 'image'
        and ref.get('asset_kind') == 'character' and ref.get('role_id') in roles
    ]
    from services.llm import call_llm
    from .checkpoints import digest
    call_key = digest(payload)[:24]
    system = SYSTEM + '''\n类型规则同样适用于 opening_requirements，不能套用字符串占位示例：
当 field="present" 时，value 只能是 JSON 布尔值 true、false 或 null，禁止字符串 "true"、"false" 和数字 1、0。
开场在场要求示例：{"shot_uid":"输入UID","role_id":"输入人物ID","field":"present","value":true,"source_quote":"该镜原文"}。
当 field="position" 或 "posture" 时，value 才是非空字符串或 null。
world_events[*].facts.present.value 和 initial_state[*].present.value 也遵守相同布尔类型规则。
facts 必须是对象，不是数组。示例只说明结构，ID和引用必须来自实际输入。'''
    for attempt in range(2):
        # A correction has new input; recovery must only reuse the same attempt.
        label = 'h3_prompt_state_trace_' + call_key + ('_repair1' if attempt else '')
        raw, _ = call_llm(system, json.dumps(payload, ensure_ascii=False), max_tokens=6500,
                          label=label, tolerate_invalid_json=True)
        try:
            record = validate_state_result(frozen, ctx, raw, existing=existing)
        except ValueError as exc:
            if attempt:
                raise ValueError('人物状态提取结果校验失败（已自动重试一次）：' + str(exc)) from exc
            payload['validation_feedback'] = {
                'error': str(exc), 'previous_response': raw,
                'instruction': '上次结果未通过校验，未保存。根据原始输入修正结构和类型后返回完整JSON；不得编造状态、原文依据或删除真实冲突来通过校验。',
            }
            if getattr(exc, 'details', None):
                payload['validation_feedback']['details'] = copy.deepcopy(exc.details)
            continue
        record['state_trace_source'] = 'llm'
        return record


def validate_state_result(segment, context, raw, *, existing=None):
    """Validate reviewed structured recovery data without invoking a provider.

    ``segment`` is the caller's chosen draft/render projection. Critical opening
    conflicts and unresolved requirements raise ValueError with source evidence.
    All inputs, including ``raw`` and historical evidence, remain unchanged.
    """
    frozen, ctx, draft, by_uid, roles, world, initializing = _prepare_inputs(segment, context)
    uids = list(by_uid)
    raw = copy.deepcopy(raw)
    allowed = {'shots', 'opening_requirements', 'conflicts', 'unresolved'} | ({'initial_state'} if initializing else set())
    if not isinstance(raw, dict) or set(raw) - allowed or not {'shots', 'opening_requirements', 'conflicts', 'unresolved'} <= set(raw):
        _fail('结果包含越权字段或缺少状态契约字段')
    rows = raw['shots']
    if not isinstance(rows, list) or len(rows) != len(uids):
        received = [row.get('shot_uid') if isinstance(row, dict) else None
                    for row in rows] if isinstance(rows, list) else []
        _fail('结果须按顺序覆盖全部Shot：预期 ' + str(len(uids)) + ' 镜，收到 '
              + (str(len(rows)) + ' 镜' if isinstance(rows, list) else '非数组'),
              details={'expected_shot_uids': uids, 'received_shot_uids': received,
                       'missing_shot_uids': [uid for uid in uids if uid not in received],
                       'instruction': '按expected_shot_uids顺序返回完整shots；无变化也须保留该镜并填写world_events=[]，不得跳过镜头或编造事件。'})
    plan = {'shots': []}
    for shot_index, (expected, row) in enumerate(zip(uids, rows), 1):
        if (not isinstance(row, dict) or set(row) != {'shot_uid', 'world_events'}
                or row['shot_uid'] != expected or not isinstance(row['world_events'], list)):
            _fail('Shot身份、顺序或字段无效')
        events = []
        for event in row['world_events']:
            if not isinstance(event, dict) or set(event) != {'role_id', 'source_quote', 'facts'}:
                _fail('状态事件包含越权字段或缺少依据')
            role = event['role_id']
            if not isinstance(role, str) or role not in roles:
                _fail('状态变化人物不在场次名单')
            quote = _quote(by_uid[expected], event['source_quote'])
            facts = event['facts']
            if not isinstance(facts, dict) or not facts or set(facts) - SCENE_FACTS:
                _fail('状态变化字段无效')
            events.append({'role_id': role, 'source_quote': quote,
                           'facts': {field: _fact(field, fact, roles,
                               location={'scope': 'world_events', 'shot_uid': expected,
                                         'shot_index': shot_index, 'role_id': role})
                               for field, fact in facts.items()}})
        plan['shots'].append({'shot_uid': expected, 'world_events': events})
    requirements = raw['opening_requirements']
    if not isinstance(requirements, list):
        _fail('开场要求须为列表')
    normalized = []
    seen = set()
    for req in requirements:
        if (not isinstance(req, dict) or not {'shot_uid', 'role_id', 'field', 'value', 'source_quote'} <= set(req)
                or set(req) - {'shot_uid', 'role_id', 'field', 'value', 'source_quote', 'relative_to_role_id'}):
            _fail('开场要求结构无效')
        identity = _identity(req, by_uid, roles)
        quote = _quote(by_uid[identity[0]], req['source_quote'])
        fact = _fact(identity[2], {k: req[k] for k in ('value', 'relative_to_role_id') if k in req}, roles,
                     location={'scope': 'opening_requirements', 'shot_uid': identity[0],
                               'shot_index': uids.index(identity[0]) + 1, 'role_id': identity[1]})
        key = (*identity, quote)
        if key in seen:
            _fail('开场要求重复')
        seen.add(key)
        normalized.append({**dict(zip(('shot_uid', 'role_id', 'field'), identity)), **fact, 'source_quote': quote})
    initial = _validate_initial(raw.get('initial_state'), roles,
                                next(iter(by_uid.values())) + '\n' + ctx.get('opening_source_excerpt', '')) if initializing else None
    opening = initial if initializing else world
    critical = {(r['shot_uid'], r['role_id'], r['field'], r['source_quote']): r
                for r in normalized if r['value'] is not None}
    issues = []
    for kind in ('conflicts', 'unresolved'):
        diagnostics = raw[kind]
        if not isinstance(diagnostics, list):
            _fail(kind + '须为列表')
        for diagnostic in diagnostics:
            if (not isinstance(diagnostic, dict) or set(diagnostic) != {'shot_uid', 'role_id', 'field', 'source_quote', 'reason'}
                    or not isinstance(diagnostic.get('reason'), str) or not diagnostic['reason'].strip()):
                _fail('开场诊断结构无效')
            identity = _identity(diagnostic, by_uid, roles)
            quote = _quote(by_uid[identity[0]], diagnostic['source_quote'])
            key = (*identity, quote)
            # Unrelated unknown facts must not create a new manual workflow.
            if key not in critical:
                continue
            item = {**diagnostic, 'source_quote': quote}
            issues.append((_diagnostic_label(frozen, by_uid, item),
                           ('开场冲突' if kind == 'conflicts' else '关键开场事实未明确') + '：' + item['reason'], quote))
    # Unknown upstream facts cannot silently satisfy an explicit opening demand.
    # Semantic equality of natural-language values remains the model's task.
    for req in critical.values():
        current = opening.get(req['role_id'], {}).get(req['field'], {}).get('value')
        if current is None:
            issues.append((_diagnostic_label(frozen, by_uid, req), '关键开场事实未明确', req['source_quote']))
        elif req['field'] == 'present' and current is not req['value']:
            issues.append((_diagnostic_label(frozen, by_uid, req), '开场在场要求与前驱状态冲突', req['source_quote']))
    rendered = list(dict.fromkeys(f'{label}：{reason}（原文：{quote}）' for label, reason, quote in issues))
    record = record_from_plan(plan, revision(frozen), ctx['input_revision'],
                              opening_state=opening, segment_uid=draft['segment_uid'],
                              previous_segment_uid=ctx.get('previous_segment_uid'))
    record.update(input_state=copy.deepcopy(opening), opening_requirements=normalized,
                  diagnostics=rendered, record_status='ready_with_issues' if rendered else 'ready',
                  state_trace_version=STATE_TRACE_VERSION, state_trace_source='reviewed_recovery',
                  source_prompt=draft['prompt'], text_pending=False, state_pending=False)
    if initializing:
        record['initial_state'] = copy.deepcopy(initial)
    return record
