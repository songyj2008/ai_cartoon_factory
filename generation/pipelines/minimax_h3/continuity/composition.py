"""Exact spatial patches on authored Shots, including unlabelled legacy prose.

The model classifies spatial semantics. Local validation proves edit locations,
protects explicit structural fields and preserves all remaining source bytes.
"""
import copy
import json
import re
from ..shot_editor import SECTION, parse_prompt, speaker_options, dialogue_units
from ..prompt_retimer import _SHOT, _time_slots
from .state import model_world
from .mapping import model_references
from ..rules import h3_visual_prompt_rules

SYSTEM = """你负责根据有效人物状态和当前稿，统筹整段镜头后逐镜优化构图；原稿是剧情权威，人物状态是空间约束，参考图是身份映射。
先整体阅读editor_prompt和全部shots，围绕本段事件、互动对象和信息变化设计连贯摄影方案，再落实到edits。不能把每次换说话人自动等同于单人近景；检查相邻镜头是否只是重复拍脸，按叙事需要选择关系镜头、双人镜、越肩、反应或单人镜。没有全景、多人同框或运镜的固定配额，合理的固定机位可以保留；不额外输出规划说明。
需要摄影机运动时，正文明确起始取景、运动方向、最终取景及所揭示的互动；固定镜头写清取景。镜头运动与人物走位分开，沿运动全过程确定入画人物。位置和坐站描述与有效状态不符时，只修正对应的纯空间片段，不改原稿剧情动作。
spatial_constraints是本场景共用空间约束，各镜均须遵守；它不授权编造剧情动作。先按input_state建立本段开场，再按每镜原稿明确动作推进位置、基本姿势和在场状态；不能每镜重置为初态。已明确则不重复，未知不猜。发生无法兼容的明确事实冲突时指出冲突，不编造动作消解。
根据本镜叙事重点、人物互动和连续空间关系，选择机位、景别、朝向、景深、遮挡与镜头运动。原稿普通构图可以调整，不是只补站位；适合则保留，不为制造差异而改。用户明确限定的独拍、同框、必须入画等要求及画内说话人必须保留。画面人物名单是待优化的当前安排，不等于全员必须入画。
在场不等于入画，但入画名单必须由真实站位与可实现的取景推导，不能先固定原稿人数，再用构图把其余在场人物排除。逐镜从input_state和scene_layout检查所有在场人物相对机位的前后、左右、视线穿越、遮挡和画框边界；移动镜头检查整个运动过程，而非仅起始画面。多个人物落在同一取景范围内时，即使只是前景背影、肩部、画缘局部、虚化背景或运动中短暂经过，也须保留其存在并计入visible_roles；虚化和不抢主体不等于不入画。
只拍部分人物必须有与既定空间关系相容的具体机位、拍摄方向和画框范围；若借遮挡排除人物，只能使用已知且能遮住该人物的真实遮挡关系。仅写“取景避开其他人”“压窄景别”“留在画外”不构成空间依据，不能用这些结论代替可实现的构图。保留已知布局、位置、朝向和空间锚点，不虚构遮挡物、挪动人物或改变座位来凑人数。机位需要调整时须给出与现有站位一致的完整构图；无法在这些约束下合理排除的人物应保留入画。未提供足够布局时不声称已证明某人必在画外，不编造图片几何细节。
不要求全员每镜入画；确实位于取景范围外或被已知物体完全遮住的人可以不入画。镜头覆盖在场人物所在区域时，不得把该位置写成空无一人，也不能让人物凭空消失。不能仅为登记人物状态扩大景别，也不能为了保留原单人或双人名单忽略取景内已有的人物。最终构图先确定实际可见人物，再输出visible_roles并同步参考用途。
镜头切换或运动造成的入画、出画不等于加入、离开场景。人物移动、起身、进入或离开场景只能来自原稿，不能为了构图编造；画外发声不自动要求入画，不改变说话人、声音渠道或画内外发声方式。
逐镜按原稿事件先后推进状态后再确定取景，不把所有镜头都套回input_state。走出画框但仍在场所内时present不变，只记录原稿明确的位置变化；穿过场所边界真正离场才记录present=false，并将该场所position置null，不能继续占据旧座位。再次进场使用本次原稿明确的落点与基本姿势，没有依据的字段置null，不自动恢复上次位置或姿势；尚未完成的移动不提前登记终点。
visible_roles是整镜曾经可见人物的并集：中途进场、离场前、镜头移动途中或局部遮挡时实际可见的人均计入，即使镜末present=false；不在场的人也可以在本镜按原稿进场。已在场的人因取景出画后再次入画，不重复进场动作，不改变位置。先确定固定物理位置与摄影机方向，再描述画面左右、前后，不能把反打后的画面方向变化写成人物换位。
人物在本段首次入镜时，简短交代与本镜有关且已知的必要位置、坐站及朝向；已有景别词不等于站位已交代，不能只在input_state有记录而最终正文缺失。本段须能独立生成，不依赖“沿用上段”“原站位”。人物状态和全员取景记录仅供内部规划与核对，最终视频正文只在对应Shot的构图中表达必要位置，不另列全场人物占位表，不重复罗列画外人物。朝向或设施未知不猜。优先采用已有固定空间锚点；相对人物位置仅在确有必要时保留，提及角色不等于该角色入画。人物外观与音色各归对应参考，不让背景人物替代主体或代说。
保留剧情动作、表情、语气、对白、声音、Shot数量与顺序和时间，不评估时长。只修改纯空间片段；构图、入画名单及参考用途须一致。
空间与动作混写时只选空间子串：如“镜头构图：近景；角色推开门，抬手示意”，source_quote只选“近景”，不要选整句，不要在replacement重述、润色或扩写推门和抬手动作。已有独立构图字段时，source_quote不得跨过字段分隔符包含后面的正文；原有动作原文保留在原处。状态引用使用这些原稿动作，不能改成新构图中的改写句。
只输出JSON：{"shots":[{"shot_uid":"原UID","visible_roles":["role_id"],"role_coverage":[{"role_id":"稳定ID","visibility":"visible","reason":"空间与取景依据"}],"edits":[{"span_id":"本镜editable_spans中的编号","replacement":"新空间描述"}],"world_events":[]}],"conflicts":[],"unresolved":[]}。shots逐项覆盖输入；人物role_id来自references，禁止编造人物。画面人物名单由程序同步，edits不编辑名单、场景使用或protected_quotes。editable_spans是按原稿字段边界编号的未保护片段，不代表每项都可以改成空间描述；你只选择纯空间片段，剧情动作、表情和声音不改。优先返回span_id，不复述或拼接原文；编号只在本镜内有效，不新增字段标签。replacement用references中的role_id表示人物，由程序映射为对应主体标签，避免自行增删<Subject N>中的空格。如果一个编号片段混有剧情动作，只用source_quote引用其中逐字、唯一的纯空间子串，不选择整段，不跨字段或跳过画面人物名单拼接引用。已有机位须替换而非追加另一套。没有空间描述且确需补充时可用唯一插入点{"insert_at":"shot_start","text":"空间构图说明"}。取景、入画和状态一致且必要空间已交代时返回edits=[]，不因缺标签而修改。
world_events只提取本镜原稿明确的位置、基本姿势、在场变化，事件含role_id/source_quote/facts；facts仅position/posture/present，每项含value及可选relative_to_role_id，无变化[]。补写已有站位不产生world_events；切镜、遮挡或构图造成的可见性变化也不产生world_events，不能用新构图制造状态。首次初始化另返回initial_state，按tracked_role_ids列首镜动作前的三项value对象，未知null。
references仅为身份映射，scene_layout为已有文字布局，未提供图片本身，不编造未交代的场景设施。不得声称已核对图片内容。"""


