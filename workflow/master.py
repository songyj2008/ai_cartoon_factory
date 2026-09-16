"""Top-level model-factory generation orchestration for UI bindings."""
from __future__ import annotations

import json
from typing import Any

from pipeline.beats import split_beats
from pipeline.quality_gate import assert_quality_gate_clear
from pipeline.story import generate_story
from services.brand import brand_block_message, is_brand_verified
from services.context import CONFIG
from services.duration import get_final_duration_seconds
from services.file_utils import load_json_file_silent
from services.logger import get_logs, log
from generation.service import get_generation_service
from workflow.video_merge import json_text, load_video_jobs, merge_segment_videos


def _json_text(data: Any) -> str:
    return json_text(data)


def _assert_brand_verified(action: str) -> None:
    if not is_brand_verified():
        raise RuntimeError(f"{action} blocked: {brand_block_message()}")


def _duration_seconds(duration_select=None, custom_duration=None) -> int:
    if custom_duration not in (None, ""):
        try:
            value = int(float(custom_duration))
            if value > 0:
                return value
        except Exception:
            pass
    text = str(duration_select or "").strip().lower()
    if text.endswith("s"):
        text = text[:-1]
    if text and text != "custom":
        try:
            value = int(float(text))
            if value > 0:
                return value
        except Exception:
            pass
    try:
        return int(get_final_duration_seconds(duration_select, custom_duration))
    except Exception:
        return int(CONFIG.get("default_video_duration") or 15)


def _load_beats_data() -> dict[str, Any] | None:
    text = load_json_file_silent("beats.json")
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _generate_story_and_beats(topic_text: str, duration_sec: int, target_beat_count: int | None = None) -> tuple[str, dict[str, Any]]:
    model_id = get_generation_service().project_default_model_id()
    story_text, _ = generate_story(topic_text, duration_sec=duration_sec, model_id=model_id)
    if not story_text:
        raise RuntimeError("story generation returned empty content")
    latest_beats_text = ""
    for latest_beats_text, _logs in split_beats(story_text, target_duration_sec=duration_sec, target_beat_count=target_beat_count, model_id=model_id):
        pass
    if not latest_beats_text:
        raise RuntimeError("beats generation failed")
    beats_data = json.loads(latest_beats_text)
    from services.ui_run_config import update_ui_run_config

    update_ui_run_config(beats_stale_for_model="")
    assert_quality_gate_clear(story_text=story_text, beats_data=beats_data, model_id=model_id)
    return story_text, beats_data


def _ensure_beats(topic_text: str, duration_sec: int, force_generate: bool = False, target_beat_count: int | None = None) -> tuple[str, dict[str, Any]]:
    if not force_generate:
        beats_data = _load_beats_data()
        if beats_data and int(beats_data.get("total_duration_sec") or 0) == int(duration_sec):
            return load_json_file_silent("story.json") or "", beats_data
    return _generate_story_and_beats(topic_text, duration_sec, target_beat_count=target_beat_count)


def _groups_html(result: dict[str, Any] | None = None) -> str:
    from ui.group_panel import render_groups_panel

    return render_groups_panel(_json_text(result) if result else load_json_file_silent("video_jobs.json"))


def generate_segment_prompts(workflow_mode=None, group_page=0):
    try:
        _assert_brand_verified("generate segment prompts")
        beats_data = _load_beats_data()
        if not beats_data:
            raise ValueError("beats.json not found; generate story beats first")
        assert_quality_gate_clear(story_text=load_json_file_silent("story.json") or "", beats_data=beats_data, model_id=get_generation_service().project_default_model_id())
        result = get_generation_service().prepare_segments(beats_data)
        return get_logs(), _groups_html(result)
    except Exception as exc:
        log(f"[generation][prompt][error] {exc}", "ERROR")
        return get_logs(), _groups_html(None)


