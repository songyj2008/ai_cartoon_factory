"""Model-aware Beat duration policy.

Beats remain semantic story units.  This module supplies only the deterministic
duration envelope for the project default model; prompt wording and workflow
assembly stay inside their respective model pipelines.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeatPolicy:
    model_id: str
    min_duration_sec: int
    max_duration_sec: int
    targeted_min_duration_sec: int
    label: str
    writer_note: str = ""


def project_beat_policy(model_id: str | None = None) -> BeatPolicy:
    """Resolve model-owned duration rules through the open model registry."""
    try:
        from generation.model_rules import rules_for_model

        rules = rules_for_model(model_id)
        return BeatPolicy(
            model_id=rules.model_id,
            min_duration_sec=rules.beat_min_duration_sec,
            max_duration_sec=rules.beat_max_duration_sec,
            targeted_min_duration_sec=rules.targeted_min_duration_sec,
            label=rules.label,
            writer_note=rules.beat_writer_note,
        )
    except Exception:
        pass
    # Preserve the legacy LTX planning range if the registry is unavailable.
    return BeatPolicy(
        model_id="ltx23_licon_msr_v2",
        min_duration_sec=15,
        max_duration_sec=30,
        targeted_min_duration_sec=8,
        label="LTX 2.3 / LiconMSR",
    )


def beat_policy_prompt(model_id: str | None = None) -> str:
    policy = project_beat_policy(model_id)
    note = f"\n- {policy.writer_note}" if policy.writer_note else ""
    return (
        "\nModel duration policy:\n"
        f"- Project default model: {policy.label} ({policy.model_id}).\n"
        f"- Each Beat must target {policy.min_duration_sec}-{policy.max_duration_sec} seconds.\n"
        f"- Split at semantic event boundaries; do not create empty transition beats merely to meet duration."
        f"{note}\n"
    )
