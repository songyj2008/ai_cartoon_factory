"""Model-neutral entry points used by UI and orchestration code."""
from __future__ import annotations

from typing import Any
from functools import wraps

from generation.job_schema import normalize_video_jobs_payload, resolved_model_id
from generation.registry import default_model_id, get_model_registry


def _serialized_submission(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from services.context import pin_runtime
        from workflow.video_merge import video_jobs_lock
        with pin_runtime(), video_jobs_lock():
            return fn(*args, **kwargs)
    return wrapped


class GenerationService:
    def __init__(self) -> None:
        self.registry = get_model_registry()

    def project_default_model_id(self) -> str:
        from services.ui_run_config import load_ui_run_config

        selected = str(load_ui_run_config().get("default_video_model_id") or default_model_id()).strip()
        return selected if self.registry.has(selected) else default_model_id()

    def _pipeline_for(self, model_id: str):
        return self.registry.get(model_id or self.project_default_model_id())

    def _model_groups_for_beats(
        self,
        beats_data: dict[str, Any],
        segment_indices: set[int] | None,
    ) -> dict[str, set[int] | None]:
        """Resolve desired per-segment models without translating their jobs.

        Existing jobs own a per-segment override.  New jobs inherit the project
        default.  The selected pipeline receives only its own source Beat
        indexes, which keeps an H3 selection from ever flowing through LTX's
        compiler (and vice versa).
        """
        from workflow.video_merge import load_video_jobs

        payload = normalize_video_jobs_payload(load_video_jobs())
        existing = {
            int(item.get("segment_index") or 0): item
            for item in payload.get("segments") or []
            if isinstance(item, dict) and int(item.get("segment_index") or 0) > 0
        }
        beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
        beat_indices = set(range(1, len(beats) + 1)) if isinstance(beats, list) else set()
        requested = {int(value) for value in segment_indices or set() if int(value) > 0}
        target_indices = requested or beat_indices
        if not target_indices:
            return {self.project_default_model_id(): None}

        groups: dict[str, set[int]] = {}
        for index in sorted(target_indices):
            segment = existing.get(index)
            model_id = resolved_model_id(segment, payload) if segment else self.project_default_model_id()
            if not self.registry.has(model_id):
                raise ValueError(f"segment {index} uses an unregistered video model: {model_id}")
            groups.setdefault(model_id, set()).add(index)
        return groups

    @_serialized_submission
    def _run_preparation_by_model(
        self,
        beats_data: dict[str, Any],
        *,
        submit: bool,
        workflow_only: bool,
        segment_indices: set[int] | None,
        merge_after: bool,
    ) -> dict[str, Any]:
        """Run each model's independent compiler/assembler for its segments."""
        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs

        groups = self._model_groups_for_beats(beats_data, segment_indices)
        for model_id, indices in groups.items():
            from generation.model_rules import assert_beats_compatible

            assert_beats_compatible(beats_data, model_id)
            pipeline = self._pipeline_for(model_id)
            # A pipeline validates its own planning rules.  H3's 4-15 second
            # rules therefore do not leak into the LTX path.
            pipeline.generate(
                beats_data,
                submit=submit,
                workflow_only=workflow_only,
                segment_indices=indices,
                merge_after=False,
            )

        result = normalize_video_jobs_payload(load_video_jobs())
        result["default_model_id"] = self.project_default_model_id()
        if merge_after:
            from workflow.video_merge import request_video_merge
            request_video_merge(result)
        save_video_jobs(result)
        return result

    def prepare_segments(self, beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> dict[str, Any]:
        return self._run_preparation_by_model(
            beats_data,
            submit=False,
            workflow_only=True,
            segment_indices=segment_indices,
            merge_after=False,
        )

    @_serialized_submission
    def regenerate_segment_prompt(self, beats_data: dict[str, Any], segment_index: int) -> dict[str, Any]:
        """Recompile one prompt with the model configured for that segment.

        The service owns model selection only. Each registered pipeline owns
        the prompt state that must be reset before its compiler runs.
        """
        from workflow.video_merge import load_video_jobs, save_video_jobs

        index = int(segment_index)
        if index <= 0:
            raise ValueError("segment index must be positive")
        payload = normalize_video_jobs_payload(load_video_jobs())
        segment = next(
            (
                item
                for item in payload.get("segments") or []
                if isinstance(item, dict) and int(item.get("segment_index") or 0) == index
            ),
            None,
        )
        if segment is None:
            raise ValueError(f"segment not found: {index}")
        model_id = resolved_model_id(segment, payload)
        pipeline = self._pipeline_for(model_id)
        reset_prompt = getattr(pipeline, "reset_prompt_for_regeneration", None)
        if not callable(reset_prompt):
            raise TypeError(f"video model does not implement prompt regeneration: {model_id}")
        reset_prompt(segment)
        save_video_jobs(payload)
        regenerate_source = getattr(pipeline, "regenerate_prompt_source", None)
        if callable(regenerate_source):
            regenerate_source(beats_data, index)
            # A fullscreen H3 autosave may have started just before the model
            # request and completed while it was running. Reload the newest
            # manifest (the user's picker is authoritative), but clear any
            # stale prompt override that request wrote back before compiling.
            latest_payload = normalize_video_jobs_payload(load_video_jobs())
            latest_segment = next(
                (
                    item
                    for item in latest_payload.get("segments") or []
                    if isinstance(item, dict) and int(item.get("segment_index") or 0) == index
                ),
                None,
            )
            if latest_segment is None:
                raise ValueError(f"segment disappeared during prompt regeneration: {index}")
            reset_prompt(latest_segment)
            save_video_jobs(latest_payload)
        return self._run_preparation_by_model(
            beats_data,
            submit=False,
            workflow_only=True,
            segment_indices={index},
            merge_after=False,
        )

    def generate_segments(
        self,
        beats_data: dict[str, Any],
        *,
        submit: bool,
        workflow_only: bool,
        segment_indices: set[int] | None = None,
        merge_after: bool = False,
    ) -> dict[str, Any]:
        return self._run_preparation_by_model(
            beats_data,
            submit=submit,
            workflow_only=workflow_only,
            segment_indices=segment_indices,
            merge_after=merge_after,
        )

    @_serialized_submission
    def submit_saved_segments(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        # Dispatch each model's selected segments independently.  The LTX-only
        # release has one group; later models add a group without changing the
        # LTX pipeline or submitter.
        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs

        payload = normalize_video_jobs_payload(load_video_jobs())
        segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
        selected = {int(value) for value in segment_indices} if segment_indices else None
        groups: dict[str, set[int]] = {}
        for segment in segments:
            index = int(segment.get("segment_index") or 0)
            if selected is not None and index not in selected:
                continue
            if index <= 0:
                continue
            model_id = resolved_model_id(segment, payload)
            groups.setdefault(model_id, set()).add(index)
        if not groups:
            raise ValueError("video_jobs.json is empty; generate segment prompts first")
        unsupported = sorted(model_id for model_id in groups if not self.registry.has(model_id))
        if unsupported:
            raise ValueError("selected segments use unregistered video models: " + ", ".join(unsupported))

        for model_id, indices in groups.items():
            self._pipeline_for(model_id).submit_saved(indices, merge_after=False, wait=wait)

        result = normalize_video_jobs_payload(load_video_jobs())
        if merge_after:
            from workflow.video_merge import request_video_merge
            request_video_merge(result)
        return result

    @_serialized_submission
    def set_segment_model_override(self, segment_index: int, model_id: str | None) -> dict[str, Any]:
        """Persist a per-segment model choice without pretending it is rendered.

        A model switch invalidates a compiled prompt/workflow and any previous
        render.  It deliberately does *not* translate LTX prompts, asset
        bindings, or node graphs to the target model; the selected pipeline
        must rebuild those from its own contracts on the next generation.
        """
        from workflow.video_merge import load_video_jobs, save_video_jobs

        index = int(segment_index)
        if index <= 0:
            raise ValueError("segment index must be positive")
        payload = normalize_video_jobs_payload(load_video_jobs())
        default_id = str(payload.get("default_model_id") or self.project_default_model_id()).strip()
        requested_override = str(model_id or "").strip()
        selected_id = requested_override or default_id
        if not self.registry.has(selected_id):
            raise ValueError(f"unsupported video model: {selected_id or '<empty>'}")

        segment = next(
            (
                item
                for item in payload.get("segments") or []
                if isinstance(item, dict) and int(item.get("segment_index") or 0) == index
            ),
            None,
        )
        if segment is None:
            raise ValueError(f"segment not found: {index}")
        status = str(segment.get("status") or "").strip().lower()
        current_render = None
        try:
            from generation.render_history import active_render

            current_render = active_render(segment)
        except Exception:
            current_render = None
        render_submission = (
            current_render.get("submission")
            if isinstance(current_render, dict) and isinstance(current_render.get("submission"), dict)
            else {}
        )
        active_provider_id = str(
            segment.get("task_id")
            or segment.get("prompt_id")
            or render_submission.get("task_id")
            or render_submission.get("prompt_id")
            or ""
        ).strip()
        if status in {"running", "pending", "queued"} and active_provider_id:
            raise ValueError("cannot change a model while its submitted task is still active")

        current_id = resolved_model_id(segment, payload)
        if current_render:
            segment["stale_render_id"] = str(current_render.get("render_id") or "")
        segment["model_override"] = requested_override or None
        segment["model_id"] = selected_id
        spec = self.registry.get(selected_id).spec
        segment["resolved_model"] = {"id": spec.id, "revision": spec.revision}
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        job["model_id"] = selected_id
        segment["job"] = job

        if selected_id != current_id:
            existing_video = str(segment.get("video_path") or job.get("video_path") or "").strip()
            if existing_video:
                segment["stale_video_path"] = existing_video
            # ``model_id`` describes the next render.  Leaving an old LTX
            # file in the active top-level result would make the normalizer
            # incorrectly label it as H3.  The immutable render snapshot keeps
            # it recoverable instead.
            segment.pop("video_path", None)
            job.pop("video_path", None)
            segment["stale_reason"] = "model changed"
            segment["status"] = "needs_regenerate"
            segment["submitted"] = False
            segment["backend"] = ""
            segment["task_id"] = ""
            segment.pop("workflow_path", None)
            segment.pop("workflow", None)
            segment["requested_media_spec"] = {}
            segment["actual_media_spec"] = {}
            payload.pop("final_video_path", None)

        save_video_jobs(payload)
        return payload

    @staticmethod
    def _normalise_asset_manifest(value: Any) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type") or "").strip().lower()
            path = str(item.get("path") or item.get("relative_path") or "").strip()
            if media_type not in {"image", "video", "audio"}:
                raise ValueError(f"asset {index} type must be image, video, or audio")
            if not path:
                raise ValueError(f"asset {index} path is required")
            key = (media_type, path.casefold())
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    # H3 persists this field as ``asset_id`` while the browser
                    # sends the same manifest back as ``id`` in a few paths.
                    # Treat both spellings as the same stable identity.  If we
                    # fall back to the list position here, opening and saving
                    # the fullscreen editor can silently rename every asset
                    # even though the user did not change a single picture.
                    "id": str(item.get("id") or item.get("asset_id") or f"asset_{index}").strip()
                    or f"asset_{index}",
                    "type": media_type,
                    "path": path,
                    "role": str(item.get("role") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                    "order": int(item.get("order") or index),
                    "source": str(item.get("source") or "segment").strip() or "segment",
                    # These remain semantic model inputs.  They are not
                    # workflow nodes: H3's adapter decides whether a video's
                    # embedded audio becomes a paired reference input.
                    "use_video_audio": bool(item.get("use_video_audio") or item.get("include_video_audio")),
                    "metadata": dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {},
                }
            )
        return normalized

    def validate_segment_generation_config(self, segment_index: int, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return model-specific deterministic validation in an API-safe form."""
        from workflow.video_merge import load_video_jobs

        payload = normalize_video_jobs_payload(load_video_jobs())
        index = int(segment_index)
        segment = next(
            (item for item in payload.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == index),
            None,
        )
        if segment is None:
            raise ValueError(f"segment not found: {index}")
        selected_id = resolved_model_id(segment, payload)
        selected_config = config if isinstance(config, dict) else segment.get("generation_config")
        pipeline = self._pipeline_for(selected_id)
        validator = getattr(pipeline, "validate_generation_config", None)
        if not callable(validator):
            return {"ok": True, "errors": [], "warnings": [], "model_id": selected_id}
        try:
            validation = validator(selected_config or {}, segment=segment)
        except TypeError:
            validation = validator(selected_config or {})
        if isinstance(validation, dict):
            return {"model_id": selected_id, "ok": not validation.get("errors"), **validation}
        if isinstance(validation, (list, tuple)):
            return {"model_id": selected_id, "ok": not validation, "errors": list(validation), "warnings": []}
        return {"model_id": selected_id, "ok": True, "errors": [], "warnings": []}

    @_serialized_submission
    def update_segment_generation_config(
        self,
        segment_index: int,
        *,
        generation_mode: str | None = None,
        asset_manifest: list[dict[str, Any]] | None = None,
        prompt_override: str | None = None,
        force_save: bool = False,
    ) -> dict[str, Any]:
        """Persist declarative per-segment model inputs, never workflow nodes.

        The browser submits only media intent and a prompt override.  Each
        model adapter turns that into its own slots, tags, and graph nodes.
        """
        from workflow.video_merge import load_video_jobs, save_video_jobs

        index = int(segment_index)
        if index <= 0:
            raise ValueError("segment index must be positive")
        payload = normalize_video_jobs_payload(load_video_jobs())
        segment = next(
            (item for item in payload.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == index),
            None,
        )
        if segment is None:
            raise ValueError(f"segment not found: {index}")
        if force_save:
            if resolved_model_id(segment, payload) != "minimax_h3_local_ref2va":
                raise ValueError("强制保存仅支持 H3 提示词")
            if asset_manifest is None or not str(prompt_override or "").strip():
                raise ValueError("强制保存需要当前素材列表和非空提示词")
        old_config = dict(segment.get("generation_config") or {})
        config = dict(old_config)
        if generation_mode is not None:
            config["generation_mode"] = str(generation_mode or "").strip() or "ref2va"
        config.setdefault("generation_mode", "ref2va")
        if asset_manifest is not None:
            config["asset_manifest"] = self._normalise_asset_manifest(asset_manifest)
        else:
            config["asset_manifest"] = self._normalise_asset_manifest(config.get("asset_manifest"))
        if prompt_override is not None:
            submitted_prompt = str(prompt_override or "").strip()
            job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
            compiled_snapshot = str(
                segment.get("compiled_prompt_snapshot")
                or job.get("final_prompt")
                or job.get("prompt")
                or ""
            ).strip()
            pipeline = self._pipeline_for(resolved_model_id(segment, payload))
            reconcile = getattr(pipeline, "reconcile_generation_config", None)
            if force_save:
                # The editor's current numbering is authoritative. Persist this
                # manifest as the baseline for subsequent ordinary saves.
                config["prompt_override"] = submitted_prompt
                config.pop("asset_match_signature", None)
            elif callable(reconcile) and submitted_prompt:
                config = reconcile(segment, old_config, config, submitted_prompt)
            elif submitted_prompt and submitted_prompt != compiled_snapshot:
                config["prompt_override"] = submitted_prompt
            else:
                config.pop("prompt_override", None)
        segment["generation_config"] = config
        from generation.deferred_regeneration import defer_regeneration_for_active_submission

        active_submission_preserved = defer_regeneration_for_active_submission(
            segment,
            reason="prompt changed",
        )
        if not active_submission_preserved:
            segment["status"] = "needs_regenerate"
            segment["submitted"] = False
            segment.pop("workflow_path", None)
            segment.pop("workflow", None)
        payload.pop("final_video_path", None)
        save_video_jobs(payload)
        return payload


_SERVICE: GenerationService | None = None


def get_generation_service() -> GenerationService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = GenerationService()
    return _SERVICE
