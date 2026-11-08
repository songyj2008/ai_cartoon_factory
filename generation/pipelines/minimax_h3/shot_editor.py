"""Deterministic, lossless Shot text editing. Time arithmetic uses centiseconds."""
from __future__ import annotations

import copy
import hashlib
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .speech_contract import preserve_delivery, validate_delivery, render_delivery, has_silence_declaration
from .prompt_retimer import _SHOT, _seconds, _time_slots

SECTION = re.compile(r"(?m)^【([^】]+)】[ \t]*$")
_LINE = re.compile(r'<d>\s*(?:\[(?:Chinese|English|中文|英文)\])?(.*?)</d>', re.S | re.I)
_OWNER = re.compile(r'(<Subject\s+\d+>|<Audio\s+\d+>|[\w\u4e00-\u9fff]+(?:[ \t]+[\w\u4e00-\u9fff]+)*)(?:\s*\(S\d+\))?([^<>；\n]*?)(?:说|报告)[：:]\s*$', re.I)
_DELIVERY_OWNER = re.compile(
    r'(<Subject\s+\d+>|<Audio\s+\d+>|[\w\u4e00-\u9fff]+?)'
    r'(?:\s*\(S\d+\))?(在画外通过电话|在画外|以画外旁白)说[：:]\s*$', re.I)
_LABELED_OWNER = re.compile(
    r'(?:对白标签[：:]\s*(?:onscreen|offscreen|telephone|narration)\s*[·；;，,]?\s*)?'
    r'说话人[：:]\s*(<Subject\s+\d+>|<Audio\s+\d+>|[\w\u4e00-\u9fff]+)'
    r'([^<>\n]*?)[：:]\s*$', re.I)
_SILENT = '本镜无对白，画内人物不做说话口型。'
_CONTINUATION = re.compile(r'接着上个\s*Shot\s*继续说[，,]\s*', re.I)


def speaker_options(manifest):
    from .asset_binder import H3AssetBinder
    from .prompt_compiler import H3PromptCompiler
    bindings = H3AssetBinder().bind({'asset_manifest': manifest or []})
    options = []
    for entry in H3PromptCompiler()._subject_reference_entries(bindings):
        if entry['asset_kind'] == 'background':
            continue
        meta = entry['metadata']
        options.append({'key': 'image:' + str(meta.get('identity_id') or entry['asset_id']),
                        'tag': entry['subject_tag'], 'label': entry['name'],
                        'speaker': str(meta.get('role_id') or entry['name'])})
    for binding in (*bindings.videos, *bindings.audios):
        if (binding.asset.metadata or {}).get('binding_source') == 'character_voice' and any(o['speaker'] == (binding.asset.metadata or {}).get('role_id') for o in options):
            continue
        tag = binding.paired_audio_tag if binding.asset.type.value == 'video' else binding.prompt_tag
        if tag:
            meta = binding.asset.metadata or {}
            options.append({'key': 'audio:' + binding.asset.asset_id, 'tag': tag,
                            'label': binding.asset.label or binding.display_role,
                            'speaker': str(meta.get('role_id') or binding.display_role)})
    for option in options:
        matches = [a for a in bindings.audios if (a.asset.metadata or {}).get('role_id') == option['speaker']]
        if len(matches) == 1:
            option['voice_asset_id'] = matches[0].asset.asset_id
    return options


