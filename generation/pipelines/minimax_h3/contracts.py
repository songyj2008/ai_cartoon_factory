"""Contracts owned by the local MiniMax H3 Ref2VA pipeline.

These types deliberately model H3 reference media independently from Licon's
``reference_images``/``background_image`` convention.  The values are also
safe to persist under a segment's ``generation_config`` field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


H3_REF2VA_GENERATION_MODE = "ref2va"
H3_REF2VA_MODEL_ID = "minimax_h3_local_ref2va"


class H3AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_asset_type(value: Any) -> H3AssetType:
    try:
        return H3AssetType(str(value or "").strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported H3 asset type: {value!r}") from exc


@dataclass(frozen=True)
class H3ReferenceAsset:
    """One H3 reference file and the role it should play in the prompt."""

    asset_id: str
    type: H3AssetType
    path: str
    role: str = ""
    label: str = ""
    order: int | None = None
    use_video_audio: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], fallback_index: int) -> "H3ReferenceAsset":
        if not isinstance(value, Mapping):
            raise TypeError("each H3 asset_manifest item must be an object")
        asset_type = normalize_asset_type(value.get("type"))
        metadata = value.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        asset_id = _optional_text(value.get("asset_id") or value.get("id")) or f"{asset_type.value}_{fallback_index}"
        path = str(value.get("path") or "").strip()
        use_video_audio = value.get("use_video_audio")
        if use_video_audio is None:
            use_video_audio = value.get("include_video_audio")
        if use_video_audio is None:
            # Do not guess that a video has a soundtrack.  A caller that has
            # probed it can opt in through metadata.has_audio.
            use_video_audio = bool(metadata.get("has_audio", False))
        return cls(
            asset_id=asset_id,
            type=asset_type,
            path=path,
            role=str(value.get("role") or "").strip(),
            label=str(value.get("label") or "").strip(),
            order=_optional_int(value.get("order")),
            use_video_audio=bool(use_video_audio),
            metadata=metadata,
        )

    @property
    def duration_sec(self) -> float | None:
        return _optional_float(self.metadata.get("duration_sec"))

    @property
    def width(self) -> int | None:
        return _optional_int(self.metadata.get("width"))

    @property
    def height(self) -> int | None:
        return _optional_int(self.metadata.get("height"))

    @property
    def size_bytes(self) -> int | None:
        return _optional_int(self.metadata.get("size_bytes"))


@dataclass(frozen=True)
class H3GenerationConfig:
    """Persistable per-segment H3 settings.

    Expected serialized shape::

        {
          "generation_mode": "ref2va",
          "asset_manifest": [{"type": "image", "path": "..."}],
          "prompt_override": "optional final H3 prompt"
        }
    """

    generation_mode: str = H3_REF2VA_GENERATION_MODE
    asset_manifest: tuple[H3ReferenceAsset, ...] = ()
    prompt_override: str | None = None
    duration_sec: float | None = None
    aspect_ratio: str | None = None
    megapixels: float | None = None
    resolution_multiple: int | None = None
    ref_image_size: str = "match"
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "H3GenerationConfig":
        data = dict(value or {}) if isinstance(value, Mapping) else {}
        items = data.get("asset_manifest")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise TypeError("generation_config.asset_manifest must be a list")
        assets = tuple(H3ReferenceAsset.from_mapping(item, index) for index, item in enumerate(items, start=1))
        known = {
            "generation_mode",
            "asset_manifest",
            "prompt_override",
            "duration_sec",
            "aspect_ratio",
            "megapixels",
            "resolution_multiple",
            "multiple",
            "ref_image_size",
            "seed",
        }
        prompt_override = _optional_text(data.get("prompt_override"))
        return cls(
            generation_mode=str(data.get("generation_mode") or H3_REF2VA_GENERATION_MODE).strip().lower(),
            asset_manifest=assets,
            prompt_override=prompt_override,
            duration_sec=_optional_float(data.get("duration_sec")),
            aspect_ratio=_optional_text(data.get("aspect_ratio")),
            megapixels=_optional_float(data.get("megapixels")),
            resolution_multiple=_optional_int(data.get("resolution_multiple", data.get("multiple"))),
            ref_image_size=str(data.get("ref_image_size") or "match").strip().lower(),
            seed=_optional_int(data.get("seed")),
            extra={key: item for key, item in data.items() if key not in known},
        )

    @classmethod
    def from_segment(cls, segment: Mapping[str, Any]) -> "H3GenerationConfig":
        config = segment.get("generation_config") if isinstance(segment, Mapping) else None
        return cls.from_mapping(config if isinstance(config, Mapping) else {})

    def assets_of_type(self, asset_type: H3AssetType) -> tuple[H3ReferenceAsset, ...]:
        indexed = [(index, item) for index, item in enumerate(self.asset_manifest) if item.type == asset_type]
        indexed.sort(key=lambda item: (item[1].order is None, item[1].order if item[1].order is not None else item[0], item[0]))
        return tuple(item for _, item in indexed)

    @property
    def images(self) -> tuple[H3ReferenceAsset, ...]:
        return self.assets_of_type(H3AssetType.IMAGE)

    @property
    def videos(self) -> tuple[H3ReferenceAsset, ...]:
        return self.assets_of_type(H3AssetType.VIDEO)

    @property
    def audios(self) -> tuple[H3ReferenceAsset, ...]:
        return self.assets_of_type(H3AssetType.AUDIO)

    def to_dict(self) -> dict[str, Any]:
        def asset_dict(asset: H3ReferenceAsset) -> dict[str, Any]:
            data: dict[str, Any] = {
                "asset_id": asset.asset_id,
                "type": asset.type.value,
                "path": asset.path,
            }
            if asset.role:
                data["role"] = asset.role
            if asset.label:
                data["label"] = asset.label
            if asset.order is not None:
                data["order"] = asset.order
            if asset.type is H3AssetType.VIDEO and asset.use_video_audio:
                data["use_video_audio"] = True
            if asset.metadata:
                data["metadata"] = dict(asset.metadata)
            return data

        data: dict[str, Any] = {
            "generation_mode": self.generation_mode,
            "asset_manifest": [asset_dict(asset) for asset in self.asset_manifest],
            "ref_image_size": self.ref_image_size,
        }
        if self.prompt_override:
            data["prompt_override"] = self.prompt_override
        if self.duration_sec is not None:
            data["duration_sec"] = self.duration_sec
        if self.aspect_ratio:
            data["aspect_ratio"] = self.aspect_ratio
        if self.megapixels is not None:
            data["megapixels"] = self.megapixels
        if self.resolution_multiple is not None:
            data["resolution_multiple"] = self.resolution_multiple
        if self.seed is not None:
            data["seed"] = self.seed
        data.update(self.extra)
        return data


def h3_generation_config_from_segment(segment: Mapping[str, Any]) -> H3GenerationConfig:
    """Return the H3 configuration persisted on a segment without mutating it."""

    return H3GenerationConfig.from_segment(segment)


def h3_assets_from_manifest(values: Iterable[Mapping[str, Any]]) -> tuple[H3ReferenceAsset, ...]:
    """Convenience parser for callers that store only an asset manifest."""

    return tuple(H3ReferenceAsset.from_mapping(value, index) for index, value in enumerate(values, start=1))
