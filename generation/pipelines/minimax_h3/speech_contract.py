"""Lossless utterance delivery: character ranges are Unicode, end exclusive."""
from __future__ import annotations

import copy
import re


FIELDS = ('delivery_spans', 'source_type', 'voice_asset_id', 'listeners', 'visible_roles')


def has_silence_declaration(text):
    """Read explicit whole-Shot declarations, including the handbook wording.

    This does not infer silence from absent dialogue, ambience or a listener.
    Spoken quotation text cannot declare the Shot's sound arrangement.
    """
    body = re.sub(r'<d>.*?</d>', '', str(text or ''), flags=re.I | re.S)
    return bool(re.search(
        r'(?:(?:本镜|本镜头|本\s*Shot)\s*|(?:^|[；;。，,\n]|声音[：:])\s*)'
        r'(?:无对白|没有对白|无台词|没有台词)(?=$|[，,；;。\s])', body, re.I))


def has_onscreen_speech(unit):
    """Use explicit delivery visibility before the utterance's default scope."""
    from .contracts import normalize_h3_scope
    spans = unit.get('delivery_spans')
    if spans is not None:
        validate_delivery(unit)
        return any(span['visibility'] == 'onscreen' and
                   (span.get('source_type') or unit.get('source_type') or 'diegetic') == 'diegetic'
                   for span in spans)
    return (normalize_h3_scope(unit.get('scope') or unit.get('speech_type')) == 'onscreen'
            and unit.get('source_type') in (None, 'diegetic'))


def preserve_delivery(source, target):
    for key in FIELDS:
        if key in source:
            target[key] = copy.deepcopy(source[key])
    return target


def validate_delivery(unit):
    if unit.get('source_type') not in (None, 'diegetic', 'telephone', 'narration', 'intercom', 'broadcast', 'outside_door'):
        raise ValueError('未知声音渠道')
    spans = unit.get('delivery_spans')
    if spans is None:
        return
    line = unit.get('line', '')
    if not isinstance(spans, list) or not spans:
        raise ValueError('发声阶段必须为非空数组')
    end = 0
    for span in spans:
        bounds = span.get('range') if isinstance(span, dict) else None
        if (not isinstance(bounds, list) or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or bounds[0] != end or not end < bounds[1] <= len(line)):
            raise ValueError('发声阶段必须按顺序完整覆盖原句，不能遗漏、重复或越界')
        if span.get('source_type') not in (None, 'diegetic', 'telephone', 'narration', 'intercom', 'broadcast', 'outside_door'):
            raise ValueError('未知阶段声音渠道')
        if span.get('visibility') not in ('onscreen', 'offscreen'):
            raise ValueError('发声阶段缺少明确的画内或画外状态')
        if span.get('mouth_mode') not in (None, 'sync_speech', 'non_speaking', 'closed_still'):
            raise ValueError('未知口型状态')
        end = bounds[1]
    if end != len(line):
        raise ValueError('发声阶段未覆盖完整原句；修改台词后请重新确认阶段')


def render_delivery(unit, owner):
    validate_delivery(unit)
    spans = unit.get('delivery_spans') or []
    result = []
    for index, span in enumerate(spans):
        source = span.get('source_type', unit.get('source_type', 'diegetic'))
        scope = source if source in ('telephone', 'narration') else span['visibility']
        delivery = {'telephone': '在画外通过电话', 'narration': '以画外旁白',
                    'offscreen': '在画外', 'onscreen': ''}[scope]
        prefix = '接续本句，' if index else ''
        label = '画内' if span['visibility'] == 'onscreen' else '画外'
        mode = {'sync_speech': '口型同步', 'non_speaking': '无说话口型', 'closed_still': '双唇静止'}.get(span.get('mouth_mode'), '')
        result.append(f'发声阶段（{label}' + ('，' + mode if mode else '') + '）')
        transition = str(span.get('transition') or '').strip()
        if transition:
            result.append('转换：' + transition)
        start, end = span['range']
        result.append(f'{prefix}{owner}{delivery}说：<d>[Chinese]{unit["line"][start:end]}</d>')
    for listener in unit.get('listeners', []):
        mode = '双唇闭合并保持静止' if listener.get('mouth_mode') == 'closed_still' else '不做说话口型，允许自然表情变化'
        result.append(str(listener['role_id']) + '在本句全部对白及停顿期间' + mode)
    return '；'.join(result)


def speech_signature(unit):
    return {key: copy.deepcopy(unit[key]) for key in
            ('speaker', 'scope', 'line', 'continues_previous', *FIELDS) if key in unit}