def parse_dialogues(body, uid, options):
    rows, spans = [], []
    for index, match in enumerate(_LINE.finditer(body)):
        prefix = body[:match.start()]
        continuation = list(_CONTINUATION.finditer(prefix))
        offset = continuation[-1].end() if continuation else 0
        same_sentence = list(re.finditer('接续本句，', prefix))
        if same_sentence and same_sentence[-1].start() >= (spans[-1]['end'] if spans else 0):
            offset = max(offset, same_sentence[-1].end())
        owner = (_LABELED_OWNER.search(prefix[offset:]) or _DELIVERY_OWNER.search(prefix[offset:])
                 or _OWNER.search(prefix[offset:]))
        if owner is None:
            raise ValueError('有对白无法确定说话人，请在台词前明确人物及“说：”')
        tag = owner[1]
        option = next((o for o in options if tag in (o['tag'], o['speaker'], o['label'])), None)
        if option is None:
            option = {'key': 'named:' + tag, 'tag': tag, 'speaker': tag, 'label': tag}
            options.append(option)
        delivery = owner[0]
        scope = ('telephone' if '电话' in delivery or 'telephone' in delivery.lower() else
                 'narration' if '旁白' in delivery or 'narration' in delivery.lower() else
                 'offscreen' if '画外' in delivery or 'offscreen' in delivery.lower() else 'onscreen')
        row = {'uid': uid + ':dialogue:' + str(index), 'speaker_key': option['key'],
               'scope': scope, 'line': match[1].strip()}
        if option.get('voice_asset_id'):
            row['voice_asset_id'] = option['voice_asset_id']
        if continuation and owner.start() + offset >= continuation[-1].end():
            row['continues_previous'] = True
        local_prefix = prefix[(spans[-1]['end'] if spans else 0):]
        phase = list(re.finditer(r'发声阶段（(画内|画外)(?:，(口型同步|无说话口型|双唇静止))?）', local_prefix))
        local_continue = '接续本句，' in local_prefix
        visibility = ('onscreen' if phase[-1][1] == '画内' else 'offscreen') if phase else ('onscreen' if scope == 'onscreen' else 'offscreen')
        phase_data = {'visibility': visibility}
        if phase and phase[-1][2]:
            phase_data['mouth_mode'] = {'口型同步': 'sync_speech', '无说话口型': 'non_speaking', '双唇静止': 'closed_still'}[phase[-1][2]]
        if phase:
            transition = re.search(r'转换：([^；\n]+)', local_prefix[phase[-1].end():])
            if transition:
                phase_data['transition'] = transition[1]
        if local_continue:
            if not rows or rows[-1]['speaker_key'] != row['speaker_key']:
                raise ValueError('镜内续说缺少同一说话人的前段')
            parent = rows[-1]
            start = len(parent['line'])
            parent.setdefault('delivery_spans', [{'range': [0, start], 'visibility': 'onscreen' if parent['scope'] == 'onscreen' else 'offscreen'}])
            parent['line'] += row['line']
            phase_source = scope if scope in ('telephone', 'narration') else 'diegetic'
            if phase_source != parent.get('source_type', 'diegetic'):
                phase_data['source_type'] = phase_source
            parent['delivery_spans'].append({'range': [start, len(parent['line'])], **phase_data})
            row['uid'] = parent['uid']
            row['parent_continuation'] = True
        else:
            if phase:
                row['delivery_spans'] = [{'range': [0, len(row['line'])], **phase_data}]
                row['source_type'] = scope if scope in ('telephone', 'narration') else 'diegetic'
            rows.append(row)
        span_start = owner.start() + offset
        if phase:
            span_start = (spans[-1]['end'] if spans else 0) + phase[-1].start()
        spans.append({'start': span_start, 'end': match.end(), 'dialogue': copy.deepcopy(row)})
    return rows, spans


def restore_dialogue_ids(shot, ids):
    if len(ids) != len(shot.get('dialogues', [])):
        raise ValueError('对白身份数量与当前原句不一致')
    mapping = {row['uid']: uid for row, uid in zip(shot['dialogues'], ids)}
    for row in shot['dialogues']:
        row['uid'] = mapping[row['uid']]
    for span in shot.get('dialogue_spans', []):
        span['dialogue']['uid'] = mapping.get(span['dialogue']['uid'], span['dialogue']['uid'])


