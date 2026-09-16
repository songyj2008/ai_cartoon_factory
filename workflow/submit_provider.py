"""Shared submit-provider contracts for video workflow backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SubmitResult:
    backend: str
    submitted: bool = False
    prompt_id: str = ""
    task_id: str = ""
    video_path: str = ""
    output_urls: list[str] = field(default_factory=list)
    submit_result: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class BaseSubmitProvider(Protocol):
    name: str

    def submit(
        self,
        api: dict[str, Any],
        job: Any,
        workflow_path: str | None = None,
        wait: bool = True,
    ) -> SubmitResult:
        raise NotImplementedError
