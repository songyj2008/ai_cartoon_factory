"""Stable contracts shared by model-specific generation pipelines."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelCapabilities:
    """Deterministic constraints exposed by a generation model."""

    duration_min_sec: float = 1.0
    duration_max_sec: float | None = None
    default_fps: int | None = None
    fixed_fps: bool = False
    reference_image_min: int = 0
    reference_image_max: int | None = None
    reference_video_max: int | None = None
    reference_audio_max: int | None = None
    reference_file_max: int | None = None
    background_required: bool = False
    supports_image_references: bool = False
    supports_video_references: bool = False
    supports_audio_references: bool = False
    produces_audio: bool = False
    prompt_max_chars: int | None = None


@dataclass(frozen=True)
class ModelSpec:
    """A registered model and the facts required to select its pipeline."""

    id: str
    display_name: str
    pipeline_key: str
    revision: str = "1"
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    execution_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class GenerationIntent:
    """Model-neutral request to render one narrative segment."""

    segment_index: int
    segment_id: str
    part_id: str
    beat: dict[str, Any]
    duration_sec: float
    model_id: str
    model_override: str | None = None
    asset_hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaSpec:
    """Requested or measured media properties for a generated segment."""

    duration_sec: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool | None = None
    audio_sample_rate: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class PreparedJob:
    """A model-specific workflow that is ready for a submit backend."""

    model_id: str
    model_revision: str
    segment_index: int
    part_id: str
    workflow: dict[str, Any]
    prompt: str
    requested_media_spec: MediaSpec
    asset_slots: list[dict[str, Any]] = field(default_factory=list)
    workflow_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Normalized backend result stored independently from a vendor response."""

    model_id: str
    backend: str
    submitted: bool = False
    prompt_id: str = ""
    task_id: str = ""
    video_path: str = ""
    output_urls: list[str] = field(default_factory=list)
    actual_media_spec: MediaSpec = field(default_factory=MediaSpec)
    raw: dict[str, Any] = field(default_factory=dict)
