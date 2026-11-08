"""Editable H3 results. Diagnostics never rewrite creative content."""
from __future__ import annotations

import copy
import math
from typing import Mapping


def positive_duration(value):
    if isinstance(value, bool):
        raise ValueError('预计时长必须为正数')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('预计时长必须为有限正数')
    return int(value) if value.is_integer() else value


def issue(code, message, *, blocking=False):
    return {'code': code, 'message': str(message), 'blocking': blocking}


def inspect_plan(response, reference, expected, *, authoritative=False, speaker_aliases=None):
    from .contracts import H3_REF2VA_MODEL_ID
    from .prompt_planner import _validated_plan, _assert_current_dialogue_preserved, validate_h3_plan_dialogue_records
    raw = response
    if isinstance(raw, Mapping) and isinstance(raw.get('model_hints'), Mapping):
        raw = raw['model_hints']
    if isinstance(raw, Mapping) and isinstance(raw.get(H3_REF2VA_MODEL_ID), Mapping):
        raw = raw[H3_REF2VA_MODEL_ID]
    problems = []
    raw = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    try:
        duration = positive_duration(raw.get('duration_sec', reference))
    except (ValueError, TypeError, OverflowError) as exc:
        duration = reference
        problems.append(issue('invalid_duration', exc, blocking=True))
    if not 4 <= duration <= 15 or duration != int(duration):
        problems.append(issue('duration_out_of_range', f'预计 {duration:g} 秒，视频提交需为 4–15 秒整数；请调整 Shot 或重新规划时长', blocking=True))
    shots = raw.get('shots')
    if not isinstance(shots, list) or not shots or any(not isinstance(s, Mapping) for s in shots):
        problems.append(issue('invalid_structure', raw.get('reason') or raw.get('_parse_error') or '模型回复没有有效 Shots，原始回复已保留，请重新生成 Segment', blocking=True))
    else:
        try:
            _validated_plan(raw, duration)
        except (ValueError, TypeError) as exc:
            problems.append(issue('invalid_timeline', exc, blocking=True))
    plan = {**raw, 'shots': [copy.deepcopy(dict(s)) for s in shots or [] if isinstance(s, Mapping)] if isinstance(shots, list) else [],
            'duration_sec': duration, 'duration_reason': str(raw.get('duration_reason') or ''),
            'planning_raw_response': copy.deepcopy(response.get('planning_raw_response', response) if isinstance(response, Mapping) else response)}
    # Resolve only explicitly assigned references. Missing/duplicate assignments
    # stay missing/duplicate and are reported; never guess a shot for a line.
    if any('dialogue_refs' in s for s in plan['shots']):
        assigned = []
        for shot in plan['shots']:
            refs = shot.pop('dialogue_refs', [])
            shot['source_dialogue_refs'] = copy.deepcopy(refs)
            if not isinstance(refs, list):
                problems.append(issue('invalid_dialogue_refs', '台词序号必须是数组'))
                continue
            if shot.get('dialogue') or shot.get('dialogues'):
                problems.append(issue('mixed_dialogue', '同时返回台词序号和正文，保留模型正文供检查'))
                continue
            values = []
            for raw_ref in refs:
                ref = raw_ref.get("number") if isinstance(raw_ref, dict) else raw_ref
                if type(ref) is int and 1 <= ref <= len(expected):
                    assigned.append(ref)
                    from .speech_contract import preserve_delivery
                    values.append(preserve_delivery(raw_ref if isinstance(raw_ref, dict) else {}, copy.deepcopy(expected[ref - 1])))
                else:
                    problems.append(issue('invalid_dialogue_refs', f'无效台词序号：{ref}'))
            if values:
                shot['dialogues'] = values
        missing = [n for n in range(1, len(expected) + 1) if n not in assigned]
        duplicates = sorted({n for n in assigned if assigned.count(n) > 1})
        if missing:
            problems.append(issue('missing_dialogue', '缺少台词：' + '；'.join(f'第 {n} 句「{expected[n-1].get("line") or expected[n-1].get("text") or ""}」' for n in missing)))
        if duplicates:
            problems.append(issue('duplicate_dialogue', '重复台词序号：' + '、'.join(map(str, duplicates))))
        if assigned != sorted(assigned):
            problems.append(issue('dialogue_order', '台词顺序与 Beat 不一致'))
    try:
        _assert_current_dialogue_preserved({'dialogue_units': expected, 'dialogue_authoritative': authoritative}, plan, speaker_aliases=speaker_aliases)
    except (ValueError, TypeError) as exc:
        problems.append(issue('dialogue_mismatch', exc, blocking=True))
    problems.extend(issue('invalid_dialogue_record', message, blocking=True)
                    for message in validate_h3_plan_dialogue_records(plan))
    plan['planning_issues'] = problems
    plan['planning_state'] = 'invalid' if not plan['shots'] else 'warning' if problems else 'ready'
    return plan


