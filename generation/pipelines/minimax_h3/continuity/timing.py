"""Carry evidenced scene records across strictly timing-only screenplay edits."""
from __future__ import annotations

import copy


def _quote_after_retime(quote, before, after):
    from ..prompt_retimer import _time_slots, validate_timing_edit
    if not isinstance(quote, str) or not quote or before.count(quote) != 1:
        if isinstance(quote, str) and quote in before and quote in after:
            return quote
        raise ValueError('时间调整后的状态引用无法唯一对应')
    validate_timing_edit(before, after)
    start = before.index(quote)
    stop = start + len(quote)
    edits = []
    old_slots, new_slots = _time_slots(before), _time_slots(after)
    for old, new in zip(old_slots, new_slots):
        if old['start'] < stop and old['end'] > start:
            if old['start'] < start or old['end'] > stop:
                raise ValueError('状态引用只覆盖了部分时间数值')
            edits.append((old['start'] - start, old['end'] - start,
                          after[new['start']:new['end']]))
    for begin, end, replacement in reversed(edits):
        quote = quote[:begin] + replacement + quote[end:]
    if quote not in after:
        raise ValueError('时间调整后的状态引用不存在')
    return quote


def advance_timing_record(segment, *, before=None):
    """Update provenance only when an exact source proves a timing-only change.

    Never trust a provenance label, matching Shot count or text_pending alone.
    Old unanchored records still require state analysis. No input/end facts,
    selected video, historical snapshot or workflow is changed here.
    """
    # Framing is independent of the ending-state record. In particular an
    # inherit declaration has shots=[], but its frozen framing can still be
    # carried across a strictly proved clock-only edit.
    if segment.get('h3_scene_framing'):
        from .framing import advance_framing_timing_record
        advance_framing_timing_record(segment, before=before)
    from ..editor_draft import snapshot
    from ..prompt_retimer import validate_timing_edit
    from ..shot_editor import parse_prompt
    from .ledger import validate_record
    from .revisions import revision
    from .state import binding_hash

    record = segment.get('h3_continuity') or {}
    if record.get('schema_version') != 3 or record.get('state_pending'):
        return False
    current = snapshot(segment)
    original = before
    if original is None or revision(original) != record.get('source_revision'):
        text = record.get('source_prompt')
        if not isinstance(text, str) or not text.strip():
            return False
        original = copy.deepcopy(segment)
        original['generation_config'] = {**original.get('generation_config', {}), 'prompt_override': text}
    if revision(original) != record.get('source_revision'):
        return False
    old = snapshot(original)
    if (old.get('scene_context_id') != current.get('scene_context_id')
            or binding_hash(old['asset_manifest']) != binding_hash(current['asset_manifest'])):
        return False
    try:
        validate_timing_edit(old['prompt'], current['prompt'])
        validate_record(record, original)
        validate_record(record, segment)
        old_doc = parse_prompt(old['prompt'], segment['segment_uid'], old['asset_manifest'])
        new_doc = parse_prompt(current['prompt'], segment['segment_uid'], current['asset_manifest'])
        updated = copy.deepcopy(record)
        bodies = {}
        for saved, a, b in zip(updated['shots'], old_doc['shots'], new_doc['shots']):
            bodies[saved['shot_uid']] = (a['body'], b['body'])
            for event in saved['world_events']:
                if 'source_quote' in event:
                    event['source_quote'] = _quote_after_retime(event['source_quote'], a['body'], b['body'])
        for requirement in updated.get('opening_requirements', []):
            a, b = bodies[requirement['shot_uid']]
            requirement['source_quote'] = _quote_after_retime(requirement['source_quote'], a, b)
    except (ValueError, KeyError, TypeError):
        return False
    updated.update(source_revision=revision(segment), source_prompt=current['prompt'], text_pending=False)
    segment['h3_continuity'] = updated
    return True
