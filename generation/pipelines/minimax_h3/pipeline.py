"""Independent ComfyUI-workflow pipeline for MiniMax H3 Ref2VA.

Nothing here imports Licon prompt, asset, or workflow code.  The pipeline is
registered separately by the model registry and writes a model-specific job
record for every selected segment.
"""
from __future__ import annotations

import copy
import json
import logging
from functools import wraps
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_logger = logging.getLogger(__name__)

from generation.contracts import ModelCapabilities, ModelSpec
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig, H3_REF2VA_GENERATION_MODE, H3_REF2VA_MODEL_ID
from generation.pipelines.minimax_h3.input_stager import H3ComfyInputStager
from generation.pipelines.minimax_h3.planner import H3SegmentPlanner
from generation.pipelines.minimax_h3.rules import build_h3_rule_set
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator, validate_generation_config
from generation.pipelines.minimax_h3.workflow_adapter import H3Ref2VAWorkflowAdapter, H3Ref2VAWorkflowProfile, H3WorkflowBuild


H3_FPS = 24


def _serialize_submission(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from services.context import pin_runtime
        from workflow.video_merge import video_jobs_lock
        from generation.account_queue import admission_lock
        with pin_runtime(), admission_lock, video_jobs_lock():
            return fn(*args, **kwargs)
    return wrapped


def _save_prepared_changes(before, prepared, rebuilt_indices):
    """Commit unlocked preparation without replacing concurrent job updates."""
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from workflow.runninghub_queue import has_active_submission

    absent = object()
    with video_jobs_lock():
        latest = load_video_jobs()
        old_rows = {int(s.get('segment_index') or 0): s for s in before.get('segments', [])}
        live_rows = {int(s.get('segment_index') or 0): s for s in latest.get('segments', [])}
        changes = []
        for row in prepared.get('segments', []):
            index = int(row.get('segment_index') or 0)
            old, live = old_rows.get(index), live_rows.get(index)
            if row == old:
                continue
            if index in rebuilt_indices and live and has_active_submission(live):
                # An accepted task is authoritative even if preparation began
                # just before another click acquired the submission lock.
                continue
            if old is None:
                if live is not None:
                    raise ValueError('分段已在准备期间新增，请重新生成当前段')
                changes.append((index, None, copy.deepcopy(row)))
                continue
            if live is None:
                raise ValueError('分段已在准备期间移除，未覆盖当前项目')
            if index in rebuilt_indices and live != old and live != row:
                raise ValueError('分段已在准备期间变化，未覆盖当前稿或视频')
            fields = {k: row.get(k, absent) for k in set(old) | set(row)
                      if old.get(k, absent) != row.get(k, absent)}
            if any(live.get(k, absent) != old.get(k, absent)
                   and live.get(k, absent) != value for k, value in fields.items()):
                raise ValueError('人物状态已在准备期间变化，未覆盖新版本')
            changes.append((index, live, fields))
        for index, live, fields in changes:
            if live is None:
                live_rows[index] = fields
                continue
            for key, value in fields.items():
                if value is absent:
                    live.pop(key, None)
                else:
                    live[key] = copy.deepcopy(value)
        latest['segments'] = [live_rows[index] for index in sorted(live_rows)]
        latest['generation_mode'] = prepared.get('generation_mode', latest.get('generation_mode', 'model_factory_segmented'))
        latest['segment_count'] = len(latest['segments'])
        latest['total_duration_sec'] = sum(float((s.get('job') or {}).get('duration_sec') or 0)
                                           for s in latest['segments'])
        save_video_jobs(latest)
        return latest


def _submission_source_signature(segment):
    """Exclude provider progress while guarding the exact prepared video input."""
    from .editor_draft import snapshot
    from .continuity.checkpoints import digest
    draft = snapshot(segment)
    return digest({'draft': {key: draft[key] for key in ('prompt', 'asset_manifest', 'duration_sec')},
                   'job': segment.get('job'),
                   'workflow_path': segment.get('workflow_path'),
                   'workflow_source_revision': segment.get('workflow_source_revision'),
                   'active_render_id': segment.get('active_render_id'),
                   'planning_state': segment.get('planning_state'),
                   'planning_issues': segment.get('planning_issues')})


def _beat_asset_signature(beat: Mapping[str, Any]) -> str:
    """Fingerprint a Beat's matched assets so a persisted manifest can be
    detected as stale after the Beat is restructured (split/insert/delete or a
    full regenerate).

    The signature is stored on ``generation_config.asset_match_signature`` and
    survives the ``H3GenerationConfig`` round-trip through its ``extra`` field.
    """
    auto = beat.get("auto_asset_match") if isinstance(beat, Mapping) else None
    auto = auto if isinstance(auto, Mapping) else {}
    identities = [
        str(entry.get("asset_identity_id") or entry.get("identity_id") or "").strip()
        for entry in (auto.get("character_images") or [])
        if isinstance(entry, Mapping)
    ]
    identities = [value for value in identities if value]
    if not identities:
        identities = [
            str(entry.get("asset_identity_id") or entry.get("identity_id") or "").strip()
            for entry in (auto.get("characters") or [])
            if isinstance(entry, Mapping)
        ]
        identities = [value for value in identities if value]
    backgrounds = [
        str(bg.get("asset_background_id") or bg.get("background_id") or "").strip()
        for bg in (auto.get("backgrounds") or [])
        if isinstance(bg, Mapping)
    ]
    backgrounds = [value for value in backgrounds if value]
    return json.dumps(
        {"characters": sorted(set(identities)), "backgrounds": sorted(set(backgrounds))},
        ensure_ascii=False,
        sort_keys=True,
    )


@dataclass
class H3VideoJob:
    """The minimal provider-facing contract for an assembled H3 workflow."""

    prompt: str
    duration_sec: float
    aligned_frame_count: int
    output_prefix: str
    part_id: str
    asset_manifest: list[dict[str, Any]] = field(default_factory=list)
    asset_slots: list[dict[str, Any]] = field(default_factory=list)
    model_id: str = H3_REF2VA_MODEL_ID
    model_revision: str = "1"
    generation_mode: str = H3_REF2VA_GENERATION_MODE
    workflow_profile: str = ""
    fps: int = H3_FPS

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "final_prompt": self.prompt,
            "duration_sec": self.duration_sec,
            "fps": self.fps,
            "total_frames": self.aligned_frame_count,
            "aligned_frame_count": self.aligned_frame_count,
            "output_prefix": self.output_prefix,
            "part_id": self.part_id,
            "asset_manifest": copy.deepcopy(self.asset_manifest),
            "asset_slots": copy.deepcopy(self.asset_slots),
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "generation_mode": self.generation_mode,
            "workflow_profile": self.workflow_profile,
        }