def draft_segment(beat, index, previous, config, plan, prompt='', extra_issues=()):
    """Keep the former usable result while publishing a failed retry as a candidate."""
    problems = [*(plan.get('planning_issues') or []), *extra_issues]
    state = 'invalid' if not prompt or plan.get('planning_state') == 'invalid' else 'warning' if problems else 'ready'
    result = copy.deepcopy(dict(previous or {}))
    result.pop('h3_force_replan', None)
    if state == 'invalid' and ((result.get('job') or {}).get('final_prompt') or (result.get('job') or {}).get('prompt')):
        result.setdefault('generation_config', {}).setdefault('prompt_override',
            result['job'].get('final_prompt') or result['job'].get('prompt'))
        result['planning_candidate'] = {'plan': plan, 'issues': problems}
        result['planning_issues'] = [*(result.get('planning_issues') or []),
            issue('retry_failed', '本次重试未产生有效分镜，已保留原提示词；详情见原始规划回复')]
        result['planning_state'] = 'warning'
        return result
    result.pop('planning_candidate', None)
    old_prompt = str((result.get('job') or {}).get('final_prompt') or (result.get('job') or {}).get('prompt') or '')
    if old_prompt and old_prompt != prompt:
        result['planning_previous'] = {'prompt': old_prompt,
            'duration_sec': (result.get('job') or {}).get('duration_sec'),
            'generation_config': copy.deepcopy(result.get('generation_config') or {})}
    config = copy.deepcopy(config)
    if prompt:
        config['prompt_override'] = prompt
    result.update(segment_index=index, segment_id=f'segment_{index:03d}', part_id=f'part_{index:03d}',
                  beat_uid=beat.get('beat_uid'), title=beat.get('title') or f'Segment {index}',
                  model_id='minimax_h3_local_ref2va', generation_mode='ref2va', generation_config=config,
                  h3_prompt_plan=plan, planning_state=state, planning_issues=problems,
                  planning_raw_response=plan.get('planning_raw_response'), workflow_path='',
                  compiled_prompt_snapshot=prompt, prompt_saved=True, submitted=False,
                  source_excerpt=beat.get('source_excerpt') or '', scene_id=beat.get('scene_id') or '',
                  scene_contexts=beat.get('scene_contexts', []), binding_schema_version=beat.get('binding_schema_version'),
                  visible_roles=beat.get('visible_roles') or [], reference_roles=beat.get('reference_roles') or [],
                  status='needs_regenerate', job={'duration_sec': config['duration_sec'], 'prompt': prompt,
                                                  'final_prompt': prompt, 'asset_manifest': config.get('asset_manifest') or []})
    return result


def refresh_saved_prompt(segment, prompt):
    """Re-evaluate submit readiness after an explicit user edit or retime."""
    from .contracts import H3GenerationConfig
    from .validator import H3Ref2VAValidator
    from .prompt_retimer import _validate_timeline
    duration = (segment.get('generation_config') or {}).get('duration_sec') or (segment.get('job') or {}).get('duration_sec') or 0
    problems = []
    report = H3Ref2VAValidator().validate(H3GenerationConfig.from_mapping(segment.get('generation_config') or {}), duration_sec=duration)
    config = H3GenerationConfig.from_mapping({**(segment.get('generation_config') or {}), 'prompt_override': prompt})
    H3Ref2VAValidator().validate_binding_contract(segment, config, report=report)
    problems.extend(issue(e.code, e.message, blocking=True) for e in report.errors)
    try:
        _validate_timeline(prompt, float(duration))
    except (ValueError, TypeError) as exc:
        problems.append(issue('invalid_timeline', exc, blocking=True))
    if not prompt.strip():
        problems.append(issue('invalid_structure', '提示词为空，请重新生成 Segment', blocking=True))
    segment['planning_state'] = 'invalid' if not prompt.strip() else 'warning' if problems else 'ready'
    segment['planning_issues'] = problems
    segment.pop('planning_candidate', None)
    segment.pop('h3_prompt_plan', None)
