"""Small Gradio component adapters used by the application UI."""
from __future__ import annotations

from typing import Any

import gradio as gr

from gradio.events import Dependency

class NullSafeDropdown(gr.Dropdown):
    """Keep null handling when Gradio reconstructs a dropdown after updates."""

    def get_block_name(self):
        return "dropdown"

    def preprocess(self, payload: Any):
        if self.multiselect:
            if payload is None:
                payload = []
            elif isinstance(payload, list):
                payload = [value for value in payload if value is not None]
        return super().preprocess(payload)
    from typing import Callable, Literal, Sequence, Any, TYPE_CHECKING
    from gradio.blocks import Block
    if TYPE_CHECKING:
        from gradio.components import Timer
        from gradio.components.base import Component


def null_safe_multiselect_dropdown(*args, **kwargs) -> gr.Dropdown:
    """Create a strict multiselect dropdown that tolerates transient nulls.

    Gradio can briefly submit ``[None]`` when a dynamically refreshed
    multiselect has no selected option.  The stock preprocessor rejects that
    payload before the event handler can treat it as an empty selection.
    """
    kwargs["multiselect"] = True
    return NullSafeDropdown(*args, **kwargs)