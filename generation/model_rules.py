"""Extensible, model-owned creative and validation rules.

Runtime orchestration depends on this contract rather than model ids.  A new
model extends the system by registering a pipeline with its own ``rules``
object; existing model rule modules do not need to be edited.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol


@dataclass(frozen=True)
class QualityRule:
    id: str
    regex: str
    reason: str
    scopes: tuple[str, ...] = ("story", "beats")
    severity: str = "error"
    blocking: bool = True


@dataclass(frozen=True)
class BeatPlanningStrategy:
    """Optional model-owned defaults layered over hard duration limits."""

    recommended_min_duration_sec: int | None = None
    recommended_max_duration_sec: int | None = None
    default_count_resolver: Callable[[int], int | None] | None = None
    max_raw_duration_drift_ratio: float | None = None
    timeline_normalizer: Callable[[dict[str, Any], int, int], dict[str, Any]] | None = None

    def default_target_count(self, target_duration_sec: int) -> int | None:
        if not self.default_count_resolver:
            return None
        value = self.default_count_resolver(max(1, int(target_duration_sec or 1)))
        return max(1, int(value)) if value else None


@dataclass(frozen=True)
class ModelRuleSet:
    model_id: str
    revision: str
    label: str
    beat_min_duration_sec: int
    beat_max_duration_sec: int
    targeted_min_duration_sec: int
    max_visible_roles: int | None = None
    max_reference_roles: int | None = None
    accept_unversioned_beats: bool = False
    capability_summary: str = ""
    template_fallback_config_key: str = ""
    story_constraints: str = ""
    story_writer_profile_name: str = "story_writer_profile.md"
    beat_writer_prompt: str = ""
    beat_writer_profile_name: str = "beat_writer_profile.md"
    beat_structure_prompt: str = ""
    beat_writer_note: str = ""
    beat_splitter_prompt: str = ""
    beat_planning: BeatPlanningStrategy = field(default_factory=BeatPlanningStrategy)
    beat_risk_detector: Callable[[dict[str, Any], int | None], dict[str, Any]] | None = None
    quality_rules: tuple[QualityRule, ...] = field(default_factory=tuple)


class RuleOwnedPipeline(Protocol):
    rules: ModelRuleSet


def rules_for_model(model_id: str | None = None) -> ModelRuleSet:
    """Resolve rules through the model registry without branching on ids."""
    from generation.registry import default_model_id, get_model_registry

    registry = get_model_registry()
    selected = str(model_id or default_model_id()).strip()
    if not registry.has(selected):
        selected = default_model_id()
    pipeline = registry.get(selected)
    rules = getattr(pipeline, "rules", None)
    if not isinstance(rules, ModelRuleSet):
        raise TypeError(f"generation pipeline {selected} does not expose a ModelRuleSet")
    return rules


def quality_rules_for(model_id: str | None = None, scope: str = "") -> tuple[QualityRule, ...]:
    rules = rules_for_model(model_id).quality_rules
    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        return rules
    return tuple(rule for rule in rules if normalized_scope in rule.scopes)


def rules_for_beats_data(beats_data: dict | None) -> ModelRuleSet:
    """Resolve persisted Beat rules; unversioned legacy data uses registry default."""
    from generation.registry import default_model_id

    policy = beats_data.get("generation_policy") if isinstance(beats_data, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    return rules_for_model(str(policy.get("model_id") or default_model_id()).strip())


def validate_rule_regexes(rules: Iterable[QualityRule]) -> None:
    import re

    for rule in rules:
        re.compile(rule.regex)


def assert_beats_compatible(beats_data: dict, model_id: str | None = None) -> None:
    rules = rules_for_model(model_id)
    policy = beats_data.get("generation_policy") if isinstance(beats_data, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    source_model_id = str(policy.get("model_id") or "").strip()
    source_revision = str(policy.get("rules_revision") or "").strip()
    if not source_model_id and rules.accept_unversioned_beats:
        return
    if source_model_id != rules.model_id or source_revision != rules.revision:
        source = source_model_id or "未记录模型"
        raise ValueError(
            f"当前 Beats 由 {source} / {source_revision or '旧规则'} 生成，"
            f"与 {rules.label} / {rules.revision} 不匹配；请按当前模型重新拆分 Beats。"
        )
