"""Private spatial planning contracts for the explicit composition operation."""
import copy
import json

RULES = '''本次输出使用包装 {"spatial_plan": {...}, "patch": {...}}。此前关于顶层shots/conflicts/unresolved的合同均指patch内部。
先由有效初态、已保存布局、原稿明确事实建立统一空间关系，再统筹全部Shot摄影方案，最后填写patch。不得把颜色、服装或可移动道具单独当作固定位置；有原图标记到固定区域的明确映射时可据此转换，否则关键关系未知应返回patch.unresolved，不猜地图。
scene_layout_details是保存布局，scene_layout只是背景通用简介；没有背景像素。前段计划仅为摄影参考，当前位置以input_state为准，不照搬旧站位。保持同侧、对侧、相邻顺序；画面左右变化不等于人物移动。普通对话保持可读的互动轴关系，越轴须有明确可读过渡，不新增剧情动作。
spatial_plan结构：{"schema_version":1,"basis":[{"basis_id":"b1","source_id":"输入spatial_sources的真实键","quote":"该来源逐字子串"}],"relations":[{"description":"有来源的固定区域或人物关系","role_ids":["真实角色ID"],"basis_ids":["b1"]}],"shots":[{"shot_uid":"原UID","basis_ids":["b1"],"views":[{"phase":"固定取景或原稿运动的起点/必要节点/终点","camera_position":"相对已知区域的机位","view_direction":"拍摄方向","axis":"互动轴及所在侧，未知则写未知","characters":[{"role_id":"真实角色ID","visibility":"visible/partial/out_of_frame/occluded/not_present","layer":"前景/主体/背景/画缘/画外","body_view":"正面/侧面/肩背/局部/不可见或未知"}]}]}]}。
每个view逐项覆盖全部tracked_role_ids，同一角色同一节点只有一个身体；不同节点可变层次。全镜各节点可见并集等于patch.visible_roles。固定镜头一个view，运动只列影响入画的必要节点，不增加Shot或时间。每镜依据指向basis；只记录与本镜有关的已知事实，不要求全场精确尺寸。人物和Shot用稳定ID，计划不写Subject编号或绝对时码。
关键未知通过原有unresolved报告，非关键未知不阻塞。空间计划只供内部，最终只改Shot必要空间文字，不输出全员占位表。'''


def sources(base, context):
    from .composition import _shots
    from .mapping import normalize_script_entities
    result = {'layout': (context.get('scene_layout') or {}).get('description', '')}
    for role, facts in context['input_state'].items():
        for field, fact in facts.items():
            if fact.get('value') is not None:
                result[f'state:{role}:{field}'] = str(fact['value'])
    for shot in _shots(base):
        result['shot:' + shot['uid']] = normalize_script_entities(shot['body'], base['asset_manifest'])[0]
    return result


def source_references(shots, context, source_texts):
    """Wire-only references; sources() remains the persisted evidence contract."""
    result = {'layout': {'field': 'scene_layout_details', 'path': ['description'], 'default': ''}}
    for role, facts in context['input_state'].items():
        for field, fact in facts.items():
            if fact.get('value') is not None:
                result[f'state:{role}:{field}'] = {
                    'field': 'input_state', 'path': [role, field, 'value'], 'as_string': True}
    for index, shot in enumerate(shots, 1):
        result['shot:' + shot['uid']] = {
            'field': 'editor_prompt', 'shot_number': index,
            'normalization': 'subject_to_role_outside_dialogue'}
    for key, ref in list(result.items()):
        text = source_texts[key]
        # A short literal is cheaper and simpler than a pointer. Keep it as-is.
        unavailable_state = ref.get('field') == 'input_state' and ref['path'][1] not in ('position', 'posture', 'present')
        if unavailable_state or len(json.dumps(ref, ensure_ascii=False)) >= len(json.dumps(text, ensure_ascii=False)):
            result[key] = text
    return result


