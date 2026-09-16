"""Sanitize and validate LiconMSR director prompts."""
from __future__ import annotations

import re
from typing import Any


TRANSITION_RISK_RE = re.compile(
    r"走进|进入|走到|走过来|迎上来|推门|门口|离开|走出|起身|落座|坐下|站起|来到"
)
TIMELINE_FORMAT_RE = re.compile(r"\b\d+\s*[-–—~至到]\s*\d+\s*秒\b")
FAILED_DIRECTOR_WORDS_RE = re.compile(
    r"当前说话人|说话权|Lip Sync|lip sync|闭口|保持闭口|不要说话|动嘴|嘴巴必须|"
    r"Speaker ownership|Current speaker|Close mouth|Keep mouth closed|"
    r"必须|不得|不允许|只允许|禁止|严格等于|不得清晰入画"
)
QUOTE_LINE_RE = re.compile(r"^\s*[“\"『].+[”\"』]\s*$")
BARE_DIALOGUE_LINE_RE = QUOTE_LINE_RE
CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _names_text(names: list[str]) -> str:
    clean = [_text(name) for name in names if _text(name)]
    if not clean:
        return "当前人物"
    return "和".join(clean) if len(clean) <= 2 else "、".join(clean)


def strip_formatting(text: str) -> str:
    prompt = _text(text)
    prompt = re.sub(r"```(?:text|json|markdown)?", "", prompt, flags=re.I).replace("```", "")
    prompt = re.sub(r"^\s*【\s*导演分镜\s*】\s*", "", prompt)
    prompt = re.sub(r"\n{3,}", "\n\n", prompt).strip()
    return prompt


def _expected_dialogue_lines(prompt_ir: dict[str, Any]) -> list[tuple[str, str]]:
    events = prompt_ir.get("events") or {}
    expected: list[tuple[str, str]] = []
    for item in events.get("dialogue_units") or []:
        line = _text(item.get("line"))
        if line:
            expected.append((_text(item.get("speaker_name") or item.get("speaker")), line))
    for item in events.get("event_flow") or []:
        line = _text(item.get("dialogue"))
        if line and all(line != existing for _speaker, existing in expected):
            expected.append((_text(item.get("actor_name") or item.get("actor")), line))
    return expected


def _missing_dialogue_lines(prompt: str, prompt_ir: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for speaker, line in _expected_dialogue_lines(prompt_ir):
        if line and line not in prompt:
            missing.append(f"{speaker}: {line}" if speaker else line)
    return missing


def _ensure_natural_setup(prompt: str, prompt_ir: dict[str, Any]) -> str:
    chars = prompt_ir.get("characters") or {}
    scene = prompt_ir.get("scene") or {}
    duration = int(prompt_ir.get("duration_sec") or 15)
    visible_names = [str(x) for x in chars.get("visible_names") or [] if str(x).strip()]
    names = _names_text(visible_names)
    scene_name = _text(scene.get("scene_name"))
    single_person = len(visible_names) == 1
    additions: list[str] = []

    if visible_names and not all(name in prompt for name in visible_names):
        additions.append(f"镜头开场聚焦在{names}身上。")
    if scene_name and scene_name not in prompt:
        additions.append(f"场景位于{scene_name}。")
    if "一镜到底" not in prompt and "无剪切" not in prompt and "没有切换" not in prompt:
        additions.append(f"整段镜头保持连续{duration}秒的自然观察。")
    if visible_names and "第一帧" not in prompt and "开场" not in prompt and "镜头开始" not in prompt:
        if single_person:
            additions.append(f"镜头开始时，{names}已经处在画面里的稳定位置。")
        else:
            additions.append(f"镜头开始时，{names}已经处在各自的位置，空间关系稳定。")

    if not additions:
        return prompt
    return "\n".join(additions + [prompt]).strip()


def sanitize_director_prompt(text: str, prompt_ir: dict[str, Any]) -> str:
    """Clean formatting, preserve natural prompt style, and reject missing dialogue."""
    prompt = strip_formatting(text)
    missing_dialogue = _missing_dialogue_lines(prompt, prompt_ir)
    if missing_dialogue:
        raise ValueError("compiled prompt missing dialogue lines: " + "; ".join(missing_dialogue))
    prompt = _ensure_natural_setup(prompt, prompt_ir)
    return re.sub(r"\n{3,}", "\n\n", prompt).strip()


def _has_bare_dialogue_lines(prompt: str) -> bool:
    return any(BARE_DIALOGUE_LINE_RE.match(line.strip()) for line in prompt.splitlines() if line.strip())


def _has_consecutive_quote_lines(prompt: str) -> bool:
    previous_quote = False
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_quote = bool(QUOTE_LINE_RE.match(stripped))
        if is_quote and previous_quote:
            return True
        previous_quote = is_quote
    return False


def _long_dialogue_lines(prompt: str, limit: int = 35) -> list[str]:
    lines: list[str] = []
    for match in re.finditer(r"[“\"『](.+?)[”\"』]", prompt):
        line = _text(match.group(1))
        if len(CN_CHAR_RE.findall(line)) > limit:
            lines.append(line)
    return lines


def director_prompt_warnings(text: str, prompt_ir: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    prompt = _text(text)
    if TRANSITION_RISK_RE.search(prompt):
        warnings.append("contains transition risk words")
    if TIMELINE_FORMAT_RE.search(prompt):
        warnings.append("contains timeline dialogue format")
    if FAILED_DIRECTOR_WORDS_RE.search(prompt):
        warnings.append("contains programmatic control wording")
    if _has_bare_dialogue_lines(prompt):
        warnings.append("contains bare dialogue lines not embedded in speaker action")
    if _has_consecutive_quote_lines(prompt):
        warnings.append("contains consecutive dialogue lines without action")
    long_lines = _long_dialogue_lines(prompt)
    if long_lines:
        warnings.append(f"contains long dialogue lines: {len(long_lines)}")
    missing_dialogue = _missing_dialogue_lines(prompt, prompt_ir)
    if missing_dialogue:
        warnings.append(f"missing dialogue lines: {len(missing_dialogue)}")
    return warnings