def inspect_shot_speech(body, uid, options, *, confirmed_empty=False):
    issues = []
    try:
        rows, spans = parse_dialogues(body, uid, options)
    except ValueError as exc:
        rows, spans = [], []
        issues.append(str(exc))
    if len(re.findall(r'<d\b[^>]*>', body, re.I)) != len(spans) or len(re.findall(r'</d\s*>', body, re.I)) != len(spans):
        issues.append('对白标签不完整或说话人无法确认')
    if any(not row['line'] for row in rows):
        issues.append('对白原文为空')
    if body.count('发声阶段（') != sum(len(row.get('delivery_spans', [])) for row in rows):
        issues.append('发声阶段标记无法完整解析，请保留原稿并重新确认阶段')
    explicit = has_silence_declaration(body)
    continued = bool(_CONTINUATION.search(body))
    state = ('unverified' if issues else 'tagged' if rows else
             'explicit_silent' if (confirmed_empty or explicit) and not continued else
             'unverified' if continued else 'silent')
    if state == 'unverified' and not issues:
        issues.append('未读取到规范对白，也没有明确的全镜无对白依据，请结合全文修正')
    rule_issues = (['无台词 Shot 尚未明确写出本镜无对白，请按 H3 规则补充声音与口型说明']
                   if state == 'silent' else [])
    return {'dialogues': rows, 'dialogue_spans': spans, 'state': state, 'issues': issues,
            'rule_issues': rule_issues}


def inspect_prompt(prompt, uid, manifest=None):
    try:
        document = parse_prompt(prompt, uid, manifest)
    except (ValueError, TypeError) as exc:
        return {'document': None, 'verified': False, 'issues': [str(exc)]}
    issues = [f'Shot {i}: {message}' for i, shot in enumerate(document['shots'], 1)
              for message in shot.get('speech_issues', [])]
    return {'document': document, 'verified': not issues, 'issues': issues}


def dialogue_changed(shot):
    if not shot.get('dialogues') and not shot.get('dialogue_spans'):
        return False
    if any(row.get('delivery_spans') for row in shot.get('dialogues', [])):
        original, _ = parse_dialogues(str(shot['body']), shot['uid'], copy.deepcopy(shot.get('speaker_options', [])))
        return shot['dialogues'] != original
    return 'dialogues' in shot and shot['dialogues'] != [s['dialogue'] for s in shot.get('dialogue_spans', [])]


def dialogue_units(shot, *, tagged=False):
    result = []
    options = {o['key']: o for o in shot.get('speaker_options', [])}
    rows = shot.get('dialogues', [])
    if not isinstance(rows, list):
        raise ValueError('Shot 对白必须为列表')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get('uid') or row['uid'] in seen:
            raise ValueError('对白身份缺失或重复，请重新读取当前稿')
        seen.add(row['uid'])
        option = options.get(row.get('speaker_key'))
        if not option:
            raise ValueError('说话人绑定已失效，请重新选择说话人')
        scope = row.get('scope', 'onscreen')
        if scope not in ('onscreen', 'offscreen', 'telephone', 'narration'):
            raise ValueError('对白发声方式无效')
        line = str(row.get('line') or '').strip()
        if not line:
            raise ValueError('台词为空，请使用“删除台词”明确移除对白')
        if re.search(r'</?d\b|^\s*\[(?:Chinese|English|中文|英文)\]', line, re.I):
            raise ValueError('台词只填写原文，不包含对白标签')
        result.append({'speaker': option['tag'] if tagged else option['speaker'], 'speaker_name': option['label'],
                       'scope': scope, 'line': line, 'dialogue_uid': row['uid'], 'shot_uid': shot['uid']})
        preserve_delivery(row, result[-1])
        validate_delivery(result[-1])
        if row.get('continues_previous'):
            result[-1]['continues_previous'] = True
    return result


def _body_with_dialogues(shot, *, mark_silent=True):
    if not dialogue_changed(shot):
        return str(shot['body'])
    units = dialogue_units(shot)
    options = {o['key']: o for o in shot['speaker_options']}
    suffixes = {'onscreen': '', 'offscreen': '在画外', 'telephone': '在画外通过电话',
                'narration': '以画外旁白'}
    rendered = {row['uid']: (render_delivery(unit, options[row['speaker_key']]['tag']) if unit.get('delivery_spans') else
                options[row['speaker_key']]['tag'] + suffixes[unit['scope']] +
                '说：<d>[Chinese]' + unit['line'] + '</d>') for row, unit in zip(shot['dialogues'], units)}
    body = str(shot['body'])
    replacements = []
    for span in shot.get('dialogue_spans', []):
        replacements.append((span['start'], span['end'], rendered.pop(span['dialogue']['uid'], '')))
    for start, end, value in reversed(replacements):
        body = body[:start] + value + body[end:]
    if rendered:
        body = body.rstrip('；; ') + '；' + '；'.join(rendered.values())
    if mark_silent and not units and not _CONTINUATION.search(body):
        body = body.rstrip('；; ') + '；' + _SILENT
    elif units:
        body = body.replace(_SILENT, '')
    return body