def _shots(base):
    text = base['prompt']
    sections = list(SECTION.finditer(text))
    detail = next((i for i, s in enumerate(sections) if s[1] == '详细镜头描述'), None)
    if detail is None:
        raise ValueError('提示词缺少【详细镜头描述】')
    begin = sections[detail].end()
    stop = sections[detail + 1].start() if detail + 1 < len(sections) else len(text)
    headers = list(_SHOT.finditer(text, begin, stop))
    if not headers:
        raise ValueError('当前稿缺少完整Shot，不能执行构图优化')
    doc = parse_prompt(text, base['segment_uid'], base['asset_manifest'])
    uids = base.get('shot_uids') or [s['uid'] for s in doc['shots']]
    if len(headers) != len(doc['shots']) or len(uids) != len(headers) or len(set(uids)) != len(uids):
        raise ValueError('Shot身份或数量与当前稿不一致')
    result = []
    for i, (uid, h, parsed) in enumerate(zip(uids, headers, doc['shots'])):
        end = headers[i + 1].start() if i + 1 < len(headers) else stop
        limit = re.search(r'(?m)^\s*限制\s*[:：]', text[h.end():end])
        if limit:
            end = h.end() + limit.start()
        start = h.end()
        while start < end and (text[start].isspace() or text[start] in '：:'):
            start += 1
        result.append({'uid': uid, 'start': start, 'end': end,
                       'body': text[start:end], 'parsed': parsed})
    return result


