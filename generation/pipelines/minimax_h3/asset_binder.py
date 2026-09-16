"""Reference ordering and prompt/workflow-slot binding for MiniMax H3 Ref2VA."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from generation.pipelines.minimax_h3.contracts import H3AssetType, H3GenerationConfig, H3ReferenceAsset


@dataclass(frozen=True)
class H3AssetBinding:
    """One manifest asset resolved to H3's dynamic ComfyUI input name."""

    asset: H3ReferenceAsset
    category_index: int
    workflow_input: str
    prompt_tag: str
    paired_audio_input: str | None = None
    paired_audio_tag: str | None = None

    @property
    def display_role(self) -> str:
        return self.asset.role or self.asset.label or self.asset.type.value


@dataclass(frozen=True)
class H3AssetBindings:
    images: tuple[H3AssetBinding, ...]
    videos: tuple[H3AssetBinding, ...]
    audios: tuple[H3AssetBinding, ...]

    @property
    def all(self) -> tuple[H3AssetBinding, ...]:
        return self.images + self.videos + self.audios

    @property
    def prompt_references(self) -> tuple[tuple[str, str, H3ReferenceAsset], ...]:
        """Return presentation order used by the Comfy H3 tokenizer.

        The H3 node emits images first.  For every video with an opted-in
        embedded soundtrack, that ``<Audio n>`` appears immediately before the
        corresponding ``<Video n>``; standalone audio follows all videos.
        """

        refs: list[tuple[str, str, H3ReferenceAsset]] = []
        refs.extend((binding.prompt_tag, "image", binding.asset) for binding in self.images)
        for binding in self.videos:
            if binding.paired_audio_tag:
                refs.append((binding.paired_audio_tag, "video_audio", binding.asset))
            refs.append((binding.prompt_tag, "video", binding.asset))
        refs.extend((binding.prompt_tag, "audio", binding.asset) for binding in self.audios)
        return tuple(refs)

    def binding_for(self, asset_id: str) -> H3AssetBinding | None:
        return next((binding for binding in self.all if binding.asset.asset_id == asset_id), None)


class H3AssetBinder:
    """Convert a persisted H3 manifest to deterministic dynamic input slots.

    The Comfy core node's public contract is ``ref_images.ref_image_N``,
    ``ref_videos.ref_video_N``, ``ref_video_audios.ref_video_audio_N`` and
    ``ref_audios.ref_audio_N``.  We intentionally bind by these names instead
    of guessing UI-only switch node ids such as 249/251/254.
    """

    def bind(
        self,
        config_or_assets: H3GenerationConfig | Mapping[str, Any] | Iterable[H3ReferenceAsset | Mapping[str, Any]],
    ) -> H3AssetBindings:
        config = self._to_config(config_or_assets)
        image_bindings = tuple(
            H3AssetBinding(
                asset=asset,
                category_index=index,
                workflow_input=f"ref_images.ref_image_{index}",
                prompt_tag=f"<Picture {index + 1}>",
            )
            for index, asset in enumerate(config.images)
        )

        audio_index = 0
        video_bindings: list[H3AssetBinding] = []
        for index, asset in enumerate(config.videos):
            paired_input = None
            paired_tag = None
            if asset.use_video_audio:
                paired_input = f"ref_video_audios.ref_video_audio_{index}"
                audio_index += 1
                paired_tag = f"<Audio {audio_index}>"
            video_bindings.append(
                H3AssetBinding(
                    asset=asset,
                    category_index=index,
                    workflow_input=f"ref_videos.ref_video_{index}",
                    prompt_tag=f"<Video {index + 1}>",
                    paired_audio_input=paired_input,
                    paired_audio_tag=paired_tag,
                )
            )

        audio_bindings: list[H3AssetBinding] = []
        for index, asset in enumerate(config.audios):
            audio_index += 1
            audio_bindings.append(
                H3AssetBinding(
                    asset=asset,
                    category_index=index,
                    workflow_input=f"ref_audios.ref_audio_{index}",
                    prompt_tag=f"<Audio {audio_index}>",
                )
            )
        return H3AssetBindings(images=image_bindings, videos=tuple(video_bindings), audios=tuple(audio_bindings))

    @staticmethod
    def _to_config(
        config_or_assets: H3GenerationConfig | Mapping[str, Any] | Iterable[H3ReferenceAsset | Mapping[str, Any]],
    ) -> H3GenerationConfig:
        if isinstance(config_or_assets, H3GenerationConfig):
            return config_or_assets
        if isinstance(config_or_assets, Mapping):
            return H3GenerationConfig.from_mapping(config_or_assets)
        values = list(config_or_assets or [])
        manifest: list[dict[str, Any]] = []
        for index, value in enumerate(values, start=1):
            if isinstance(value, H3ReferenceAsset):
                manifest.append(
                    {
                        "asset_id": value.asset_id,
                        "type": value.type.value,
                        "path": value.path,
                        "role": value.role,
                        "label": value.label,
                        "order": value.order,
                        "use_video_audio": value.use_video_audio,
                        "metadata": value.metadata,
                    }
                )
            elif isinstance(value, Mapping):
                manifest.append(dict(value))
            else:
                raise TypeError(f"asset {index} is not an H3 asset object")
        return H3GenerationConfig.from_mapping({"asset_manifest": manifest})
