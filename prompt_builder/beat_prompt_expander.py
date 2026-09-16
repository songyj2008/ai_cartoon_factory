"""LLM-based Beat -> LiconMSR cinematic prompt expansion.

The beat generator owns story structure. This module uses a compiler-style
PromptIR: code extracts scene, characters, dialogue, actions and hard rules;
the LLM only rewrites that explicit IR into natural director-shot prose.
"""
from __future__ import annotations

from typing import Any

from prompt_builder.director_prompt_guard import director_prompt_warnings, sanitize_director_prompt
from prompt_builder.licon_director_compiler import compile_ir_to_llm_prompt
from prompt_builder.profile_loader import load_ltx23_profile
from prompt_builder.prompt_ir import build_prompt_ir, fallback_director_prompt
from services.context import CONFIG
from services.llm import call_llm_text
from services.logger import log


def _text(value: Any) -> str:
    return str(value or "").strip()


def licon_prompt_expander_enabled() -> bool:
    return bool(CONFIG.get("licon_prompt_expander_enabled", True))


def expand_beat_to_licon_prompt(beat: dict[str, Any], base_plot: str, segment_index: int, duration_sec: int) -> str:
    """Expand a normalized beat into a LiconMSR director prompt via PromptIR."""
    beat_for_ir = dict(beat or {})
    if base_plot:
        beat_for_ir["plot"] = _text(base_plot)
    prompt_ir = build_prompt_ir(beat_for_ir, segment_index, int(duration_sec or 15))
    fallback = fallback_director_prompt(prompt_ir)

    if not licon_prompt_expander_enabled():
        return sanitize_director_prompt(fallback, prompt_ir)

    system_prompt = load_ltx23_profile()
    user_prompt = compile_ir_to_llm_prompt(prompt_ir)

    try:
        prompt, _cost = call_llm_text(
            system_prompt,
            user_prompt,
            max_tokens=int(CONFIG.get("licon_prompt_expander_max_tokens", 2600) or 2600),
            label=f"licon_prompt_compile_{segment_index:03d}",
        )
        prompt = sanitize_director_prompt(prompt, prompt_ir)
        if not prompt:
            raise ValueError("compiled prompt is empty")
        warnings = director_prompt_warnings(prompt, prompt_ir)
        for warning in warnings:
            log(f"[prompt_compile][warn] segment {segment_index}: {warning}", "WARN")
        log(f"[prompt_compile] segment {segment_index} chars={len(prompt)}", "STEP")
        return prompt
    except Exception as exc:
        log(f"[prompt_compile][warn] segment {segment_index} failed, fallback to deterministic prompt: {exc}", "WARN")
        return sanitize_director_prompt(fallback, prompt_ir)