def _cast(body):
    dialogue = [m.span() for m in re.finditer(r'<d>.*?</d>', body, re.S | re.I)]
    matches = [m for m in re.finditer(r'(?:^|[；;\n])\s*(?:画面人物|入画人物)\s*[:：]\s*([^；;\r\n]+)', body)
               if not any(m.start(1) < b and m.end(1) > a for a,b in dialogue)]
    if len(matches) > 1:
        raise ValueError('入画人物列表重复，请保留唯一名单')
    return matches[0] if matches else None


def _protected(shot):
    body = shot['body']
    spans = [m.span() for m in re.finditer(r'<d>.*?</d>|“[^”]*”|「[^」]*」|『[^』]*』', body, re.S | re.I)]
    spans.extend((s['start'], s['end']) for s in _time_slots(body))
    # Authored field delimiters only: no action/emotion word heuristics.
    label = r'(?:声音|音效|配乐|本句音色参考|对白标签|说话人|必须准确显示的文字|exact_text)\s*[:：][^；;\r\n]*'
    spans.extend(m.span() for m in re.finditer(label, body))
    spans.extend(m.span() for m in re.finditer(r'(?:本镜|本镜头)\s*(?:无对白|无台词)[^；;\r\n]*', body))
    spans.extend(m.span() for m in re.finditer(r'场景使用\s*<Subject\s+\d+>', body))
    cast = _cast(body)
    if cast:
        start, end = cast.span()
        # The preceding separator belongs to the previous field as well.
        # Protect the cast label/value here and check separator preservation
        # separately, so a camera quote ending in an unchanged ';' is legal.
        if body[start:start + 1] in '；;\n':
            start += 1
        spans.append((start, end))
    spans.extend((s['start'], s['end']) for s in shot['parsed'].get('dialogue_spans', []))
    return spans


def editable_spans(shot):
    """Address existing unprotected substrings; directing choices stay with the model."""
    body, protected = shot['body'], sorted(_protected(shot))
    result = []
    for field in re.finditer(r'[^；;\r\n]+', body):
        ranges = [(field.start(), field.end())]
        for a, b in protected:
            remaining = []
            for left, right in ranges:
                if right <= a or left >= b:
                    remaining.append((left, right))
                else:
                    if left < a: remaining.append((left, a))
                    if b < right: remaining.append((b, right))
            ranges = remaining
        for left, right in ranges:
            raw = body[left:right]
            label = re.match(r'\s*(?:镜头构图|人物调度|机位|景别|构图)\s*[:：]\s*', raw)
            if label:
                left += label.end()
            text = body[left:right].strip()
            if text and body.count(text) == 1:
                result.append({'span_id': 'span_' + str(len(result) + 1), 'text': text})
    return result


