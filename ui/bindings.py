"""Gradio event bindings for the model-factory video pipeline."""
from __future__ import annotations

import json
from html import escape as html_escape
import os
from pathlib import Path
import subprocess
import sys
import uuid

import gradio as gr

from pipeline.beat_splitter.guard import detect_complex_beats
from pipeline.beat_splitter.io import ensure_beat_uids
from pipeline.beat_splitter.splitter import split_complex_beat as split_one_complex_beat
from pipeline.events import ensure_event_fields_on_beats
from pipeline.quality_gate import format_quality_block_message, strip_quality_annotations
from services.brand import brand_block_message, is_brand_verified
from services.context import CONFIG, assert_current_runtime_writable, get_current_episode_name, get_project_dir
from services.file_utils import load_json_file_silent
from services.logger import clear_logs, get_logs, log
from services.ui_run_config import load_ui_run_config
from ui.beat_split_migration import migrate_after_beat_delete, migrate_after_beat_insert, migrate_after_complex_beat_split
from ui.beat_copy import persist_beat_copy
from ui.group_panel import group_total_pages, render_groups_panel
from ui.progress_panel import render_workflow_progress, workflow_status_from_run_state
from ui.project_bible_panel import manual_plan_project_bible, selected_project_asset_gallery
from ui.project_handlers import (
    _episode_choices,
    _beats_display_to_json,
    _story_display_to_json,
    archive_current_generation,
    beats_json_to_display,
    clean_project,
    clear_quality_issues,
    current_default_video_model,
    generate_story_with_duration,
    generate_novel_segments_with_duration,
    latest_final_video_preview_payload,
    mark_and_persist_quality_issues,
    novel_source_ui_update,
    restore_project_ui_state,
    save_runtime_ui_options,
    save_service_keys,
    save_submit_backend_selection,
    save_workflow_config,
    save_story_and_beats_edits,
    save_duration_selection,
    save_default_video_model_with_workflow,
    rename_current_project,
    set_current_project,
    set_current_project_episode,
    split_beats_with_duration,
    story_json_to_display,
    update_custom_duration_visibility,
)
from ui.video_feedback import analyze_video_feedback, apply_video_feedback_rules, refresh_video_feedback_parts
from ui.asset_library_panel import (
    load_existing_asset_selection,
    fill_asset_metadata_from_text,
    preview_selected_asset_for_upload,
    preview_selected_asset_path_only,
    refresh_asset_library_controls,
    refresh_asset_target_dir_options,
    save_selected_asset_metadata,
)
from workflow.master import generate_final_video, generate_segment_prompts, one_click_full_generation, placeholder_compose_video, regenerate_part_from_slot, regenerate_segment_prompt_from_slot
from workflow.runninghub_sync import sync_runninghub_video_jobs
from workflow.segment_runner import save_segment_director_prompt, save_segment_director_prompts_batch, set_segment_video_ok


def _selected_submit_backend(submit_comfyui=False, submit_runninghub=False) -> str:
    if bool(submit_runninghub):
        return "runninghub"
    if bool(submit_comfyui):
        return "comfyui"
    return ""


