"""Let the LLM retime an existing H3 prompt without rewriting its content."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from services.context import CONFIG


_NUMBER = r"\d+(?:\.\d+)?"
_CLOCK = r"(?:\d{1,2}:){1,2}\d{2}(?:\.\d{1,3})?"
_UNIT = r"(?:秒|s(?:ec(?:ond)?s?)?\b)"
_RANGE = re.compile(rf"(?<![\d.:])(?P<start>{_NUMBER})\s*(?:{_UNIT}\s*)?[-–—~至]\s*(?P<end>{_NUMBER})\s*{_UNIT}", re.I)
_SHOT = re.compile(
    rf"^\s*\[Shot\s+\d+\]\s*(?:At\s+(?P<at>{_CLOCK})\s*)?"
    rf"[（(]\s*(?P<start>{_CLOCK}|{_NUMBER})\s*(?:{_UNIT}\s*)?"
    rf"[-–—~至]\s*(?P<end>{_CLOCK}|{_NUMBER})\s*{_UNIT}\s*[）)]", re.I | re.M,
)
_SYSTEM = """
同一句的镜内发声阶段只预算一次，保留阶段顺序、入画事件、说话人及声音渠道。不要把镜内续说改成跨镜续说。
你是 H3 现有视频提示词的时长规划器。本次只重新规划时间，不重新创作或重写提示词。
prompt_with_time_slots 是用户当前编辑区完整底稿，包含未保存修改；其中 ⟦time_001⟧ 等标记代表可调整的时间数值。
time_slots 提供这些标记的原数值和秒数。人物、台词、动作、情绪、场景、镜头数量与顺序、素材编号、音色归属和其他文字全部保持不变。
逐个 Shot 判断是否有正在发生的言语，包括画内对白、画外音、电话声音、旁白和跨镜续说；不能仅因画面主体没有开口，或本镜没有重复写台词，就判断为无台词。
有台词的 Shot，以原台词自然说完所需时间为主要预算，包含当前语气下必要的重音、换气和简短停顿。表情、情绪表现、姿势、视线、简短手势和镜头变化作为辅助，与发声并行，不逐项额外加时，不为辅助表演拉长开口前准备或说完后的留白。
无台词的 Shot，按原稿必要动作、表情变化、事件发展及明确叙事节拍自然完成所需时间估算；静态外观和场景描述不逐项占时，不凭描述篇幅或情绪标签延长时间，也不新增台词填时间。音乐、环境音和音效不算台词，其素材长度不决定镜头长度。
只有原稿明确要求先后发生、无法与发声并行的必要事件才单独占时，并在时长依据中说明；不改变或删去原稿动作，不增加动作、空镜、停顿或台词填时间，不加速硬塞台词。
多人顺序接话按实际顺序计时；明确重叠的发声按重叠关系估算，不自行制造抢话。同一句跨 Shot 续说只计一次，将其连续发声时间分配到覆盖的镜头，不逐镜重算整句。
先估计各 Shot，再汇总分段时长并统一确定 4–15 秒整数总长；不逐镜向上取整或追加保险余量，不预设目标秒数，也不以最短或最长为优化目标。
duration_reason 说明各 Shot 的估时依据；区分提示词中的计划时间、成片总时长与实际逐句测量，不把历史样本的计划时间当作当前场景的固定时长或语速。
reference_duration_sec 仅是页面参考值，可以缩短或延长。在 4–15 秒中选择一个整数作为本段总时长。
为所有 time_slots 返回新秒数，时间戳也统一用秒数返回。相同时间点的所有引用必须同步；任务概述总秒数等于 duration_sec。
原有 Shot 从 0 连续覆盖新总时长，At 时间等于对应 Shot 起点；镜内台词区间和动作时间属于对应 Shot，跨镜续说不重复。
只输出 JSON：{"duration_sec": 11, "duration_reason": "简短说明估时依据", "timings": {"time_001": 11, "time_002": 0}}。
timings 必须恰好包含输入的所有时间标记，值为非负秒数，精度最多毫秒；不要返回改写的提示词或其他字段。
若保留全部台词和必要事件无法在 15 秒内自然完成，返回 {"status":"needs_replan","reason":"具体原因，需要拆段"}，不能删改内容或自动拆段。
""".strip()


def _call_model(system_prompt: str, user_prompt: str, **kwargs: Any):
    from services.llm import call_llm

    return call_llm(system_prompt, user_prompt, **kwargs)


def _seconds(value: str) -> float:
    result = 0.0
    for part in value.split(":"):
        result = result * 60 + float(part)
    return result


def _time_slots(text: str) -> list[dict[str, Any]]:
    # Dialogue, quoted screen text, and reference descriptions are immutable.
    protected = [(m.start(), m.end()) for m in re.finditer(
        r'<d>.*?</d>|“[^”]*”|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"'
        r'|【(?:主体定义|参考保留关系|人物音色绑定)】.*?(?=\n【|\Z)'
        r'|(?:必须准确显示的文字|exact_text)\s*[:：][^\r\n]*', text, re.S | re.I,
    )]
    spans = set()
    for match in re.finditer(rf"(?<![\d:]){_CLOCK}(?![\d:])", text):
        spans.add(match.span())
    for match in _RANGE.finditer(text):
        spans.update((match.span("start"), match.span("end")))
    for match in re.finditer(rf"(?<![\d.:]){_NUMBER}(?=\s*{_UNIT})", text, re.I):
        spans.add(match.span())
    result = []
    for start, end in sorted(spans, key=lambda span: (span[0], -span[1])):
        if any(start < stop and end > begin for begin, stop in protected):
            continue
        if result and start < result[-1]["end"]:
            continue
        original = text[start:end]
        result.append({"id": f"time_{len(result) + 1:03d}", "start": start, "end": end,
                       "original": original, "seconds": _seconds(original)})
    return result


def _format_time(value: float, original: str) -> str:
    if ":" not in original:
        precision = max(len(original.partition(".")[2]), 3 if not value.is_integer() else 0)
        return f"{value:.{precision}f}"
    milliseconds = int(round(value * 1000))
    minutes, remainder = divmod(milliseconds, 60000)
    seconds, fraction = divmod(remainder, 1000)
    prefix = f"{minutes:02d}:"
    if original.count(":") == 2:
        hours, minutes = divmod(minutes, 60)
        prefix = f"{hours:02d}:{minutes:02d}:"
    return prefix + f"{seconds:02d}" + (f".{fraction:03d}" if "." in original or fraction else "")


def _validate_timeline(text: str, duration: int) -> None:
    shots = list(_SHOT.finditer(text))
    headers = list(re.finditer(r"^\s*\[Shot\s+\d+\]", text, re.M | re.I))
    if len(shots) != len(headers):
        raise ValueError("当前 Shot 时间格式无法校验，请使用 [Shot 1] (0-3 秒) 格式")
    end = 0.0
    for shot in shots:
        start, stop = _seconds(shot["start"]), _seconds(shot["end"])
        if abs(start - end) > 1e-6 or stop <= start or stop > duration:
            raise ValueError("重新规划的 Shot 时间须从 0 连续覆盖本段时长，不能重叠或越界")
        if shot["at"] and abs(_seconds(shot["at"]) - start) > 1e-6:
            raise ValueError("重新规划的 At 时间与 Shot 起点不一致")
        end = stop
    if shots and abs(end - duration) > 1e-6:
        raise ValueError("重新规划的最后一个 Shot 必须结束于本段总时长")


def retime_h3_prompt(editor_prompt: str, segment_index: int, *, reference_duration_sec: int,
                     asset_manifest: list[dict[str, Any]] | None = None, draft_mode=False) -> dict[str, Any]:
    text = str(editor_prompt or "")
    if not text.strip():
        raise ValueError("编辑区提示词为空，无法重新规划时长")
    slots = _time_slots(text)
    if not slots:
        raise ValueError("当前提示词没有可调整的时间描述，请先提供 Shot 秒数或时间戳")
    marked = text
    for slot in reversed(slots):
        marked = marked[:slot["start"]] + "⟦" + slot["id"] + "⟧" + marked[slot["end"]:]
    from services.llm_payloads import reference_context
    payload = {"prompt_with_time_slots": marked, "time_slots": [
        {key: slot[key] for key in ("id", "original", "seconds")} for slot in slots
    ], "reference_duration_sec": reference_duration_sec, "asset_manifest": reference_context(asset_manifest)}
    from services.video_experience import retrieve, PLANNING_RULES as EXPERIENCE_RULES
    from services.speech_timing import task_planning_context, PLANNING_RULES
    from services.logger import log
    try:
        experiences = retrieve(asset_manifest or [], prompt=text, purpose='timing')
    except Exception as exc:
        log(f'[video_learning] 经验库暂不可用，本次按原提示词规划：{exc}', 'WARN')
        experiences = []
    payload['speech_timing_context'] = task_planning_context(asset_manifest or [], prompt=text)
    system = _SYSTEM
    system += '\n每段 H3 总时长必须为 4–15 秒整数，Shot 内部可用小数秒；本次为单段重新规划，放不下时报告需要拆段。'
    system += '\n\n' + PLANNING_RULES
    if experiences:
        payload['approved_prompt_experiences'] = experiences
        system += '\n\n' + EXPERIENCE_RULES
        system += ('\n本次经验仅辅助原稿的时间分配，不授权改写声画内容。'
                   '即使历史案例的声音衔接不同，也不得增加、删除或改写当前镜头、动作、对白及音色绑定；'
                   '只返回原有 time_slots 的新秒数。')
    raw, _ = _call_model(system, json.dumps(payload, ensure_ascii=False),
                         max_tokens=int(CONFIG.get("h3_prompt_regenerator_max_tokens", 3500) or 3500),
                         label=f"h3_prompt_retime_{int(segment_index):03d}")
    if not isinstance(raw, dict):
        raise ValueError("时长规划结果必须为 JSON 对象")
    if raw.get("status") == "needs_replan":
        raise ValueError("当前提示词需要拆段：" + str(raw.get("reason") or "无法在 15 秒内自然完成"))
    try:
        duration = float(raw["duration_sec"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("时长规划缺少合法的 duration_sec") from exc
    if (isinstance(raw["duration_sec"], bool) or not math.isfinite(duration) or duration <= 0
            or (not draft_mode and (not 4 <= duration <= 15 or not duration.is_integer()))):
        raise ValueError("H3 重新规划的总时长必须为 4–15 秒整数")
    reason = str(raw.get("duration_reason") or "").strip()
    if not reason:
        raise ValueError("时长规划缺少时长依据")
    timings = raw.get("timings")
    if not isinstance(timings, dict) or set(timings) != {slot["id"] for slot in slots}:
        raise ValueError("时长规划必须返回全部原有时间标记，不能增加或遗漏")
    for slot in slots:
        value = timings[slot["id"]]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("时长规划的时间数值无效")
        if not 0 <= value <= duration or abs(value - round(value, 3)) > 1e-6:
            raise ValueError("时长规划的时间数值越界或精度超过毫秒")
    boundaries = {match.span(key) for match in _SHOT.finditer(text) for key in ("start", "end", "at")
                  if match[key] is not None}
    absolute_times = {}
    by_span = {(slot['start'], slot['end']): slot for slot in slots}
    for slot in slots:
        if ':' in slot['original'] or (slot['start'], slot['end']) in boundaries:
            previous = absolute_times.setdefault(slot['seconds'], timings[slot['id']])
            if abs(previous - timings[slot['id']]) > 1e-6:
                raise ValueError("同一原始时间点在 Shot、At 和正文中的新时间必须一致")
    clock_ranges = re.finditer(rf"(?P<start>{_CLOCK})\s*[-–—~至]\s*(?P<end>{_CLOCK})", text)
    for match in [*_RANGE.finditer(text), *clock_ranges]:
        start, stop = by_span.get(match.span('start')), by_span.get(match.span('end'))
        if start and stop and timings[start['id']] >= timings[stop['id']]:
            raise ValueError("重新规划的时间区间必须先开始后结束")
    original_shots = list(_SHOT.finditer(text))
    for index, shot in enumerate(original_shots):
        body_end = original_shots[index + 1].start() if index + 1 < len(original_shots) else len(text)
        begin_slot, end_slot = by_span.get(shot.span('start')), by_span.get(shot.span('end'))
        if not begin_slot or not end_slot:
            continue
        for slot in slots:
            if (':' in slot['original'] and shot.end() <= slot['start'] < body_end
                    and _seconds(shot['start']) <= slot['seconds'] <= _seconds(shot['end'])
                    and not timings[begin_slot['id']] <= timings[slot['id']] <= timings[end_slot['id']]):
                raise ValueError("镜内动作或对白的时间不能超出所属 Shot；原有跨镜续说保留跨镜安排")
    summary = re.search(r"【任务概述】.*?(?=\n【|\Z)", text, re.S)
    total_slots = [slot for slot in slots if summary and summary.start() <= slot["start"] < summary.end()][:1]
    total_slots.extend(slot for slot in slots if re.search(
        r"(?:^|\n)\s*(?:总时长|本段时长|视频时长|目标时长|时长)\s*[:：=]\s*$", text[:slot["start"]]))
    if any(timings[slot["id"]] != duration for slot in total_slots):
        raise ValueError("任务概述的总秒数与重新规划时长不一致")
    updated = text
    for slot in reversed(slots):
        updated = updated[:slot["start"]] + _format_time(float(timings[slot["id"]]), slot["original"]) + updated[slot["end"]:]
    _validate_timeline(updated, duration)
    return {"prompt": updated, "duration_sec": int(duration) if duration.is_integer() else duration, "duration_reason": reason}


def validate_timing_edit(before,after):
    def mask(text):
        for slot in reversed(_time_slots(text)):
            text=text[:slot['start']]+'<time>'+text[slot['end']:]
        return text
    if mask(before)!=mask(after):raise ValueError('时长操作修改了非时间正文')