def resolve_span_edits(result, shots):
    """Resolve model-selected IDs to exact original bytes for the existing validator."""
    patch = copy.deepcopy(result)
    if not isinstance(patch, dict) or not isinstance(patch.get('shots'), list):
        return patch
    catalogue = {shot['uid']: {span['span_id']: span['text'] for span in editable_spans(shot)} for shot in shots}
    for row in patch['shots']:
        if not isinstance(row, dict) or not isinstance(row.get('edits'), list):
            continue
        for edit in row['edits']:
            if not isinstance(edit, dict) or 'span_id' not in edit:
                continue
            if set(edit) != {'span_id', 'replacement'}:
                raise ValueError('编号空间补丁只允许span_id和replacement，不同时提供source_quote')
            uid = row.get('shot_uid')
            spans = catalogue.get(uid, {}) if isinstance(uid, str) else {}
            identifier = edit['span_id']
            if not isinstance(identifier, str) or identifier not in spans:
                raise ValueError('空间片段编号不存在，请选择对应Shot的editable_spans：' + repr(identifier))
            replacement = edit['replacement']
            edit.clear()
            edit.update(source_quote=spans[identifier], replacement=replacement)
    return patch


def plan_composition_patch(base, context):
    from services.llm import call_llm
    from . import spatial_plan
    from .framing import CONTRACT, FramingConflict, validate_framing_plan, role_names, validate_layout, directing_input
    validate_layout(context)
    base = dict(base, _framing_context=context)
    shots = _shots(base)
    payload = {
        'editor_prompt': directing_input(base['prompt']),
        'visibility_authority': '当前Shot画面人物名单和既有参考用途都是待优化安排，不是人物禁止入画要求。主体定义/参考保留关系是编译器自动生成的输出章节，已从editor_prompt移除；人物身份使用references。真实剧情、对白与限制仍逐字保留。',
        'shots': [{'shot_uid': s['uid'], 'shot_number': index,
                   'body_source': {'field': 'editor_prompt', 'shot_number': index},
                   'editable_spans': editable_spans(s)} for index, s in enumerate(shots, 1)],
        'spatial_constraints': context.get('spatial_constraints', ''),
        'input_state': model_world(context['input_state']),
        'tracked_role_ids': context['tracked_role_ids'],
        'initializing': context.get('initializing', False),
        'diagnostics': context.get('diagnostics', []),
        'references': model_references(base['asset_manifest']),
        'role_names': role_names(base, context),
        'scene_layout_details': context.get('scene_layout', {}),
        'scene_layout': [{'scene_id': a.get('metadata', {}).get('scene_id'),
                          'description': a['metadata'].get('description_cn', '')}
                         for a in base['asset_manifest']
                         if a.get('metadata', {}).get('asset_kind') == 'background'],
    }
    contract = '''输出结构必须严格遵守以下规则：
1. shots、visible_roles、edits、world_events 是数组；facts 是一个 JSON 对象，绝不能写成数组。
2. world_events 无变化时为 []；有变化时，每个事件结构为
{"role_id":"输入中的真实人物ID","source_quote":"本镜原文逐字引用","facts":{"position":{"value":"位置文字","relative_to_role_id":"输入中的真实人物ID"},"posture":{"value":"姿势文字"},"present":{"value":true}}}。
此示例仅说明结构，不是剧情内容。facts 只包含原稿明确变化的字段，未变化字段省略；relative_to_role_id 仅在相对位置明确时提供。present.value 只能是布尔值或 null，position/posture.value 只能是字符串或 null。
3. 不得输出示例占位文字。shots 的数量、顺序、shot_uid 与输入完全一致。每项必须且仅含 shot_uid、visible_roles、role_coverage、edits、world_events。
4. initializing=false 时，顶层必须且仅有 shots、conflicts、unresolved，禁止输出 initial_state（即使为空或 null 也禁止）。
5. initializing=true 时，顶层必须且仅有 shots、conflicts、unresolved、initial_state。initial_state 必须是以 tracked_role_ids 中全部人物ID为键的对象，每个人包含 position、posture、present 三项 value 对象；未知值填 null。它表示首镜动作前的状态，不能用本段末态替代。
只输出完整 JSON 对象，不输出解释、Markdown 或局部修补。'''
    system = '\n\n'.join((SYSTEM, contract, CONTRACT,
        '旧画面人物名单及自动参考用途不属于用户明确限制，不能把某人当前未列入名单解释为用户禁止其入镜。'
        '先设计满足原稿事件和已知占位的摄影方案，再决定可见人物。布局不足以排除静默人物时，应先考虑在不新增剧情动作、'
        '不改变坐站位置的前提下，通过合理机位/取景保留其存在；不能只为保留旧单人或双人名单报告未知。'
        '只有真实作者限制或关键物理关系无法通过权限内的空间调整解决，才返回具体conflicts/unresolved。',
        h3_visual_prompt_rules()))
    payload['output_contract'] = {
        'required_top_level_fields': ['shots', 'conflicts', 'unresolved', 'initial_state'] if context.get('initializing') else ['shots', 'conflicts', 'unresolved'],
        'required_shot_fields': ['shot_uid', 'visible_roles', 'role_coverage', 'edits', 'world_events'],
        'facts_type': 'object',
        'allowed_fact_fields': ['position', 'posture', 'present'],
        'preferred_edit': {'span_id': '从本镜editable_spans选择', 'replacement': '新空间描述，人物使用role_id'},
        'edit_addressing': '已有片段优先用span_id；各片段分别修改，不抄写、改写或拼接多个原文片段。编号片段混有剧情动作时，才用逐字唯一的纯空间source_quote子串。',
    }
    source_texts = spatial_plan.sources(base, context)
    payload['spatial_sources'] = spatial_plan.source_references(shots, context, source_texts)
    payload['previous_spatial_plan'] = context.get('previous_spatial_plan')
    # output_contract is the patch contract, not the envelope contract.
    system += '\noutput_contract只约束patch内部；顶层包裹严格为spatial_plan和patch。'
    system += '\neditor_prompt是唯一完整原稿。shots按shot_number关联原稿对应Shot；editable_spans只是可寻址的候选片段，其余动作、对白、时间及作者限制仍以原稿为准，不得改写。'
    system += '\nspatial_sources中的字符串是原文，对象是来源引用。basis.source_id从表中取键；对象须沿引用读取实际原文后填写逐字quote。Shot来源仅在对白之外将Subject标签按references替换为role_id（对白原文不替换）；state来源将value转为字符串。引用对象本身不能作为quote。'
    system += '\n\n' + spatial_plan.RULES
    for attempt in range(2):
        # Keep the original request and its bounded correction independently
        # resumable instead of changing input under one deterministic task ID.
        label = 'h3_composition_patch' + ('_repair1' if attempt else '')
        result, _ = call_llm(system, json.dumps(payload, ensure_ascii=False),
                            label=label, max_tokens=8000, tolerate_invalid_json=True)
        try:
            if not isinstance(result, dict) or set(result) != {'patch', 'spatial_plan'}:
                raise ValueError('构图结果必须包含patch和spatial_plan')
            patch = resolve_span_edits(result['patch'], shots)
            validate_initial_state(patch, context)
            validate_state_patch(base, patch, context['tracked_role_ids'])
            validate_framing_plan(base, context, patch)
            text, manifest = finalize_composition(base, patch)
            validate_spatial_edit(base, dict(base, prompt=text, asset_manifest=manifest, composition_patch=patch))
            bind_final_state_quotes(base, patch, text, asset_manifest=manifest)
            spatial_plan.validate(result['spatial_plan'], patch, context['tracked_role_ids'], source_texts)
            from .constraints import prompt_with_constraints
            text = prompt_with_constraints(text, context.get('spatial_constraints', ''))
            audit = spatial_plan.review(base, context, result['spatial_plan'], patch, text, manifest, attempt)
            if audit['issues']:
                if any(i['kind'] in ('input_conflict', 'unresolved') for i in audit['issues']):
                    raise FramingConflict('空间依据需处理：' + json.dumps(audit['issues'], ensure_ascii=False))
                raise ValueError('构图语义复核未通过：' + json.dumps(audit['issues'], ensure_ascii=False))
            return {'patch': patch, 'spatial_plan': result['spatial_plan'], 'spatial_review': audit,
                    'spatial_sources': source_texts}
        except FramingConflict as exc:
            # A genuine authored contradiction stops immediately. An unresolved
            # framing choice gets one model-owned redesign within the SAME
            # bounded correction budget; local code never invents a camera.
            if attempt or not exc.only_unresolved:
                raise
            payload['validation_feedback'] = {
                'error': str(exc), 'previous_response': result,
                'instruction': '本次是构图优化而非冻结镜头审查。原名单不禁止其他在场人物入画；自动参考用途也不是作者禁令。'
                '重新统筹全部Shot，先判断能否通过权限内的机位、取景与可见名单调整解决这些未知，保留实际可见的静默人物。'
                '不编造移动、离场、遮挡或未知几何，不修改原稿动作、对白、时间与真实作者限制。'
                '若缺少固定区域、同侧或对侧等位置依据，扩大画幅或把全员写进画面不等于补足依据；仍须保留该关键未知。'
                '若仍有真实关键缺项，保留具体unresolved；否则返回完整修正结果并清空已解决的诊断。',
            }
        except ValueError as exc:
            if attempt:
                raise ValueError('人物状态优化返回结果校验失败（已自动重试一次）：' + str(exc)) from exc
            # Keep the original input authoritative. Only the model may revise
            # its response; never coerce facts or discard an unauthorized state.
            payload['validation_feedback'] = {
                'error': str(exc), 'previous_response': result,
                'instruction': '上一份结果未通过校验，尚未应用。按原始输入和输出结构重新生成完整结果，不改写原稿剧情。请优先直接选择对应Shot的editable_spans编号，以span_id和replacement返回，不再手抄source_quote；多处修改用多条edits分别选择，不合并跨越保护字段的片段。replacement中人物使用references的role_id。不需要修改的镜头返回edits=[]。',
            }


