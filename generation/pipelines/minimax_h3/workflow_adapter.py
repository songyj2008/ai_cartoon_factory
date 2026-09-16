"""Declarative ComfyUI workflow assembly for local MiniMax H3 Ref2VA."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from generation.contracts import MediaSpec, PreparedJob
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder, H3AssetBinding, H3AssetBindings
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig, H3_REF2VA_MODEL_ID
from generation.pipelines.minimax_h3.prompt_compiler import H3CompiledPrompt, H3PromptCompiler
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator


@dataclass(frozen=True)
class H3NodeInput:
    node_id: str
    input_name: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "H3NodeInput":
        return cls(node_id=str(value.get("node_id") or value.get("id") or ""), input_name=str(value.get("input_name") or value.get("input") or ""))


@dataclass(frozen=True)
class H3ComfyNodeTypes:
    load_image: str = "LoadImage"
    load_image_path_input: str = "image"
    load_image_output_index: int = 0
    load_video: str = "LoadVideo"
    load_video_path_input: str = "file"
    load_video_keep_audio_input: str = "keep_audio"
    load_video_output_index: int = 0
    get_video_components: str = "GetVideoComponents"
    get_video_components_video_input: str = "video"
    get_video_components_frames_output_index: int = 0
    get_video_components_audio_output_index: int = 1
    load_audio: str = "LoadAudio"
    load_audio_path_input: str = "audio"
    load_audio_output_index: int = 0
    h3_reference_to_video: str = "MiniMaxH3ReferenceToVideo"


@dataclass(frozen=True)
class H3ReferenceSlots:
    """Fixed executable reference slots exported by one API workflow."""

    image_loader_node_ids: tuple[str, ...] = ()
    video_loader_node_ids: tuple[str, ...] = ()
    video_components_node_ids: tuple[str, ...] = ()
    audio_loader_node_ids: tuple[str, ...] = ()

    @property
    def all_node_ids(self) -> tuple[str, ...]:
        return (
            self.image_loader_node_ids
            + self.video_loader_node_ids
            + self.video_components_node_ids
            + self.audio_loader_node_ids
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "H3ReferenceSlots":
        data = value if isinstance(value, Mapping) else {}

        def node_ids(name: str) -> tuple[str, ...]:
            return tuple(str(item).strip() for item in data.get(name) or () if str(item).strip())

        result = cls(
            image_loader_node_ids=node_ids("image_loader_node_ids"),
            video_loader_node_ids=node_ids("video_loader_node_ids"),
            video_components_node_ids=node_ids("video_components_node_ids"),
            audio_loader_node_ids=node_ids("audio_loader_node_ids"),
        )
        if len(result.video_loader_node_ids) != len(result.video_components_node_ids):
            raise ValueError("H3 workflow profile needs one video components node for each video loader node")
        all_ids = result.all_node_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("H3 workflow profile reference slot node ids must be unique")
        return result


@dataclass(frozen=True)
class H3ReferenceControl:
    """One declarative workflow gate for an optional H3 reference group.

    Some exported H3 graphs pass reference media through switch/control nodes
    before it reaches ``MiniMaxH3ReferenceToVideo``.  The adapter does not
    know those node ids: a workflow Profile declares the affected input and
    the values to use when the matching media group is present or absent.
    """

    node_id: str
    input_name: str
    media_kind: str
    min_count: int = 1
    enabled_value: Any = True
    disabled_value: Any = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "H3ReferenceControl":
        node_id = str(value.get("node_id") or value.get("id") or "").strip()
        input_name = str(value.get("input_name") or value.get("input") or "").strip()
        media_kind = str(value.get("media_kind") or value.get("media") or value.get("kind") or "").strip().lower()
        if not node_id or not input_name or media_kind not in {"image", "video", "audio", "video_audio", "any"}:
            raise ValueError("H3 reference control needs node_id, input_name, and media_kind")
        try:
            min_count = max(1, int(value.get("min_count") or 1))
        except (TypeError, ValueError):
            min_count = 1
        return cls(
            node_id=node_id,
            input_name=input_name,
            media_kind=media_kind,
            min_count=min_count,
            enabled_value=value.get("enabled_value", value.get("on", True)),
            disabled_value=value.get("disabled_value", value.get("off", False)),
        )


def _reference_controls(value: Any) -> tuple[H3ReferenceControl, ...]:
    if isinstance(value, Mapping):
        values = []
        for media_kind, item in value.items():
            if isinstance(item, Mapping):
                values.append({"media_kind": media_kind, **dict(item)})
            elif isinstance(item, list):
                values.extend(
                    {"media_kind": media_kind, **dict(control)}
                    for control in item
                    if isinstance(control, Mapping)
                )
    else:
        values = value if isinstance(value, list) else []
    return tuple(H3ReferenceControl.from_mapping(item) for item in values if isinstance(item, Mapping))


@dataclass(frozen=True)
class H3Ref2VAWorkflowProfile:
    """All workflow-specific facts, kept out of H3 business logic.

    The default matches the supplied API workflow.  A future workflow export
    with different node ids should provide another profile instead of changing
    the adapter implementation.
    """

    profile_id: str
    h3_node_id: str
    prompt: H3NodeInput
    seed: H3NodeInput
    duration: H3NodeInput
    resolution_aspect_ratio: H3NodeInput
    resolution_megapixels: H3NodeInput
    resolution_multiple: H3NodeInput
    ref_image_size_input: str = "ref_image_size"
    output_prefix: H3NodeInput | None = None
    reference_slots: H3ReferenceSlots = field(default_factory=H3ReferenceSlots)
    reference_controls: tuple[H3ReferenceControl, ...] = ()
    node_types: H3ComfyNodeTypes = field(default_factory=H3ComfyNodeTypes)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "H3Ref2VAWorkflowProfile":
        # ``config.json`` keeps the initial project profile flat; accept that
        # shape as well as the nested portable profile shape documented here.
        if "prompt_node" in value or "condition_node" in value:
            def flat_target(prefix: str, fallback_input: str) -> H3NodeInput:
                node_id = str(value.get(f"{prefix}_node") or "").strip()
                input_name = str(value.get(f"{prefix}_input") or fallback_input).strip()
                if not node_id or not input_name:
                    raise ValueError(f"H3 workflow profile needs {prefix}_node and {prefix}_input")
                return H3NodeInput(node_id=node_id, input_name=input_name)

            def loader(name: str, defaults: tuple[str, str]) -> Mapping[str, Any]:
                raw = value.get(name)
                data = raw if isinstance(raw, Mapping) else {}
                return {"class_type": str(data.get("class_type") or defaults[0]), "input_name": str(data.get("input_name") or defaults[1]), **dict(data)}

            image = loader("image_loader", ("LoadImage", "image"))
            video = loader("video_loader", ("LoadVideo", "file"))
            components = loader("video_components", ("GetVideoComponents", "video"))
            audio = loader("audio_loader", ("LoadAudio", "audio"))
            return cls(
                profile_id=str(value.get("profile_id") or "minimax_h3_ref2va").strip(),
                h3_node_id=str(value.get("condition_node") or value.get("h3_node_id") or "").strip(),
                prompt=flat_target("prompt", "value"),
                seed=flat_target("seed", "noise_seed"),
                duration=flat_target("duration", "value"),
                resolution_aspect_ratio=H3NodeInput(
                    str(value.get("resolution_node") or "").strip(),
                    str(value.get("resolution_aspect_input") or "aspect_ratio").strip(),
                ),
                resolution_megapixels=H3NodeInput(
                    str(value.get("resolution_node") or "").strip(),
                    str(value.get("resolution_megapixels_input") or "megapixels").strip(),
                ),
                resolution_multiple=H3NodeInput(
                    str(value.get("resolution_node") or "").strip(),
                    str(value.get("resolution_multiple_input") or "multiple").strip(),
                ),
                ref_image_size_input=str(value.get("ref_image_size_input") or "ref_image_size").strip() or "ref_image_size",
                output_prefix=(
                    H3NodeInput(
                        str(value.get("output_node") or "").strip(),
                        str(value.get("output_prefix_input") or "filename_prefix").strip(),
                    )
                    if str(value.get("output_node") or "").strip()
                    else None
                ),
                reference_slots=H3ReferenceSlots.from_mapping(value.get("reference_slots")),
                reference_controls=_reference_controls(value.get("reference_controls") or value.get("reference_switches")),
                node_types=H3ComfyNodeTypes(
                    load_image=str(image["class_type"]),
                    load_image_path_input=str(image["input_name"]),
                    load_image_output_index=int(image.get("output_index") or 0),
                    load_video=str(video["class_type"]),
                    load_video_path_input=str(video["input_name"]),
                    load_video_keep_audio_input=str(video.get("keep_audio_input") or "keep_audio"),
                    load_video_output_index=int(video.get("output_index") or 0),
                    get_video_components=str(components["class_type"]),
                    get_video_components_video_input=str(components["input_name"]),
                    get_video_components_frames_output_index=int(components.get("frames_output_index") or 0),
                    get_video_components_audio_output_index=int(components.get("audio_output_index") or 1),
                    load_audio=str(audio["class_type"]),
                    load_audio_path_input=str(audio["input_name"]),
                    load_audio_output_index=int(audio.get("output_index") or 0),
                    h3_reference_to_video=str(value.get("h3_class_type") or "MiniMaxH3ReferenceToVideo"),
                ),
            )
        nodes = value.get("nodes") if isinstance(value.get("nodes"), Mapping) else value
        if not isinstance(nodes, Mapping):
            raise TypeError("H3 workflow profile nodes must be an object")

        def target(name: str) -> H3NodeInput:
            raw = nodes.get(name)
            if not isinstance(raw, Mapping):
                raise ValueError(f"H3 workflow profile is missing node target: {name}")
            result = H3NodeInput.from_mapping(raw)
            if not result.node_id or not result.input_name:
                raise ValueError(f"H3 workflow profile target {name} needs node_id and input_name")
            return result

        node_types_value = value.get("node_types")
        node_types = H3ComfyNodeTypes(**dict(node_types_value)) if isinstance(node_types_value, Mapping) else H3ComfyNodeTypes()
        h3_node_id = str(nodes.get("h3_node_id") or nodes.get("reference_to_video_node") or "").strip()
        if not h3_node_id:
            raise ValueError("H3 workflow profile needs h3_node_id")
        return cls(
            profile_id=str(value.get("profile_id") or "minimax_h3_ref2va").strip(),
            h3_node_id=h3_node_id,
            prompt=target("prompt"),
            seed=target("seed"),
            duration=target("duration"),
            resolution_aspect_ratio=target("resolution_aspect_ratio"),
            resolution_megapixels=target("resolution_megapixels"),
            resolution_multiple=target("resolution_multiple"),
            ref_image_size_input=str(
                value.get("ref_image_size_input") or nodes.get("ref_image_size_input") or "ref_image_size"
            ).strip() or "ref_image_size",
            output_prefix=(
                H3NodeInput.from_mapping(nodes["output_prefix"])
                if isinstance(nodes.get("output_prefix"), Mapping)
                else None
            ),
            reference_slots=H3ReferenceSlots.from_mapping(value.get("reference_slots")),
            reference_controls=_reference_controls(value.get("reference_controls") or value.get("reference_switches")),
            node_types=node_types,
        )


DEFAULT_H3_REF2VA_WORKFLOW_PROFILE = H3Ref2VAWorkflowProfile(
    profile_id="minimax_h3_ref2va_api_v1",
    h3_node_id="170",
    prompt=H3NodeInput("167", "value"),
    seed=H3NodeInput("154", "noise_seed"),
    duration=H3NodeInput("157", "value"),
    resolution_aspect_ratio=H3NodeInput("158", "aspect_ratio"),
    resolution_megapixels=H3NodeInput("158", "megapixels"),
    resolution_multiple=H3NodeInput("158", "multiple"),
    ref_image_size_input="ref_image_size",
    output_prefix=H3NodeInput("160", "filename_prefix"),
    reference_slots=H3ReferenceSlots(
        image_loader_node_ids=("161", "205", "206", "207", "208", "209", "210", "211", "212"),
        video_loader_node_ids=("246", "247", "248"),
        video_components_node_ids=("169", "238", "241"),
        audio_loader_node_ids=("164", "244", "245"),
    ),
    reference_controls=(),
)


@dataclass(frozen=True)
class H3WorkflowBuild:
    workflow: dict[str, Any]
    prompt: H3CompiledPrompt
    config: H3GenerationConfig
    bindings: H3AssetBindings
    asset_slots: tuple[dict[str, Any], ...]
    requested_media_spec: MediaSpec
    profile_id: str

    def to_prepared_job(
        self,
        *,
        segment_index: int,
        part_id: str,
        model_id: str = H3_REF2VA_MODEL_ID,
        model_revision: str = "1",
        workflow_path: str = "",
    ) -> PreparedJob:
        return PreparedJob(
            model_id=model_id,
            model_revision=model_revision,
            segment_index=segment_index,
            part_id=part_id,
            workflow=self.workflow,
            prompt=self.prompt.text,
            requested_media_spec=self.requested_media_spec,
            asset_slots=list(self.asset_slots),
            workflow_path=workflow_path,
            metadata={
                "generation_mode": self.config.generation_mode,
                "workflow_profile": self.profile_id,
                "asset_manifest": self.config.to_dict().get("asset_manifest", []),
                "prompt_reference_tags": [
                    {"tag": tag, "kind": kind, "asset_id": asset.asset_id}
                    for tag, kind, asset in self.prompt.references
                ],
            },
        )


class H3Ref2VAWorkflowAdapter:
    """Build API workflow JSON from H3 config without hard-coded switch nodes."""

    _dynamic_input_prefixes = (
        "ref_images.ref_image_",
        "ref_videos.ref_video_",
        "ref_video_audios.ref_video_audio_",
        "ref_audios.ref_audio_",
    )

    def __init__(
        self,
        profile: H3Ref2VAWorkflowProfile | Mapping[str, Any] | None = None,
        *,
        asset_binder: H3AssetBinder | None = None,
        prompt_compiler: H3PromptCompiler | None = None,
        validator: H3Ref2VAValidator | None = None,
        path_resolver: Callable[[str], str] | None = None,
    ) -> None:
        if profile is None:
            profile = DEFAULT_H3_REF2VA_WORKFLOW_PROFILE
        self.profile = H3Ref2VAWorkflowProfile.from_mapping(profile) if isinstance(profile, Mapping) else profile
        self.asset_binder = asset_binder or H3AssetBinder()
        self.prompt_compiler = prompt_compiler or H3PromptCompiler()
        self.validator = validator or H3Ref2VAValidator()
        self.path_resolver = path_resolver or self._default_path_resolver

    def build_for_segment(
        self,
        template: Mapping[str, Any],
        segment: Mapping[str, Any],
        *,
        duration_sec: float | None = None,
        check_files: bool = False,
        output_prefix: str | None = None,
    ) -> H3WorkflowBuild:
        config = H3GenerationConfig.from_segment(segment)
        selected_duration = duration_sec
        if selected_duration is None:
            selected_duration = config.duration_sec
        if selected_duration is None:
            selected_duration = segment.get("estimated_duration_sec") or segment.get("duration_sec")
        return self.build(
            template,
            segment,
            config,
            duration_sec=selected_duration,
            check_files=check_files,
            output_prefix=output_prefix,
        )

    def build(
        self,
        template: Mapping[str, Any],
        segment: Mapping[str, Any],
        config: H3GenerationConfig | Mapping[str, Any],
        *,
        duration_sec: float | None,
        check_files: bool = False,
        output_prefix: str | None = None,
    ) -> H3WorkflowBuild:
        if not isinstance(template, Mapping):
            raise TypeError("H3 API workflow template must be an object keyed by node id")
        config = H3GenerationConfig.from_mapping(config) if isinstance(config, Mapping) else config
        report = self.validator.validate(config, duration_sec=duration_sec, check_files=check_files)
        report.raise_for_errors()
        if duration_sec is None:
            raise ValueError("H3 duration_sec is required")
        duration = int(float(duration_sec))
        api = copy.deepcopy(dict(template))
        h3_node = self._require_node(api, self.profile.h3_node_id)
        if str(h3_node.get("class_type") or "") != self.profile.node_types.h3_reference_to_video:
            raise ValueError(
                f"workflow node {self.profile.h3_node_id} must be {self.profile.node_types.h3_reference_to_video}"
            )

        bindings = self.asset_binder.bind(config)
        prompt = self.prompt_compiler.compile(segment, config, bindings, duration_sec=duration)
        self._patch_fixed_inputs(api, config, prompt.text, duration, output_prefix=output_prefix)
        self._clear_previous_reference_bindings(api)
        self._patch_reference_controls(api, bindings)
        asset_slots = self._bind_reference_slots(api, bindings)

        requested_media_spec = MediaSpec(duration_sec=float(duration), fps=24, has_audio=True)
        return H3WorkflowBuild(
            workflow=api,
            prompt=prompt,
            config=config,
            bindings=bindings,
            asset_slots=tuple(asset_slots),
            requested_media_spec=requested_media_spec,
            profile_id=self.profile.profile_id,
        )

    def _patch_fixed_inputs(
        self,
        api: dict[str, Any],
        config: H3GenerationConfig,
        prompt: str,
        duration: int,
        *,
        output_prefix: str | None = None,
    ) -> None:
        self._set_input(api, self.profile.prompt, prompt)
        self._set_input(api, self.profile.duration, duration)
        self._set_input(api, self.profile.resolution_aspect_ratio, config.aspect_ratio or self._read_input(api, self.profile.resolution_aspect_ratio))
        if config.megapixels is not None:
            self._set_input(api, self.profile.resolution_megapixels, config.megapixels)
        if config.resolution_multiple is not None:
            self._set_input(api, self.profile.resolution_multiple, config.resolution_multiple)
        if config.seed is not None:
            self._set_input(api, self.profile.seed, config.seed)
        h3_inputs = self._require_node(api, self.profile.h3_node_id).setdefault("inputs", {})
        h3_inputs[self.profile.ref_image_size_input] = config.ref_image_size
        if output_prefix and self.profile.output_prefix is not None:
            self._set_input(api, self.profile.output_prefix, output_prefix)

    def _clear_previous_reference_bindings(self, api: dict[str, Any]) -> None:
        inputs = self._require_node(api, self.profile.h3_node_id).setdefault("inputs", {})
        for key in list(inputs):
            if any(key.startswith(prefix) for prefix in self._dynamic_input_prefixes):
                inputs.pop(key, None)

    def _patch_reference_controls(self, api: dict[str, Any], bindings: H3AssetBindings) -> None:
        """Apply only Profile-declared switch values for optional media groups."""
        counts = {
            "image": len(bindings.images),
            "video": len(bindings.videos),
            "audio": len(bindings.audios),
            "video_audio": sum(1 for item in bindings.videos if item.paired_audio_input),
            "any": len(bindings.all),
        }
        for control in self.profile.reference_controls:
            enabled = counts.get(control.media_kind, 0) >= control.min_count
            self._set_input(
                api,
                H3NodeInput(control.node_id, control.input_name),
                control.enabled_value if enabled else control.disabled_value,
            )

    def _bind_reference_slots(
        self,
        api: dict[str, Any],
        bindings: H3AssetBindings,
    ) -> list[dict[str, Any]]:
        slots_profile = self.profile.reference_slots
        self._validate_slot_capacity(bindings, slots_profile)
        h3_inputs = self._require_node(api, self.profile.h3_node_id).setdefault("inputs", {})
        slots: list[dict[str, Any]] = []
        for index, binding in enumerate(bindings.images):
            node_id = slots_profile.image_loader_node_ids[index]
            path = self.path_resolver(binding.asset.path)
            node = self._require_typed_node(api, node_id, self.profile.node_types.load_image)
            node.setdefault("inputs", {})[self.profile.node_types.load_image_path_input] = path
            h3_inputs[binding.workflow_input] = [node_id, self.profile.node_types.load_image_output_index]
            slots.append(
                self._asset_slot(
                    binding,
                    node_id,
                    self.profile.node_types.load_image_path_input,
                    path,
                    self.profile.node_types.load_image,
                )
            )
        self._remove_unused_nodes(api, slots_profile.image_loader_node_ids[len(bindings.images):])

        for index, binding in enumerate(bindings.videos):
            load_node_id = slots_profile.video_loader_node_ids[index]
            components_node_id = slots_profile.video_components_node_ids[index]
            path = self.path_resolver(binding.asset.path)
            load_node = self._require_typed_node(api, load_node_id, self.profile.node_types.load_video)
            load_node.setdefault("inputs", {})[self.profile.node_types.load_video_path_input] = path
            if self.profile.node_types.load_video_keep_audio_input:
                load_node["inputs"][self.profile.node_types.load_video_keep_audio_input] = bool(
                    binding.asset.use_video_audio
                )
            components_node = self._require_typed_node(
                api,
                components_node_id,
                self.profile.node_types.get_video_components,
            )
            components_node.setdefault("inputs", {})[
                self.profile.node_types.get_video_components_video_input
            ] = [load_node_id, self.profile.node_types.load_video_output_index]
            h3_inputs[binding.workflow_input] = [
                components_node_id,
                self.profile.node_types.get_video_components_frames_output_index,
            ]
            if binding.paired_audio_input:
                h3_inputs[binding.paired_audio_input] = [
                    components_node_id,
                    self.profile.node_types.get_video_components_audio_output_index,
                ]
            slots.append(
                self._asset_slot(
                    binding,
                    load_node_id,
                    self.profile.node_types.load_video_path_input,
                    path,
                    self.profile.node_types.load_video,
                )
            )
        self._remove_unused_nodes(api, slots_profile.video_loader_node_ids[len(bindings.videos):])
        self._remove_unused_nodes(api, slots_profile.video_components_node_ids[len(bindings.videos):])

        for index, binding in enumerate(bindings.audios):
            node_id = slots_profile.audio_loader_node_ids[index]
            path = self.path_resolver(binding.asset.path)
            node = self._require_typed_node(api, node_id, self.profile.node_types.load_audio)
            node.setdefault("inputs", {})[self.profile.node_types.load_audio_path_input] = path
            h3_inputs[binding.workflow_input] = [node_id, self.profile.node_types.load_audio_output_index]
            slots.append(
                self._asset_slot(
                    binding,
                    node_id,
                    self.profile.node_types.load_audio_path_input,
                    path,
                    self.profile.node_types.load_audio,
                )
            )
        self._remove_unused_nodes(api, slots_profile.audio_loader_node_ids[len(bindings.audios):])
        return slots

    @staticmethod
    def _validate_slot_capacity(bindings: H3AssetBindings, slots: H3ReferenceSlots) -> None:
        requested = {
            "images": (len(bindings.images), len(slots.image_loader_node_ids)),
            "videos": (len(bindings.videos), len(slots.video_loader_node_ids)),
            "audios": (len(bindings.audios), len(slots.audio_loader_node_ids)),
        }
        for kind, (count, capacity) in requested.items():
            if count > capacity:
                raise ValueError(f"H3 workflow profile has {capacity} {kind} slots, but {count} were requested")

    @staticmethod
    def _remove_unused_nodes(api: dict[str, Any], node_ids: tuple[str, ...]) -> None:
        for node_id in node_ids:
            api.pop(str(node_id), None)

    def _require_typed_node(self, api: Mapping[str, Any], node_id: str, class_type: str) -> dict[str, Any]:
        node = self._require_node(api, node_id)
        actual = str(node.get("class_type") or "")
        if actual != class_type:
            raise ValueError(f"workflow node {node_id} must be {class_type}, got {actual or '<empty>'}")
        return node

    @staticmethod
    def _asset_slot(
        binding: H3AssetBinding,
        node_id: str,
        input_name: str,
        path: str,
        class_type: str,
    ) -> dict[str, Any]:
        return {
            "name": f"{binding.asset.type.value}_{binding.category_index + 1}",
            "asset_id": binding.asset.asset_id,
            "media_type": binding.asset.type.value,
            "node_id": node_id,
            "input_name": input_name,
            "class_type": class_type,
            "path": path,
            "source_path": binding.asset.path,
            "target_input": binding.workflow_input,
            "prompt_tag": binding.prompt_tag,
            "paired_audio_target_input": binding.paired_audio_input or "",
            "paired_audio_prompt_tag": binding.paired_audio_tag or "",
        }

    @staticmethod
    def _default_path_resolver(value: str) -> str:
        path = Path(str(value or "").strip())
        return str(path.resolve()) if path else ""

    @staticmethod
    def _require_node(api: Mapping[str, Any], node_id: str) -> dict[str, Any]:
        node = api.get(str(node_id))
        if not isinstance(node, dict):
            raise KeyError(f"H3 workflow is missing node {node_id}")
        node.setdefault("inputs", {})
        return node

    def _set_input(self, api: dict[str, Any], target: H3NodeInput, value: Any) -> None:
        self._require_node(api, target.node_id).setdefault("inputs", {})[target.input_name] = value

    def _read_input(self, api: dict[str, Any], target: H3NodeInput) -> Any:
        return self._require_node(api, target.node_id).setdefault("inputs", {}).get(target.input_name)
