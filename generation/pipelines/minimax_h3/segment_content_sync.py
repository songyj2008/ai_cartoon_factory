"""Synchronize one H3 screenplay without changing structure or render history."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .contracts import H3_REF2VA_MODEL_ID as MODEL
from .shot_editor import dialogue_units, inspect_prompt, silent_body


def _unique(values):
    return list(dict.fromkeys(str(v) for v in values if v))


def sync_segment_content(beat, segment, *, prompt, generation_config, inspection=None,
                         source_excerpt=None, plan=None):
    timing_source = copy.deepcopy(segment)
    beat, segment = copy.deepcopy(beat), copy.deepcopy(segment)
    if 'h3_editor_constraints' in segment:
        beat['h3_editor_constraints'] = copy.deepcopy(segment['h3_editor_constraints'])
    manifest = generation_config.get('asset_manifest') or []
    from .continuity.state import finalize_constraints
    segment['h3_editor_constraints']=finalize_constraints(segment.get('h3_editor_constraints'),prompt,prompt,manifest)
    beat['h3_editor_constraints']=copy.deepcopy(segment['h3_editor_constraints'])
    inspected = inspection or inspect_prompt(prompt, segment.get('segment_uid') or segment.get('part_id') or 'editor', manifest)
    if inspected['verified']:
        from .revision_review import compare_revision
        reference_check = compare_revision(prompt, prompt, manifest=manifest)
        binding_issues = [message for message in reference_check['blocking_issues']
                          if message.startswith('参考绑定不存在：')]
        if binding_issues:
            inspected = {**inspected, 'verified': False, 'issues': binding_issues}
    diagnostics = {'verified': inspected['verified'], 'issues': list(inspected['issues'])}
    segment.update(generation_config=copy.deepcopy(generation_config), h3_editor_prompt=prompt,
                   compiled_prompt_snapshot=prompt, prompt_saved=True, h3_content_sync=diagnostics)
    beat['plot'] = prompt
    if source_excerpt is not None:
        beat['source_excerpt'] = segment['source_excerpt'] = source_excerpt
    if not inspected['verified']:
        # Keep the last known structure, but never describe it as this draft's contract.
        beat['dialogue_authoritative'] = False
        return beat, segment, diagnostics
    document = inspected['document']
    shots = document['shots']
    saved = segment.get('h3_shot_editor') or {}
    if len(saved.get('shot_uids') or []) == len(shots):
        for shot, uid in zip(shots, saved['shot_uids']):
            shot['uid'] = uid
    record=segment.get('h3_continuity') or {}
    if record.get('record_kind')=='inherit':
        record['state_pending']=False
    elif record.get('schema_version')==3 and [s.get('shot_uid') for s in record.get('shots',[])]!=[s['uid'] for s in shots]:
        record['state_pending']=True
    units = [unit for shot in shots for unit in dialogue_units(shot)]
    from .continuity.context import resolve_shot_contexts
    resolve_shot_contexts(document,segment.get('scene_context_id'),saved.get('shot_context_ids'))
    beat.update(dialogue_units=units, dialogue_authoritative=True,
                dialogue_prompt_hash=hashlib.sha256(prompt.strip().encode()).hexdigest())
    hints = copy.deepcopy(beat.get('model_hints') or {})
    hints[MODEL] = {**hints.get(MODEL, {}), 'shots': [
        {'start_sec': s['original_start_cs'] / 100, 'end_sec': s['original_end_cs'] / 100,
         'shot_uid': s['uid'], 'dialogues': dialogue_units(s), 'description': silent_body(s), **{k: copy.deepcopy(s[k]) for k in ('visible_roles', 'scene_id', 'scene_context_id') if k in s}}
        for s in shots]}
    if plan is not None:
        segment['h3_prompt_plan']=copy.deepcopy(plan)
        hints[MODEL]=copy.deepcopy(plan)
    else:
        segment.pop('h3_prompt_plan',None)
    # A previous cached plan must not override newly saved dialogue.
    hints[MODEL].pop('planning_fingerprint', None)
    beat['model_hints'] = hints
    selected, images = [], {}
    for asset in manifest:
        meta = asset.get('metadata') or {}
        role = meta.get('role_id')
        if asset.get('type') == 'image' and role and meta.get('asset_kind') != 'background':
            selected.append(role)
            if meta.get('image_id'):
                images[role] = meta['image_id']
    visible = _unique([*(beat.get('visible_roles') or []),
                       *(u['speaker'] for u in units if u['scope'] == 'onscreen')])
    if all('visible_roles' in shot for shot in shots):
        visible = _unique(role for shot in shots for role in shot['visible_roles'])
    offscreen = _unique(u['speaker'] for u in units if u['scope'] != 'onscreen' or any(p.get('visibility') == 'offscreen' for p in u.get('delivery_spans', [])))
    beat.update(visible_roles=visible, offscreen_speakers=offscreen,
                important_roles=_unique([*(beat.get('important_roles') or []), *visible,
                                         *offscreen, *selected]))
    if selected or not any(a.get('type') == 'image' and (a.get('metadata') or {}).get('asset_kind') != 'background'
                           for a in manifest):
        beat.update(reference_roles=_unique(selected), reference_image_ids=images)
    beat.setdefault('reference_roles', [])
    beat.setdefault('reference_image_ids', {})
    for key in ('visible_roles', 'reference_roles', 'offscreen_speakers', 'important_roles', 'reference_image_ids'):
        segment[key] = copy.deepcopy(beat[key])
    scenes = [s.get('scene_id') for s in shots]
    if scenes and all(scenes):
        beat['scene_id'] = segment['scene_id'] = scenes[0] if len(set(scenes)) == 1 else ''
    segment['h3_shot_editor'] = {
        'schema_version': 2,
        'prompt_hash': hashlib.sha256(json.dumps(prompt, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest(),
        'shot_uids': [s['uid'] for s in shots],
        'shot_context_ids': {s['uid']:s['scene_context_id'] for s in shots if s.get('scene_context_id')},
        'dialogue_uids': {s['uid']: [r['uid'] for r in s['dialogues']] for s in shots},
    }
    # A timing-only edit does not change position/posture/presence. Advance
    # exact source provenance before the save reconciler marks text_pending.
    from .continuity.timing import advance_timing_record
    advance_timing_record(segment, before=timing_source)
    return beat, segment, diagnostics


def write_content_pair(root, *, beats, jobs):
    """Caller holds the runtime lock. Reuse the existing recoverable two-file journal."""
    from . import shot_edit_service as storage

    root = Path(root)
    from .continuity.revisions import reconcile_ledger_revisions
    reconcile_ledger_revisions(jobs)
    storage._recover(root)
    before = {name: json.loads((root / name).read_text(encoding='utf-8-sig'))
              for name in ('beats.json', 'video_jobs.json')}
    path = storage._journal(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'state': 'prepared', 'before': before}
    storage._replace_json(path, record)
    try:
        storage._replace_json(root / 'beats.json', beats)
        storage._replace_json(root / 'video_jobs.json', jobs)
        storage._replace_json(path, {**record, 'state': 'committed'})
    except Exception:
        storage._recover(root)
        raise
    path.unlink(missing_ok=True)
