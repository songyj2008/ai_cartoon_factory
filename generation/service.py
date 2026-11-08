"""Model-neutral entry points used by UI and orchestration code."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from contextlib import nullcontext
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


def _pinned_runtime(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from services.context import pin_runtime
        with pin_runtime():
            return fn(*args, **kwargs)
    return wrapped


class GenerationService:
    def preview_shot_edits(self, payload):
        from generation.pipelines.minimax_h3.shot_edit_service import preview
        return preview(payload)

    def commit_shot_edits(self, payload):
        from generation.pipelines.minimax_h3.shot_edit_service import commit
        return commit(payload)

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

    @_pinned_runtime
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
        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs, video_jobs_lock

        from pipeline.planning_budget import budget_report
        report = budget_report(beats_data)
        if report['outside_budget']:
            from services.logger import log
            log('参考预算提示：' + report['message'] + '；继续按完整内容生成。', 'WARN')
        groups = self._model_groups_for_beats(beats_data, segment_indices)
        for model_id, indices in groups.items():
            from generation.model_rules import assert_beats_compatible

            assert_beats_compatible(beats_data, model_id)
            pipeline = self._pipeline_for(model_id)
            # A pipeline validates its own planning rules.  H3's 4-15 second
            # rules therefore do not leak into the LTX path.
            with (nullcontext() if model_id == 'minimax_h3_local_ref2va' else video_jobs_lock()):
                pipeline.generate(
                    beats_data,
                    submit=submit,
                    workflow_only=workflow_only,
                    segment_indices=indices,
                    merge_after=False,
                )

        with video_jobs_lock():
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

    @_pinned_runtime
    def regenerate_segment_prompt(self, beats_data: dict[str, Any], segment_index: int) -> dict[str, Any]:
        """Recompile one prompt with the model configured for that segment.

        The service owns model selection only. Each registered pipeline owns
        the prompt state that must be reset before its compiler runs.
        """
        from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock

        index = int(segment_index)
        if index <= 0:
            raise ValueError("segment index must be positive")
        from services.context import get_project_dir
        from generation.pipelines.minimax_h3.shot_edit_service import _recover
        @_serialized_submission
        def begin_regeneration():
            import copy
            _recover(get_project_dir())
            payload = normalize_video_jobs_payload(load_video_jobs())
            segment = next((item for item in payload.get('segments') or []
                            if isinstance(item, dict) and int(item.get('segment_index') or 0) == index), None)
            if segment is None:
                raise ValueError(f'segment not found: {index}')
            model_id = resolved_model_id(segment, payload)
            pipeline = self._pipeline_for(model_id)
            reset_prompt = getattr(pipeline, 'reset_prompt_for_regeneration', None)
            if not callable(reset_prompt):
                raise TypeError(f'video model does not implement prompt regeneration: {model_id}')
            previous = ((segment.get('generation_config') or {}).get('prompt_override'),
                        copy.deepcopy(segment.get('h3_prompt_plan')), segment.get('h3_prompt_source'))
            reset_prompt(segment)
            save_video_jobs(payload)
            return pipeline, reset_prompt, previous

        pipeline, reset_prompt, previous = begin_regeneration()
        previous_override, previous_plan, previous_source = previous
        try:
            regenerate_source = getattr(pipeline, "regenerate_prompt_source", None)
            if callable(regenerate_source):
                regenerate_source(beats_data, index)
                # A fullscreen H3 autosave may have started just before the model
                # request and completed while it was running. Reload the newest
                # manifest (the user's picker is authoritative), but clear any
                # stale prompt override that request wrote back before compiling.
                with video_jobs_lock():
                    latest_payload = normalize_video_jobs_payload(load_video_jobs())
                    latest_segment = next((item for item in latest_payload.get('segments') or []
                                           if isinstance(item, dict) and int(item.get('segment_index') or 0) == index), None)
                    if latest_segment is None:
                        raise ValueError(f'segment disappeared during prompt regeneration: {index}')
                    reset_prompt(latest_segment)
                    save_video_jobs(latest_payload)
            return self._run_preparation_by_model(
                beats_data,
                submit=False,
                workflow_only=True,
                segment_indices={index},
                merge_after=False,
            )

        except Exception:
            # A rejected plan must not destroy the last usable hand-edited
            # screenplay. Restore only fields this operation cleared, keeping
            # any newer explicit prompt or picker selection authoritative.
            with video_jobs_lock():
                latest = normalize_video_jobs_payload(load_video_jobs())
                current = next((item for item in latest.get("segments") or []
                                if isinstance(item, dict) and int(item.get("segment_index") or 0) == index), None)
                if current is not None:
                    config = dict(current.get("generation_config") or {})
                    if previous_override and not config.get("prompt_override"):
                        config["prompt_override"] = previous_override
                        current["generation_config"] = config
                    if previous_plan is not None and "h3_prompt_plan" not in current:
                        current["h3_prompt_plan"] = previous_plan
                    if previous_source is None:
                        current.pop("h3_prompt_source", None)
                    else:
                        current["h3_prompt_source"] = previous_source
                    save_video_jobs(latest)
            raise

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

    @_pinned_runtime
    def submit_saved_segments(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        if channel_id:
            from generation.channels import get_channel
            from workflow.video_merge import load_video_jobs

            selected = {int(value) for value in segment_indices or set()}
            if len(selected) != 1:
                raise ValueError("选择视频渠道时必须指定一个分段")
            index = next(iter(selected))
            payload = normalize_video_jobs_payload(load_video_jobs())
            segment = next((item for item in payload.get("segments") or [] if isinstance(item, dict)
                            and int(item.get("segment_index") or 0) == index), None)
            if segment is None:
                raise ValueError(f"segment not found: {index}")
            channel = get_channel(channel_id)
            reason = channel.unavailable_reason(segment, payload)
            if reason:
                raise ValueError(reason)
            return channel.submit(self, index, wait=wait)
        return self._submit_saved_direct(segment_indices, merge_after=merge_after, wait=wait)

    def _submit_saved_direct(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
        backend: str | None = None,
        expected_backend: str | None = None,
    ) -> dict[str, Any]:
        # Dispatch each model's selected segments independently.  The LTX-only
        # release has one group; later models add a group without changing the
        # LTX pipeline or submitter.
        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs

        payload = normalize_video_jobs_payload(load_video_jobs())
        segments = [item for item in payload.get("segments") or [] if isinstance(item, dict)]
        selected = {int(value) for value in segment_indices} if segment_indices else None
        if expected_backend:
            owned = {int(item.get("segment_index") or 0) for item in segments
                     if str(item.get("backend") or "").strip().lower() == expected_backend}
            if selected is None or not selected <= owned:
                raise ValueError(f"queued segments no longer belong to {expected_backend}")
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
            options = {"backend": backend} if backend else {}
            self._pipeline_for(model_id).submit_saved(indices, merge_after=False, wait=wait, **options)

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
        if value is not None and not isinstance(value, list):
            raise ValueError('参考素材清单无效，未覆盖已保存的配置')
        items = value if value is not None else []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError("参考素材条目无效，未覆盖已保存的配置")
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
    def update_segment_duration(self, segment_index: int, duration_sec, *, patch_workflows: bool = True) -> dict:
        """Use the same beat-duration synchronizer as the Beats editor."""
        import json
        import math
        from services.context import get_project_dir, assert_current_runtime_writable
        from ui.project_handlers import _beat_duration_seconds, _sync_video_jobs_with_beat_durations

        assert_current_runtime_writable("修改分段时长")
        value = float(duration_sec)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("时长必须为正数")
        from generation.job_schema import resolved_model_id
        duration = value
        root = get_project_dir()
        beats_path = root / "beats.json"
        data = json.loads(beats_path.read_text(encoding="utf-8-sig"))
        beats = data.get("beats") or []
        if not 0 < segment_index <= len(beats) or not isinstance(beats[segment_index - 1], dict):
            raise ValueError("找不到分段对应的 Beat")
        jobs_path = root / "video_jobs.json"
        jobs = json.loads(jobs_path.read_text(encoding="utf-8-sig"))
        if not any(int(s.get("segment_index") or 0) == segment_index for s in jobs.get("segments", [])):
            raise ValueError("找不到当前分段")
        beat = beats[segment_index - 1]
        current_segment = next(s for s in jobs.get('segments', []) if int(s.get('segment_index') or 0) == segment_index)
        from workflow.runninghub_queue import has_active_submission
        if has_active_submission(current_segment):
            raise ValueError('视频正在生成，请完成后再修改分段时长')
        if resolved_model_id(current_segment, jobs) != 'minimax_h3_local_ref2va':
            duration = _beat_duration_seconds(value)
        else:
            # Draft timing is not a workflow constraint; preserve fractional edits.
            from generation.pipelines.minimax_h3.planning_drafts import refresh_saved_prompt
            current_segment['generation_config'] = {**(current_segment.get('generation_config') or {}), 'duration_sec': duration}
            current_segment.setdefault('job', {})['duration_sec'] = duration
            refresh_saved_prompt(current_segment, str(current_segment['generation_config'].get('prompt_override') or current_segment.get('compiled_prompt_snapshot') or current_segment['job'].get('final_prompt') or ''))
        beat["estimated_duration_sec"] = duration
        if "duration_sec" in beat:
            beat["duration_sec"] = duration
        data["total_duration_sec"] = sum(_beat_duration_seconds(b.get("estimated_duration_sec") or b.get("duration_sec") or 15) for b in beats)
        if resolved_model_id(current_segment, jobs) == 'minimax_h3_local_ref2va':
            data['total_duration_sec'] = sum(float(b.get('estimated_duration_sec') or b.get('duration_sec') or 0) for b in beats)
        if not data.get("target_duration_sec"):
            data["target_duration_sec"] = data["total_duration_sec"]
        if resolved_model_id(current_segment, jobs) == 'minimax_h3_local_ref2va':
            from workflow.video_merge import save_video_jobs
            current_segment.pop('workflow_path', None)
            if not current_segment.get('submitted'):
                current_segment['status'] = 'needs_regenerate'
            jobs['total_duration_sec'] = sum(float((s.get('job') or {}).get('duration_sec') or 0) for s in jobs['segments'])
            save_video_jobs(jobs)
            result = json.dumps(jobs, ensure_ascii=False)
        else:
            result = _sync_video_jobs_with_beat_durations(data, jobs_path, patch_workflows=patch_workflows)
        beats_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(result)

    @_serialized_submission
    def _prepare_h3_prompt(self, segment, old_config, config, submitted_prompt, *, force_save=False, preserve_revision=False):
        from generation.pipelines.minimax_h3.auto_binder import enrich_asset_manifest_metadata
        from services.character_voice import refresh_character_voices
        from services.context import ASSETS_DIR
        from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
        from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
        from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler

        config = dict(config)
        saved_prompt = str((old_config or {}).get('prompt_override') or segment.get('compiled_prompt_snapshot') or '').strip()
        if (segment.get('scene_context_id') and submitted_prompt is not None
                and str(submitted_prompt).strip() == saved_prompt
                and H3GenerationConfig.from_mapping(old_config).asset_manifest
                == H3GenerationConfig.from_mapping(config).asset_manifest):
            # Save-before-action must not enrich, rename, revoice or recompile
            # an unchanged, already authored continuous-scene screenplay.
            return {**config, 'prompt_override': saved_prompt}
        manifest = enrich_asset_manifest_metadata(config.get("asset_manifest") or [])
        config["asset_manifest"] = manifest if segment.get('h3_prompt_source') == 'shot_edit' else refresh_character_voices(manifest, ASSETS_DIR)
        if submitted_prompt is not None:
            text = str(submitted_prompt or "").strip()
            if force_save or preserve_revision:
                config["prompt_override"] = text
                config.pop("asset_match_signature", None)
            elif text:
                config = self._pipeline_for("minimax_h3_local_ref2va").reconcile_generation_config(segment, old_config, config, text)
            else:
                config.pop("prompt_override", None)
        if config.get("prompt_override"):
            from generation.pipelines.minimax_h3.shot_editor import inspect_prompt, dialogue_units
            from services.character_voice import attach_character_voices
            from services.project_bible import load_project_bible
            current = list(config.get('asset_manifest') or [])
            inspected = inspect_prompt(config['prompt_override'], 'voice-save', current)
            if inspected.get('verified'):
                units = [unit for shot in inspected['document']['shots'] for unit in dialogue_units(shot)]
                voiced = attach_character_voices(current, {'dialogue_units': units}, ASSETS_DIR,
                                                 load_project_bible())
                # Saving does not implicitly delete or renumber selected audio.
                existing_ids = {a.get('asset_id') or a.get('id') for a in current}
                config['asset_manifest'] = current + [a for a in voiced
                    if (a.get('asset_id') or a.get('id')) not in existing_ids]
            config["prompt_override"] = H3PromptCompiler.compact_reference_sections(
                config["prompt_override"], H3AssetBinder().bind(H3GenerationConfig.from_mapping(config))
            )
        return config

    def preview_segment_prompt(self, segment_index, prompt_override, asset_manifest=None, *, force_save=False, prompt_provenance=None):
        from workflow.video_merge import load_video_jobs
        from generation.job_schema import resolved_model_id
        jobs = load_video_jobs()
        segment = next((item for item in jobs.get("segments") or [] if int(item.get("segment_index") or 0) == int(segment_index)), None)
        if segment is None:
            raise ValueError("segment not found")
        model_id = resolved_model_id(segment, jobs)
        pipeline = self._pipeline_for(model_id)
        old = dict(segment.get("generation_config") or {})
        config = dict(old)
        config["asset_manifest"] = self._normalise_asset_manifest(asset_manifest if asset_manifest is not None else old.get("asset_manifest"))
        if model_id == "minimax_h3_local_ref2va":
            config = self._prepare_h3_prompt(segment, old, config, prompt_override, force_save=force_save,
                preserve_revision=(prompt_provenance or {}).get("source") == "web_revision")
            text = str(config.get("prompt_override") or "")
        else:
            text = str(prompt_override or "").strip()
        limit = pipeline.spec.capabilities.prompt_max_chars
        return {"prompt_chars": len(text), "prompt_max_chars": limit, "over_limit": bool(limit and len(text) > limit)}

    @_serialized_submission
    def update_segment_generation_config(
        self,
        segment_index: int,
        *,
        generation_mode: str | None = None,
        asset_manifest: list[dict[str, Any]] | None = None,
        prompt_override: str | None = None,
        prompt_provenance: dict[str, Any] | None = None,
        force_save: bool = False,
        base_revision: str | None = None,
        segment_uid: str | None = None,
    ) -> dict[str, Any]:
        """Persist declarative per-segment model inputs, never workflow nodes.

        The browser submits only media intent and a prompt override.  Each
        model adapter turns that into its own slots, tags, and graph nodes.
        """
        from workflow.video_merge import load_video_jobs, save_video_jobs
        from services.context import get_project_dir
        from generation.pipelines.minimax_h3.shot_edit_service import _recover
        _recover(get_project_dir())

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
        if segment_uid is not None and segment.get('segment_uid') != segment_uid:
            raise ValueError('目标分段已变化，当前草稿保留，请重新读取')
        if base_revision is not None:
            from generation.pipelines.minimax_h3.editor_draft import reference_revision
            if reference_revision(segment) != base_revision:
                raise ValueError('后台提示词或参考素材已变化，当前草稿保留，未覆盖新版本')
        if force_save:
            if resolved_model_id(segment, payload) != "minimax_h3_local_ref2va":
                raise ValueError("强制保存仅支持 H3 提示词")
            if asset_manifest is None or not str(prompt_override or "").strip():
                raise ValueError("强制保存需要当前素材列表和非空提示词")
        old_config = dict(segment.get("generation_config") or {})
        timing_source = copy.deepcopy(segment)
        preserve_revision = (prompt_provenance or {}).get("source") in {"web_revision", "duration_replan"}
        config = dict(old_config)
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        compiled_snapshot = str(
            segment.get("compiled_prompt_snapshot")
            or job.get("final_prompt")
            or job.get("prompt")
            or ""
        ).strip()
        old_prompt = str(old_config.get("prompt_override") or compiled_snapshot).strip()
        if generation_mode is not None:
            config["generation_mode"] = str(generation_mode or "").strip() or "ref2va"
        config.setdefault("generation_mode", "ref2va")
        if asset_manifest is not None:
            normalized_manifest = self._normalise_asset_manifest(asset_manifest)
            if normalized_manifest != self._normalise_asset_manifest(old_config.get("asset_manifest")):
                config["reference_selection_source"] = "manual"
                config["asset_manifest"] = normalized_manifest
            else:
                # An unchanged picker round-trip must not replace the saved
                # identity/metadata representation and invalidate scene facts.
                config["asset_manifest"] = (copy.deepcopy(old_config.get("asset_manifest") or [])
                    if segment.get('scene_context_id') and resolved_model_id(segment, payload) == 'minimax_h3_local_ref2va'
                    else normalized_manifest)
        else:
            config["asset_manifest"] = (copy.deepcopy(old_config.get("asset_manifest") or [])
                if segment.get('scene_context_id') and resolved_model_id(segment, payload) == 'minimax_h3_local_ref2va'
                else self._normalise_asset_manifest(config.get("asset_manifest")))
        if resolved_model_id(segment, payload) == "minimax_h3_local_ref2va":
            if (prompt_provenance or {}).get('source') == 'duration_replan':
                from generation.pipelines.minimax_h3.prompt_retimer import validate_timing_edit
                validate_timing_edit(old_prompt,str(prompt_override))
                if self._normalise_asset_manifest(config['asset_manifest']) != self._normalise_asset_manifest(old_config.get('asset_manifest')):
                    raise ValueError('时长规划不能修改素材绑定')
                config['prompt_override']=str(prompt_override)
            else:
                config = self._prepare_h3_prompt(segment, old_config, config, prompt_override,
                    force_save=force_save, preserve_revision=preserve_revision)
        elif prompt_override is not None:
            submitted_prompt = str(prompt_override or "").strip()
            pipeline = self._pipeline_for(resolved_model_id(segment, payload))
            reconcile = getattr(pipeline, "reconcile_generation_config", None)
            if force_save or preserve_revision:
                config["prompt_override"] = submitted_prompt
                config.pop("asset_match_signature", None)
            elif callable(reconcile) and submitted_prompt:
                config = reconcile(segment, old_config, config, submitted_prompt)
            elif submitted_prompt and submitted_prompt != compiled_snapshot:
                config["prompt_override"] = submitted_prompt
            else:
                config.pop("prompt_override", None)
        if resolved_model_id(segment, payload) == 'minimax_h3_local_ref2va':
            from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
            from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
            parsed_config = H3GenerationConfig.from_mapping(config)
            report = H3Ref2VAValidator().validate(parsed_config)
            H3Ref2VAValidator().validate_binding_contract(segment, parsed_config, report=report)
            # This is an editor save, not a video submission. Keep positive
            # timing drafts editable; refresh_saved_prompt records these limits
            # as blocking diagnostics and provider submission remains strict.
            report.errors = [issue for issue in report.errors
                             if issue.code not in {'duration_not_integer', 'duration_out_of_range'}]
            report.raise_for_errors()
        if prompt_override is not None:
            current_prompt = str(config.get("prompt_override") or compiled_snapshot).strip()
            # Shot identities survive text/reference edits. Content sync checks
            # the actual Shot structure; a full-text hash is not an identity.
            segment["prompt_saved"] = True
            current_info = old_config.get("prompt_version_info") if isinstance(old_config.get("prompt_version_info"), dict) else {}
            latest_revision = old_config.get("latest_prompt_revision") if isinstance(old_config.get("latest_prompt_revision"), dict) else {}
            previous_version = max(
                int(current_info.get("version") or 0),
                int(latest_revision.get("version") or 0),
            )
            provenance = dict(prompt_provenance or {})
            is_revision = provenance.get("source") == "web_revision"
            if is_revision:
                version = max(1, int(provenance.get("version") or previous_version + 1))
                updated_at = provenance.get("updated_at") or datetime.now(timezone.utc).isoformat()
                if isinstance(updated_at, (int, float)):
                    updated_at = datetime.fromtimestamp(updated_at, tz=timezone.utc).isoformat()
                info = {
                    "version": version,
                    "updated_at": updated_at,
                    "provider": str(provenance.get("provider") or "网页 LLM"),
                    "source": "web_revision",
                    "task_id": str(provenance.get("task_id") or ""),
                }
                config["prompt_version_info"] = info
                config["latest_prompt_revision"] = {**info, "prompt": current_prompt}
            elif current_prompt != old_prompt or not current_info:
                config["prompt_version_info"] = {
                    "version": previous_version + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "provider": "时长规划" if provenance.get("source") == "duration_replan" else "手动编辑",
                    "source": "duration_replan" if provenance.get("source") == "duration_replan" else "manual",
                    "task_id": "",
                }
        segment["generation_config"] = config
        content_pair = None
        if resolved_model_id(segment, payload) == 'minimax_h3_local_ref2va' and prompt_override is not None:
            import json
            import uuid
            from services.context import get_project_dir
            from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
            from generation.pipelines.minimax_h3.shot_edit_service import _recover
            root = get_project_dir()
            _recover(root)
            beats_path = root / 'beats.json'
            text = str(config.get('prompt_override') or compiled_snapshot)
            segment['h3_editor_prompt'] = text
            if beats_path.is_file():
                beats = json.loads(beats_path.read_text(encoding='utf-8-sig'))
                rows = beats.get('beats') or []
                uid = segment.get('beat_uid')
                matches = [b for b in rows if uid and b.get('beat_uid') == uid]
                if not uid and len(rows) == len(payload.get('segments') or []) and 0 < index <= len(rows):
                    matches = [rows[index - 1]]
                    uid = matches[0].get('beat_uid') or 'beat_' + uuid.uuid4().hex
                    matches[0]['beat_uid'] = segment['beat_uid'] = uid
                if len(matches) == 1:
                    updated_beat, updated_segment, diagnostics = sync_segment_content(
                        matches[0], segment, prompt=text, generation_config=config)
                    matches[0].clear()
                    matches[0].update(updated_beat)
                    segment.clear()
                    segment.update(updated_segment)
                    content_pair = (root, beats)
                else:
                    segment['h3_content_sync'] = {'verified': False, 'issues': ['无法唯一定位对应 Beat，全文已保留']}
            else:
                segment['h3_content_sync'] = {'verified': False, 'issues': ['缺少 Beat 文件，全文已保留']}
            from generation.pipelines.minimax_h3.planning_drafts import refresh_saved_prompt
            refresh_saved_prompt(segment, str(config.get('prompt_override') or compiled_snapshot))
            for message in (segment.get('h3_content_sync') or {}).get('issues', []):
                segment.setdefault('planning_issues', []).append(
                    {'code': 'unverified_prompt_structure', 'message': message, 'blocking': True})
                segment['planning_state'] = 'warning'
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
        if (prompt_provenance or {}).get('source') == 'duration_replan':
            from generation.pipelines.minimax_h3.continuity.timing import advance_timing_record
            advance_timing_record(segment, before=timing_source)
        payload.pop("final_video_path", None)
        if content_pair:
            from generation.pipelines.minimax_h3.segment_content_sync import write_content_pair
            write_content_pair(content_pair[0], beats=content_pair[1], jobs=payload)
        else:
            save_video_jobs(payload)
        return payload


_SERVICE: GenerationService | None = None


def get_generation_service() -> GenerationService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = GenerationService()
    return _SERVICE