def _current_submit_backend() -> str:
    ui_config = load_ui_run_config()
    backend = str(ui_config.get("ui_submit_backend") or CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    if backend not in {"comfyui", "runninghub"}:
        return ""
    return backend


def _apply_submit_backend(submit_comfyui=False, submit_runninghub=False, workflow_only=False) -> tuple[bool, str]:
    backend = _selected_submit_backend(submit_comfyui, submit_runninghub)
    save_submit_backend_selection(backend)
    CONFIG["submit_to_comfyui"] = bool(backend) and not bool(workflow_only)
    if backend:
        CONFIG["video_submit_backend"] = backend
    return bool(backend) and not bool(workflow_only), backend


def _save_backend_for_ui(backend: str, workflow_only=False) -> str:
    backend = save_submit_backend_selection(backend)
    CONFIG["submit_to_comfyui"] = bool(backend) and not bool(workflow_only)
    if backend:
        CONFIG["video_submit_backend"] = backend
    return backend


def _backend_checkbox_updates(backend: str, workflow_only=False):
    interactive = not bool(workflow_only) and is_brand_verified()
    return (
        gr.update(value=backend == "comfyui", interactive=interactive),
        gr.update(value=backend == "runninghub", interactive=interactive),
    )


def _on_submit_comfyui_change(submit_comfyui, submit_runninghub, workflow_only):
    if bool(submit_comfyui):
        backend = "comfyui"
    elif bool(submit_runninghub):
        backend = "runninghub"
    else:
        backend = ""
    _save_backend_for_ui(backend, workflow_only)
    return _backend_checkbox_updates(backend, workflow_only)


def _on_submit_runninghub_change(submit_comfyui, submit_runninghub, workflow_only):
    if bool(submit_runninghub):
        backend = "runninghub"
    elif bool(submit_comfyui):
        backend = "comfyui"
    else:
        backend = ""
    _save_backend_for_ui(backend, workflow_only)
    return _backend_checkbox_updates(backend, workflow_only)


def _on_workflow_only_change(dry_run, workflow_only, no_concat, submit_comfyui, submit_runninghub):
    save_runtime_ui_options(dry_run, workflow_only, no_concat)
    backend = _selected_submit_backend(submit_comfyui, submit_runninghub)
    _save_backend_for_ui(backend, workflow_only)
    return _backend_checkbox_updates(backend, workflow_only)


def _load_video_jobs_text():
    return load_json_file_silent("video_jobs.json") or ""


def _progress_html():
    return render_workflow_progress(workflow_status_from_run_state({}))


def _archive_readonly_message(action: str = "修改") -> str:
    episode = get_current_episode_name() or "归档集"
    message = f"[archive][readonly] 已归档的集数只能查看，不能{action}：{episode}"
    log(message, "WARN")
    return get_logs()


def _is_archive_readonly() -> bool:
    return bool(get_current_episode_name())


def _brand_blocked(action: str) -> bool:
    if is_brand_verified():
        return False
    log(f"[brand][blocked] {action}: {brand_block_message()}", "ERROR")
    return True


def _segment_video_regen_concurrency_limit() -> int:
    """Queue width for manual per-part video regeneration.

    ComfyUI must stay serial. RunningHub task creation can be parallel because
    manual regeneration returns after task submission instead of waiting for
    the remote render to complete.
    """
    backend = _current_submit_backend()
    if backend == "runninghub":
        submit_backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
        runninghub_config = submit_backends.get("runninghub") if isinstance(submit_backends, dict) else {}
        raw = (
            CONFIG.get("runninghub_segment_video_regen_concurrency_limit")
            or CONFIG.get("segment_video_regen_concurrency_limit")
            or (runninghub_config or {}).get("segment_video_regen_concurrency_limit")
            or (runninghub_config or {}).get("concurrency_limit")
            or 4
        )
    else:
        raw = 1
    try:
        return max(1, int(raw))
    except Exception:
        return 1


def _runtime_refresh(page=0):
    page = max(0, int(page or 0))
    video_jobs_text = _load_video_jobs_text()
    return (
        story_json_to_display(load_json_file_silent("story.json") or ""),
        video_jobs_text,
        get_logs(),
        render_groups_panel(video_jobs_text, page=page),
        _progress_html(),
    )


def _runtime_refresh_with_beats(page=0):
    story_text, video_jobs_text, logs, panel, progress = _runtime_refresh(page=page)
    return story_text, beats_json_to_display(load_json_file_silent("beats.json") or ""), video_jobs_text, logs, panel, progress


def _runtime_status_refresh(group_page=0):
    """Refresh remote task state without discarding the selected segment page."""
    page = max(0, int(group_page or 0))
    episode_update = gr.update(
        choices=_episode_choices(),
        value=get_current_episode_name(),
    )
    # The server worker owns polling and queue dispatch. UI refresh only reads
    # persisted results, so an inactive browser cannot stall the queue.
    video_jobs_text = _load_video_jobs_text()
    return video_jobs_text, render_groups_panel(video_jobs_text, page=page), _progress_html(), episode_update


def _submission_inputs_match_saved(story_text, beats_text) -> list[str]:
    saved_story = story_json_to_display(load_json_file_silent("story.json") or "")
    saved_beats = beats_json_to_display(load_json_file_silent("beats.json") or "")
    problems: list[str] = []
    if strip_quality_annotations(story_text).strip() != strip_quality_annotations(saved_story).strip():
        problems.append("剧情")
    if strip_quality_annotations(beats_text).strip() != strip_quality_annotations(saved_beats).strip():
        problems.append("Beats")
    return problems


def _segment_pager_updates(groups_text, page, unmarked_only=False, delta=0):
    unmarked_only = bool(unmarked_only)
    total = group_total_pages(groups_text, unmarked_only)
    current = max(0, min(int(page or 0) + int(delta or 0), total - 1))
    return (
        current,
        render_groups_panel(groups_text, page=current, unmarked_only=unmarked_only),
        f"<span class='pager-label'>第 {current + 1} / {total} 页</span>",
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def _toggle_segment_ok_filter(groups_text, page, unmarked_only):
    """Toggle the project/episode-wide OK filter without moving the user."""
    next_unmarked_only = not bool(unmarked_only)
    pager_updates = _segment_pager_updates(groups_text, page, next_unmarked_only)
    return (
        next_unmarked_only,
        *pager_updates,
    )


def _switch_main_page(page):
    raw_page = str(page or "workbench").strip()
    page = {
        "创作工作台": "workbench",
        "素材库": "assets",
        "设置": "settings",
        "workbench": "workbench",
        "assets": "assets",
        "settings": "settings",
    }.get(raw_page, "workbench")
    return (
        gr.update(visible=page == "workbench"),
        gr.update(visible=page == "assets"),
        gr.update(visible=page == "settings"),
    )


def _switch_settings_subpage(page):
    page = str(page or "model").strip()
    page = {
        "模型配置": "model",
        "工作流配置": "workflow",
        "开发者选项": "developer",
        "model": "model",
        "workflow": "workflow",
        "developer": "developer",
    }.get(page, "model")
    return (
        gr.update(visible=page == "model"),
        gr.update(visible=page == "workflow"),
        gr.update(visible=page == "developer"),
    )


def _segment_preserve_on_reset(segment: dict) -> bool:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return bool(
        segment.get("preserve_on_reset")
        or segment.get("video_ok")
        or segment.get("protected_from_reset")
        or job.get("preserve_on_reset")
        or job.get("video_ok")
        or job.get("protected_from_reset")
    )


def _protected_segment_output_paths(payload: dict) -> set[Path]:
    paths: set[Path] = set()
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict) or not _segment_preserve_on_reset(segment):
            continue
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        values = [
            segment.get("video_path"),
            segment.get("workflow_path"),
            segment.get("stale_video_path"),
            job.get("video_path"),
            job.get("workflow_path"),
            job.get("stale_video_path"),
        ]
        for render in segment.get("renders") or []:
            if not isinstance(render, dict):
                continue
            output = render.get("output") if isinstance(render.get("output"), dict) else {}
            workflow = render.get("workflow") if isinstance(render.get("workflow"), dict) else {}
            values.extend((output.get("video_path"), workflow.get("path")))
        for value in values:
            path_text = str(value or "").strip()
            if not path_text:
                continue
            try:
                paths.add(Path(path_text).resolve())
            except Exception:
                pass
    return paths


def _clear_segment_outputs_for_new_beats():
    assert_current_runtime_writable("清空旧分段")
    project_dir = get_project_dir()
    video_jobs_path = project_dir / "video_jobs.json"
    protected_paths: set[Path] = set()
    has_protected_segments = False
    if video_jobs_path.exists():
        try:
            payload = json.loads(video_jobs_path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                has_protected_segments = any(
                    _segment_preserve_on_reset(item)
                    for item in payload.get("segments") or []
                    if isinstance(item, dict)
                )
                protected_paths = _protected_segment_output_paths(payload)
                if has_protected_segments:
                    payload.pop("final_video_path", None)
                    video_jobs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    video_jobs_path.unlink()
            else:
                video_jobs_path.unlink()
        except Exception:
            video_jobs_path.unlink()
    render_log_path = project_dir / "logs" / "render_log.json"
    if render_log_path.exists() and not has_protected_segments:
        render_log_path.unlink()
    deleted_workflows = 0
    for workflow_dir in (project_dir / "workflows" / "licon_msr", project_dir / "workflows" / "minimax_h3"):
        if not workflow_dir.exists():
            continue
        for path in workflow_dir.glob("*.json"):
            if path.is_file():
                try:
                    if path.resolve() in protected_paths:
                        continue
                except Exception:
                    pass
                path.unlink()
                deleted_workflows += 1

    video_suffixes = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif"}
    deleted_videos = 0
    for media_dir in (project_dir / "videos", project_dir / "final"):
        if not media_dir.exists():
            continue
        for path in media_dir.rglob("*"):
            if not path.exists() or path.is_dir() or path.suffix.lower() not in video_suffixes:
                continue
            try:
                if path.resolve() in protected_paths:
                    continue
                path.unlink()
                deleted_videos += 1
            except PermissionError:
                log(f"[beats][cleanup][warn] 视频文件被占用，未删除：{path}", "WARN")

        for directory in sorted([p for p in media_dir.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    if has_protected_segments:
        log(
            f"[beats] 已清理未保护的旧模型分段输出；保留视频OK分段。"
            f"删除 {deleted_workflows} 个 workflow 文件、{deleted_videos} 个旧视频文件",
            "STEP",
        )
        jobs_text = load_json_file_silent("video_jobs.json") or ""
        return jobs_text, render_groups_panel(jobs_text), _progress_html()
    log(f"[beats] 已清空旧视频分段、{deleted_workflows} 个 workflow 文件、{deleted_videos} 个旧视频文件", "STEP")
    return "", render_groups_panel(""), _progress_html()


def _split_beats_with_cleared_segments(story_text, duration_select, custom_duration, target_beat_count=None, topic_text=None):
    if _brand_blocked("split beats"):
        jobs = _load_video_jobs_text()
        yield story_text or "", "", jobs, get_logs(), render_groups_panel(jobs), _progress_html(), "", gr.update(), "", gr.update()
        return
    if _is_archive_readonly():
        yield story_text or "", "", _load_video_jobs_text(), _archive_readonly_message("拆分剧情 Beats"), render_groups_panel(_load_video_jobs_text()), _progress_html(), "", gr.update(), "", gr.update()
        return
    empty_jobs, empty_panel, progress = _clear_segment_outputs_for_new_beats()
    yield story_text or "", "", empty_jobs, get_logs(), empty_panel, progress, "", gr.update(value=1), "", gr.update(value=False)
    for story_value, beats_value, logs in split_beats_with_duration(
        story_text,
        duration_select,
        custom_duration,
        topic_text=topic_text,
        target_beat_count=target_beat_count,
    ):
        report_text = ""
        index_update = gr.update()
        notice = ""
        try:
            if str(beats_value or "").strip():
                beats_data = _load_saved_beats_payload()
                report = detect_complex_beats(beats_data)
                report_text = _risk_report_text(report)
                index_update = gr.update(value=_suggested_complex_beat_index(report))
                notice = _beat_risk_notice_html(report)
                report_path = get_project_dir() / "temp" / "beat_complexity_report.json"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_text, encoding="utf-8")
                if report.get("risky_count"):
                    log(f"[beat_splitter] 自动检测到 {report.get('risky_count')} 个复杂 Beat", "WARN")
        except Exception as exc:
            log(f"[beat_splitter][auto_detect][warn] {exc}", "WARN")
        yield story_value, beats_value, "", get_logs(), render_groups_panel(""), _progress_html(), report_text, index_update, notice, gr.update(value=False)


def _generate_novel_segments_with_ui(novel_text, duration_select, custom_duration, segment_count):
    try:
        jobs_text, logs = generate_novel_segments_with_duration(
            novel_text, duration_select, custom_duration, segment_count
        )
        beats_text = beats_json_to_display(load_json_file_silent("beats.json") or "")
        return jobs_text, logs, render_groups_panel(jobs_text), _progress_html(), beats_text, novel_text
    except Exception as exc:
        log(f"[novel_beats][error] {exc}", "ERROR")
        jobs = _load_video_jobs_text()
        beats_text = beats_json_to_display(load_json_file_silent("beats.json") or "")
        return jobs, get_logs(), render_groups_panel(jobs), _progress_html(), beats_text, novel_text


def _save_current_story_beats_or_block(story_text, beats_text):
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, _archive_readonly_message("保存剧情或 Beats"), render_groups_panel(jobs), _progress_html(), True
    saved_story, saved_beats, saved_jobs, saved_logs = save_story_and_beats_edits(story_text, beats_text)
    current_text = f"{saved_story or ''}\n{saved_beats or ''}"
    if "🔴【风险" in current_text or "❌" in current_text:
        return saved_story, saved_beats, saved_jobs, saved_logs, render_groups_panel(saved_jobs), _progress_html(), True
    return saved_story, saved_beats, saved_jobs, saved_logs, render_groups_panel(saved_jobs), _progress_html(), False


def _save_story_beats_and_refresh(story_text, beats_text, topic_text=None):
    if _brand_blocked("save story/beats"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html()
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, _archive_readonly_message("保存剧情或 Beats"), render_groups_panel(jobs), _progress_html()
    saved_story, saved_beats, saved_jobs, saved_logs = save_story_and_beats_edits(story_text, beats_text, topic_text)
    return saved_story, saved_beats, saved_jobs, saved_logs, render_groups_panel(saved_jobs), _progress_html()


def _generate_story_readonly_guarded(topic_text, story_input_text, duration_select, custom_duration):
    if _brand_blocked("generate story"):
        return story_input_text or "", get_logs()
    if _is_archive_readonly():
        return story_input_text or "", _archive_readonly_message("生成完整剧情")
    return generate_story_with_duration(topic_text, story_input_text, duration_select, custom_duration)


def _generate_segment_prompts(story_text, beats_text, workflow_mode, page, ignore_beat_risk=False):
    if _brand_blocked("generate segment prompts"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html(), gr.update()
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, _archive_readonly_message("生成分段提示词"), render_groups_panel(jobs), _progress_html(), gr.update()
    saved_story, saved_beats, saved_jobs, saved_logs, panel, progress, blocked = _save_current_story_beats_or_block(story_text, beats_text)
    if blocked:
        return saved_story, saved_beats, saved_jobs, saved_logs, panel, progress, gr.update()
    if not bool(ignore_beat_risk):
        try:
            report = _detect_saved_beat_risks()
            if int(report.get("risky_count") or 0) > 0:
                log("[beat_splitter] 检测到复杂 Beat。请先拆分复杂 Beat，或勾选“忽略 Beat 风险，继续生成分段提示词”。", "WARN")
                return saved_story, saved_beats, saved_jobs, get_logs(), panel, progress, _beat_risk_notice_html(report)
        except Exception as exc:
            log(f"[beat_splitter][preflight][warn] {exc}", "WARN")
    _clear_segment_outputs_for_new_beats()
    log("[render] beats saved; prepare editable LiconMSR segment prompts", "STEP")
    generate_segment_prompts(workflow_mode="api", group_page=0)
    story_text, beats_text, video_jobs_text, logs, panel, progress = _runtime_refresh_with_beats(page=page)
    return story_text, beats_text, video_jobs_text, logs, panel, progress, gr.update()


def _load_saved_beats_payload() -> dict:
    raw = load_json_file_silent("beats.json") or "{}"
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("beats"), list):
        raise ValueError("beats.json 内容无效，请先生成或保存 Beats")
    return data


def _risk_report_text(report: dict) -> str:
    return json.dumps(report or {}, ensure_ascii=False, indent=2)


def _beat_risk_notice_html(report: dict | None) -> str:
    report = report or {}
    risky_count = int(report.get("risky_count") or 0)
    beat_count = int(report.get("beat_count") or 0)
    if risky_count <= 0:
        if beat_count <= 0:
            return ""
        return (
            "<div class='beat-risk-notice ok'>"
            "<b>Beat 风险检测通过</b><span>当前 beats 未发现复杂度风险。</span>"
            "</div>"
        )
    risky = report.get("risky_reports") if isinstance(report.get("risky_reports"), list) else []
    risk_items: list[str] = []
    for item in risky:
        if not isinstance(item, dict):
            continue
        try:
            beat_index = int(item.get("beat_index") or 1)
        except (TypeError, ValueError):
            beat_index = 1
        title = html_escape(str(item.get("title") or f"Beat {beat_index}"))
        codes = ", ".join(html_escape(str(code)) for code in (item.get("risk_codes") or []))
        risk_items.append(
            "<li class='beat-risk-item'>"
            f"<button type='button' class='beat-risk-select' data-beat-index='{beat_index}' "
            f"aria-label='选择 Beat {beat_index}: {title}'>Beat {beat_index}</button>"
            f"<span class='beat-risk-title'>{title}</span>"
            f"<span class='beat-risk-codes'>{codes or '复杂度较高'}</span>"
            "</li>"
        )
    risk_list = "<ul class='beat-risk-list'>" + "".join(risk_items) + "</ul>" if risk_items else ""
    return (
        "<div class='beat-risk-notice warn'>"
        f"<b>检测到 {risky_count} 个复杂 Beat</b>"
        "<span>以下列出全部风险 Beat；点击编号可将它填入“指定 Beat”。</span>"
        f"{risk_list}"
        "<span>选择后可点击“继续拆分指定 Beat”，或勾选“忽略 Beat 风险”继续生成分段提示词。</span>"
        "</div>"
    )


def _beat_action_error_html(action: str, exc: Exception) -> str:
    return (
        "<div class='beat-risk-notice warn'>"
        f"<b>{html_escape(action)}失败</b>"
        f"<span>{html_escape(str(exc))}</span>"
        "</div>"
    )


def _suggested_complex_beat_index(report: dict) -> int:
    risky = report.get("risky_reports") if isinstance(report.get("risky_reports"), list) else []
    if risky:
        try:
            return int(risky[0].get("beat_index") or 1)
        except Exception:
            return 1
    return 1


def _detect_saved_beat_risks() -> dict:
    return detect_complex_beats(_load_saved_beats_payload())


def _detect_beat_risks(story_text, beats_text):
    if _brand_blocked("detect beat risks"):
        return story_text or "", beats_text or "", "", gr.update(), "", get_logs()
    try:
        if _is_archive_readonly():
            report = detect_complex_beats(_load_saved_beats_payload())
            return story_text or "", beats_text or "", _risk_report_text(report), _suggested_complex_beat_index(report), _beat_risk_notice_html(report), get_logs()
        saved_story, saved_beats, _saved_jobs, _saved_logs = save_story_and_beats_edits(story_text, beats_text)
        beats_data = _load_saved_beats_payload()
        report = detect_complex_beats(beats_data)
        report_path = get_project_dir() / "temp" / "beat_complexity_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_risk_report_text(report), encoding="utf-8")
        log(f"[beat_splitter] 风险检测完成：{report.get('risky_count', 0)} / {report.get('beat_count', 0)} 个 beat 有风险", "STEP")
        log(f"[beat_splitter] report saved: {report_path}", "STEP")
        return saved_story, saved_beats, _risk_report_text(report), _suggested_complex_beat_index(report), _beat_risk_notice_html(report), get_logs()
    except Exception as exc:
        log(f"[beat_splitter][detect][error] {exc}", "ERROR")
        return story_text or "", beats_text or "", _risk_report_text({"error": str(exc)}), gr.update(), _beat_action_error_html("检测 Beat 风险", exc), get_logs()


def _split_complex_beat_from_ui(story_text, beats_text, beat_index):
    if _brand_blocked("split complex beat"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html(), "", gr.update(), "", gr.update()
    try:
        assert_current_runtime_writable("拆分复杂 Beat")
        saved_story, _saved_beats, _saved_jobs, _saved_logs = save_story_and_beats_edits(story_text, beats_text)
        beats_data = _load_saved_beats_payload()
        index = int(float(beat_index or 0))
        if index <= 0:
            raise ValueError("请输入要拆分的 Beat 序号")
        result = split_one_complex_beat(
            beats_data=beats_data,
            beat_index=index,
            config=CONFIG,
            temp_dir=get_project_dir() / "temp" / "beat_splitter",
        )
        split_data = result["beats"]
        beats_path = get_project_dir() / "beats.json"
        beats_path.write_text(json.dumps(split_data, ensure_ascii=False, indent=2), encoding="utf-8")
        detail = {
            "beat_index": result["beat_index"],
            "risk_report": result["risk_report"],
            "replacement_count": result["replacement_count"],
            "replacement_beats": result["replacement_beats"],
        }
        detail_path = get_project_dir() / "temp" / "beat_split_detail.json"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        replacement_count = int(result.get("replacement_count") or 0)
        migrated_payload = migrate_after_complex_beat_split(index, replacement_count, split_data)
        report = detect_complex_beats(split_data)
        log(f"[beat_splitter] Beat {index} 已拆分为 {replacement_count} 个 replacement beats", "STEP")
        log(f"[beat_splitter] split detail saved: {detail_path}", "STEP")
        video_jobs_text = json.dumps(migrated_payload, ensure_ascii=False, indent=2) if migrated_payload else (load_json_file_silent("video_jobs.json") or "")
        split_notice = (
            "<div class='beat-risk-notice ok'>"
            f"<b>Beat {index} 已拆分</b><span>已按剧情顺序替换为 {replacement_count} 个 Beat，后续 Beat 已重新编号。</span>"
            "</div>"
        )
        return (
            saved_story,
            beats_json_to_display(json.dumps(split_data, ensure_ascii=False)),
            video_jobs_text,
            get_logs(),
            render_groups_panel(video_jobs_text),
            _progress_html(),
            _risk_report_text(report),
            _suggested_complex_beat_index(report),
            split_notice + _beat_risk_notice_html(report),
            gr.update(value=False),
        )
    except Exception as exc:
        log(f"[beat_splitter][split][error] {exc}", "ERROR")
        return (
            story_text or "",
            beats_text or "",
            load_json_file_silent("video_jobs.json") or "",
            get_logs(),
            render_groups_panel(_load_video_jobs_text()),
            _progress_html(),
            _risk_report_text({"error": str(exc)}),
            gr.update(),
            _beat_action_error_html("拆分指定 Beat", exc),
            gr.update(),
        )


def _delete_beat_from_ui(story_text, beats_text, beat_index):
    if _brand_blocked("delete beat"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html(), "", gr.update(), "", gr.update()
    try:
        assert_current_runtime_writable("删除 Beat")
        clean_story_text = strip_quality_annotations(story_text or "")
        clean_beats_text = strip_quality_annotations(beats_text or "")
        story_data = _story_display_to_json(clean_story_text)
        (get_project_dir() / "story.json").write_text(json.dumps(story_data, ensure_ascii=False, indent=2), encoding="utf-8")
        # Deleting a Beat must not rematch every remaining Beat and overwrite
        # their manually established visible/reference roles.
        beats_data = ensure_beat_uids(ensure_event_fields_on_beats(_beats_display_to_json(clean_beats_text)))
        beats = beats_data.get("beats") if isinstance(beats_data, dict) else None
        if not isinstance(beats, list) or not beats:
            raise ValueError("没有可删除的 Beat")
        index = int(float(beat_index or 0))
        if index <= 0 or index > len(beats):
            raise ValueError(f"请输入 1 到 {len(beats)} 之间的 Beat 序号")
        if len(beats) <= 1:
            raise ValueError("至少需要保留 1 个 Beat")
        deleted = beats.pop(index - 1)
        for new_index, beat in enumerate(beats, start=1):
            if not isinstance(beat, dict):
                continue
            beat["id"] = new_index
            beat["order"] = new_index
            beat["node_id"] = f"beat_{new_index:03d}"
        beats_data["beats"] = beats
        beats_data["total_duration_sec"] = sum(
            int(round(float((beat or {}).get("estimated_duration_sec") or (beat or {}).get("duration_sec") or 0)))
            for beat in beats
            if isinstance(beat, dict)
        )
        beats_path = get_project_dir() / "beats.json"
        beats_path.write_text(json.dumps(beats_data, ensure_ascii=False, indent=2), encoding="utf-8")
        migrated_payload = migrate_after_beat_delete(index, beats_data)
        report = detect_complex_beats(beats_data)
        video_jobs_text = json.dumps(migrated_payload, ensure_ascii=False, indent=2) if migrated_payload else (load_json_file_silent("video_jobs.json") or "")
        deleted_title = str((deleted or {}).get("title") or f"Beat {index}") if isinstance(deleted, dict) else f"Beat {index}"
        next_index = min(index, len(beats))
        notice = (
            "<div class='beat-risk-notice ok'>"
            f"<b>Beat {index} 已删除</b><span>{html_escape(deleted_title)} 已移除，后续 Beat 和 part 已重新编号。</span>"
            "</div>"
        )
        log(f"[beat_editor] 删除 Beat {index}: {deleted_title}", "STEP")
        return (
            story_json_to_display(json.dumps(story_data, ensure_ascii=False)),
            beats_json_to_display(json.dumps(beats_data, ensure_ascii=False)),
            video_jobs_text,
            get_logs(),
            render_groups_panel(video_jobs_text),
            _progress_html(),
            _risk_report_text(report),
            next_index,
            notice + _beat_risk_notice_html(report),
            gr.update(value=False),
        )
    except Exception as exc:
        log(f"[beat_editor][delete][error] {exc}", "ERROR")
        return (
            story_text or "",
            beats_text or "",
            load_json_file_silent("video_jobs.json") or "",
            get_logs(),
            render_groups_panel(_load_video_jobs_text()),
            _progress_html(),
            _risk_report_text({"error": str(exc)}),
            gr.update(),
            _beat_action_error_html("删除指定 Beat", exc),
            gr.update(),
        )


def _copy_beat_from_ui(story_text, beats_text, source_index, target_index):
    # Yield before doing work so repeat clicks are disabled during the operation.
    yield (*([gr.update()] * 10), gr.update(interactive=False, value="正在复制…"))
    try:
        if _brand_blocked("copy beat"):
            raise ValueError("请先完成授权验证")
        assert_current_runtime_writable("复制 Beat")
        beats_data = _beats_display_to_json(strip_quality_annotations(beats_text or ""))
        beats_data, jobs = persist_beat_copy(get_project_dir(), beats_data, source_index, target_index)
        source, target = int(source_index), int(target_index)
        jobs_text = json.dumps(jobs, ensure_ascii=False, indent=2)
        notice = (
            "<div class='beat-risk-notice ok' role='status'>"
            f"<b>已将 Beat {source} 复制到 Beat {target}</b>"
            "<span>原目标位置及后续 Beat 已顺延；复制件未包含生成记录和小说原文。</span></div>"
        )
        log(f"[beat_editor] copied Beat {source} to {target}", "STEP")
        yield (story_text or "", beats_json_to_display(json.dumps(beats_data, ensure_ascii=False)),
               jobs_text, get_logs(), render_groups_panel(jobs_text), _progress_html(),
               "", target, notice, gr.update(value=False), gr.update(interactive=True, value="复制 Beat"))
    except Exception as exc:
        log(f"[beat_editor][copy][error] {exc}", "ERROR")
        yield (gr.update(), gr.update(), gr.update(), get_logs(), gr.update(), gr.update(),
               gr.update(), gr.update(), _beat_action_error_html("复制 Beat", exc), gr.update(),
               gr.update(interactive=True, value="复制 Beat"))


def _add_beat_from_ui(story_text, beats_text, beat_index):
    if _brand_blocked("add beat"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html(), "", gr.update(), "", gr.update()
    try:
        assert_current_runtime_writable("新增 Beat")
        clean_story_text = strip_quality_annotations(story_text or "")
        clean_beats_text = strip_quality_annotations(beats_text or "")
        story_data = _story_display_to_json(clean_story_text)
        beats_data = ensure_beat_uids(ensure_event_fields_on_beats(_beats_display_to_json(clean_beats_text)))
        beats = beats_data.get("beats") if isinstance(beats_data, dict) else None
        if not isinstance(beats, list) or not beats:
            raise ValueError("没有可作为插入位置的 Beat")
        index = int(float(beat_index or 0))
        if index <= 0 or index > len(beats):
            raise ValueError(f"请输入 1 到 {len(beats)} 之间的 Beat 序号")

        previous = beats[index - 1] if isinstance(beats[index - 1], dict) else {}
        duration = int(round(float(previous.get("estimated_duration_sec") or previous.get("duration_sec") or 10)))
        inserted_index = index + 1
        new_beat = {
            "id": inserted_index,
            "order": inserted_index,
            "node_id": f"beat_{inserted_index:03d}",
            "beat_uid": f"beat_{uuid.uuid4().hex}",
            "title": f"新增 Beat {inserted_index}",
            "plot": "",
            "scene_id": str(previous.get("scene_id") or ""),
            "important_roles": [],
            "visible_roles": [],
            "reference_roles": [],
            "offscreen_speakers": [],
            "mentioned_roles": [],
            "dialogue_units": [],
            "action_units": [],
            "event_units": [],
            "identity_constraints": {},
            "background_extras": [],
            "estimated_duration_sec": duration,
            "manually_added": True,
        }
        beats.insert(index, new_beat)
        for new_index, beat in enumerate(beats, start=1):
            if not isinstance(beat, dict):
                continue
            beat["id"] = new_index
            beat["order"] = new_index
            beat["node_id"] = f"beat_{new_index:03d}"
        beats_data["beats"] = beats
        beats_data["total_duration_sec"] = sum(
            int(round(float((beat or {}).get("estimated_duration_sec") or (beat or {}).get("duration_sec") or 0)))
            for beat in beats
            if isinstance(beat, dict)
        )

        # Shift existing segment display positions before committing beats. If
        # an active downstream task blocks migration, neither file is changed.
        migrated_payload = migrate_after_beat_insert(index, beats_data)
        (get_project_dir() / "story.json").write_text(json.dumps(story_data, ensure_ascii=False, indent=2), encoding="utf-8")
        (get_project_dir() / "beats.json").write_text(json.dumps(beats_data, ensure_ascii=False, indent=2), encoding="utf-8")
        report = detect_complex_beats(beats_data)
        video_jobs_text = json.dumps(migrated_payload, ensure_ascii=False, indent=2) if migrated_payload else (load_json_file_silent("video_jobs.json") or "")
        notice = (
            "<div class='beat-risk-notice ok'>"
            f"<b>已新增 Beat {inserted_index}</b><span>已插入到 Beat {index} 后面；请填写标题、剧情、角色、动作或对白后保存。</span>"
            "</div>"
        )
        log(f"[beat_editor] 在 Beat {index} 后新增 Beat {inserted_index}", "STEP")
        return (
            story_json_to_display(json.dumps(story_data, ensure_ascii=False)),
            beats_json_to_display(json.dumps(beats_data, ensure_ascii=False)),
            video_jobs_text,
            get_logs(),
            render_groups_panel(video_jobs_text),
            _progress_html(),
            _risk_report_text(report),
            inserted_index,
            notice + _beat_risk_notice_html(report),
            gr.update(value=False),
        )
    except Exception as exc:
        log(f"[beat_editor][add][error] {exc}", "ERROR")
        return (
            story_text or "",
            beats_text or "",
            load_json_file_silent("video_jobs.json") or "",
            get_logs(),
            render_groups_panel(_load_video_jobs_text()),
            _progress_html(),
            _risk_report_text({"error": str(exc)}),
            gr.update(),
            _beat_action_error_html("新增 Beat", exc),
            gr.update(),
        )


def _render_video(story_text, beats_text, dry_run, workflow_only, submit_comfyui, submit_runninghub, no_concat, workflow_mode, page, segment_edits_payload=""):
    if _brand_blocked("render video"):
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html()
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, _archive_readonly_message("生成视频"), render_groups_panel(jobs), _progress_html()
    changed_inputs = _submission_inputs_match_saved(story_text, beats_text)
    if changed_inputs:
        message = "、".join(changed_inputs) + " 已修改，请先保存并重新生成分段提示词；现有分段不会被删除。"
        log(f"[render] {message}", "WARN")
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html()
    try:
        edits = json.loads(str(segment_edits_payload or "[]"))
        save_segment_director_prompts_batch(edits if isinstance(edits, list) else [])
    except Exception as exc:
        log(f"[render] 保存当前分段编辑失败，已取消提交: {exc}", "ERROR")
        jobs = _load_video_jobs_text()
        return story_text or "", beats_text or "", jobs, get_logs(), render_groups_panel(jobs), _progress_html()
    submit, backend = _apply_submit_backend(submit_comfyui, submit_runninghub, workflow_only or dry_run)
    if not _load_video_jobs_text():
        log("[render] 没有可提交的 LiconMSR 视频分段，请先生成分段提示词", "ERROR")
        return story_text or "", beats_text or "", "", get_logs(), render_groups_panel(""), _progress_html()

    log(f"[render] {'submit to ' + backend if submit else 'workflow only'}", "STEP")
    generate_final_video(
        dry_run=bool(dry_run),
        workflow_only=bool(workflow_only) or bool(dry_run) or not submit,
        no_concat=bool(no_concat),
        workflow_mode="api",
        group_page=0,
    )
    return _runtime_refresh_with_beats(page=page)


def _save_segment_prompt(payload_text, page=0):
    page = max(0, int(page or 0))
    if _brand_blocked("save segment prompt"):
        jobs = _load_video_jobs_text()
        return jobs, get_logs(), render_groups_panel(jobs, page=page), _progress_html(), gr.update()
    try:
        assert_current_runtime_writable("保存分段提示词")
        import json

        payload = json.loads(str(payload_text or "{}"))
        segment_index = int(payload.get("segment_index") or 0)
        director_prompt = str(payload.get("director_prompt") or "").strip()
        full_prompt = str(payload.get("full_prompt") or "").strip() if "full_prompt" in payload else None
        reference_roles = payload.get("reference_roles") if "reference_roles" in payload else None
        reference_image_ids = payload.get("reference_image_ids") if "reference_image_ids" in payload else None
        scene_id = str(payload.get("scene_id") or "") if "scene_id" in payload else None
        material_override = reference_roles is not None or reference_image_ids is not None or scene_id is not None
        save_segment_director_prompt(
            segment_index,
            director_prompt,
            reference_roles=reference_roles,
            reference_image_ids=reference_image_ids,
            scene_id=scene_id,
            full_prompt_override=full_prompt,
        )
        beats_update = beats_json_to_display(load_json_file_silent("beats.json") or "") if material_override else gr.update()
        return load_json_file_silent("video_jobs.json") or "", get_logs(), render_groups_panel(_load_video_jobs_text(), page=page), _progress_html(), beats_update
    except Exception as exc:
        log(f"[LICON_MSR][prompt_save][error] {exc}", "ERROR")
        return _load_video_jobs_text(), get_logs(), render_groups_panel(_load_video_jobs_text(), page=page), _progress_html(), gr.update()


def _video_ok_updates(jobs_text, status_text, page, unmarked_only):
    """Refresh a video-OK action while retaining the global list state."""
    current, panel, label, prev, next_ = _segment_pager_updates(jobs_text, page, unmarked_only)
    return jobs_text, status_text, current, panel, _progress_html(), label, prev, next_


def _toggle_segment_video_ok(payload_text, page=0, unmarked_only=False):
    page = max(0, int(page or 0))
    if _brand_blocked("mark segment video ok"):
        jobs = _load_video_jobs_text()
        return _video_ok_updates(jobs, get_logs(), page, unmarked_only)
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return _video_ok_updates(jobs, _archive_readonly_message("标记分段视频OK"), page, unmarked_only)
    try:
        assert_current_runtime_writable("标记分段视频OK")
        payload = json.loads(str(payload_text or "{}"))
        segment_index = int(payload.get("segment_index") or 0)
        video_ok = bool(payload.get("video_ok"))
        result = set_segment_video_ok(segment_index, video_ok)
        jobs_text = json.dumps(result, ensure_ascii=False, indent=2)
        return _video_ok_updates(jobs_text, get_logs(), page, unmarked_only)
    except Exception as exc:
        log(f"[LICON_MSR][video_ok][error] {exc}", "ERROR")
        if "没有可保留的视频文件" in str(exc):
            log("[LICON_MSR][video_ok] 请先生成该分段视频，再标记视频OK保护", "WARN")
        jobs = _load_video_jobs_text()
        return _video_ok_updates(jobs, get_logs(), page, unmarked_only)


def _merge_video():
    if _brand_blocked("merge video"):
        return get_logs(), _load_video_jobs_text(), _progress_html()
    if _is_archive_readonly():
        return _archive_readonly_message("合并最终视频"), _load_video_jobs_text(), _progress_html()
    placeholder_compose_video()
    return get_logs(), _load_video_jobs_text(), _progress_html()


def _open_videos_dir():
    try:
        final_dir = get_project_dir() / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(final_dir.resolve()))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(final_dir.resolve())])
        else:
            subprocess.Popen(["xdg-open", str(final_dir.resolve())])
        log(f"[ui] opened final video dir: {final_dir.resolve()}", "STEP")
    except Exception as exc:
        log(f"[ui][open_videos_dir][error] {exc}", "ERROR")
    return get_logs()


def _regen_segment(segment_index_text, page=0, story_text="", beats_text=""):
    if _brand_blocked("regenerate segment prompt"):
        return _runtime_refresh(page=page)
    if _is_archive_readonly():
        return _runtime_refresh(page=page)
    # The action must consume the Beat currently visible in the editor, not a
    # previously saved beats.json snapshot.
    save_story_and_beats_edits(story_text, beats_text)
    regenerate_segment_prompt_from_slot(segment_index_text)
    return _runtime_refresh(page=page)


def _regen_segment_video(segment_index_text, page=0):
    page = max(0, int(page or 0))
    if _brand_blocked("regenerate segment video"):
        yield _runtime_refresh(page=page)
        return
    if _is_archive_readonly():
        _archive_readonly_message("重新生成视频")
        yield _runtime_refresh(page=page)
        return
    if _current_submit_backend() == "runninghub":
        try:
            log("[LICON_MSR][runninghub][regen] checking remote task status before retry", "STEP")
            sync_runninghub_video_jobs()
        except Exception as exc:
            log(f"[LICON_MSR][runninghub][sync][warn] {exc}", "WARN")
    segment_index = segment_index_text
    saved_current_inputs = False
    payload = None
    try:
        payload = json.loads(str(segment_index_text or "{}"))
    except Exception:
        segment_index = segment_index_text
    if isinstance(payload, dict):
        segment_index = payload.get("segment_index") or payload.get("index") or segment_index_text
        director_prompt = str(payload.get("director_prompt") or "").strip()
        has_reference_roles = isinstance(payload.get("reference_roles"), list)
        has_reference_image_ids = isinstance(payload.get("reference_image_ids"), dict)
        has_scene_id = "scene_id" in payload
        reference_roles = [str(role) for role in payload.get("reference_roles") or []] if has_reference_roles else None
        reference_image_ids = dict(payload.get("reference_image_ids") or {}) if has_reference_image_ids else None
        scene_id = str(payload.get("scene_id") or "") if has_scene_id else None
        if director_prompt or has_reference_roles or has_reference_image_ids or has_scene_id:
            try:
                save_segment_director_prompt(
                    int(segment_index or 0),
                    director_prompt,
                    reference_roles=reference_roles,
                    reference_image_ids=reference_image_ids,
                    scene_id=scene_id,
                )
                saved_current_inputs = True
            except Exception as exc:
                log(f"[LICON_MSR][regen][save_current_error] {exc}", "ERROR")
                yield _runtime_refresh(page=page)
                return
    try:
        segment_index = int(float(segment_index or 0))
        if segment_index <= 0:
            raise ValueError("invalid segment index")
        log(f"[LICON_MSR] 手动重新生成视频 part_{segment_index:03d}", "STEP")
        if saved_current_inputs:
            yield _runtime_refresh(page=page)
        regenerate_part_from_slot(segment_index)
    except Exception as exc:
        log(f"[LICON_MSR][regen][error] {exc}", "ERROR")
    yield _runtime_refresh(page=page)


def _one_click_direct(topic_text, submit_comfyui, submit_runninghub, workflow_mode, auto_cleanup, duration_select_value, custom_duration, target_beat_count, page, resume_mode, workflow_only, no_concat):
    if _brand_blocked("one click full generation"):
        jobs = _load_video_jobs_text()
        return (
            story_json_to_display(load_json_file_silent("story.json") or ""),
            beats_json_to_display(load_json_file_silent("beats.json") or ""),
            jobs,
            get_logs(),
            render_groups_panel(jobs),
            _progress_html(),
            gr.update(),
        )
    if _is_archive_readonly():
        jobs = _load_video_jobs_text()
        return (
            story_json_to_display(load_json_file_silent("story.json") or ""),
            beats_json_to_display(load_json_file_silent("beats.json") or ""),
            jobs,
            _archive_readonly_message("一键全流程"),
            render_groups_panel(jobs),
            _progress_html(),
            gr.update(),
        )
    submit, backend = _apply_submit_backend(submit_comfyui, submit_runninghub, workflow_only)
    log(f"[one_click] submit backend: {backend or 'workflow only'}", "STEP")
    last = None
    try:
        for item in one_click_full_generation(
            topic_text,
            submit_to_comfyui=submit,
            workflow_mode="api",
            auto_cleanup=False,
            duration_select_value=duration_select_value,
            custom_duration=custom_duration,
            target_beat_count=target_beat_count,
            page=0,
            resume_mode="restart",
            merge_after=not bool(no_concat),
        ):
            last = item
    except Exception as exc:
        story_marked, beats_marked, issues = mark_and_persist_quality_issues(
            story_json_to_display(load_json_file_silent("story.json") or ""),
            beats_json_to_display(load_json_file_silent("beats.json") or ""),
        )
        if issues:
            log(format_quality_block_message(issues), "ERROR")
            return story_marked, beats_marked, "", get_logs(), render_groups_panel(""), _progress_html(), None
        log(f"[one_click][error] {exc}", "ERROR")
        return story_marked, beats_marked, "", get_logs(), render_groups_panel(""), _progress_html(), None
    if not last:
        return "", "", "", get_logs(), render_groups_panel(""), _progress_html(), None
    story_text, beats_text, _old_guides, result_text, _panel_html, _old_gallery, logs = last
    clear_quality_issues()
    return story_json_to_display(story_text), beats_json_to_display(beats_text), result_text, logs, render_groups_panel(result_text), _progress_html(), None


def _restore_direct_state():
    restored = restore_project_ui_state()
    try:
        topic = restored[0]
        duration_select = restored[1]
        custom_duration = restored[2]
        story_text = restored[3]
        beats_text = restored[4]
        video_jobs_text = restored[5]
        status = restored[6]
        bible_characters = restored[7]
        bible_backgrounds = restored[8]
        selected_gallery = restored[9]
        dry_run = restored[10]
        workflow_only = restored[11]
        no_concat = restored[12]
        submit_comfyui = restored[13]
        submit_runninghub = restored[14]
        novel_update = restored[15]
        novel_segment_count = restored[16] if len(restored) > 16 else 4
        target_beat_count = restored[17] if len(restored) > 17 else ""
    except Exception:
        topic = ""
        duration_select = "60s"
        custom_duration = 60
        story_text = story_json_to_display(load_json_file_silent("story.json") or "")
        beats_text = beats_json_to_display(load_json_file_silent("beats.json") or "")
        video_jobs_text = _load_video_jobs_text()
        status = get_logs()
        bible_characters = None
        bible_backgrounds = None
        selected_gallery = []
        dry_run = False
        workflow_only = False
        no_concat = True
        backend = _current_submit_backend()
        submit_comfyui = backend == "comfyui"
        submit_runninghub = backend == "runninghub"
        novel_update = novel_source_ui_update()
        novel_segment_count = 4
        target_beat_count = ""
    submit_comfyui_update, submit_runninghub_update = _backend_checkbox_updates(
        _selected_submit_backend(submit_comfyui, submit_runninghub),
        workflow_only,
    )
    pager_total = group_total_pages(video_jobs_text)
    default_model_update = gr.update(value=current_default_video_model())
    return (
        topic,
        duration_select,
        custom_duration,
        story_text,
        beats_text,
        video_jobs_text,
        status,
        bible_characters,
        bible_backgrounds,
        selected_gallery,
        dry_run,
        workflow_only,
        submit_comfyui_update,
        submit_runninghub_update,
        no_concat,
        render_groups_panel(video_jobs_text),
        _progress_html(),
        0,
        f"<span class='pager-label'>第 1 / {pager_total} 页</span>",
        gr.update(interactive=True),
        gr.update(interactive=True),
        default_model_update,
        novel_update,
        novel_segment_count,
        target_beat_count,
    )


def _project_change(project_name, outgoing_topic=None):
    if _brand_blocked("change project"):
        return (
            gr.update(),
            story_json_to_display(load_json_file_silent("story.json") or ""),
            beats_json_to_display(load_json_file_silent("beats.json") or ""),
            _load_video_jobs_text(),
            get_logs(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            render_groups_panel(_load_video_jobs_text()),
            _progress_html(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    restored = set_current_project(project_name, outgoing_topic)
    try:
        output_dir = restored[0]
        story_text = restored[1]
        beats_text = restored[2]
        video_jobs_text = restored[3]
        status = restored[4]
        project_output_dir = restored[5]
        project_videos_dir = restored[6]
        project_final_video = restored[7]
        bible_characters = restored[8]
        bible_backgrounds = restored[9]
        selected_gallery = restored[10]
        episode_update = restored[11] if len(restored) > 11 else gr.update()
        novel_update = restored[12] if len(restored) > 12 else gr.update(value="", interactive=True)
        novel_segment_count = restored[13] if len(restored) > 13 else 4
        target_beat_count = restored[14] if len(restored) > 14 else ""
        topic_value = restored[15] if len(restored) > 15 else ""
        backend = _current_submit_backend()
        submit_comfyui = backend == "comfyui"
        submit_runninghub = backend == "runninghub"
    except Exception:
        output_dir = ""
        story_text = story_json_to_display(load_json_file_silent("story.json") or "")
        beats_text = beats_json_to_display(load_json_file_silent("beats.json") or "")
        video_jobs_text = _load_video_jobs_text()
        status = get_logs()
        project_output_dir = ""
        project_videos_dir = ""
        project_final_video = ""
        bible_characters = None
        bible_backgrounds = None
        selected_gallery = []
        episode_update = gr.update()
        novel_update = gr.update(value="", interactive=True)
        topic_value = ""
        novel_segment_count = 4
        target_beat_count = ""
        backend = _current_submit_backend()
        submit_comfyui = backend == "comfyui"
        submit_runninghub = backend == "runninghub"
    default_model_update = gr.update(value=current_default_video_model())
    submit_comfyui_update, submit_runninghub_update = _backend_checkbox_updates(
        _selected_submit_backend(submit_comfyui, submit_runninghub),
        False,
    )
    return (
        output_dir,
        story_text,
        beats_text,
        video_jobs_text,
        status,
        project_output_dir,
        project_videos_dir,
        project_final_video,
        bible_characters,
        bible_backgrounds,
        selected_gallery,
        submit_comfyui_update,
        submit_runninghub_update,
        render_groups_panel(video_jobs_text),
        _progress_html(),
        episode_update,
        default_model_update,
        novel_update,
        novel_segment_count,
        target_beat_count,
        topic_value,
    )


def _set_current_project_episode_guarded(project_episode):
    if _brand_blocked("change episode"):
        jobs = _load_video_jobs_text()
        return (
            gr.update(),
            story_json_to_display(load_json_file_silent("story.json") or ""),
            beats_json_to_display(load_json_file_silent("beats.json") or ""),
            jobs,
            get_logs(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            render_groups_panel(jobs),
            _progress_html(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )
    return set_current_project_episode(project_episode)


def _set_current_project_episode_from_picker_guarded(project_episode):
    """Keep the sidebar selector aligned when the header episode picker is used."""
    result = _set_current_project_episode_guarded(project_episode)
    return (*result, gr.update(value=get_current_episode_name()))


def _clean_project_direct():
    if _brand_blocked("clean project"):
        jobs = _load_video_jobs_text()
        backend = _current_submit_backend()
        submit_comfyui_update, submit_runninghub_update = _backend_checkbox_updates(backend, False)
        return get_logs(), story_json_to_display(load_json_file_silent("story.json") or ""), beats_json_to_display(load_json_file_silent("beats.json") or ""), jobs, gr.update(), gr.update(), gr.update(), submit_comfyui_update, submit_runninghub_update, render_groups_panel(jobs), _progress_html(), gr.update(), gr.update()
    result = clean_project()
    try:
        status = result[0]
        story_text = result[1]
        beats_text = result[2]
        video_jobs_text = result[3]
        bible_characters = result[4]
        bible_backgrounds = result[5]
        selected_gallery = result[6]
    except Exception:
        status = get_logs()
        story_text = ""
        beats_text = ""
        video_jobs_text = ""
        bible_characters = None
        bible_backgrounds = None
        selected_gallery = []
    backend = _current_submit_backend()
    submit_comfyui_update, submit_runninghub_update = _backend_checkbox_updates(backend, False)
    return status, story_text, beats_text, video_jobs_text, bible_characters, bible_backgrounds, selected_gallery, submit_comfyui_update, submit_runninghub_update, render_groups_panel(video_jobs_text), _progress_html(), gr.update(value="", interactive=True), str(load_ui_run_config().get("topic") or "")


def _rename_current_project_guarded(project_rename_text):
    if _brand_blocked("rename project"):
        return gr.update(), gr.update(), gr.update(), get_logs()
    return rename_current_project(project_rename_text)


def _archive_current_generation_guarded(archive_episode_name):
    if _brand_blocked("archive generation"):
        return gr.update(), get_logs(), gr.update(), gr.update()
    return archive_current_generation(archive_episode_name)


def _manual_plan_project_bible_guarded(topic, bible_characters, bible_backgrounds):
    if _brand_blocked("save selected assets"):
        return get_logs(), gr.update(), gr.update(), gr.update()
    return manual_plan_project_bible(topic, bible_characters, bible_backgrounds)


def _analyze_video_feedback_guarded(feedback_part_select, feedback_issue_types, feedback_note):
    if _brand_blocked("analyze video feedback"):
        return gr.update(), get_logs()
    return analyze_video_feedback(feedback_part_select, feedback_issue_types, feedback_note)


def _apply_video_feedback_rules_guarded(feedback_analysis):
    if _brand_blocked("apply video feedback rules"):
        return get_logs()
    return apply_video_feedback_rules(feedback_analysis)


def bind_events(demo, components):
    refresh_episode_picker_js = """() => { setTimeout(() => {
        if (!window.AICF) return;
        window.AICF.promptRuntimeRevision = Number(window.AICF.promptRuntimeRevision || 0) + 1;
        window.AICF.segmentPromptDrafts = {};
        if (window.AICF.closePromptModal) window.AICF.closePromptModal();
        if (window.AICF.scheduleSegmentDirectorSync) window.AICF.scheduleSegmentDirectorSync();
        if (window.AICF.refreshEpisodePicker) window.AICF.refreshEpisodePicker();
    }, 350); }"""
    refresh_episode_runtime_js = """() => {
        setTimeout(() => {
            if (!window.AICF) return;
            window.AICF.promptRuntimeRevision = Number(window.AICF.promptRuntimeRevision || 0) + 1;
            window.AICF.segmentPromptDrafts = {};
            // A modal belongs to the previous episode's card.  Close it before
            // the new DOM is rendered so it can never show or save stale text.
            if (window.AICF.closePromptModal) window.AICF.closePromptModal();
            if (window.AICF.scheduleSegmentDirectorSync) window.AICF.scheduleSegmentDirectorSync();
            if (window.AICF.refreshEpisodePicker) window.AICF.refreshEpisodePicker();
        }, 120);
    }"""

    auto_cleanup_box = components["auto_cleanup_box"]
    asset_aliases = components["asset_aliases"]
    asset_description_cn = components["asset_description_cn"]
    asset_display_name = components["asset_display_name"]
    asset_existing_select = components["asset_existing_select"]
    asset_gallery = components["asset_gallery"]
    asset_current_path = components["asset_current_path"]
    asset_id = components["asset_id"]
    asset_library_log = components["asset_library_log"]
    asset_pose = components["asset_pose"]
    asset_save_meta_btn = components["asset_save_meta_btn"]
    asset_tags_cn = components["asset_tags_cn"]
    asset_type = components["asset_type"]
    asset_target_dir = components["asset_target_dir"]
    asset_target_path = components["asset_target_path"]
    asset_upload_file = components["asset_upload_file"]
    asset_use_for = components["asset_use_for"]
    beats_json = components["beats_json"]
    beat_risk_report = components["beat_risk_report"]
    beat_risk_notice = components["beat_risk_notice"]
    bible_background_select = components["bible_background_select"]
    bible_character_select = components["bible_character_select"]
    clean_btn = components["clean_btn"]
    clear_log_btn = components["clear_log_btn"]
    custom_duration_box = components["custom_duration_box"]
    target_beat_count_box = components["target_beat_count_box"]
    novel_text = components["novel_text"]
    novel_segment_count = components["novel_segment_count"]
    generate_novel_segments_btn = components["generate_novel_segments_btn"]
    complex_beat_index = components["complex_beat_index"]
    detect_beat_risk_btn = components["detect_beat_risk_btn"]
    add_beat_btn = components["add_beat_btn"]
    copy_beat_btn = components["copy_beat_btn"]
    copy_beat_source = components["copy_beat_source"]
    copy_beat_target = components["copy_beat_target"]
    delete_beat_btn = components["delete_beat_btn"]
    dry_run_box = components["dry_run_box"]
    duration_select = components["duration_select"]
    final_video_path_display = components["final_video_path_display"]
    final_video_preview_payload = components["final_video_preview_payload"]
    final_video_refresh_btn = components["final_video_refresh_btn"]
    gen_story_btn = components["gen_story_btn"]
    generate_segment_prompts_btn = components["generate_segment_prompts_btn"]
    group_page_state = components["group_page_state"]
    segment_ok_filter_state = components["segment_ok_filter_state"]
    group_prev_page_btn = components["group_prev_page_btn"]
    group_page_label = components["group_page_label"]
    group_next_page_btn = components["group_next_page_btn"]
    groups_panel = components["groups_panel"]
    ignore_beat_risk_box = components["ignore_beat_risk_box"]
    main_page = components["main_page"]
    manual_bible_btn = components["manual_bible_btn"]
    merge_video_btn = components["merge_video_btn"]
    minimal_mode_box = components["minimal_mode_box"]
    no_concat_box = components["no_concat_box"]
    one_click_full_btn = components["one_click_full_btn"]
    one_click_resume_mode = components["one_click_resume_mode"]
    output_dir = components["output_dir"]
    project_final_video_box = components["project_final_video_box"]
    project_name = components["project_name"]
    project_episode = components["project_episode"]
    project_rename_text = components["project_rename_text"]
    rename_project_btn = components["rename_project_btn"]
    archive_episode_name = components["archive_episode_name"]
    archive_generation_btn = components["archive_generation_btn"]
    archive_path_box = components["archive_path_box"]
    project_output_dir_box = components["project_output_dir_box"]
    project_videos_dir_box = components["project_videos_dir_box"]
    render_video_btn = components["render_video_btn"]
    runtime_refresh_timer = components["runtime_refresh_timer"]
    selected_asset_gallery = components["selected_asset_gallery"]
    segment_regen_btn = components["segment_regen_btn"]
    segment_regen_payload = components["segment_regen_payload"]
    segment_video_regen_btn = components["segment_video_regen_btn"]
    segment_video_regen_payload = components["segment_video_regen_payload"]
    segment_video_ok_btn = components["segment_video_ok_btn"]
    segment_video_ok_payload = components["segment_video_ok_payload"]
    segment_ok_filter_btn = components["segment_ok_filter_btn"]
    segment_prompt_save_btn = components["segment_prompt_save_btn"]
    segment_prompt_save_payload = components["segment_prompt_save_payload"]
    segment_prompt_batch_payload = components["segment_prompt_batch_payload"]
    episode_select_btn = components["episode_select_btn"]
    episode_select_payload = components["episode_select_payload"]
    open_videos_dir_btn = components["open_videos_dir_btn"]
    save_beats_btn = components["save_beats_btn"]
    split_beats_btn = components["split_beats_btn"]
    split_complex_beat_btn = components["split_complex_beat_btn"]
    status = components["status"]
    story_json = components["story_json"]
    submit_comfyui_box = components["submit_comfyui_box"]
    submit_runninghub_box = components["submit_runninghub_box"]
    deepseek_api_key_box = components["deepseek_api_key_box"]
    deepseek_model_box = components["deepseek_model_box"]
    fps_box = components["fps_box"]
    comfyui_backend_url_box = components["comfyui_backend_url_box"]
    runninghub_api_key_box = components["runninghub_api_key_box"]
    save_service_keys_btn = components["save_service_keys_btn"]
    save_workflow_config_btn = components["save_workflow_config_btn"]
    workflow_config_status = components["workflow_config_status"]
    topic = components["topic"]
    video_jobs_json = components["video_jobs_json"]
    workflow_mode_box = components["workflow_mode_box"]
    default_video_model_box = components["default_video_model_box"]
    workflow_only_box = components["workflow_only_box"]
    workflow_template_label = components["workflow_template_label"]
    model_workflow_template_box = components["model_workflow_template_box"]
    workflow_description_box = components["workflow_description_box"]
    runninghub_plus_row = components["runninghub_plus_row"]
    runninghub_concurrency_row = components["runninghub_concurrency_row"]
    runninghub_plus_box = components["runninghub_plus_box"]
    runninghub_concurrency_box = components["runninghub_concurrency_box"]
    workflow_progress_panel = components["workflow_progress_panel"]
    workbench_page = components["workbench_page"]
    asset_library_page = components["asset_library_page"]
    settings_page = components["settings_page"]
    settings_subpage = components["settings_subpage"]
    settings_model_page = components["settings_model_page"]
    settings_workflow_page = components["settings_workflow_page"]
    settings_developer_page = components["settings_developer_page"]
    feedback_part_select = components["feedback_part_select"]
    feedback_refresh_btn = components["feedback_refresh_btn"]
    feedback_issue_types = components["feedback_issue_types"]
    feedback_note = components["feedback_note"]
    feedback_analyze_btn = components["feedback_analyze_btn"]
    feedback_analysis = components["feedback_analysis"]
    feedback_apply_rules_btn = components["feedback_apply_rules_btn"]

    asset_outputs = [
        asset_gallery,
        asset_current_path,
        asset_target_path,
        bible_character_select,
        bible_background_select,
        selected_asset_gallery,
        asset_library_log,
    ]

    main_page.change(
        fn=_switch_main_page,
        inputs=main_page,
        outputs=[workbench_page, asset_library_page, settings_page],
        queue=False,
    )
    main_page.change(
        fn=refresh_asset_library_controls,
        inputs=[asset_current_path, asset_existing_select, asset_target_dir],
        outputs=[asset_existing_select, asset_target_dir],
        show_progress="hidden",
        queue=False,
    )
    settings_subpage.change(
        fn=_switch_settings_subpage,
        inputs=settings_subpage,
        outputs=[settings_model_page, settings_workflow_page, settings_developer_page],
        queue=False,
    )

    components["asset_metadata_parse_btn"].click(
        fn=fill_asset_metadata_from_text,
        inputs=[components["asset_metadata_paste"]],
        outputs=[asset_display_name, asset_aliases, asset_tags_cn,
                 asset_description_cn, asset_use_for, asset_pose,
                 components["asset_metadata_parse_status"]],
        queue=False,
    )

    asset_save_event = asset_save_meta_btn.click(
        fn=save_selected_asset_metadata,
        inputs=[
            asset_current_path,
            asset_type,
            asset_id,
            asset_target_dir,
            asset_display_name,
            asset_aliases,
            asset_tags_cn,
            asset_description_cn,
            asset_use_for,
            asset_pose,
        ],
        outputs=asset_outputs,
    )
    asset_save_event.then(
        fn=refresh_asset_library_controls,
        inputs=[asset_current_path, asset_existing_select, asset_target_dir],
        outputs=[asset_existing_select, asset_target_dir],
        show_progress="hidden",
        queue=False,
    )
    asset_target_dir.focus(
        fn=refresh_asset_target_dir_options,
        inputs=asset_target_dir,
        outputs=asset_target_dir,
        show_progress="hidden",
        queue=False,
    )
    asset_existing_select.change(
        fn=load_existing_asset_selection,
        inputs=asset_existing_select,
        outputs=[
            asset_type,
            asset_id,
            asset_target_dir,
            asset_display_name,
            asset_aliases,
            asset_tags_cn,
            asset_description_cn,
            asset_use_for,
            asset_pose,
            asset_gallery,
            asset_current_path,
            asset_target_path,
            asset_library_log,
        ],
        show_progress="hidden",
    )
    asset_upload_file.change(
        fn=preview_selected_asset_for_upload,
        inputs=[asset_type, asset_upload_file, asset_id, asset_target_dir],
        outputs=[
            asset_type,
            asset_id,
            asset_target_dir,
            asset_display_name,
            asset_aliases,
            asset_tags_cn,
            asset_description_cn,
            asset_use_for,
            asset_pose,
            asset_gallery,
            asset_current_path,
            asset_target_path,
            asset_library_log,
        ],
        show_progress="hidden",
    )
    for target_preview_input in (asset_type, asset_target_dir):
        target_preview_input.change(
            fn=preview_selected_asset_path_only,
            inputs=[asset_type, asset_upload_file, asset_id, asset_target_dir, asset_current_path],
            outputs=[
                asset_type,
                asset_id,
                asset_target_dir,
                asset_display_name,
                asset_aliases,
                asset_tags_cn,
                asset_description_cn,
                asset_use_for,
                asset_pose,
                asset_gallery,
                asset_current_path,
                asset_target_path,
                asset_library_log,
            ],
            show_progress="hidden",
        )
    asset_id.blur(
        fn=preview_selected_asset_path_only,
        inputs=[asset_type, asset_upload_file, asset_id, asset_target_dir, asset_current_path],
        outputs=[
            asset_type,
            asset_id,
            asset_target_dir,
            asset_display_name,
            asset_aliases,
            asset_tags_cn,
            asset_description_cn,
            asset_use_for,
            asset_pose,
            asset_gallery,
            asset_current_path,
            asset_target_path,
            asset_library_log,
        ],
        show_progress="hidden",
    )

    project_change_event = project_name.change(
        fn=_project_change,
        inputs=[project_name, topic],
        outputs=[
            output_dir,
            story_json,
            beats_json,
            video_jobs_json,
            status,
            project_output_dir_box,
            project_videos_dir_box,
            project_final_video_box,
            bible_character_select,
            bible_background_select,
            selected_asset_gallery,
            submit_comfyui_box,
            submit_runninghub_box,
            groups_panel,
            workflow_progress_panel,
            project_episode,
            default_video_model_box,
            novel_text,
            novel_segment_count,
            target_beat_count_box,
            topic,
        ],
    )
    project_change_event.then(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_picker_js,
    )
    project_episode_change_event = project_episode.change(
        fn=_set_current_project_episode_guarded,
        inputs=project_episode,
        outputs=[
            output_dir,
            story_json,
            beats_json,
            video_jobs_json,
            status,
            project_output_dir_box,
            project_videos_dir_box,
            project_final_video_box,
            bible_character_select,
            bible_background_select,
            selected_asset_gallery,
            groups_panel,
            workflow_progress_panel,
            novel_text,
            novel_segment_count,
            target_beat_count_box,
            topic,
        ],
    )
    project_episode_change_event.then(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_runtime_js,
    )
    episode_select_event = episode_select_btn.click(
        fn=_set_current_project_episode_from_picker_guarded,
        inputs=episode_select_payload,
        outputs=[
            output_dir,
            story_json,
            beats_json,
            video_jobs_json,
            status,
            project_output_dir_box,
            project_videos_dir_box,
            project_final_video_box,
            bible_character_select,
            bible_background_select,
            selected_asset_gallery,
            groups_panel,
            workflow_progress_panel,
            novel_text,
            novel_segment_count,
            target_beat_count_box,
            topic,
            project_episode,
        ],
    )
    episode_select_event.then(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_runtime_js,
    )
    rename_project_event = rename_project_btn.click(
        fn=_rename_current_project_guarded,
        inputs=project_rename_text,
        outputs=[project_name, project_episode, output_dir, status],
    )
    rename_project_event.then(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_picker_js,
    )
    archive_generation_event = archive_generation_btn.click(
        fn=_archive_current_generation_guarded,
        inputs=archive_episode_name,
        outputs=[archive_episode_name, status, archive_path_box, project_episode],
    )
    archive_generation_event.then(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_picker_js,
    )

    bible_character_select.change(
        fn=selected_project_asset_gallery,
        inputs=[bible_character_select, bible_background_select],
        outputs=selected_asset_gallery,
    )
    bible_background_select.change(
        fn=selected_project_asset_gallery,
        inputs=[bible_character_select, bible_background_select],
        outputs=selected_asset_gallery,
    )
    manual_bible_btn.click(
        fn=_manual_plan_project_bible_guarded,
        inputs=[topic, bible_character_select, bible_background_select],
        outputs=[status, selected_asset_gallery, bible_character_select, bible_background_select],
    )

    clean_event = clean_btn.click(
        fn=_clean_project_direct,
        outputs=[
            status,
            story_json,
            beats_json,
            video_jobs_json,
            bible_character_select,
            bible_background_select,
            selected_asset_gallery,
            submit_comfyui_box,
            submit_runninghub_box,
            groups_panel,
            workflow_progress_panel,
            novel_text,
            topic,
        ],
    )
    clean_event.success(
        fn=None,
        inputs=None,
        outputs=None,
        js=refresh_episode_runtime_js,
    )

    duration_select.change(fn=update_custom_duration_visibility, inputs=[duration_select, custom_duration_box], outputs=custom_duration_box)
    custom_duration_box.change(fn=save_duration_selection, inputs=[duration_select, custom_duration_box])
    workflow_only_box.change(
        fn=_on_workflow_only_change,
        inputs=[dry_run_box, workflow_only_box, no_concat_box, submit_comfyui_box, submit_runninghub_box],
        outputs=[submit_comfyui_box, submit_runninghub_box],
        queue=False,
        show_progress="hidden",
    )
    default_video_model_box.change(
        fn=save_default_video_model_with_workflow,
        inputs=default_video_model_box,
        outputs=[
            workflow_template_label,
            model_workflow_template_box,
            workflow_description_box,
            runninghub_plus_row,
            runninghub_concurrency_row,
            workflow_config_status,
        ],
        queue=False,
        show_progress="hidden",
    )
    auto_cleanup_box.change(
        fn=save_runtime_ui_options,
        inputs=[dry_run_box, workflow_only_box, no_concat_box, auto_cleanup_box, workflow_mode_box],
        queue=False,
        show_progress="hidden",
    )
    no_concat_box.change(
        fn=save_runtime_ui_options,
        inputs=[dry_run_box, workflow_only_box, no_concat_box],
        queue=False,
        show_progress="hidden",
    )
    minimal_mode_box.change(
        fn=save_runtime_ui_options,
        inputs=[dry_run_box, workflow_only_box, no_concat_box, auto_cleanup_box, workflow_mode_box, minimal_mode_box],
        queue=False,
        show_progress="hidden",
    )
    submit_comfyui_box.change(
        fn=_on_submit_comfyui_change,
        inputs=[submit_comfyui_box, submit_runninghub_box, workflow_only_box],
        outputs=[submit_comfyui_box, submit_runninghub_box],
        queue=False,
        show_progress="hidden",
    )
    submit_runninghub_box.change(
        fn=_on_submit_runninghub_change,
        inputs=[submit_comfyui_box, submit_runninghub_box, workflow_only_box],
        outputs=[submit_comfyui_box, submit_runninghub_box],
        queue=False,
        show_progress="hidden",
    )
    save_workflow_config_btn.click(
        fn=save_workflow_config,
        inputs=[
            default_video_model_box,
            model_workflow_template_box,
            runninghub_plus_box,
            runninghub_concurrency_box,
        ],
        outputs=workflow_config_status,
        show_progress="hidden",
    )

    gen_story_btn.click(
        fn=_generate_story_readonly_guarded,
        inputs=[topic, story_json, duration_select, custom_duration_box],
        outputs=[story_json, status],
        show_progress="hidden",
    )
    generate_novel_segments_btn.click(
        fn=_generate_novel_segments_with_ui,
        inputs=[novel_text, duration_select, custom_duration_box, novel_segment_count],
        outputs=[video_jobs_json, status, groups_panel, workflow_progress_panel, beats_json, novel_text],
        show_progress="full",
    )
    split_beats_btn.click(
        fn=_split_beats_with_cleared_segments,
        inputs=[story_json, duration_select, custom_duration_box, target_beat_count_box, topic],
        outputs=[
            story_json,
            beats_json,
            video_jobs_json,
            status,
            groups_panel,
            workflow_progress_panel,
            beat_risk_report,
            complex_beat_index,
            beat_risk_notice,
            ignore_beat_risk_box,
        ],
        js="""(...values) => {
            const field = document.getElementById('target_beat_count_proxy');
            values[3] = field ? field.value : values[3];
            return values;
        }""",
        show_progress="hidden",
    )
    save_beats_btn.click(
        fn=_save_story_beats_and_refresh,
        inputs=[story_json, beats_json, topic],
        outputs=[story_json, beats_json, video_jobs_json, status, groups_panel, workflow_progress_panel],
        show_progress="hidden",
    )
    detect_beat_risk_btn.click(
        fn=_detect_beat_risks,
        inputs=[story_json, beats_json],
        outputs=[story_json, beats_json, beat_risk_report, complex_beat_index, beat_risk_notice, status],
        show_progress="full",
    )
    split_complex_beat_btn.click(
        fn=_split_complex_beat_from_ui,
        inputs=[story_json, beats_json, complex_beat_index],
        outputs=[
            story_json,
            beats_json,
            video_jobs_json,
            status,
            groups_panel,
            workflow_progress_panel,
            beat_risk_report,
            complex_beat_index,
            beat_risk_notice,
            ignore_beat_risk_box,
        ],
        show_progress="full",
    )
    copy_beat_btn.click(
        fn=_copy_beat_from_ui,
        inputs=[story_json, beats_json, copy_beat_source, copy_beat_target],
        outputs=[story_json, beats_json, video_jobs_json, status, groups_panel,
                 workflow_progress_panel, beat_risk_report, complex_beat_index,
                 beat_risk_notice, ignore_beat_risk_box, copy_beat_btn],
        show_progress="full",
        trigger_mode="once",
        concurrency_limit=1,
    )
    add_beat_btn.click(
        fn=_add_beat_from_ui,
        inputs=[story_json, beats_json, complex_beat_index],
        outputs=[
            story_json,
            beats_json,
            video_jobs_json,
            status,
            groups_panel,
            workflow_progress_panel,
            beat_risk_report,
            complex_beat_index,
            beat_risk_notice,
            ignore_beat_risk_box,
        ],
        show_progress="full",
    )
    delete_beat_btn.click(
        fn=_delete_beat_from_ui,
        inputs=[story_json, beats_json, complex_beat_index],
        outputs=[
            story_json,
            beats_json,
            video_jobs_json,
            status,
            groups_panel,
            workflow_progress_panel,
            beat_risk_report,
            complex_beat_index,
            beat_risk_notice,
            ignore_beat_risk_box,
        ],
        show_progress="full",
    )
    group_prev_page_btn.click(
        fn=lambda groups_text, page, unmarked_only: _segment_pager_updates(groups_text, page, unmarked_only, -1),
        inputs=[video_jobs_json, group_page_state, segment_ok_filter_state],
        outputs=[group_page_state, groups_panel, group_page_label, group_prev_page_btn, group_next_page_btn],
        show_progress="hidden",
    )
    group_next_page_btn.click(
        fn=lambda groups_text, page, unmarked_only: _segment_pager_updates(groups_text, page, unmarked_only, 1),
        inputs=[video_jobs_json, group_page_state, segment_ok_filter_state],
        outputs=[group_page_state, groups_panel, group_page_label, group_prev_page_btn, group_next_page_btn],
        show_progress="hidden",
    )
    video_jobs_json.change(
        fn=lambda groups_text, page, unmarked_only: _segment_pager_updates(groups_text, page, unmarked_only),
        inputs=[video_jobs_json, group_page_state, segment_ok_filter_state],
        outputs=[group_page_state, groups_panel, group_page_label, group_prev_page_btn, group_next_page_btn],
        show_progress="hidden",
    )
    segment_ok_filter_btn.click(
        fn=_toggle_segment_ok_filter,
        inputs=[video_jobs_json, group_page_state, segment_ok_filter_state],
        outputs=[segment_ok_filter_state, group_page_state, groups_panel, group_page_label, group_prev_page_btn, group_next_page_btn],
        show_progress="hidden",
    )
    generate_segment_prompts_btn.click(
        fn=_generate_segment_prompts,
        inputs=[story_json, beats_json, workflow_mode_box, group_page_state, ignore_beat_risk_box],
        outputs=[story_json, beats_json, video_jobs_json, status, groups_panel, workflow_progress_panel, beat_risk_notice],
        show_progress="hidden",
    )
    render_video_btn.click(
        fn=_render_video,
        inputs=[
            story_json,
            beats_json,
            dry_run_box,
            workflow_only_box,
            submit_comfyui_box,
            submit_runninghub_box,
            no_concat_box,
            workflow_mode_box,
            group_page_state,
            segment_prompt_batch_payload,
        ],
        outputs=[story_json, beats_json, video_jobs_json, status, groups_panel, workflow_progress_panel],
        js="""(...values) => {
            values[9] = JSON.stringify((window.AICF && window.AICF.collectSegmentPromptEdits)
                ? window.AICF.collectSegmentPromptEdits()
                : []);
            return values;
        }""",
        show_progress="hidden",
    )
    merge_video_btn.click(fn=_merge_video, outputs=[status, video_jobs_json, workflow_progress_panel], show_progress="hidden")
    segment_regen_btn.click(
        fn=_regen_segment,
        inputs=[segment_regen_payload, group_page_state, story_json, beats_json],
        outputs=[story_json, video_jobs_json, status, groups_panel, workflow_progress_panel],
        show_progress="full",
    )
    segment_video_regen_btn.click(
        fn=_regen_segment_video,
        inputs=[segment_video_regen_payload, group_page_state],
        outputs=[story_json, video_jobs_json, status, groups_panel, workflow_progress_panel],
        trigger_mode="multiple",
        concurrency_limit=_segment_video_regen_concurrency_limit(),
        concurrency_id="segment_video_regen",
        show_progress="hidden",
    )
    segment_video_ok_btn.click(
        fn=_toggle_segment_video_ok,
        inputs=[segment_video_ok_payload, group_page_state, segment_ok_filter_state],
        outputs=[
            video_jobs_json,
            status,
            group_page_state,
            groups_panel,
            workflow_progress_panel,
            group_page_label,
            group_prev_page_btn,
            group_next_page_btn,
        ],
        show_progress="hidden",
    )
    segment_prompt_save_btn.click(
        fn=_save_segment_prompt,
        inputs=[segment_prompt_save_payload, group_page_state],
        outputs=[video_jobs_json, status, groups_panel, workflow_progress_panel, beats_json],
        show_progress="hidden",
    )
    feedback_refresh_btn.click(fn=refresh_video_feedback_parts, outputs=feedback_part_select)
    feedback_analyze_btn.click(
        fn=_analyze_video_feedback_guarded,
        inputs=[feedback_part_select, feedback_issue_types, feedback_note],
        outputs=[feedback_analysis, status],
    )
    feedback_apply_rules_btn.click(fn=_apply_video_feedback_rules_guarded, inputs=feedback_analysis, outputs=status)

    one_click_full_btn.click(
        fn=_one_click_direct,
        inputs=[
            topic,
            submit_comfyui_box,
            submit_runninghub_box,
            workflow_mode_box,
            auto_cleanup_box,
            duration_select,
            custom_duration_box,
            target_beat_count_box,
            group_page_state,
            one_click_resume_mode,
            workflow_only_box,
            no_concat_box,
        ],
        outputs=[story_json, beats_json, video_jobs_json, status, groups_panel, workflow_progress_panel, one_click_full_btn],
        js="""(...values) => {
            const field = document.getElementById('target_beat_count_proxy');
            values[7] = field ? field.value : values[7];
            return values;
        }""",
        show_progress="hidden",
    )

    runtime_refresh_timer.tick(
        fn=_runtime_status_refresh,
        inputs=[group_page_state],
        outputs=[video_jobs_json, groups_panel, workflow_progress_panel, project_episode],
        queue=False,
        show_progress="hidden",
    )

    clear_log_btn.click(fn=clear_logs, outputs=status)
    final_video_refresh_btn.click(fn=latest_final_video_preview_payload, outputs=[final_video_path_display, final_video_preview_payload], queue=False)
    open_videos_dir_btn.click(fn=_open_videos_dir, outputs=status, queue=False, show_progress="hidden")
    save_service_keys_btn.click(
        fn=save_service_keys,
        inputs=[
            deepseek_api_key_box,
            runninghub_api_key_box,
            comfyui_backend_url_box,
            deepseek_model_box,
            fps_box,
            output_dir,
        ],
        outputs=[deepseek_api_key_box, runninghub_api_key_box, comfyui_backend_url_box, status],
        queue=False,
    )

    demo.load(
        fn=_restore_direct_state,
        outputs=[
            topic,
            duration_select,
            custom_duration_box,
            story_json,
            beats_json,
            video_jobs_json,
            status,
            bible_character_select,
            bible_background_select,
            selected_asset_gallery,
            dry_run_box,
            workflow_only_box,
            submit_comfyui_box,
            submit_runninghub_box,
            no_concat_box,
            groups_panel,
            workflow_progress_panel,
            group_page_state,
            group_page_label,
            group_prev_page_btn,
            group_next_page_btn,
            default_video_model_box,
            novel_text,
            novel_segment_count,
            target_beat_count_box,
        ],
    )

    return demo
