"""Review revision structure and report differences without rewriting screenplay text."""
from __future__ import annotations

import re

from .speech_contract import speech_signature
from .shot_editor import dialogue_units, inspect_prompt
from .prompt_retimer import _validate_timeline


def compare_revision(base_prompt, revised_prompt, *, manifest=None, target_duration_sec=None,
                     fixed_constraints=None):
    before = inspect_prompt(base_prompt, 'baseline', manifest)
    after = inspect_prompt(revised_prompt, 'candidate', manifest)
    blocking = list(after['issues'])
    rule_issues = []
    if after['document'] is not None:
        rule_issues.extend(f'Shot {index}: {message}'
                        for index, shot in enumerate(after['document']['shots'], 1)
                        for message in shot.get('speech_rule_issues', []))
    warnings, changes = list(rule_issues), []
    if target_duration_sec is not None:
        try:
            _validate_timeline(revised_prompt, float(target_duration_sec))
        except (ValueError, TypeError) as exc:
            blocking.append(str(exc))
    if manifest is not None:
        from .asset_binder import H3AssetBinder
        from .prompt_compiler import H3PromptCompiler
        bindings = H3AssetBinder().bind({'asset_manifest': manifest})
        allowed = {tag.casefold() for tag, _kind, _asset in bindings.prompt_references}
        allowed.update(entry['subject_tag'].casefold()
                       for entry in H3PromptCompiler()._subject_reference_entries(bindings))
        for tag in set(re.findall(r'<(?:Subject|Picture|Video|Audio)\s+\d+>', revised_prompt, re.I)):
            if tag.casefold() not in allowed:
                blocking.append(f'参考绑定不存在：{tag}')
    if before['document'] is None:
        warnings.append('原稿结构未确认，仅核对修正版结构，不推断旧台词')
    elif after['document'] is not None:
        old, new = before['document']['shots'], after['document']['shots']
        if len(old) != len(new):
            changes.append({'field': 'shot_count', 'before': len(old), 'after': len(new)})
        for index, (a, b) in enumerate(zip(old, new), 1):
            def speech(shot):
                try:
                    return [speech_signature(unit) for unit in dialogue_units(shot)]
                except ValueError:
                    return None
            values = {
                'time_range': ([a['original_start_cs'], a['original_end_cs']],
                               [b['original_start_cs'], b['original_end_cs']]),
                'dialogues': (speech(a), speech(b)),
                'speech_state': tuple('silent' if s['speech_state'] == 'explicit_silent'
                                      else s['speech_state'] for s in (a, b)),
            }
            for field, (previous, current) in values.items():
                if previous != current:
                    changes.append({'shot': index, 'field': field, 'before': previous, 'after': current})
    fixed = fixed_constraints or {}
    for change in changes:
        if change['field'] in fixed.get('unchanged_fields', []):
            blocking.append(f"明确固定的字段发生变化：Shot {change.get('shot', '全部')} {change['field']}")
    if changes:
        warnings.append('修正版包含逐镜事实变化；差异已列出，不靠关键词推断反馈授权范围')
    return {'parsed': after['document'] is not None, 'blocking_issues': list(dict.fromkeys(blocking)),
            'warnings': warnings, 'rule_issues': rule_issues, 'changes': changes}