def bind_final_state_quotes(base, patch, text, *, asset_manifest=None):
    """Bind evidence to final source bytes using the shared whitespace rule."""
    from .evidence import source_quote
    rows = copy.deepcopy(patch['shots'])
    manifest = base['asset_manifest'] if asset_manifest is None else asset_manifest
    final_shots = _shots(dict(base, prompt=text, asset_manifest=manifest))
    for original, row, final in zip(_shots(base), rows, final_shots):
        for event in row['world_events']:
            revised = source_quote(original['body'], event['source_quote'])
            quote = None
            if revised is not None:
                for edit in row['edits']:
                    if edit.get('source_quote'):
                        matched = source_quote(revised, edit['source_quote'])
                        if matched is not None:
                            revised = revised.replace(matched, edit['replacement'])
                if manifest != base['asset_manifest']:
                    from ..asset_binder import H3AssetBinder
                    from ..prompt_reconciler import H3PromptReconciler
                    revised = H3PromptReconciler()._remap_reference_tags(
                        revised, H3AssetBinder().bind(base), H3AssetBinder().bind({'asset_manifest': manifest}))
                quote = source_quote(final['body'], revised)
            if quote is None:
                raise ValueError('Shot ' + row['shot_uid'] + ' 的状态引用无法对应最终稿：' + event['source_quote']
                                 + '。请保留这段原稿动作，只修改纯空间子串；不要用包含动作的整句作构图替换，也不要把改写后的动作作为新依据')
            event['source_quote'] = quote
    return rows


