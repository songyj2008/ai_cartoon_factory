"""Compare authored plans with a requested budget without changing any content."""
import math


def budget_report(data, target_duration_sec=None, target_beat_count=None):
    beats = data.get('beats') or []
    target_count = int(target_beat_count or data.get('target_beat_count') or 0)
    target_seconds = float(target_duration_sec or data.get('target_duration_sec') or 0)
    seconds = sum(float(b.get('estimated_duration_sec') or b.get('duration_sec') or 0) for b in beats)
    count_margin = max(1, math.ceil(target_count * .2))
    problems = []
    if target_count and abs(len(beats) - target_count) > count_margin:
        problems.append(f'目标 {target_count} 段，实际 {len(beats)} 段（差 {len(beats)-target_count:+d} 段），参考范围 {max(1, target_count-count_margin)}–{target_count+count_margin} 段')
    if target_seconds > 0 and abs(seconds - target_seconds) > target_seconds * .2 + 1e-6:
        problems.append(f'目标 {target_seconds:g} 秒，预计 {seconds:g} 秒（差 {seconds-target_seconds:+g} 秒），参考范围 {target_seconds*.8:g}–{target_seconds*1.2:g} 秒')
    return {'target_beat_count': target_count, 'actual_beat_count': len(beats),
            'target_duration_sec': target_seconds, 'estimated_duration_sec': seconds,
            'exceeds_reference': bool((target_count and len(beats) > target_count + count_margin)
                                      or (target_seconds > 0 and seconds > target_seconds * 1.2 + 1e-6)),
            'outside_budget': bool(problems), 'message': '；'.join(problems)}