def generate_final_video(dry_run=False, workflow_only=False, no_concat=False, workflow_mode=None, group_page=0):
    try:
        _assert_brand_verified("generate final video")
        if bool(dry_run) or bool(workflow_only):
            beats_data = _load_beats_data()
            if not beats_data:
                raise ValueError("beats.json not found; generate story beats first")
            assert_quality_gate_clear(story_text=load_json_file_silent("story.json") or "", beats_data=beats_data, model_id=get_generation_service().project_default_model_id())
            result = get_generation_service().prepare_segments(beats_data)
        else:
            result = get_generation_service().submit_saved_segments(merge_after=not bool(no_concat))
        return get_logs(), _groups_html(result)
    except Exception as exc:
        log(f"[generation][error] {exc}", "ERROR")
        return get_logs(), _groups_html(None)


def placeholder_compose_video():
    try:
        _assert_brand_verified("merge final video")
        path = merge_segment_videos(load_video_jobs())
        log(f"[FINAL VIDEO] merge complete: {path}", "STEP")
    except Exception as exc:
        log(f"[FINAL VIDEO][error] {exc}", "ERROR")
    return get_logs()


def regenerate_segment_prompt_from_slot(slot_index, groups_text="", guides_text="", page=0):
    try:
        _assert_brand_verified("regenerate segment prompt")
        index = int(slot_index or 0)
        if index <= 0:
            raise ValueError("invalid segment index")
        beats_data = _load_beats_data()
        if not beats_data:
            raise ValueError("beats.json not found")
        service = get_generation_service()
        current_jobs = load_video_jobs()
        current_segment = next(
            (
                item
                for item in current_jobs.get("segments") or []
                if isinstance(item, dict) and int(item.get("segment_index") or 0) == index
            ),
            None,
        )
        if current_segment is None:
            raise ValueError(f"segment not found: {index}")
        from generation.job_schema import resolved_model_id

        model_id = resolved_model_id(current_segment, current_jobs)
        assert_quality_gate_clear(
            story_text=load_json_file_silent("story.json") or "",
            beats_data=beats_data,
            model_id=model_id,
        )
        result = service.regenerate_segment_prompt(beats_data, index)
        return get_logs(), _groups_html(result), _json_text(result), None, None, None, None
    except Exception as exc:
        log(f"[generation][prompt_regen][error] {exc}", "ERROR")
        return get_logs(), _groups_html(None), load_json_file_silent("video_jobs.json"), None, None, None, None


def regenerate_part_from_slot(slot_index, groups_text="", guides_text="", page=0, wait=False):
    try:
        _assert_brand_verified("regenerate segment video")
        index = int(slot_index or 0)
        if index <= 0:
            raise ValueError("invalid segment index")
        result = get_generation_service().submit_saved_segments(segment_indices={index}, wait=wait)
        return get_logs(), _groups_html(result), _json_text(result), None, None, None, None
    except Exception as exc:
        log(f"[generation][regen][error] {exc}", "ERROR")
        return get_logs(), _groups_html(None), load_json_file_silent("video_jobs.json"), None, None, None, None


def one_click_full_generation(
    topic_text,
    submit_to_comfyui=True,
    workflow_mode=None,
    auto_cleanup=True,
    duration_select_value=None,
    custom_duration=None,
    target_beat_count=None,
    page=0,
    resume_mode=None,
    merge_after=False,
):
    _assert_brand_verified("one click full generation")
    duration_sec = _duration_seconds(duration_select_value, custom_duration)
    log("[one_click] model-factory segmented pipeline start", "STEP")
    story_text, beats_data = _ensure_beats(topic_text, duration_sec, force_generate=True, target_beat_count=target_beat_count)
    assert_quality_gate_clear(story_text=story_text, beats_data=beats_data, model_id=get_generation_service().project_default_model_id())
    result = get_generation_service().generate_segments(
        beats_data,
        submit=False,
        workflow_only=True,
    )
    if submit_to_comfyui:
        result = get_generation_service().submit_saved_segments(merge_after=bool(merge_after))
    log("[one_click] model-factory pipeline complete", "STEP")
    yield (
        story_text,
        _json_text(beats_data),
        "",
        _json_text(result),
        _groups_html(result),
        [],
        get_logs(),
    )