def silent_body(shot):
    return _body_with_dialogues({**shot, 'dialogues': []}, mark_silent=False)


def centiseconds(value: Any) -> int:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Shot 时长必须为有限数值")
    return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def seconds_text(value: int) -> str:
    return f"{int(value) // 100}.{int(value) % 100:02d}"


def clock_text(value: int, original: str = '00:00.00') -> str:
    minutes, rest = divmod(int(value), 6000)
    seconds, fraction = divmod(rest, 100)
    if original.count(':') == 2:
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:02d}"
    return f"{minutes:02d}:{seconds:02d}.{fraction:02d}"


def read_visible_roles(body, options):
    """Read authored cast fields by tag, role id or name, without guessing from plot."""
    import json
    from .dialogue_text import transform_outside_dialogue
    # Dialogue contents must never be mistaken for editor metadata.
    parts=[]
    transform_outside_dialogue(body,lambda text:parts.append(text) or text)
    text='\n'.join(parts)
    structured=re.search(r'(?m)(?:^|[；;])\s*visible_roles\s*[:：]\s*(\[[^\]\n]*\])',text)
    cast=re.search(r'(?:画面人物|入画人物)\s*[:：]\s*([^；;\n]+)',text)
    if structured:
        try: values=json.loads(structured[1])
        except ValueError as exc:
            tags=re.findall(r'<Subject\s+\d+>',structured[1])
            rest=re.sub(r'<Subject\s+\d+>','',structured[1]).strip('[] ,、\t')
            if not tags or rest: raise ValueError('visible_roles 请使用人物ID或姓名的JSON数组') from exc
            values=tags
        if any(not isinstance(value,str) for value in values):
            raise ValueError('visible_roles 数组只能包含人物ID、姓名或Subject编号')
    elif cast:
        value=cast[1].strip().rstrip('。.')
        if value in ('无','无人','无人物','[]'): return []
        values=[v.strip().strip('"\'') for v in re.split(r'[、，,]',value) if v.strip()]
    else:
        # A direct camera subject or an explicitly onscreen line is authored
        # visibility, unlike a prose mention, gaze target or offscreen speaker.
        tag=r'<\s*Subject\s+(\d+)\s*>'
        direct=re.finditer(r'(?:^|[；;。\n])\s*(?:(?:镜头|声音)\s*[:：]\s*)?'+tag+
                           r'\s*(?:(?:正面|侧面|背面)\s*)?(?:极近特写|大特写|特写|中近景|近景|中景|全景|画内对白)(?=[，,。；;\s]|$)',text)
        values=list(dict.fromkeys('<Subject '+m[1]+'>' for m in direct))
        if not values: return None
    result=[]
    for value in values:
        value=value.strip()
        matches={o['speaker'] for o in options if value in (o['tag'],o['speaker'],o['label'])}
        if not matches and re.fullmatch(r'<Subject\s+\d+>',value) and not options:
            matches={value}
        if len(matches)!=1:
            raise ValueError('入画人物无法唯一对应当前参考：'+value)
        role=next(iter(matches))
        if role not in result: result.append(role)
    return result


