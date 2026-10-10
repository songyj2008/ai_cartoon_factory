"""Video delivery channels, independent from the per-segment generation model.

New channels live in a sibling module exporting ``channel``.  The registry
discovers them without a central website switch statement.
"""
from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any


def all_channels() -> dict[str, Any]:
    result = {}
    catalog = Path(__file__).with_name("registry.json")
    configured = json.loads(catalog.read_text(encoding="utf-8")) if catalog.is_file() else []
    module_names = list(dict.fromkeys([*configured, *(f"{__name__}.{item.name}" for item in pkgutil.iter_modules(__path__))]))
    for module_name in module_names:
        if module_name.rsplit(".", 1)[-1].startswith("_"):
            continue
        module = importlib.import_module(module_name)
        channel = getattr(module, "channel", None)
        if channel is None:
            continue
        if channel.id in result:
            raise ValueError(f"duplicate video channel: {channel.id}")
        result[channel.id] = channel
    return result


def get_channel(channel_id: str):
    try:
        return all_channels()[channel_id]
    except KeyError as exc:
        raise ValueError(f"unknown video channel: {channel_id}") from exc


def options_for(segment: dict, payload: dict) -> list[dict]:
    options = []
    for channel in all_channels().values():
        reason = channel.unavailable_reason(segment, payload)
        options.append({"id": channel.id, "label": channel.label, "available": not bool(reason),
                        "reason": reason or "", "uses_workflow": bool(channel.uses_workflow)})
    return options