def validate_initial_state(patch, context):
    if not isinstance(patch, dict):
        raise ValueError('优化结果必须是 JSON 对象')
    if not context.get('initializing'):
        if 'initial_state' in patch:
            raise ValueError('initializing=false：场景已初始化，禁止返回 initial_state，顶层只允许 shots')
        return
    initial = patch.get('initial_state')
    if not isinstance(initial, dict) or set(initial) != set(context['tracked_role_ids']):
        raise ValueError('initializing=true：initial_state 必须覆盖 tracked_role_ids 中的全部人物')
    for role, facts in initial.items():
        if not isinstance(facts, dict) or set(facts) != {'position', 'posture', 'present'}:
            raise ValueError('initial_state.' + role + ' 必须包含 position/posture/present 三项对象')
        for field, fact in facts.items():
            if not isinstance(fact, dict) or 'value' not in fact:
                raise ValueError('initial_state.' + role + '.' + field + ' 必须是包含 value 的对象')
            value = fact['value']
            if value is not None and not (type(value) is bool if field == 'present' else isinstance(value, str)):
                raise ValueError('initial_state.' + role + '.' + field + '.value 类型无效')
            if fact.get('relative_to_role_id') is not None and fact['relative_to_role_id'] not in initial:
                raise ValueError('initial_state 关联人物身份无效')