class MiniMaxH3Ref2VAPipeline:
    """Model-factory implementation for H3 reference-to-video."""

    def __init__(self, spec: ModelSpec | None = None) -> None:
        from services.context import CONFIG

        self._config = CONFIG
        self._model_config = self._configured_model()
        if spec is None:
            spec = ModelSpec(
                id=H3_REF2VA_MODEL_ID,
                display_name=str(self._model_config.get("display_name") or "MiniMax H3 / Ref2VA (local)"),
                pipeline_key=str(self._model_config.get("pipeline") or "minimax_h3_ref2va"),
                revision=str(self._model_config.get("revision") or "h3-base-ref2va"),
                capabilities=ModelCapabilities(
                    duration_min_sec=4,
                    duration_max_sec=15,
                    default_fps=H3_FPS,
                    fixed_fps=True,
                    reference_image_min=0,
                    reference_image_max=9,
                    reference_video_max=3,
                    reference_audio_max=3,
                    reference_file_max=12,
                    supports_image_references=True,
                    supports_video_references=True,
                    supports_audio_references=True,
                    produces_audio=True,
                    prompt_max_chars=7000,
                ),
                execution_profiles=dict(self._model_config.get("execution_profiles") or {}),
            )
        self.spec = spec
        self.rules = build_h3_rule_set(
            spec.id,
            spec.display_name,
            max(1, int(spec.capabilities.duration_min_sec)),
            int(spec.capabilities.duration_max_sec or 15),
        )
        raw_profile = self._model_config.get("workflow_profile")
        self.workflow_profile = (
            H3Ref2VAWorkflowProfile.from_mapping(raw_profile)
            if isinstance(raw_profile, Mapping)
            else None
        )
        self.planner = H3SegmentPlanner()
        self.validator = H3Ref2VAValidator()
        self.adapter = H3Ref2VAWorkflowAdapter(
            profile=self.workflow_profile,
            validator=self.validator,
            path_resolver=self._resolve_asset_path,
        )

    def _configured_model(self) -> dict[str, Any]:
        models = self._config.get("models") if isinstance(self._config.get("models"), dict) else {}
        model = models.get(H3_REF2VA_MODEL_ID) if isinstance(models, dict) else {}
        return dict(model or {})

    def plan(self, beats_data: dict[str, Any]):
        return self.planner.plan(beats_data)

    def validate_generation_config(self, config: dict[str, Any] | None, *, segment: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return API-safe H3 validation for a persisted segment config."""

        duration = None
        if isinstance(segment, Mapping):
            job = segment.get("job") if isinstance(segment.get("job"), Mapping) else {}
            duration = job.get("duration_sec") or segment.get("duration_sec") or segment.get("estimated_duration_sec")
        report = validate_generation_config(config, duration_sec=duration, check_files=False)
        if segment is not None:
            self.validator.validate_binding_contract(segment, H3GenerationConfig.from_mapping(config), report=report)
        return {
            "ok": report.is_valid,
            "errors": [issue.__dict__ for issue in report.errors],
            "warnings": [issue.__dict__ for issue in report.warnings],
            "model_id": self.spec.id,
            "generation_mode": H3_REF2VA_GENERATION_MODE,
        }

    def reconcile_generation_config(
        self,
        segment: Mapping[str, Any],
        old_config: Mapping[str, Any],
        new_config: Mapping[str, Any],
        submitted_prompt: str,
    ) -> dict[str, Any]:
        """Synchronize H3's compiler-owned prompt sections with its manifest."""
        from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler

        old_h3 = H3GenerationConfig.from_mapping(old_config)
        new_h3 = H3GenerationConfig.from_mapping(new_config)
        saved_prompt = str(old_config.get('prompt_override') or segment.get('compiled_prompt_snapshot') or '').strip()
        if str(submitted_prompt or '').strip() == saved_prompt and old_h3.asset_manifest == new_h3.asset_manifest:
            # Saving before another action is not a request to rename actors
            # or expand prose. Keep an already authored scene byte-stable.
            return {**new_config, 'prompt_override': saved_prompt}
        prompt = H3PromptReconciler(self.adapter.prompt_compiler).reconcile(
            submitted_prompt,
            old_h3,
            new_h3,
        )
        result = dict(new_config)
        if prompt:
            result["prompt_override"] = prompt
        else:
            result.pop("prompt_override", None)
        return result

    def prepare(self, beats_data: dict[str, Any], segment_indices: set[int] | None = None) -> dict[str, Any]:
        return self.generate(
            beats_data,
            submit=False,
            workflow_only=True,
            segment_indices=segment_indices,
            merge_after=False,
        )

    def reset_prompt_for_regeneration(self, segment: dict[str, Any]) -> None:
        """Let H3's compiler rebuild the prompt while preserving Ref2VA inputs."""

        config = dict(segment.get("generation_config") or {})
        if segment.get('h3_editor_prompt') or segment.get('h3_prompt_source') in {'shot_edit', 'duration_replan'}:
            segment['h3_editor_prompt'] = str(config.get('prompt_override') or segment.get('compiled_prompt_snapshot') or '')
        config.pop("prompt_override", None)
        segment["generation_config"] = config
        segment.pop("h3_prompt_plan", None)
        segment['h3_force_replan'] = True
        segment["h3_prompt_source"] = "current_beat"

    def generate(
        self,
        beats_data: dict[str, Any],
        *,
        submit: bool,
        workflow_only: bool,
        segment_indices: set[int] | None = None,
        merge_after: bool = False,
    ) -> dict[str, Any]:
        """Compile selected semantic beats into saved H3 workflow jobs."""

        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs

        if not isinstance(beats_data, Mapping):
            raise TypeError("beats_data must be an object")
        beats = beats_data.get("beats")
        if not isinstance(beats, list) or not beats:
            raise ValueError("beats data is empty; cannot generate H3 segments")
        selected = {int(index) for index in segment_indices or set() if int(index) > 0}
        if selected:
            unknown = sorted(index for index in selected if index > len(beats))
            if unknown:
                raise ValueError("selected H3 segment indexes do not exist: " + ", ".join(map(str, unknown)))
        else:
            selected = set(range(1, len(beats) + 1))

        original = copy.deepcopy(load_video_jobs())
        payload = copy.deepcopy(original)
        existing = {
            int(item.get("segment_index") or 0): item
            for item in payload.get("segments") or []
            if isinstance(item, dict) and int(item.get("segment_index") or 0) > 0
        }
        template = self._load_template()
        planning_only = bool(not submit or workflow_only)
        for index in sorted(selected):
            beat = beats[index - 1]
            if not isinstance(beat, Mapping):
                raise ValueError(f"Beat {index} is not an object")
            existing_segment = existing.get(index)
            from workflow.runninghub_queue import has_active_submission

            # Rebuilding clears provider IDs. Keep the accepted job intact so
            # retries and background dispatch cannot submit a second video.
            if existing_segment and has_active_submission(existing_segment):
                continue
            import uuid
            from .continuity.checkpoints import digest
            if existing_segment is None: existing_segment={'segment_uid':'segment_'+digest({'beat':beat.get('beat_uid') or beat.get('id'),'index':index})[:32]}
            uid=existing_segment.get('segment_uid') or 'segment_'+uuid.uuid4().hex
            existing_segment['segment_uid']=uid
            existing_segment.setdefault('segment_index', index)
            existing[index] = existing_segment
            payload['segments'] = [existing[i] for i in sorted(existing)]
            try:
                # Segment drafting is independent of continuity groups. Scene
                # initialization and predecessor state belong to explicit editor
                # actions and scene video preparation after manual assignment.
                segment = self._prepare_segment(
                    template, beat, index, existing_segment,
                    check_files=bool(submit and not workflow_only),
                    previous_segment_context=None,
                    planning_only=planning_only,
                )
            except (ValueError, TypeError) as exc:
                if submit and not workflow_only:
                    raise
                from .planning_drafts import draft_segment, issue
                segment = draft_segment(beat, index, existing_segment,
                    dict((existing_segment or {}).get('generation_config') or {'duration_sec': beat.get('estimated_duration_sec') or 0}),
                    {'shots': [], 'planning_issues': [issue('planning_failed', exc, blocking=True)]})
            segment.setdefault('segment_uid',uid)
            existing[index] = segment

        payload["segments"] = [existing[index] for index in sorted(existing)]
        payload["generation_mode"] = "model_factory_segmented"
        payload["segment_count"] = len(payload["segments"])
        payload["total_duration_sec"] = sum(
            float((item.get("job") or {}).get("duration_sec") or 0)
            for item in payload["segments"]
            if isinstance(item, Mapping)
        )
        for segment in payload['segments']:
            segment.pop('_continuity_use_draft', None)
        payload = _save_prepared_changes(original, payload, selected)

        if submit and not workflow_only:
            return self.submit_saved(selected, merge_after=merge_after, wait=True)
        if merge_after:
            final_path = merge_segment_videos(payload)
            payload["final_video_path"] = str(final_path) if final_path else ""
            save_video_jobs(payload)
        return payload

    def submit_saved(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Submit saved video inputs independently of optional scene analysis."""

        from services.context import pin_runtime
        with pin_runtime():
            guard = self._prepare_saved_submission_state(segment_indices)
            return self._submit_prepared_saved(segment_indices, merge_after=merge_after,
                                                wait=wait, backend=backend,
                                                preparation_guard=guard)

    def _prepare_saved_submission_state(self, segment_indices):
        from generation.job_schema import resolved_model_id
        from workflow.video_merge import load_video_jobs
        from workflow.runninghub_queue import has_active_submission
        from .continuity.revisions import revision
        from .editor_draft import snapshot

        selected = {int(index) for index in segment_indices or set() if int(index) > 0}
        before = copy.deepcopy(load_video_jobs())
        prepared = copy.deepcopy(before)
        indices = sorted(int(s.get('segment_index') or 0) for s in prepared.get('segments', [])
                   if (not selected or int(s.get('segment_index') or 0) in selected)
                   and resolved_model_id(s, prepared) == self.spec.id)
        rebuilt = set()
        for index in indices:
            segment = next(s for s in prepared['segments'] if int(s.get('segment_index') or 0) == index)
            if has_active_submission(segment):
                continue
            try:
                if segment.get('planning_state') == 'invalid':
                    raise ValueError('本段没有有效分镜，请先重新生成 Segment')
                if any(i.get('code') in ('invalid_structure', 'invalid_timeline', 'draft_compile',
                                         'unverified_prompt_structure')
                       for i in segment.get('planning_issues') or []):
                    raise ValueError('分镜结构或时间线无效，请编辑 Shot 或重新生成 Segment 后提交')
                import uuid
                current = segment
                uid = current.setdefault('segment_uid', 'segment_' + uuid.uuid4().hex)
                draft = snapshot(current)
                if not str(draft['prompt']).strip():
                    raise ValueError('没有已保存的视频提示词，请先保存提示词')
                rebuild_workflow = (not current.get('workflow_path')
                                    or current.get('workflow_source_revision') != revision(current)
                                    or str((current.get('job') or {}).get('final_prompt')
                                           or (current.get('job') or {}).get('prompt') or '') != draft['prompt']
                                    or H3GenerationConfig.from_mapping({'asset_manifest': (current.get('job') or {}).get('asset_manifest', [])}).to_dict()['asset_manifest']
                                    != H3GenerationConfig.from_mapping(draft).to_dict()['asset_manifest'])
                if rebuild_workflow:
                    source = copy.deepcopy(current)
                    source.pop('h3_force_replan', None)
                    source.pop('h3_auto_duration', None)
                    source['h3_editor_prompt'] = draft['prompt']
                    source['generation_config'] = dict(source.get('generation_config') or {},
                        prompt_override=draft['prompt'], duration_sec=draft['duration_sec'])
                    # Compile locally from saved inputs only. Old Beat content
                    # and continuity metadata cannot start an LLM on this path.
                    beat = {'id': index, 'estimated_duration_sec': draft['duration_sec']}
                    fresh = self._prepare_segment(self._load_template(), beat,
                                                  index, source, check_files=True)
                    fresh.setdefault('segment_uid', uid)
                    fresh['workflow_source_revision'] = revision(fresh)
                    current.clear()
                    current.update(fresh)
                    rebuilt.add(index)
            except Exception as exc:
                # Preparation failures must not turn an already successful
                # render into a failed video or erase its selected output.
                part_id = segment.get('part_id') or f'part_{index:03d}'
                raise RuntimeError(f'MiniMax H3 preparation failed: {part_id}: {exc}') from exc
        for segment in prepared['segments']:
            segment.pop('_continuity_use_draft', None)
        committed = (_save_prepared_changes(before, prepared, rebuilt)
                     if prepared != before else prepared)
        guard = {'targets': {int(s.get('segment_index') or 0): _submission_source_signature(s)
                             for s in committed['segments'] if int(s.get('segment_index') or 0) in indices}}
        return guard

    @_serialize_submission
    def _submit_prepared_saved(
        self,
        segment_indices: set[int] | None = None,
        *,
        merge_after: bool = False,
        wait: bool = True,
        backend: str | None = None,
        preparation_guard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit prepared workflows while serializing provider acceptance."""

        from generation.job_schema import resolved_model_id
        from workflow.video_merge import load_video_jobs, merge_segment_videos, save_video_jobs

        payload = load_video_jobs()
        selected = {int(index) for index in segment_indices or set() if int(index) > 0}
        targets = [
            segment
            for segment in payload.get("segments") or []
            if isinstance(segment, dict)
            and (not selected or int(segment.get("segment_index") or 0) in selected)
            and resolved_model_id(segment, payload) == self.spec.id
        ]
        if not targets:
            raise ValueError("no saved MiniMax H3 Ref2VA segment workflows are selected")

        from workflow.runninghub_queue import has_active_submission

        targets = [segment for segment in targets if not has_active_submission(segment)]
        if targets and preparation_guard:
            target_changed = any(preparation_guard['targets'].get(int(s.get('segment_index') or 0))
                                 != _submission_source_signature(s) for s in targets)
            if target_changed:
                raise ValueError('提交前稿件已变化，已有视频已保留，请重新提交当前稿')
        provider = self._submit_provider(backend) if backend else self._submit_provider()
        backend_name = str(getattr(provider, "name", "") or "").strip().lower()
        if backend_name == "runninghub":
            from generation.scheduler import GenerationScheduler
            from workflow.runninghub_queue import active_task_id

            # A repeated click must not replace an accepted remote task. Both
            # manual submission and background dispatch hold the job-file lock.
            targets = [
                segment for segment in targets
                if not (
                    str(segment.get("status") or "").lower() in {"running", "pending"}
                    and active_task_id(segment)
                )
            ]

            limit = GenerationScheduler().concurrency_limit(self.spec.id, backend_name)
            from generation.account_queue import admitted_indices
            admitted = admitted_indices(payload, targets, model_id=self.spec.id, limit=limit)
            queued_targets = [s for s in targets if int(s['segment_index']) not in admitted]
            targets = [s for s in targets if int(s['segment_index']) in admitted]
            for segment in queued_targets:
                segment["status"] = "queued"
                segment["backend"] = "runninghub"
                segment["submitted"] = False
                segment["error"] = ""
            if queued_targets:
                save_video_jobs(payload)
        failures: list[str] = []
        for segment in targets:
            part_id = str(segment.get("part_id") or f"part_{int(segment.get('segment_index') or 0):03d}")
            try:
                if segment.get('planning_state') == 'invalid':
                    raise ValueError('本段没有有效分镜，请先重新生成 Segment')
                if not segment.get('workflow_path'):
                    raise ValueError('工作流已在准备后变化，请重新提交当前稿')
                # Persist the accepted task immediately, then fill the next
                # slot. The server worker downloads completed remote results.
                self._submit_segment(provider, segment, wait=bool(wait) and backend_name != "runninghub")
                segment.pop('video_queue_entered_at', None)
            except Exception as exc:
                from workflow.runninghub_queue import is_queue_full_error

                if backend_name == "runninghub" and is_queue_full_error(exc):
                    segment["status"] = "queued"
                    segment["backend"] = "runninghub"
                    segment["submitted"] = False
                    segment["task_id"] = ""
                    segment["prompt_id"] = ""
                    segment["error"] = ""
                    continue
                segment["status"] = "failed"
                segment["error"] = str(exc)
                failures.append(f"{part_id}: {exc}")
            finally:
                save_video_jobs(payload)
        from workflow.runninghub_usage import refresh_runninghub_usage_summaries

        refresh_runninghub_usage_summaries(payload)
        save_video_jobs(payload)
        if failures:
            raise RuntimeError("MiniMax H3 submission failed: " + "; ".join(failures))
        if merge_after:
            from workflow.video_merge import request_video_merge
            request_video_merge(payload)
        return payload

    def _prepare_segment(
        self,
        template: dict[str, Any],
        beat: Mapping[str, Any],
        index: int,
        existing: Mapping[str, Any] | None,
        *,
        check_files: bool,
        previous_segment_context: Mapping[str, Any] | None = None,
        planning_only: bool = False,
    ) -> dict[str, Any]:
        from workflow.asset_matcher import match_beat_assets

        previous = copy.deepcopy(dict(existing or {}))
        usable_previous = copy.deepcopy(previous)
        force_replan = previous.pop('h3_force_replan', False)
        if force_replan:
            # A fresh Segment establishes a new authored baseline on its next
            # saved optimization. Keep the persisted one if preparation fails.
            previous.pop('h3_prompt_baseline', None)
            usable_previous.pop('h3_prompt_baseline', None)
            previous['generation_config'] = dict(previous.get('generation_config') or {})
            previous['generation_config'].pop('prompt_override', None)
            previous.pop('h3_prompt_plan', None)
            if previous.get('h3_prompt_source') != 'current_beat' and not previous.get('h3_editor_prompt'):
                previous['h3_prompt_source'] = 'current_beat'
        editor_prompt = str(previous.pop('h3_editor_prompt', '') or '').strip()
        dialogue_contract = None
        timing_only = previous.get('h3_prompt_source') == 'duration_replan'
        if previous.get('h3_prompt_source') == 'shot_edit':
            editor_prompt = str((previous.get('generation_config') or {}).get('prompt_override') or editor_prompt)
        if timing_only:
            editor_prompt = str((previous.get('generation_config') or {}).get('prompt_override') or editor_prompt)
        if editor_prompt:
            if beat.get('dialogue_authoritative'):
                # Parse the current editor, including any later retime/manual
                # edit, rather than importing an older Beat's speech contract.
                from .shot_editor import parse_prompt, dialogue_units
                parsed = parse_prompt(editor_prompt, 'editor',
                                      (previous.get('generation_config') or {}).get('asset_manifest') or [])
                dialogue_contract = [u for s in parsed['shots'] for u in dialogue_units(s, tagged=True)]
            # A screenplay edited in the modal supersedes Beat creative fields.
            # Keep identity/duration bookkeeping, never the old cast or dialogue.
            beat = {key: beat[key] for key in ('id', 'beat_uid', 'title', 'part_id', 'segment_id',
                    'estimated_duration_sec', 'duration_sec', 'source_excerpt', 'scene_contexts', 'scene_context_id') if key in beat}
            beat['plot'] = editor_prompt
        else:
            beat = match_beat_assets(
                dict(beat), model_id=self.spec.id,
                reference_role_limit=self.rules.max_reference_roles or 9,
            )
        previous_config = previous.get("generation_config") if isinstance(previous.get("generation_config"), Mapping) else None
        config_data = previous_config or {}
        if not config_data and isinstance(beat.get("generation_config"), Mapping):
            config_data = dict(beat.get("generation_config") or {})
        duration = self._duration_for(beat, config_data)
        if planning_only:
            from .planning_drafts import positive_duration
            duration = positive_duration(duration)
        else:
            self.planner.validate_duration(duration, index)
        config_data = dict(config_data)
        config_data.setdefault("generation_mode", H3_REF2VA_GENERATION_MODE)
        config_data["duration_sec"] = duration
        existing_manifest = config_data.get("asset_manifest")
        if isinstance(existing_manifest, list) and existing_manifest and not config_data.get('prompt_override'):
            try:
                from generation.pipelines.minimax_h3.auto_binder import enrich_asset_manifest_metadata

                config_data["asset_manifest"] = enrich_asset_manifest_metadata(existing_manifest)
            except Exception as exc:
                _logger.warning("H3 asset metadata enrichment skipped for segment %s: %s", index, exc)
        config = H3GenerationConfig.from_mapping(config_data)
        part_id = str(previous.get("part_id") or beat.get("part_id") or f"part_{index:03d}").strip() or f"part_{index:03d}"
        segment_id = str(previous.get("segment_id") or beat.get("segment_id") or f"segment_{index:03d}").strip() or f"segment_{index:03d}"

        # ── Auto-bind matched assets without inventing a default background ──
        authored_prompt = bool(editor_prompt or config.prompt_override)
        try:
            from generation.pipelines.minimax_h3.auto_binder import auto_bind_asset_manifest, merge_auto_backgrounds

            matched_manifest = auto_bind_asset_manifest(dict(beat)) or []
            current_signature = _beat_asset_signature(dict(beat))
            # Once a segment has a persisted manifest, the fullscreen picker
            # is the source of truth.  Prompt regeneration must never silently
            # re-add or drop a character/background merely because the Beat's
            # current auto-match changed (e.g. a text edit shrinks the matched
            # identity set without any structural split).  Auto-binding is only
            # an initial default for a segment that has never persisted an
            # asset_manifest; a genuine Beat restructure is handled by
            # beat_split_migration, which strips asset_manifest so this branch
            # re-binds from scratch.
            has_persisted_manifest = previous_config is not None and "asset_manifest" in previous_config
            known_auto = config_data.get('reference_selection_source') == 'automatic'
            # A saved screenplay owns its selected references even when those
            # references were initially automatic. Editor input deliberately
            # omits old Beat matching facts; an empty match is not a deletion.
            if not authored_prompt and (not has_persisted_manifest or known_auto):
                if config.asset_manifest:
                    config_data["asset_manifest"] = merge_auto_backgrounds(
                        list(config_data.get("asset_manifest") or []),
                        matched_manifest,
                    )
                elif matched_manifest:
                    config_data["asset_manifest"] = matched_manifest
                config_data["asset_match_signature"] = current_signature
            if config.asset_manifest or (not has_persisted_manifest and matched_manifest):
                config = H3GenerationConfig.from_mapping(config_data)
        except Exception as exc:
            _logger.warning("H3 auto-bind skipped for %s: %s", part_id, exc)

        from services.character_voice import attach_character_voices
        from services.context import ASSETS_DIR
        if not editor_prompt:
            config_data["asset_manifest"] = attach_character_voices(
                list(config_data.get("asset_manifest") or []), dict(beat), ASSETS_DIR,
            )
        config = H3GenerationConfig.from_mapping(config_data)
        if config.asset_manifest and not planning_only:
            self._validate_source_assets(config, duration)
        prefix = f"MiniMax_H3/segments/{part_id}"
        segment_source = dict(beat)
        if previous_segment_context is not None:
            segment_source['h3_scene_constraints'] = previous_segment_context.get('spatial_constraints', '')
        if timing_only:
            segment_source['h3_timing_only'] = True
        segment_source["generation_config"] = config.to_dict()
        prompt_plan = None
        auto_duration = previous.pop('h3_auto_duration', False) is True or planning_only
        if not config.prompt_override:
            from generation.pipelines.minimax_h3.prompt_planner import plan_h3_prompt

            source_dialogue = []
            # Initial Beats can store their authored dialogue in native H3
            # Shots. Explicit regeneration instead starts from the current
            # plot/units and must not restore deleted lines from old hints.
            saved_job = usable_previous.get('job') or {}
            has_saved_prompt = bool((usable_previous.get('generation_config') or {}).get('prompt_override')
                or usable_previous.get('compiled_prompt_snapshot') or saved_job.get('final_prompt') or saved_job.get('prompt'))
            if not editor_prompt and not beat.get('dialogue_authoritative') and not has_saved_prompt and previous.get('h3_prompt_source') != 'current_beat' and not beat.get("dialogue_units"):
                compiler = self.adapter.prompt_compiler
                for shot in compiler._raw_shots(beat):
                    for value in compiler._shot_dialogue_values(shot):
                        speaker, scope, line, continuation = compiler._dialogue_parts(value)
                        if line:
                            source_dialogue.append({
                                "speaker": speaker, "scope": scope, "line": line,
                                "continues_previous": continuation,
                            })
            if previous_segment_context is not None:
                from .continuity.mapping import normalize_script_entities
                _,role_aliases=normalize_script_entities('',config.to_dict()['asset_manifest'])
                for asset in config.to_dict()['asset_manifest']:
                    role=(asset.get('metadata') or {}).get('role_id')
                    if role:
                        for name in (asset.get('role'),asset.get('label')):
                            if name:role_aliases[name]=role
                previous_segment_context={**previous_segment_context,'role_aliases':role_aliases}
                if editor_prompt: editor_prompt=normalize_script_entities(editor_prompt,config.to_dict()['asset_manifest'])[0]
            prompt_plan = plan_h3_prompt(
                beat, index, duration_sec=duration,
                asset_manifest=config.to_dict()["asset_manifest"],
                previous_segment_context=previous_segment_context,
                editor_constraints=previous.get("h3_editor_constraints"),
                cached_plan=previous.get("h3_prompt_plan"), source_dialogue=source_dialogue,
                continuity_context=previous_segment_context,
                **({'auto_duration': True} if auto_duration else {}),
                **({'editor_prompt': editor_prompt} if editor_prompt else {}),
                **({'dialogue_contract': dialogue_contract} if dialogue_contract is not None else {}),
                **({'draft_mode': True} if planning_only else {}),
                **({'force_replan': True} if force_replan or previous.get('h3_prompt_source') == 'current_beat' else {}),
            )
            if auto_duration:
                old_duration = duration
                duration = prompt_plan['duration_sec']
                config_data['duration_sec'] = duration
                config = H3GenerationConfig.from_mapping(config_data)
                segment_source['generation_config'] = config.to_dict()
                previous['h3_duration_adjustment'] = {'reference_sec': old_duration, 'duration_sec': duration,
                                                      'reason': prompt_plan['duration_reason']}
                if config.asset_manifest and not planning_only:
                    self._validate_source_assets(config, duration)
            segment_source["model_hints"] = {**(beat.get("model_hints") or {}), self.spec.id: prompt_plan}
        # Final shots, not the pre-planning Beat, own automatic references.
        if prompt_plan is not None and not editor_prompt and beat.get("binding_schema_version"):
            from services.project_bible import load_project_bible
            from .semantic_bindings import normalize_beat_bindings, binding_coverage_errors
            try:
                beat = normalize_beat_bindings(segment_source, bible=load_project_bible())
                beat = match_beat_assets(beat, model_id=self.spec.id,
                                         reference_role_limit=self.rules.max_reference_roles or 9)
                automatic = auto_bind_asset_manifest(beat) or []
                existing_items = list(config_data.get('asset_manifest') or [])
                # Unknown historical/manual selections remain authoritative.
                def automatic_asset(item):
                    return str((item.get('metadata') or {}).get('binding_source') or '').startswith('auto_')
                selection_source = config_data.get('reference_selection_source')
                manual = selection_source == 'manual' or (selection_source != 'automatic' and
                    has_persisted_manifest and any(not automatic_asset(a) for a in existing_items))
                if not manual:
                    from .auto_binder import order_automatic_references
                    automatic = order_automatic_references(automatic, beat['model_hints'][self.spec.id]['shots'])
                    config_data['asset_manifest'] = attach_character_voices(automatic, beat, ASSETS_DIR)
                    config_data['reference_selection_source'] = 'automatic'
                config_data['binding_schema_version'] = 1
                config_data['asset_match_signature'] = _beat_asset_signature(beat)
                config = H3GenerationConfig.from_mapping(config_data)
                segment_source = dict(beat, generation_config=config.to_dict())
                failures = binding_coverage_errors(beat, config.to_dict()['asset_manifest'], bible=load_project_bible())
                if failures:
                    raise ValueError('；'.join(failures))
            except ValueError as exc:
                if not planning_only:
                    raise
                from .planning_drafts import issue
                prompt_plan.setdefault('planning_issues', []).append(issue('invalid_binding', exc, blocking=True))
        if prompt_plan is not None and not editor_prompt:
            # Re-evaluate the final dialogue, including manually selected image
            # manifests. Do not let obsolete Beat units choose the voices.
            from services.project_bible import load_project_bible
            voice_source = dict(segment_source, dialogue_units=[])
            config_data['asset_manifest'] = attach_character_voices(
                list(config_data.get('asset_manifest') or []), voice_source, ASSETS_DIR,
                load_project_bible() if beat.get('binding_schema_version') else None)
            config = H3GenerationConfig.from_mapping(config_data)
            segment_source['generation_config'] = config.to_dict()
        if planning_only:
            from .asset_binder import H3AssetBinder
            from .planning_drafts import draft_segment, issue
            plan = prompt_plan or {'shots': [], 'planning_issues': []}
            problems = []
            prompt = ''
            try:
                prompt = self.adapter.prompt_compiler.compile(
                    segment_source, config, H3AssetBinder().bind(config), duration_sec=duration, draft=True,
                ).text
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                problems.append(issue('draft_compile', exc, blocking=True))
            report = H3Ref2VAValidator().validate(config, duration_sec=duration, check_files=False)
            existing_codes = {i['code'] for i in plan.get('planning_issues') or []}
            problems.extend(issue(e.code, e.message, blocking=True) for e in report.errors if e.code not in existing_codes)
            return draft_segment(beat, index, usable_previous, config.to_dict(), plan, prompt, problems)
        adapter = self.adapter
        if config.asset_manifest:
            # RunningHub uploads the original files itself.  Only the local
            # ComfyUI backend needs loader paths staged beneath input/.
            if self._submit_backend_name() == "comfyui":
                adapter = self._staged_adapter(part_id)
        build = adapter.build(
            template,
            segment_source,
            config,
            duration_sec=duration,
            # Files were validated above with an explicit project base dir.
            # The adapter sees either staged filenames or no reference assets.
            check_files=False,
            output_prefix=prefix,
        )
        if build.config.prompt_override:
            # The fullscreen editor owns the final prompt.  Its Shot timeline
            # may intentionally differ from the source Beat after a duration
            # edit (for example, replacing an 8s/3-Shot plan with a 5s/2-Shot
            # prompt).  Do not recompile the stale Beat merely to manufacture
            # a baseline snapshot: that secondary compile must never block a
            # valid manual workflow build.
            compiled_prompt_snapshot = build.prompt.text
        else:
            compiled_prompt_snapshot = adapter.prompt_compiler.compile(
                segment_source,
                build.config,
                build.bindings,
                duration_sec=duration,
            ).text
        workflow_path = self._save_workflow(build.workflow, part_id)
        job = self._job_from_build(build, duration=duration, prefix=prefix, part_id=part_id)
        active_backgrounds = [
            asset
            for asset in build.config.asset_manifest
            if str((asset.metadata or {}).get("asset_kind") or "").strip().lower() == "background"
        ]
        active_scene_id = ""
        if active_backgrounds:
            active_scene_id = str((active_backgrounds[0].metadata or {}).get("scene_id") or "").strip()
            if not active_scene_id:
                active_scene_id = str(beat.get("scene_id") or previous.get("scene_id") or "").strip()

        saved_config = build.config.to_dict()
        # Serialization can rename id to asset_id without changing a binding.
        # Keep the saved editor representation when the typed assets are equal;
        # otherwise an unchanged workflow rebuild invalidates its state trace.
        if authored_prompt and previous_config is not None and 'asset_manifest' in previous_config:
            saved_assets = H3GenerationConfig.from_mapping(previous_config).to_dict()['asset_manifest']
            if saved_assets == saved_config.get('asset_manifest'):
                saved_config['asset_manifest'] = copy.deepcopy(previous_config['asset_manifest'])
        if build.config.prompt_override:
            saved_config["prompt_override"] = build.prompt.text
        if prompt_plan is not None:
            previous["h3_prompt_plan"] = prompt_plan
        previous.update(
            {
                "segment_id": segment_id,
                "segment_index": index,
                "part_id": part_id,
                "title": str(beat.get("title") or previous.get("title") or f"Segment {index}"),
                "model_id": self.spec.id,
                "resolved_model": {"id": self.spec.id, "revision": self.spec.revision},
                "generation_mode": H3_REF2VA_GENERATION_MODE,
                "generation_config": saved_config,
                "compiled_prompt_snapshot": compiled_prompt_snapshot,
                "job": job.to_dict(),
                "workflow_path": str(workflow_path),
                "workflow_profile": build.profile_id,
                "requested_media_spec": {
                    **build.requested_media_spec.to_dict(),
                    "requested_duration_sec": duration,
                    "aligned_frame_count": job.aligned_frame_count,
                    "aligned_duration_sec": job.aligned_frame_count / H3_FPS,
                },
                "actual_media_spec": {},
                "backend": "",
                "submitted": False,
                "prompt_id": "",
                "task_id": "",
                "output_urls": [],
                "submit_result": {},
                "raw_submit": {},
                "error": "",
                "prompt_saved": True,
                "status": "workflow_ready",
                # H3 backgrounds are optional.  Never retain a scene id after
                # its background binding has disappeared; otherwise a stale
                # automatic match can reappear in the editor on regeneration.
                "scene_contexts": beat.get("scene_contexts", previous.get("scene_contexts", [])),
                "binding_schema_version": beat.get("binding_schema_version"),
                "scene_id": active_scene_id,
                "visible_roles": list(beat.get("visible_roles") or previous.get("visible_roles") or []),
                "reference_roles": list(beat.get("reference_roles") or previous.get("reference_roles") or []),
                # 小说原文摘录：随分段保留，供编辑器与重新编译时直接引用原文。
                "source_excerpt": str(beat.get("source_excerpt") or previous.get("source_excerpt") or "").strip(),
            }
        )
        # A new graph is not the current output.  The render history retains an
        # earlier successful LTX/H3 video until the new H3 submission succeeds.
        previous.pop("video_path", None)
        from .continuity.revisions import revision
        previous['workflow_source_revision'] = revision(previous)
        return previous

    def _submit_segment(self, provider: Any, segment: dict[str, Any], *, wait: bool) -> None:
        from generation.media import inspect_media
        from generation.render_history import begin_render, update_render

        config = H3GenerationConfig.from_mapping(segment.get('generation_config') or {})
        duration = float((segment.get('job') or {}).get('duration_sec') or config.duration_sec or 0)
        self._validate_source_assets(config, duration)
        self.validator.validate_binding_contract(segment, config).raise_for_errors()
        if segment.get('planning_state') == 'invalid':
            raise ValueError('没有可提交的分镜，请重新生成 Segment')
        from .prompt_retimer import _validate_timeline
        prompt = str((segment.get('job') or {}).get('final_prompt') or (segment.get('job') or {}).get('prompt') or '')
        _validate_timeline(prompt, duration)
        H3Ref2VAValidator().validate_prompt(prompt).raise_for_errors()
        if any(i.get('blocking') or i.get('code') in ('invalid_structure', 'invalid_timeline', 'draft_compile')
               for i in segment.get('planning_issues') or []):
            raise ValueError('分镜结构或时间线需要调整后才能提交视频')

        job_data = segment.get("job") if isinstance(segment.get("job"), Mapping) else {}
        workflow_value = str(segment.get("workflow_path") or "").strip()
        if not workflow_value:
            raise ValueError("saved H3 workflow path is empty; prepare the workflow before submission")
        workflow_path = Path(workflow_value)
        if not workflow_path.is_file():
            raise FileNotFoundError(f"saved H3 workflow is not a file: {workflow_path}")
        with workflow_path.open("r", encoding="utf-8-sig") as handle:
            workflow = json.load(handle)
        if not isinstance(workflow, dict):
            raise ValueError(f"saved H3 workflow is not an API object: {workflow_path}")
        if segment.get('scene_context_id'):
            prompt_slot = self.adapter.profile.prompt
            actual_prompt = (workflow.get(str(prompt_slot.node_id)) or {}).get('inputs', {}).get(
                prompt_slot.input_name)
            if actual_prompt != prompt:
                raise ValueError('保存工作流的实际提示词与已保存提示词不同，请重新准备当前稿')
        job = H3VideoJob(
            prompt=str(job_data.get("final_prompt") or job_data.get("prompt") or ""),
            duration_sec=float(job_data.get("duration_sec") or 0),
            aligned_frame_count=int(job_data.get("aligned_frame_count") or job_data.get("total_frames") or 0),
            output_prefix=str(job_data.get("output_prefix") or ""),
            part_id=str(job_data.get("part_id") or segment.get("part_id") or ""),
            asset_manifest=list(job_data.get("asset_manifest") or []),
            asset_slots=list(job_data.get("asset_slots") or []),
            model_id=self.spec.id,
            model_revision=self.spec.revision,
            generation_mode=H3_REF2VA_GENERATION_MODE,
            workflow_profile=str(job_data.get("workflow_profile") or segment.get("workflow_profile") or ""),
        )
        if not job.prompt or job.duration_sec <= 0 or job.aligned_frame_count <= 0:
            raise ValueError(f"saved H3 job is incomplete for {job.part_id or 'segment'}")
        backend_name = str(getattr(provider, "name", "") or "comfyui").strip().lower()
        if backend_name == "comfyui":
            assets_changed = self._stage_saved_workflow_assets(workflow, job)
        else:
            assets_changed = self._prepare_remote_workflow_assets(workflow, job)
        if assets_changed:
            workflow_path = self._save_workflow(workflow, job.part_id or str(segment.get('part_id') or 'part'))
            segment['workflow_path'] = str(workflow_path)
            job_data["asset_slots"] = copy.deepcopy(job.asset_slots)
            segment["job"] = {**dict(job_data), "asset_slots": copy.deepcopy(job.asset_slots)}
        from generation.pipelines.minimax_h3.continuity.revisions import snapshot_for_render
        render = begin_render(
            segment,
            model_id=self.spec.id,
            model_revision=self.spec.revision,
            generation_mode=H3_REF2VA_GENERATION_MODE,
            prompt_snapshot=job.prompt,
            asset_manifest=job.asset_manifest,
            requested_media_spec=segment.get("requested_media_spec") if isinstance(segment.get("requested_media_spec"), dict) else {},
            workflow_path=str(workflow_path),
            workflow_profile=job.workflow_profile,
            template_id=str(self._template_path()),
            backend=backend_name,
            status="running",
            source="minimax_h3_ref2va",
            metadata={"asset_slots": copy.deepcopy(job.asset_slots), "h3_continuity_snapshot": snapshot_for_render(segment), "h3_editor_constraints_snapshot": copy.deepcopy(segment.get("h3_editor_constraints") or {}),
                      "h3_scene_framing_snapshot": copy.deepcopy(segment.get('h3_scene_framing') or {})},
        )
        segment["status"] = "running"
        try:
            result = provider.submit(workflow, job, workflow_path=str(workflow_path), wait=wait)
        except Exception as exc:
            from workflow.runninghub_queue import is_queue_full_error

            if is_queue_full_error(exc):
                update_render(segment, str(render.get("render_id") or ""), status="queued")
            raise
        actual = inspect_media(result.video_path).to_dict() if result.video_path else {}
        normalized = {
            "model_id": self.spec.id,
            "backend": result.backend,
            "submitted": bool(result.submitted),
            "prompt_id": result.prompt_id,
            "task_id": result.task_id,
            "video_path": result.video_path,
            "output_urls": list(result.output_urls or []),
            "workflow_path": str(workflow_path),
            "workflow_profile": job.workflow_profile,
            "requested_media_spec": segment.get("requested_media_spec") or {},
            "actual_media_spec": actual,
            "asset_manifest": job.asset_manifest,
            "job": job.to_dict(),
            "submit_result": result.submit_result or {},
            "raw_submit": result.raw or {},
            "status": "success" if result.video_path else "running" if result.submitted else "workflow_ready",
        }
        update_render(
            segment,
            str(render.get("render_id") or ""),
            normalized,
            actual_media_spec=actual,
            activate_on_success=True,
            project=True,
        )
        segment.update(normalized)
        segment["status"] = normalized["status"]
        if result.backend == "runninghub" and result.task_id:
            from workflow.runninghub_usage import record_runninghub_attempt

            submitted = result.submit_result if isinstance(result.submit_result, dict) else {}
            outputs = submitted.get("outputs") if isinstance(submitted.get("outputs"), list) else []
            record_runninghub_attempt(
                segment,
                result.task_id,
                status=normalized["status"],
                outputs=outputs,
                submitted_prompt=job.prompt,
                video_path=result.video_path,
                model_id=self.spec.id,
            )

    def _stage_saved_workflow_assets(self, workflow: dict[str, Any], job: H3VideoJob) -> bool:
        """Refresh a saved H3 graph's loader inputs just before local submit.

        This covers graphs prepared by an older application version (which may
        still contain absolute source paths) and refreshes an input copy when a
        user replaced the original reference file after preparation.
        """

        if not job.asset_manifest:
            return False
        if not job.asset_slots:
            raise ValueError("saved H3 job has reference assets but no loader-slot metadata; regenerate the workflow")

        config = H3GenerationConfig.from_mapping(
            {
                "generation_mode": job.generation_mode,
                "duration_sec": job.duration_sec,
                "asset_manifest": job.asset_manifest,
            }
        )
        self._validate_source_assets(config, job.duration_sec)
        paths_by_asset_id = {
            str(asset.get("asset_id") or asset.get("id") or "").strip(): str(asset.get("path") or "").strip()
            for asset in job.asset_manifest
            if isinstance(asset, Mapping)
        }
        stager = self._input_stager(job.part_id or "saved")
        changed = False
        refreshed_slots: list[dict[str, Any]] = []
        for raw_slot in job.asset_slots:
            if not isinstance(raw_slot, Mapping):
                raise ValueError("saved H3 job has an invalid loader-slot record; regenerate the workflow")
            slot = dict(raw_slot)
            asset_id = str(slot.get("asset_id") or "").strip()
            source_path = paths_by_asset_id.get(asset_id) or str(slot.get("source_path") or slot.get("path") or "").strip()
            source_path = self._resolve_asset_path(source_path)
            staged_path = stager.stage(source_path)
            node_id = str(slot.get("node_id") or "").strip()
            input_name = str(slot.get("input_name") or "").strip()
            node = workflow.get(node_id)
            if not node_id or not input_name or not isinstance(node, dict):
                raise ValueError(f"saved H3 workflow is missing loader node metadata for asset {asset_id or '<unknown>'}")
            inputs = node.setdefault("inputs", {})
            if inputs.get(input_name) != staged_path:
                inputs[input_name] = staged_path
                changed = True
            if slot.get("path") != staged_path:
                slot["path"] = staged_path
                changed = True
            if slot.get("source_path") != source_path:
                slot["source_path"] = source_path
                changed = True
            refreshed_slots.append(slot)
        job.asset_slots = refreshed_slots
        return changed

    def _prepare_remote_workflow_assets(self, workflow: dict[str, Any], job: H3VideoJob) -> bool:
        """Attach validated original files to saved slots for remote upload.

        Older H3 jobs may contain only ComfyUI-input-relative staged paths.
        The asset manifest remains the source of truth, so RunningHub can
        upload those original files without requiring a local ComfyUI tree.
        """

        if not job.asset_manifest:
            return False
        if not job.asset_slots:
            raise ValueError("saved H3 job has reference assets but no loader-slot metadata; regenerate the workflow")

        config = H3GenerationConfig.from_mapping(
            {
                "generation_mode": job.generation_mode,
                "duration_sec": job.duration_sec,
                "asset_manifest": job.asset_manifest,
            }
        )
        self._validate_source_assets(config, job.duration_sec)
        paths_by_asset_id = {
            str(asset.get("asset_id") or asset.get("id") or "").strip(): str(asset.get("path") or "").strip()
            for asset in job.asset_manifest
            if isinstance(asset, Mapping)
        }
        changed = False
        refreshed_slots: list[dict[str, Any]] = []
        for raw_slot in job.asset_slots:
            if not isinstance(raw_slot, Mapping):
                raise ValueError("saved H3 job has an invalid loader-slot record; regenerate the workflow")
            slot = dict(raw_slot)
            asset_id = str(slot.get("asset_id") or "").strip()
            source_path = paths_by_asset_id.get(asset_id) or str(slot.get("source_path") or slot.get("path") or "").strip()
            source_path = self._resolve_asset_path(source_path)
            node_id = str(slot.get("node_id") or "").strip()
            input_name = str(slot.get("input_name") or "").strip()
            if not node_id or not input_name or not isinstance(workflow.get(node_id), dict):
                raise ValueError(f"saved H3 workflow is missing loader node metadata for asset {asset_id or '<unknown>'}")
            if slot.get("source_path") != source_path:
                slot["source_path"] = source_path
                changed = True
            refreshed_slots.append(slot)
        job.asset_slots = refreshed_slots
        return changed

    def _load_template(self) -> dict[str, Any]:
        path = self._template_path()
        if not path.exists():
            raise FileNotFoundError(f"MiniMax H3 Ref2VA API template not found: {path}")
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"MiniMax H3 Ref2VA template is not an API object: {path}")
        return data

    def _template_path(self) -> Path:
        from services.context import BASE_DIR

        configured = str(self._model_config.get("template_api_path") or "templates/MiniMaxH3_Ref2VA_api.json")
        path = Path(configured)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    def _save_workflow(self, workflow: Mapping[str, Any], part_id: str) -> Path:
        from services.context import get_project_dir
        from .continuity.checkpoints import digest
        import uuid

        target_dir = get_project_dir() / "workflows" / "minimax_h3"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(character if character.isalnum() or character in "-_" else "_" for character in part_id) or "part"
        # Concurrent unlocked builds must never overwrite another screenplay's
        # graph or mutate the workflow referenced by a historical render.
        path = target_dir / f"{safe_id}_ref2va_{digest(workflow)[:24]}_api.json"
        if path.exists():
            if json.loads(path.read_text(encoding='utf-8-sig')) != workflow:
                raise ValueError('已保存工作流内容与指纹不匹配，未覆盖原文件')
            return path.resolve()
        temporary = target_dir / ('.h3_' + uuid.uuid4().hex[:16] + '.tmp')
        try:
            temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding='utf-8')
            # Publish a complete file atomically. A competing build can only
            # target this same name when it has the same graph contents.
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path.resolve()

    def _validate_source_assets(self, config: H3GenerationConfig, duration_sec: float) -> None:
        from services.context import BASE_DIR

        self.validator.validate(
            config,
            duration_sec=duration_sec,
            check_files=True,
            base_dir=BASE_DIR,
        ).raise_for_errors()

    def _staged_adapter(self, part_id: str) -> H3Ref2VAWorkflowAdapter:
        return H3Ref2VAWorkflowAdapter(
            profile=self.workflow_profile,
            validator=self.validator,
            path_resolver=self._input_stager(part_id).stage,
        )

    def _input_stager(self, part_id: str) -> H3ComfyInputStager:
        from services.context import BASE_DIR, get_current_project_name

        project = str(get_current_project_name() or "current")
        scope = f"{project}_{part_id}"
        return H3ComfyInputStager(
            input_dir=self._comfy_input_dir(),
            base_dir=BASE_DIR,
            scope=scope,
        )

    def _comfy_input_dir(self) -> Path:
        from services.context import BASE_DIR, CONFIG

        backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), Mapping) else {}
        comfy = backends.get("comfyui") if isinstance(backends, Mapping) else {}
        configured = (
            comfy.get("input_dir") if isinstance(comfy, Mapping) else None
        ) or CONFIG.get("comfy_input_dir") or "../ComfyUI/input"
        path = Path(str(configured))
        return path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()

    def _job_from_build(self, build: H3WorkflowBuild, *, duration: float, prefix: str, part_id: str) -> H3VideoJob:
        return H3VideoJob(
            prompt=build.prompt.text,
            duration_sec=float(duration),
            aligned_frame_count=self._aligned_frame_count(duration),
            output_prefix=prefix,
            part_id=part_id,
            asset_manifest=list(build.config.to_dict().get("asset_manifest") or []),
            asset_slots=list(build.asset_slots),
            model_id=self.spec.id,
            model_revision=self.spec.revision,
            generation_mode=build.config.generation_mode,
            workflow_profile=build.profile_id,
        )

    @staticmethod
    def _aligned_frame_count(duration_sec: float) -> int:
        frames = max(5, round(float(duration_sec) * H3_FPS))
        return frames + (5 - (frames % 17)) % 17

    @staticmethod
    def _duration_for(beat: Mapping[str, Any], config_data: Mapping[str, Any]) -> float:
        value = config_data.get("duration_sec") or beat.get("h3_duration_sec") or beat.get("estimated_duration_sec") or beat.get("duration_sec")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("H3 segment duration is required") from exc

    @staticmethod
    def _resolve_asset_path(value: str) -> str:
        from services.context import BASE_DIR

        path = Path(str(value or "").strip())
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path.resolve())

    def _submit_backend_name(self) -> str:
        return str(self._config.get("video_submit_backend") or "comfyui").strip().lower()

    def _submit_provider(self, backend: str | None = None) -> Any:
        from workflow.submit_factory import get_submit_provider

        return get_submit_provider(model_id=self.spec.id, backend=backend) if backend else get_submit_provider(model_id=self.spec.id)
