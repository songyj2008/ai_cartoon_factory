"""LTX prompt compilation boundary.

The established LTX profiles and guards remain the implementation source.
Keeping this boundary prevents later model compilers from reusing LTX prose
rules accidentally.
"""
from __future__ import annotations

from typing import Any

from prompt_builder.beat_to_video_prompt import compile_beats_to_video_jobs


class LtxPromptCompiler:
    def compile_jobs(self, beats_data: dict[str, Any]) -> list[dict[str, Any]]:
        return compile_beats_to_video_jobs(beats_data)