def validate_state_patch(base, patch, tracked_roles=None):
    from .evidence import source_quote
    from .reducer import reduce_plan
    shots = _shots(base)
    if not isinstance(patch, dict) or set(patch) - {'shots', 'initial_state', 'conflicts', 'unresolved'}:
        raise ValueError('构图结果包含越权字段')
    rows = patch.get('shots')
    if not isinstance(rows, list) or len(rows) != len(shots):
        raise ValueError('构图结果须覆盖全部Shot')
    for shot, row in zip(shots, rows):
        if (not isinstance(row, dict) or not {'shot_uid', 'visible_roles', 'edits', 'world_events'} <= set(row)
                or set(row) - {'shot_uid', 'visible_roles', 'role_coverage', 'edits', 'world_events'}
                or row.get('shot_uid') != shot['uid']):
            raise ValueError('构图补丁Shot身份或字段无效')
        if not isinstance(row['world_events'], list):
            raise ValueError('缺少镜头状态变化列表')
        for event in row['world_events']:
            if (not isinstance(event, dict) or set(event) - {'role_id', 'facts', 'source_quote'}
                    or source_quote(shot['body'], event.get('source_quote')) is None):
                raise ValueError('状态变化缺少本镜原稿依据')
            if not isinstance(event.get('facts'), dict):
                raise ValueError('world_events.facts 必须是 JSON 对象，例如 {"posture":{"value":"坐着"}}，不能是数组')
    if tracked_roles is not None:
        reduce_plan({}, patch, base['segment_uid'], tracked_roles)


def _spatial_text(value, tags):
    if (not isinstance(value, str) or not value.strip()
            or re.search(r'[\r\n]|</?d\b|【|\[Shot\b|场景使用\s*<Subject\b|'
                         r'(?:画面人物|入画人物|声音|音效|配乐|本句音色参考|对白标签|说话人|'
                         r'必须准确显示的文字|exact_text|限制)\s*[:：]', value, re.I)
            or _time_slots(value)):
        raise ValueError('空间补丁不能注入名单、场景、对白、声音或时间结构')
    if tags:
        pattern = r'(?<![A-Za-z0-9_])(' + '|'.join(re.escape(r) for r in sorted(tags, key=len, reverse=True)) + r')(?![A-Za-z0-9_])'
        value = re.sub(pattern, lambda m: tags[m[0]], value)
    return value


