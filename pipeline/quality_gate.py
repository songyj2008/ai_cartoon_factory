"""Deterministic story/beat quality gate for AI-video generation stability."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from services.context import BASE_DIR
from generation.model_rules import QualityRule, rules_for_model


CUSTOM_RULES_PATH = BASE_DIR / "assets" / "custom_quality_rules.json"

ANNOTATION_RE = re.compile(r"^\s*[🔴●○◉•]?\s*【风险\d+】.*$", re.M)
TAG_RE = re.compile(r"❌([^❌]+)❌")


@dataclass(frozen=True)
class QualityIssue:
    scope: str
    index: int
    phrase: str
    reason: str
    model_id: str = ""
    rules_revision: str = ""
    rule_id: str = ""
    severity: str = "error"
    blocking: bool = True


def strip_quality_annotations(text: str) -> str:
    """Remove UI-only quality annotations before saving edited story/beats."""
    value = str(text or "")
    value = ANNOTATION_RE.sub("", value)
    value = TAG_RE.sub(r"\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _custom_risk_patterns(model_id: str, scope: str) -> list[QualityRule]:
    try:
        data = json.loads(CUSTOM_RULES_PATH.read_text(encoding="utf-8-sig")) if CUSTOM_RULES_PATH.exists() else []
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    patterns: list[QualityRule] = []
    for item in data:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        regex = str(item.get("regex") or "").strip()
        reason = str(item.get("reason") or "").strip()
        raw_models = item.get("models") or item.get("applies_to") or []
        raw_models = [raw_models] if isinstance(raw_models, str) else raw_models
        raw_scopes = item.get("scopes") or ("story", "beats")
        raw_scopes = [raw_scopes] if isinstance(raw_scopes, str) else raw_scopes
        models = [str(value or "").strip() for value in raw_models]
        scopes = tuple(str(value or "").strip() for value in raw_scopes)
        if models and model_id not in models:
            continue
        if scope and scope not in scopes:
            continue
        if not regex or not reason:
            continue
        try:
            re.compile(regex)
        except re.error:
            continue
        patterns.append(
            QualityRule(
                id=str(item.get("id") or f"custom_{len(patterns) + 1}"),
                regex=regex,
                reason=reason,
                scopes=scopes,
                severity=str(item.get("severity") or "error"),
                blocking=bool(item.get("blocking", True)),
            )
        )
    return patterns


def _all_risk_patterns(model_id: str, scope: str) -> tuple[object, list[QualityRule]]:
    rule_set = rules_for_model(model_id)
    owned = [rule for rule in rule_set.quality_rules if scope in rule.scopes]
    return rule_set, owned + _custom_risk_patterns(rule_set.model_id, scope)


def _scan_text(text: str, scope: str, model_id: str | None = None) -> list[QualityIssue]:
    source = strip_quality_annotations(text)
    issues: list[QualityIssue] = []
    rule_set, risk_rules = _all_risk_patterns(str(model_id or ""), "beats" if scope.startswith("beat") else scope)
    for rule in risk_rules:
        for match in re.finditer(rule.regex, source):
            phrase = match.group(0)
            issues.append(
                QualityIssue(
                    scope=scope,
                    index=len(issues) + 1,
                    phrase=phrase,
                    reason=rule.reason,
                    model_id=rule_set.model_id,
                    rules_revision=rule_set.revision,
                    rule_id=rule.id,
                    severity=rule.severity,
                    blocking=rule.blocking,
                )
            )
    return issues


def _mark_text(text: str, issues: list[QualityIssue]) -> str:
    """Return clean editable text; visual risk markers are rendered by the UI.

    Older versions injected risk notes and ❌ wrappers directly into the editor
    value. That made the warning text look like real content and competed with
    editing. Keep the textarea/CodeMirror content clean and let the browser draw
    line-gutter arrows from the persisted/scanned quality issues instead.
    """
    return strip_quality_annotations(text)


def validate_story_display(story_text: str, model_id: str | None = None) -> tuple[str, list[QualityIssue]]:
    issues = _scan_text(story_text, "story", model_id)
    return _mark_text(story_text, issues), issues


def validate_beats_display(beats_text: str, model_id: str | None = None) -> tuple[str, list[QualityIssue]]:
    clean = strip_quality_annotations(beats_text)
    issues = _scan_text(clean, "beats", model_id)
    return _mark_text(clean, issues), issues


def _story_text_from_json(text: str) -> str:
    try:
        data = json.loads(str(text or ""))
    except Exception:
        return str(text or "")
    if isinstance(data, dict):
        return str(data.get("story") or data.get("summary") or data.get("ending") or "")
    return str(text or "")


def validate_story_json_text(story_json_text: str, model_id: str | None = None) -> list[QualityIssue]:
    return _scan_text(_story_text_from_json(story_json_text), "story", model_id)


def validate_beats_data(beats_data: dict[str, Any] | None, model_id: str | None = None) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    if not isinstance(beats, list):
        return issues
    for beat_index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        title = str(beat.get("title") or f"Beat {beat_index}")
        plot = str(beat.get("plot") or "")
        scan_text = f"{title}\n{plot}"
        for issue in _scan_text(scan_text, f"beat_{beat_index}", model_id):
            issues.append(QualityIssue(scope=f"Beat {beat_index}《{title}》", index=len(issues) + 1, phrase=issue.phrase, reason=issue.reason, model_id=issue.model_id, rules_revision=issue.rules_revision, rule_id=issue.rule_id, severity=issue.severity, blocking=issue.blocking))
    return issues


def validate_generation_state(story_text: str = "", beats_data: dict[str, Any] | None = None, model_id: str | None = None) -> list[QualityIssue]:
    issues = validate_story_json_text(story_text, model_id) if story_text else []
    issues.extend(validate_beats_data(beats_data, model_id))
    return [QualityIssue(scope=item.scope, index=index, phrase=item.phrase, reason=item.reason, model_id=item.model_id, rules_revision=item.rules_revision, rule_id=item.rule_id, severity=item.severity, blocking=item.blocking) for index, item in enumerate(issues, start=1)]


def format_quality_block_message(issues: list[QualityIssue]) -> str:
    if not issues:
        return ""
    blocking = [issue for issue in issues if issue.blocking]
    model_label = rules_for_model(issues[0].model_id).label if issues and issues[0].model_id else "当前模型"
    lines = [f"[quality_gate][blocked] {model_label} 规则检测到 {len(issues)} 处问题，其中 {len(blocking)} 处阻断后续生成。"]
    for idx, issue in enumerate(issues, start=1):
        lines.append(f"  {idx}. {issue.scope}: {issue.phrase} -> {issue.reason}")
    lines.append("请在页面红色标记处修改并保存，确认无风险后再继续生成。")
    return "\n".join(lines)


def quality_issues_payload(issues: list[QualityIssue]) -> dict[str, Any]:
    """Serializable quality gate state for UI restore and manual review."""
    return {
        "blocked": bool(issues),
        "issues": [
            {
                "scope": issue.scope,
                "index": index,
                "phrase": issue.phrase,
                "reason": issue.reason,
                "model_id": issue.model_id,
                "rules_revision": issue.rules_revision,
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "blocking": issue.blocking,
            }
            for index, issue in enumerate(issues, start=1)
        ],
    }


def assert_quality_gate_clear(story_text: str = "", beats_data: dict[str, Any] | None = None, model_id: str | None = None) -> None:
    issues = validate_generation_state(story_text=story_text, beats_data=beats_data, model_id=model_id)
    blocking = [issue for issue in issues if issue.blocking]
    if blocking:
        raise ValueError(format_quality_block_message(issues))
