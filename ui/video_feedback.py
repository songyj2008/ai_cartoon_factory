"""Video issue feedback loop for improving deterministic quality gates."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import gradio as gr

from services.context import BASE_DIR, get_project_dir
from services.llm import call_llm
from services.logger import get_logs, log

CUSTOM_RULES_PATH = BASE_DIR / "assets" / "custom_quality_rules.json"
FEEDBACK_ANALYSIS_FILE = "video_feedback_analysis.json"

ISSUE_TYPES = [
    "人物错乱 / 身份漂移",
    "说话人变了 / 口型归属错误",
    "多出人物 / 背景人物变主体",
    "人物与背景不搭 / 空间接触错误",
    "角色从画外进入 / 第一帧不稳定",
    "人物比例异常 / 肢体变形",
    "动作过渡太多 / 剧情不稳定",
]


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _video_jobs() -> dict[str, Any]:
    return _load_json(get_project_dir() / "video_jobs.json", {}) if get_project_dir() else {}


def video_part_choices() -> list[tuple[str, str]]:
    data = _video_jobs()
    choices: list[tuple[str, str]] = []
    for item in data.get("segments") or []:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("segment_index") or 0)
        part_id = str(item.get("part_id") or f"part_{idx:03d}")
        title = str(item.get("title") or f"Segment {idx}").strip()
        job = item.get("job") if isinstance(item.get("job"), dict) else {}
        duration = job.get("duration_sec") or "?"
        label = f"{part_id}｜{title}｜{duration}s"
        choices.append((label, part_id))
    return choices


def refresh_video_feedback_parts():
    return gr.update(choices=video_part_choices())


def _selected_segments(selected_parts: list[str] | str | None) -> list[dict[str, Any]]:
    selected = selected_parts or []
    if isinstance(selected, str):
        selected = [selected]
    selected_set = {str(x) for x in selected if str(x).strip()}
    data = _video_jobs()
    segments = []
    for item in data.get("segments") or []:
        if not isinstance(item, dict):
            continue
        part_id = str(item.get("part_id") or "")
        if part_id in selected_set:
            segments.append(item)
    return segments


def _feedback_payload(segments: list[dict[str, Any]], issue_types: list[str] | str | None, issue_note: str) -> dict[str, Any]:
    issues = issue_types or []
    if isinstance(issues, str):
        issues = [issues]
    compact_segments = []
    for item in segments:
        job = item.get("job") if isinstance(item.get("job"), dict) else {}
        compact_segments.append(
            {
                "part_id": item.get("part_id"),
                "title": item.get("title"),
                "scene_id": item.get("scene_id"),
                "visible_roles": item.get("visible_roles") or [],
                "reference_roles": item.get("reference_roles") or [],
                "offscreen_speakers": item.get("offscreen_speakers") or [],
                "mentioned_roles": item.get("mentioned_roles") or [],
                "prompt": job.get("prompt") or "",
            }
        )
    return {"issue_types": issues, "issue_note": str(issue_note or "").strip(), "segments": compact_segments}


def analyze_video_feedback(selected_parts, issue_types, issue_note):
    segments = _selected_segments(selected_parts)
    if not segments:
        log("[video_feedback][error] 请先选择至少一个视频 part", "ERROR")
        return "", get_logs()
    if not issue_types and not str(issue_note or "").strip():
        log("[video_feedback][error] 请至少选择一个问题类型或填写问题描述", "ERROR")
        return "", get_logs()

    payload = _feedback_payload(segments, issue_types, issue_note)
    system_prompt = """
你是通用 AI 视频生成质检分析器。
任务：根据用户反馈的视频问题、对应 part 的视频提示词和角色/背景绑定信息，分析问题可能来自哪些提示词表达。
要求：
- 仅分析本次反馈，区分原因假设与已知事实，不推广为全局运行规则。
- 只输出 JSON object。
- candidate_rules 固定返回空数组；仅提供当前提示词的局部改写建议。
- reason 是给用户看的风险原因。
输出格式：
{
  "summary": "一句话概括问题根因",
  "causes": ["原因1", "原因2"],
  "rewrite_advice": ["提示词改写建议1"],
  "candidate_rules": []
}
"""
    user_prompt = "视频问题反馈和对应分段信息：\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        data, _ = call_llm(system_prompt, user_prompt, max_tokens=3000, label="video_feedback")
    except Exception as exc:
        log(f"[video_feedback][error] LLM 分析失败: {exc}", "ERROR")
        return "", get_logs()

    if not isinstance(data, dict):
        data = {"summary": "LLM 返回格式异常", "raw": data, "candidate_rules": []}
    _save_json(get_project_dir() / FEEDBACK_ANALYSIS_FILE, data)
    log("[video_feedback] 已生成问题归因和候选质检规则", "STEP")
    return json.dumps(data, ensure_ascii=False, indent=2), get_logs()


def _valid_rule(rule: dict[str, Any]) -> dict[str, str] | None:
    regex = str(rule.get("regex") or "").strip()
    reason = str(rule.get("reason") or "").strip()
    category = str(rule.get("category") or "custom").strip() or "custom"
    if not regex or not reason:
        return None
    try:
        re.compile(regex)
    except re.error:
        return None
    return {"regex": regex, "reason": reason, "category": category, "enabled": True}


def apply_video_feedback_rules(analysis_text: str):
    log("[video_feedback] 单次反馈不再写入全局质检规则，请使用局部提示词修正", "WARN")
    return get_logs()