def merge_composition_patch(base, patch):
    shots = _shots(base)
    tags = {o['speaker']: o['tag'] for o in speaker_options(base['asset_manifest']) if o['key'].startswith('image:')}
    validate_state_patch(base, patch)
    changes = []
    for index, (shot, row) in enumerate(zip(shots, patch['shots']), 1):
        try:
            cast = row['visible_roles']
            if (not isinstance(cast, list) or any(not isinstance(r, str) or r not in tags for r in cast)
                    or len(set(cast)) != len(cast)):
                raise ValueError('入画人物没有对应图片参考')
            for unit in dialogue_units(shot['parsed']):
                spans = unit.get('delivery_spans')
                onscreen = (any(s['visibility'] == 'onscreen' for s in spans)
                            if spans else unit.get('scope') == 'onscreen')
                if onscreen and unit['speaker'] not in cast:
                    raise ValueError('原稿画内说话人必须保留入画：' + unit['speaker'])
            body = shot['body']
            protected = _protected(shot)
            local = []
            if not isinstance(row['edits'], list):
                raise ValueError('edits必须是列表，未修改时填[]')
            for edit in row['edits']:
                if not isinstance(edit, dict):
                    raise ValueError('空间补丁须为对象')
                if set(edit) == {'insert_at', 'text'}:
                    if edit['insert_at'] != 'shot_start':
                        raise ValueError('无效的空间插入点')
                    local.append((0, 0, '镜头构图：' + _spatial_text(edit['text'], tags) + '；'))
                    continue
                if set(edit) != {'source_quote', 'replacement'}:
                    raise ValueError('空间补丁字段无效')
                quote = edit['source_quote']
                if not isinstance(quote, str) or not quote or body.count(quote) != 1:
                    raise ValueError('空间引用必须逐字、唯一匹配本镜原文；无效source_quote=' + repr(quote)
                                     + '。请从本镜body原文复制，不改写人物标签、代词或标点')
                start = body.index(quote)
                end = start + len(quote)
                overlaps = [body[max(start,a):min(end,b)] for a,b in protected if start < b and end > a]
                if overlaps:
                    raise ValueError('空间补丁触及对白、时间、声音或场景保护区；冲突原文=' + repr(overlaps[0])
                                     + '；source_quote=' + repr(quote) + '。请缩小引用，仅选择纯空间子串')
                replacement = _spatial_text(edit['replacement'], tags)
                cast_boundary = _cast(body)
                if cast_boundary and body[cast_boundary.start():cast_boundary.start()+1] in '；;\n':
                    boundary = cast_boundary.start()
                    if start <= boundary < end and not replacement.endswith(body[boundary]):
                        raise ValueError('空间补丁不能删除或改写入画名单前的字段分隔符；请缩小source_quote，保留分隔符原文')
                # Explicit spatial fields have deterministic delimiters. A
                # camera edit cannot swallow the following action sentence.
                # Unlabelled prose continues to use model-selected substrings.
                for field in re.finditer(r'(?:^|[；;\n])\s*(?:镜头构图|人物调度|机位|景别|构图)\s*[:：][^；;\r\n]*', body):
                    field_start = field.start()
                    if body[field_start:field_start + 1] in '；;\n':
                        field_start += 1
                    if start < field.end() and end > field_start and not (field_start <= start and end <= field.end()):
                        raise ValueError('空间引用跨越构图字段边界，包含后面的正文；请只选择该字段内的纯空间子串，保留原稿动作，不要整句重写')
                local.append((start, end, replacement))
            ordered = sorted(local)
            if any(b[0] < a[1] or b[0] == a[0] for a, b in zip(ordered, ordered[1:])):
                raise ValueError('空间补丁重叠或插入点重复')
            cast_match = _cast(body)
            cast_text = '、'.join(tags[r] for r in cast) or '无'
            if cast_match:
                if cast != shot['parsed'].get('visible_roles'):
                    local.append((*cast_match.span(1), cast_text))
            else:
                insertion = next((e for e in local if e[:2] == (0, 0)), None)
                if insertion:
                    local.remove(insertion)
                local.append((0, 0, '画面人物：' + cast_text + '；' + (insertion[2] if insertion else '')))
            for start, end, replacement in local:
                changes.append((shot['start'] + start, shot['start'] + end, replacement))
        except ValueError as exc:
            raise ValueError(f'Shot {index}：{exc}') from exc
    text = base['prompt']
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def finalize_composition(base, patch):
    """Replay spatial edits, then project references by stable asset identity."""
    from .assets import composition_references
    from ..asset_binder import H3AssetBinder
    from ..contracts import H3GenerationConfig
    from ..prompt_compiler import H3PromptCompiler
    from ..prompt_reconciler import H3PromptReconciler
    context = base.get('_framing_context')
    if context is not None:
        from .framing import validate_framing_plan
        validate_framing_plan(base, context, patch)
    text = merge_composition_patch(base, patch)
    manifest = composition_references(base, text, patch['shots'])
    if manifest != base['asset_manifest']:
        text = H3PromptReconciler().reconcile(text, H3GenerationConfig.from_mapping(base),
                                            H3GenerationConfig.from_mapping({'asset_manifest': manifest}))
    else:
        text = H3PromptCompiler.compact_reference_sections(text, H3AssetBinder().bind(base))
    return text, manifest


def validate_spatial_edit(base, candidate):
    patch = candidate.get('composition_patch')
    if not patch:
        raise ValueError('构图操作修改了补丁之外的受保护正文')
    text, manifest = finalize_composition(base, patch)
    guard = candidate.get('state_guard') or {}
    if 'spatial_constraints' in guard:
        from .constraints import prompt_with_constraints
        text = prompt_with_constraints(text, guard['spatial_constraints'])
    if text != candidate['prompt'] or manifest != candidate['asset_manifest']:
        raise ValueError('构图操作修改了补丁之外的受保护正文')
