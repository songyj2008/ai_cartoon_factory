"""Deterministic MiniMax H3 Ref2VA validation.

This module validates the model contract, not creative quality.  It accepts
unprobed manifests during editing and can perform strict filesystem checks at
submit time when a caller supplies ``check_files=True``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from generation.pipelines.minimax_h3.contracts import (
    H3AssetType,
    H3GenerationConfig,
    H3ReferenceAsset,
    H3_REF2VA_GENERATION_MODE,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".wav", ".mp3"}


@dataclass(frozen=True)
class H3ValidationIssue:
    code: str
    message: str
    asset_id: str = ""
    severity: str = "error"


@dataclass
class H3ValidationReport:
    config: H3GenerationConfig | None = None
    errors: list[H3ValidationIssue] = field(default_factory=list)
    warnings: list[H3ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add_error(self, code: str, message: str, asset_id: str = "") -> None:
        self.errors.append(H3ValidationIssue(code=code, message=message, asset_id=asset_id))

    def add_warning(self, code: str, message: str, asset_id: str = "") -> None:
        self.warnings.append(H3ValidationIssue(code=code, message=message, asset_id=asset_id, severity="warning"))

    def raise_for_errors(self) -> "H3ValidationReport":
        if self.errors:
            details = "; ".join(issue.message for issue in self.errors)
            raise ValueError(f"invalid MiniMax H3 Ref2VA generation config: {details}")
        return self


class H3Ref2VAValidator:
    """Validate limits published for the local H3 Ref2VA integration."""

    duration_min_sec = 4
    duration_max_sec = 15
    image_limit = 9
    video_limit = 3
    audio_limit = 3
    total_asset_limit = 12
    media_duration_min_sec = 2
    media_duration_max_sec = 15
    media_total_duration_max_sec = 15
    image_size_limit_bytes = 30 * 1024 * 1024
    video_size_limit_bytes = 50 * 1024 * 1024
    audio_size_limit_bytes = 15 * 1024 * 1024

    def validate(
        self,
        config: H3GenerationConfig,
        *,
        duration_sec: float | None = None,
        check_files: bool = False,
        base_dir: str | Path | None = None,
        require_media_metadata: bool = False,
    ) -> H3ValidationReport:
        report = H3ValidationReport(config=config)
        if config.generation_mode != H3_REF2VA_GENERATION_MODE:
            report.add_error(
                "unsupported_generation_mode",
                f"MiniMax H3 local Ref2VA only supports generation_mode={H3_REF2VA_GENERATION_MODE!r}",
            )
        if config.ref_image_size not in {"match", "max"}:
            report.add_error("invalid_ref_image_size", "ref_image_size must be 'match' or 'max'")
        if config.resolution_multiple is not None and config.resolution_multiple <= 0:
            report.add_error("invalid_resolution_multiple", "resolution_multiple must be positive")

        actual_duration = duration_sec if duration_sec is not None else config.duration_sec
        if actual_duration is not None:
            self._validate_output_duration(report, actual_duration)

        images, videos, audios = config.images, config.videos, config.audios
        if len(images) > self.image_limit:
            report.add_error("too_many_images", f"H3 Ref2VA supports at most {self.image_limit} reference images")
        if len(videos) > self.video_limit:
            report.add_error("too_many_videos", f"H3 Ref2VA supports at most {self.video_limit} reference videos")
        if len(audios) > self.audio_limit:
            report.add_error("too_many_audios", f"H3 Ref2VA supports at most {self.audio_limit} standalone reference audios")
        if len(config.asset_manifest) > self.total_asset_limit:
            report.add_error("too_many_assets", f"H3 Ref2VA supports at most {self.total_asset_limit} uploaded reference files")
        if audios and not (images or videos):
            report.add_error("audio_requires_visual_reference", "H3 standalone audio references require at least one image or video reference")

        seen_asset_ids: set[str] = set()
        for asset in config.asset_manifest:
            if asset.asset_id in seen_asset_ids:
                report.add_error("duplicate_asset_id", f"duplicate asset_id: {asset.asset_id}", asset.asset_id)
            seen_asset_ids.add(asset.asset_id)
            self._validate_asset(
                report,
                asset,
                check_files=check_files,
                base_dir=base_dir,
                require_media_metadata=require_media_metadata,
            )

        self._validate_media_total(report, videos, "video")
        self._validate_media_total(report, audios, "audio")
        if config.prompt_override is not None:
            self.validate_prompt(config.prompt_override, report=report)
        return report

    def validate_prompt(self, prompt: str, *, report: H3ValidationReport | None = None) -> H3ValidationReport:
        report = report or H3ValidationReport()
        text = str(prompt or "").strip()
        if not text:
            report.add_error("empty_prompt", "H3 prompt must not be empty")
        elif len(text) > 7000:
            report.add_error("prompt_too_long", "H3 prompt must be at most 7000 characters")
        if "[已移除主体：" in text or "[已移除图片：" in text:
            report.add_error(
                "removed_reference_still_used",
                "提示词仍引用已移除的主体或图片；请在任务概述/详细镜头中替换或删除红色标记后再生成",
            )
        return report

    def validate_segment_structure(
        self,
        segment: Mapping[str, Any],
        *,
        duration_sec: float,
        report: H3ValidationReport | None = None,
    ) -> H3ValidationReport:
        """Validate deterministic H3 shot timing without judging creativity."""
        report = report or H3ValidationReport()
        all_hints = segment.get("model_hints") if isinstance(segment, Mapping) else None
        hints = all_hints.get("minimax_h3_local_ref2va") if isinstance(all_hints, Mapping) else None
        hints = hints if isinstance(hints, Mapping) else {}
        shots = hints.get("shots") or segment.get("h3_shots") or segment.get("shots") or []
        shots = shots if isinstance(shots, list) else []
        shot_mode = str(hints.get("shot_mode") or segment.get("shot_mode") or "").strip().lower()
        if shot_mode == "one_take" and len(shots) > 1:
            report.add_error("one_take_multishot_conflict", "H3 one_take mode cannot contain more than one Shot")
        previous_start = -1.0
        for index, shot in enumerate(shots, start=1):
            if not isinstance(shot, Mapping):
                report.add_error("invalid_shot", f"Shot {index} must be an object")
                continue
            start_raw, end_raw = shot.get("start_sec"), shot.get("end_sec")
            if start_raw in (None, "") and end_raw in (None, ""):
                continue
            try:
                start, end = float(start_raw), float(end_raw)
            except (TypeError, ValueError):
                report.add_error("invalid_shot_time", f"Shot {index} start_sec/end_sec must be numbers")
                continue
            if start < 0 or end <= start:
                report.add_error("invalid_shot_range", f"Shot {index} must satisfy 0 <= start_sec < end_sec")
            if end > float(duration_sec) + 1e-6:
                report.add_error(
                    "shot_exceeds_duration",
                    f"Shot {index} ends at {end:g}s, beyond the requested {float(duration_sec):g}s duration",
                )
            if start < previous_start:
                report.add_error("shot_order_invalid", f"Shot {index} starts before the preceding Shot")
            previous_start = start
        return report

    def _validate_output_duration(self, report: H3ValidationReport, value: float) -> None:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            report.add_error("invalid_duration", "H3 duration must be a number")
            return
        if duration != int(duration):
            report.add_error("duration_not_integer", "H3 duration must be an integer number of seconds")
        if duration < self.duration_min_sec or duration > self.duration_max_sec:
            report.add_error(
                "duration_out_of_range",
                f"H3 duration must be between {self.duration_min_sec} and {self.duration_max_sec} seconds",
            )

    def _validate_asset(
        self,
        report: H3ValidationReport,
        asset: H3ReferenceAsset,
        *,
        check_files: bool,
        base_dir: str | Path | None,
        require_media_metadata: bool,
    ) -> None:
        if not asset.path:
            report.add_error("missing_asset_path", "reference asset path is required", asset.asset_id)
            return
        suffix = Path(asset.path).suffix.lower()
        valid_extensions = {
            H3AssetType.IMAGE: IMAGE_EXTENSIONS,
            H3AssetType.VIDEO: VIDEO_EXTENSIONS,
            H3AssetType.AUDIO: AUDIO_EXTENSIONS,
        }[asset.type]
        if suffix and suffix not in valid_extensions:
            report.add_error(
                "unsupported_asset_extension",
                f"unsupported {asset.type.value} extension {suffix or '<none>'}",
                asset.asset_id,
            )
        resolved_path: Path | None = None
        if check_files:
            path = Path(asset.path)
            if not path.is_absolute() and base_dir is not None:
                path = Path(base_dir) / path
            if not path.exists() or not path.is_file():
                report.add_error("asset_not_found", f"reference asset does not exist: {path}", asset.asset_id)
            else:
                resolved_path = path

        max_size = {
            H3AssetType.IMAGE: self.image_size_limit_bytes,
            H3AssetType.VIDEO: self.video_size_limit_bytes,
            H3AssetType.AUDIO: self.audio_size_limit_bytes,
        }[asset.type]
        size_bytes = asset.size_bytes
        if size_bytes is None and resolved_path is not None:
            try:
                size_bytes = resolved_path.stat().st_size
            except OSError:
                # Existence was already checked.  A later staging operation
                # will surface a transient filesystem failure with context.
                size_bytes = None
        if size_bytes is not None and size_bytes > max_size:
            report.add_error("asset_too_large", f"{asset.type.value} exceeds H3 per-file size limit", asset.asset_id)
        self._validate_dimensions(report, asset, require_media_metadata=require_media_metadata)
        if asset.type in {H3AssetType.VIDEO, H3AssetType.AUDIO}:
            self._validate_reference_duration(report, asset, require_media_metadata=require_media_metadata)

    def _validate_dimensions(self, report: H3ValidationReport, asset: H3ReferenceAsset, *, require_media_metadata: bool) -> None:
        if asset.type is H3AssetType.AUDIO:
            return
        width, height = asset.width, asset.height
        if width is None or height is None:
            if require_media_metadata:
                report.add_error("missing_dimensions", "media width and height metadata are required", asset.asset_id)
            else:
                report.add_warning("unknown_dimensions", "media width and height have not been probed", asset.asset_id)
            return
        if not (256 <= width <= 5760 and 256 <= height <= 5760):
            report.add_error("dimensions_out_of_range", "H3 reference media dimensions must be between 256 and 5760", asset.asset_id)
        if asset.type is H3AssetType.VIDEO:
            ratio = width / height if height else 0
            if not 0.4 <= ratio <= 2.5:
                report.add_error("video_aspect_out_of_range", "H3 reference video aspect ratio must be between 2:5 and 5:2", asset.asset_id)

    def _validate_reference_duration(
        self,
        report: H3ValidationReport,
        asset: H3ReferenceAsset,
        *,
        require_media_metadata: bool,
    ) -> None:
        duration = asset.duration_sec
        if duration is None:
            if require_media_metadata:
                report.add_error("missing_duration", "reference media duration metadata is required", asset.asset_id)
            else:
                report.add_warning("unknown_duration", "reference media duration has not been probed", asset.asset_id)
            return
        if duration < self.media_duration_min_sec or duration > self.media_duration_max_sec:
            report.add_error(
                "reference_duration_out_of_range",
                f"H3 reference {asset.type.value} duration must be between {self.media_duration_min_sec} and {self.media_duration_max_sec} seconds",
                asset.asset_id,
            )

    def _validate_media_total(
        self,
        report: H3ValidationReport,
        assets: tuple[H3ReferenceAsset, ...],
        media_name: str,
    ) -> None:
        durations = [asset.duration_sec for asset in assets]
        if not assets or any(value is None for value in durations):
            return
        total = sum(float(value or 0) for value in durations)
        if total > self.media_total_duration_max_sec:
            report.add_error(
                f"{media_name}_duration_total_out_of_range",
                f"H3 total reference {media_name} duration must be at most {self.media_total_duration_max_sec} seconds",
            )


def validate_generation_config(
    config: Mapping[str, Any] | H3GenerationConfig | None,
    *,
    duration_sec: float | None = None,
    check_files: bool = False,
    base_dir: str | Path | None = None,
    require_media_metadata: bool = False,
) -> H3ValidationReport:
    """Validate persisted ``generation_config`` without mutating it.

    Set ``check_files=True`` at submission time.  Keeping it false while an
    editor is being populated lets the UI report all model-limit errors before
    files have been copied into ComfyUI's input folder.
    """

    if isinstance(config, H3GenerationConfig):
        parsed = config
    else:
        try:
            parsed = H3GenerationConfig.from_mapping(config if isinstance(config, Mapping) else {})
        except (TypeError, ValueError) as exc:
            report = H3ValidationReport()
            report.add_error("invalid_generation_config", str(exc))
            return report
    return H3Ref2VAValidator().validate(
        parsed,
        duration_sec=duration_sec,
        check_files=check_files,
        base_dir=base_dir,
        require_media_metadata=require_media_metadata,
    )