def validate(plan, patch, roles, source_texts):
    def require(ok, message):
        if not ok:
            raise ValueError('空间计划：' + message)
    require(isinstance(plan, dict) and set(plan) == {'schema_version', 'basis', 'relations', 'shots'}
            and plan['schema_version'] == 1, '结构无效')
    require(all(isinstance(plan[k], list) for k in ('basis', 'relations', 'shots')), '字段必须是数组')
    ids = set()
    for basis in plan['basis']:
        require(isinstance(basis, dict) and set(basis) == {'basis_id', 'source_id', 'quote'}, '依据结构无效')
        key, source, quote = basis['basis_id'], basis['source_id'], basis['quote']
        require(isinstance(key, str) and bool(key.strip()) and key not in ids, '依据编号重复或为空')
        require(isinstance(source, str) and source in source_texts and isinstance(source_texts[source], str) and isinstance(quote, str)
                and bool(quote.strip()) and quote in source_texts[source], '依据未引用真实输入')
        ids.add(key)
    def refs(value, allowed):
        return isinstance(value, list) and all(isinstance(v, str) and v in allowed for v in value) and len(value) == len(set(value))
    for relation in plan['relations']:
        require(isinstance(relation, dict) and set(relation) == {'description', 'role_ids', 'basis_ids'}, '关系结构无效')
        require(isinstance(relation['description'], str) and bool(relation['description'].strip())
                and refs(relation['role_ids'], roles) and refs(relation['basis_ids'], ids)
                and bool(relation['basis_ids']), '关系缺少有效依据')
    require(len(plan['shots']) == len(patch['shots']), 'Shot数量不一致')
    for shot, expected in zip(plan['shots'], patch['shots']):
        require(isinstance(shot, dict) and set(shot) == {'shot_uid', 'basis_ids', 'views'}
                and shot['shot_uid'] == expected['shot_uid'], 'Shot身份或顺序不一致')
        require(refs(shot['basis_ids'], ids), '镜头依据不存在')
        require(isinstance(shot['views'], list) and bool(shot['views']), '缺少取景节点')
        visible = set()
        for view in shot['views']:
            require(isinstance(view, dict) and set(view) == {'phase', 'camera_position', 'view_direction', 'axis', 'characters'}, '取景节点结构无效')
            require(all(isinstance(view[k], str) and view[k].strip() for k in ('phase', 'camera_position', 'view_direction', 'axis')), '机位与方向不得为空')
            require(isinstance(view['characters'], list), '人物必须为数组')
            seen = set()
            for person in view['characters']:
                require(isinstance(person, dict) and set(person) == {'role_id', 'visibility', 'layer', 'body_view'}, '人物投影结构无效')
                role = person['role_id']
                require(isinstance(role, str) and role in roles and role not in seen, '同一节点人物重复或身份未知')
                require(person['visibility'] in ('visible', 'partial', 'out_of_frame', 'occluded', 'not_present')
                        and all(isinstance(person[k], str) and person[k].strip() for k in ('layer', 'body_view')), '人物投影无效')
                seen.add(role)
                if person['visibility'] in ('visible', 'partial'):
                    visible.add(role)
            require(seen == set(roles), '节点未覆盖全员')
        require(visible == set(expected['visible_roles']), '全程可见并集与补丁不一致')


def review(base, context, plan, patch, text, manifest, attempt):
    from services.llm import call_llm
    from .composition import _shots
    from .mapping import model_references
    system = '''只读复核构图候选，不改稿。原稿剧情和有效人物状态是事实边界，已保存布局提供固定关系，未提供背景像素。
核对内部关系、全部镜头和最终正文：同侧/对侧/沿边顺序是否保持；反打是否偷偷换位；机位、方向、遮挡与可见名单是否自洽；前景肩背和清晰主体是否串人或重复身体；运动全过程是否漏人；是否把移动后状态重置；是否编造转身走位；Shot是否落实计划且没有重复全员占位表。不得把计划自己的结论当作依据。
只输出 {"issues":[{"shot_uid":"真实Shot UID","kind":"design/input_conflict/unresolved","reason":"具体问题","quote":"原稿或最终正文逐字依据"}]}。无问题issues=[]。非关键未知不阻塞，真实输入冲突不得用新剧情消解。'''
    payload = {'original_prompt': base['prompt'], 'candidate_prompt': text,
               'input_state': context['input_state'], 'scene_layout': context.get('scene_layout'),
               'spatial_plan': plan, 'patch': patch, 'references': model_references(manifest)}
    result, _ = call_llm(system, json.dumps(payload, ensure_ascii=False), label='h3_composition_review' + ('_repair1' if attempt else ''), tolerate_invalid_json=True)
    if not isinstance(result, dict) or set(result) != {'issues'} or not isinstance(result['issues'], list):
        raise ValueError('空间复核结构无效')
    evidence = {s['uid']: [s['body']] for s in _shots(base)}
    for shot in _shots(dict(base, prompt=text, asset_manifest=manifest)):
        evidence[shot['uid']].append(shot['body'])
    for issue in result['issues']:
        if (not isinstance(issue, dict) or set(issue) != {'shot_uid', 'kind', 'reason', 'quote'}
                or issue['shot_uid'] not in [s['shot_uid'] for s in patch['shots']]
                or issue['kind'] not in ('design', 'input_conflict', 'unresolved')
                or not isinstance(issue['reason'], str) or not issue['reason'].strip()
                or not isinstance(issue['quote'], str) or not issue['quote'].strip()
                or not any(issue['quote'] in p for p in evidence[issue['shot_uid']])):
            raise ValueError('空间复核缺少真实镜头、原文或原因')
    return result


def predecessor_plan(jobs, context):
    """Use only a valid selected source, never prepare upstream work."""
    from .versions import source_segment
    from .readiness import read_ready_context
    from .framing import validate_framing_record
    from .state import model_world
    uid = context.get('previous_segment_uid')
    row = next((r for r in jobs['segments'] if r['segment_uid'] == uid), None)
    if row is None:
        return None
    try:
        view = source_segment(row)
        record = validate_framing_record(view, read_ready_context(jobs, uid))
        if (record['scene_layout'] != context.get('scene_layout')
                or record['shots'][-1]['state_after'] != model_world(context['input_state'])):
            return None
        return copy.deepcopy(record.get('spatial_plan'))
    except (ValueError, KeyError, TypeError):
        return None