def parse_prompt(prompt: str, uid: str, manifest: list | None = None) -> dict:
    text = str(prompt or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    matches = list(SECTION.finditer(text))
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({'title': match[1], 'body': text[match.end():end].strip()})
    detail = next((s for s in sections if s['title'] == '详细镜头描述'), None)
    if detail is None:
        raise ValueError('提示词缺少【详细镜头描述】，无法读取 Shot')
    headers = list(_SHOT.finditer(detail['body']))
    if not headers or len(headers) != len(re.findall(r'(?im)^\s*\[Shot\s+\d+\]', detail['body'])):
        raise ValueError('Shot 时间无法解析，请使用 [Shot 1]（0.00–2.10 秒）格式')
    options = speaker_options(manifest)
    shots, previous = [], 0
    for i, header in enumerate(headers):
        start = centiseconds(_seconds(header['start']))
        end = centiseconds(_seconds(header['end']))
        if start != previous or end <= start:
            raise ValueError(f'Shot {i + 1} 时间必须连续且时长大于 0')
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(detail['body'])
        body = detail['body'][header.end():body_end].lstrip(' ：:').strip()
        shot_uid = 'shot_' + hashlib.sha256(f'{uid}:{i}:{body}'.encode()).hexdigest()[:24]
        lines = re.findall(r'<d>\s*(?:\[Chinese\])?(.*?)</d>', body, re.S | re.I)
        local_options = copy.deepcopy(options)
        speech = inspect_shot_speech(body, shot_uid, local_options)
        dialogues, spans = speech['dialogues'], speech['dialogue_spans']
        shots.append({'uid': shot_uid, 'origin_uid': uid, 'body': body,
                      'dialogues': dialogues, 'dialogue_spans': spans, 'speaker_options': local_options,
                      'speech_state': speech['state'], 'speech_issues': speech['issues'],
                      'speech_rule_issues': speech['rule_issues'],
                      'original_start_cs': start, 'original_end_cs': end,
                      'duration_cs': end - start, 'summary': '；'.join(lines) or body[:100],
                      'reference_manifest': copy.deepcopy(manifest or [])})
        cast = read_visible_roles(body,options)
        if cast is not None:
            shots[-1]['visible_roles'] = cast
        # Resolve only the compiler's explicit scene tag, never prose mentions.
        scene_tag = re.search(r'场景使用\s*(<Subject\s+\d+>)', body)
        if scene_tag:
            from .asset_binder import H3AssetBinder
            from .prompt_compiler import H3PromptCompiler
            refs = H3PromptCompiler()._subject_reference_entries(H3AssetBinder().bind({'asset_manifest': manifest or []}))
            scene = next((r['metadata'].get('scene_id') for r in refs
                          if r['subject_tag'] == scene_tag[1] and r['asset_kind'] == 'background'), None)
            if scene:
                shots[-1]['scene_id'] = scene
        if not scene_tag and not any((a.get('metadata') or {}).get('asset_kind') == 'background' for a in manifest or []):
            shots[-1]['scene_id'] = ''
        elif not scene_tag:
            # A unique explicitly bound background supplies the scene default.
            # Multiple backgrounds still require an authored per-shot selection.
            backgrounds=[a for a in manifest or [] if a.get('type')=='image' and
                         (a.get('metadata') or {}).get('asset_kind')=='background']
            if len(backgrounds)==1 and (backgrounds[0].get('metadata') or {}).get('scene_id'):
                shots[-1]['scene_id']=backgrounds[0]['metadata']['scene_id']
        previous = end
    return {'uid': uid, 'sections': sections, 'preamble': detail['body'][:headers[0].start()].strip(),
            'shots': shots, 'structure_changed': False}


def timeline(document: dict) -> list[dict]:
    result, start = [], 0
    for shot in document.get('shots') or []:
        duration = shot.get('duration_cs')
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            raise ValueError('每个 Shot 时长须为大于 0 的整数百分秒')
        result.append({**shot, 'start_cs': start, 'end_cs': start + duration})
        start += duration
    return result


def duration_errors(document: dict, *, draft=False) -> list[str]:
    try:
        shots = timeline(document)
    except ValueError as exc:
        return [str(exc)]
    if not shots:
        return ['本段为空，请撤销或删除空段']
    total = shots[-1]['end_cs']
    if draft and total > 0:
        return []
    if total > 1500:
        return [f'超出 {seconds_text(total - 1500)} 秒，请转移 Shot 或调整时长']
    if total < 400:
        return [f'不足 4 秒，还需 {seconds_text(400 - total)} 秒']
    if total % 100:
        return [f'合计 {seconds_text(total)} 秒，需明确补齐 {seconds_text(100 - total % 100)} 秒或手调到整数']
    return []


def _map_time(value: int, shot: dict, all_shots: list[dict]) -> int:
    candidates = [s for s in all_shots if s.get('origin_uid') == shot.get('origin_uid')
                  and s['original_start_cs'] <= value <= s['original_end_cs']]
    if not candidates:
        raise ValueError(f'正文时间 {seconds_text(value)} 秒对应的 Shot 已移除，请调整该时间约束')
    # At a shared boundary prefer the local Shot, otherwise the preceding Shot.
    target = next((s for s in candidates if s['uid'] == shot['uid']), candidates[0])
    old_duration = target['original_end_cs'] - target['original_start_cs']
    if old_duration <= 0:
        raise ValueError('原 Shot 时间区间无效')
    offset = Decimal(value - target['original_start_cs']) * target['duration_cs'] / old_duration
    return target['start_cs'] + int(offset.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _body_with_times(shot: dict, all_shots: list[dict]) -> str:
    body = _body_with_dialogues(shot)
    replacements = []
    for slot in _time_slots(body):
        old = centiseconds(slot['seconds'])
        # Only explicit timestamps/ranges in the original timeline are events.
        # A standalone “保持 2 秒” is a duration, not an absolute timestamp.
        is_range = any(m.start() <= slot['start'] < m.end() for m in re.finditer(
            r'\d+(?:\.\d+)?\s*[-–—~至]\s*\d+(?:\.\d+)?\s*秒', body))
        if ':' not in slot['original'] and not is_range:
            continue
        new = _map_time(old, shot, all_shots)
        value = clock_text(new, slot['original']) if ':' in slot['original'] else seconds_text(new)
        replacements.append((slot['start'], slot['end'], value))
    for start, end, value in reversed(replacements):
        body = body[:start] + value + body[end:]
    editable_spans = {(s['start'], s['end']) for s in _time_slots(body)}
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*[-–—~至]\s*(\d+(?:\.\d+)?)\s*秒', body):
        if match.span(1) in editable_spans and match.span(2) in editable_spans and Decimal(match[1]) >= Decimal(match[2]):
            raise ValueError('镜内时间区间在调整后塌缩或倒序，请增加时长或编辑约束')
    return body


def render_prompt(document: dict, remap=None) -> str:
    shots = timeline(document)
    if not shots:
        return ''
    total = shots[-1]['end_cs']
    changed_dialogue = any(dialogue_changed(s) for s in shots)
    lines = []
    for i, shot in enumerate(shots, 1):
        body = _body_with_times(shot, shots)
        if remap:
            body = remap(body, shot.get('reference_manifest') or [])
        at = '' if i == 1 else f' At {clock_text(shot["start_cs"])}'
        lines.append(f'[Shot {i}]{at}（{seconds_text(shot["start_cs"])}–{seconds_text(shot["end_cs"])} 秒）：{body}')
    sections = copy.deepcopy(document['sections'])
    for section in sections:
        if section['title'] == '详细镜头描述':
            section['body'] = '\n'.join(filter(None, [document.get('preamble', ''), *lines]))
        elif section['title'] == '任务概述':
            if document.get('structure_changed') or changed_dialogue:
                section['body'] = f'[reference generation + audio reference] {seconds_text(total)} 秒。按详细镜头描述的顺序执行。'
            else:
                section['body'] = re.sub(r'(?<![\d.])\d+(?:\.\d+)?\s*秒', seconds_text(total) + ' 秒', section['body'], count=1)
        elif section['title'] == '整体声音环境' and (document.get('structure_changed') or changed_dialogue):
            section['body'] = '按保留镜头中明确描述的对白、语气、动作声及环境声执行。'
    return '\n\n'.join(f'【{s["title"]}】\n{s["body"]}' for s in sections if s['body'].strip())
