"""Project selection, restore, duration, and runtime UI handlers."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import re
import shutil
import traceback
from html import escape
from pathlib import Path

import gradio as gr

from pipeline.beats import split_beats
from pipeline.novel_segments import generate_novel_beats
from pipeline.events import ensure_event_fields_on_beats
from pipeline.quality_gate import (
    format_quality_block_message,
    quality_issues_payload,
    strip_quality_annotations,
    validate_beats_display,
    validate_story_display,
)
from pipeline.story import generate_story
from prompt_builder.prompt_ir import role_display_name
from services.context import (
    BASE_DIR,
    CONFIG,
    CONFIG_PATH,
    EPISODES_DIR,
    OUTPUT_DIR,
    PROJECTS_DIR,
    assert_current_runtime_writable,
    get_current_episode_name,
    get_current_project_name,
    get_episodes_dir,
    get_output_dir,
    get_project_dir,
    set_current_episode,
    set_current_project_name,
)
from services.duration import DEFAULT_DURATION_SECONDS, DURATION_PRESETS, duration_state_values, get_final_duration_seconds
from services.file_utils import ensure_project_dirs, load_json_file, load_json_file_silent
from services.logger import get_logs, log
from services.ui_run_config import load_ui_run_config, update_ui_run_config
from services.secrets import (
    DEEPSEEK_API_KEY_ENV,
    RUNNINGHUB_API_KEY_ENV,
    masked_secret,
    set_secret,
)
from ui.group_panel import render_groups_panel, render_guide_reference_gallery
from ui.progress_panel import one_click_is_active, render_workflow_progress, workflow_status_from_run_state
from ui.project_bible_panel import project_bible_ui_state


QUALITY_ISSUES_FILE = "quality_issues.json"
NOVEL_SOURCE_FILE = "novel_source.json"


def _quality_issues_path() -> Path:
    return get_project_dir() / QUALITY_ISSUES_FILE


def _novel_source_path() -> Path:
    return get_project_dir() / NOVEL_SOURCE_FILE


def load_novel_source_text() -> str:
    """Load the verbatim novel submitted for the active project/episode."""
    path = _novel_source_path()
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("text") or "")
    return str(payload) if isinstance(payload, str) else ""


def save_novel_source_text(novel_text: str, *, source: str = "generate_novel_segments") -> str:
    """Persist the latest submitted novel before model generation starts."""
    assert_current_runtime_writable("保存完整小说")
    text = str(novel_text or "")
    if not text.strip():
        raise ValueError("完整小说不能为空")
    ensure_project_dirs()
    path = _novel_source_path()
    payload = {
        "schema": "novel_source.v1",
        "source": str(source or "generate_novel_segments"),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
    }
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return text


def novel_source_ui_update():
    """Return project/episode-owned novel text with archived episodes read-only."""
    return gr.update(
        value=load_novel_source_text(),
        interactive=not bool(get_current_episode_name()),
    )


def persist_quality_issues(issues) -> None:
    ensure_project_dirs()
    path = _quality_issues_path()
    if not issues:
        clear_quality_issues()
        return
    path.write_text(json.dumps(quality_issues_payload(issues), ensure_ascii=False, indent=2), encoding="utf-8")


def clear_quality_issues() -> None:
    path = _quality_issues_path()
    if path.exists():
        path.unlink()


def mark_and_persist_quality_issues(story_text: str = "", beats_text: str = "") -> tuple[str, str, list]:
    story_marked, story_issues = validate_story_display(story_text or "")
    if str(beats_text or "").strip():
        beats_marked, beat_issues = validate_beats_display(beats_text or "")
    else:
        beats_marked, beat_issues = beats_text or "", []
    issues = story_issues + beat_issues
    persist_quality_issues(issues)
    return story_marked, beats_marked, issues


INVALID_PROJECT_NAME_CHARS = set('<>:"/\\|?*')


def _safe_project_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise ValueError("项目名称不能为空")
    if text in {".", ".."} or any(ch in INVALID_PROJECT_NAME_CHARS for ch in text):
        raise ValueError("项目名称不能包含 <>:\"/\\|?* 或 . ..")
    return text


def _sidecar_project_dir(name: str) -> Path:
    return PROJECTS_DIR / name


def _project_choices():
    return [("当前项目" if p == "current" else p, p) for p in list_projects()]


def list_projects():
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not (PROJECTS_DIR / "current").exists():
        (PROJECTS_DIR / "current").mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "current").mkdir(parents=True, exist_ok=True)
    names = sorted([p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()])
    if "current" not in names:
        names.insert(0, "current")
    return names


def list_project_episodes(project_name=None):
    name = _safe_project_name(project_name or get_current_project_name() or "current")
    root = EPISODES_DIR / name
    episodes = [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
    episodes.sort(key=_episode_sort_key)
    return [("当前生成", "")] + [(episode, episode) for episode in episodes]


def _episode_sort_key(episode_name: str) -> tuple[int, int, str]:
    """Keep numbered episode directories in their natural viewing order."""
    name = str(episode_name or "")
    digits = "".join(ch for ch in name if ch.isdigit())
    if digits:
        return (0, int(digits), name.casefold())
    return (1, 0, name.casefold())


def _episode_choices(project_name=None):
    return list_project_episodes(project_name)


def _project_file_text(name: str) -> str:
    return load_json_file_silent(name) or ""


def story_json_to_display(text: str) -> str:
    """Show only the concrete story text in the UI."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(data, dict):
        return raw
    return str(data.get("story") or data.get("summary") or data.get("ending") or "").strip()


def beats_json_to_display(text: str) -> str:
    """Show beats as editable plot text with editable per-beat duration."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    beats = data.get("beats") if isinstance(data, dict) else []
    if not isinstance(beats, list):
        return raw
    blocks = []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        title = str(beat.get("title") or f"Beat {index}").strip()
        plot = str(beat.get("plot") or "").strip()
        duration = _beat_duration_seconds(beat.get("estimated_duration_sec") or beat.get("duration_sec") or 15)
        blocks.append(f"【Beat {index}】{title}\n时长：{duration}秒\n{plot}".strip())
    return "\n\n".join(blocks)


def _story_display_to_json(display_text: str) -> dict:
    display_text = strip_quality_annotations(display_text)
    existing_raw = load_json_file_silent("story.json") or "{}"
    try:
        data = json.loads(existing_raw)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    story = str(display_text or "").strip()
    data["story"] = story
    data["summary"] = story[:120]
    # Manual editor text is the current source of truth. Do not keep an old
    # title from a previous story, otherwise beat generation may mix in stale
    # plot traces through payload.title.
    data["title"] = story[:32] or "手动剧情"
    return data


DURATION_LINE_RE = re.compile(
    r"^\s*(?:时长|持续时长|预计时长|estimated_duration_sec|duration_sec|duration)\s*[:：=]\s*(?:约\s*)?(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds)?\s*$",
    re.IGNORECASE,
)
BEAT_HEADING_LINE_RE = re.compile(
    r"^\s*(?:【|\[)\s*Beat\s*(\d+)\s*(?:】|\])?\s*(.*)$",
    re.IGNORECASE,
)
BEAT_HEADING_FIND_RE = re.compile(
    r"(?m)^\s*(?:【|\[)\s*Beat\s*\d+\s*(?:】|\])?.*$",
    re.IGNORECASE,
)


def _beat_duration_seconds(value, default=15) -> int:
    try:
        duration = int(round(float(value)))
    except Exception:
        duration = int(default or 15)
    return max(1, duration)


def _parse_beat_display_blocks(display_text: str) -> list[dict]:
    text = strip_quality_annotations(display_text).strip()
    if not text:
        return []
    matches = list(BEAT_HEADING_FIND_RE.finditer(text))
    if not matches:
        return [
            {"title": f"剧情段落{index}", "duration": 15, "plot": part.strip()}
            for index, part in enumerate(re.split(r"\n\s*\n", text), start=1)
            if part.strip()
        ]
    blocks = []
    for index, match in enumerate(matches, start=1):
        heading = match.group(0).strip()
        heading_match = BEAT_HEADING_LINE_RE.match(heading)
        title = str((heading_match.group(2) if heading_match else "") or f"剧情段落{index}").strip()
        start = match.end()
        end = matches[index].start() if index < len(matches) else len(text)
        body_lines = []
        duration = None
        for line in text[start:end].strip().splitlines():
            duration_match = DURATION_LINE_RE.match(line.strip())
            if duration_match and duration is None:
                duration = _beat_duration_seconds(duration_match.group(1))
                continue
            body_lines.append(line)
        blocks.append({"title": title, "duration": duration or 15, "plot": "\n".join(body_lines).strip()})
    return blocks


def _beats_data_without_durations(data: dict) -> dict:
    cloned = copy.deepcopy(data if isinstance(data, dict) else {})
    for beat in cloned.get("beats") or []:
        if isinstance(beat, dict):
            beat.pop("estimated_duration_sec", None)
            beat.pop("duration_sec", None)
    cloned.pop("total_duration_sec", None)
    return cloned


def _editable_beat_content(beat: dict) -> dict:
    """Return only the fields directly editable in the Beat editor.

    Asset matching and event enrichment are derived data. They must not make a
    duration-only edit look like a story edit and clear LiconMSR prompts.
    """
    source = beat if isinstance(beat, dict) else {}
    return {
        "title": str(source.get("title") or "").strip(),
        "plot": str(source.get("plot") or "").strip(),
    }


def _editable_beat_content_is_unchanged(old_beats_data: dict, new_beats_data: dict) -> bool:
    old_beats = old_beats_data.get("beats") if isinstance(old_beats_data.get("beats"), list) else []
    new_beats = new_beats_data.get("beats") if isinstance(new_beats_data.get("beats"), list) else []
    if len(old_beats) != len(new_beats):
        return False
    return all(
        _editable_beat_content(old_beat) == _editable_beat_content(new_beat)
        for old_beat, new_beat in zip(old_beats, new_beats)
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


def _sync_video_jobs_with_beat_durations(beats_data: dict, video_jobs_path: Path) -> str:
    if not video_jobs_path.exists():
        return ""
    try:
        payload = json.loads(video_jobs_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return load_json_file_silent("video_jobs.json") or ""
    if not isinstance(payload, dict):
        return load_json_file_silent("video_jobs.json") or ""
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    fps = int(payload.get("fps") or CONFIG.get("fps") or 50)
    changed_parts = []
    for index, beat in enumerate(beats or [], start=1):
        if not isinstance(beat, dict):
            continue
        duration = _beat_duration_seconds(beat.get("estimated_duration_sec") or beat.get("duration_sec") or 15)
        for segment in segments:
            if not isinstance(segment, dict) or int(segment.get("segment_index") or 0) != index:
                continue
            job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
            old_duration = _beat_duration_seconds(job.get("duration_sec") or 15)
            if old_duration != duration:
                job["duration_sec"] = duration
                job["fps"] = int(job.get("fps") or fps)
                job["total_frames"] = duration * int(job.get("fps") or fps)
                segment["job"] = job
                # Also sync duration to generation_config and requested_media_spec
                # which the pipeline reads when building workflows.
                gen_cfg = segment.get("generation_config")
                if isinstance(gen_cfg, dict):
                    gen_cfg["duration_sec"] = duration
                    segment["generation_config"] = gen_cfg
                media_spec = segment.get("requested_media_spec")
                if isinstance(media_spec, dict):
                    media_spec["duration_sec"] = duration
                    media_spec["requested_duration_sec"] = duration
                    # Update aligned frame count for H3 (24 fps) or LTX (50 fps).
                    spec_fps = int(media_spec.get("fps") or fps)
                    aligned_frames = _h3_aligned_frame_count(duration) if spec_fps == 24 else duration * spec_fps
                    media_spec["aligned_frame_count"] = int(aligned_frames)
                    media_spec["aligned_duration_sec"] = aligned_frames / spec_fps
                    segment["requested_media_spec"] = media_spec
                # Update the saved workflow JSON so regenerating isn't required.
                _patch_workflow_duration(segment, duration)
                changed_parts.append(str(segment.get("part_id") or f"part_{index:03d}"))
            break
    if changed_parts:
        payload["segments"] = segments
        payload["segment_count"] = len(segments)
        payload["total_duration_sec"] = sum(_beat_duration_seconds((item.get("job") or {}).get("duration_sec") or 0, 0) for item in segments if isinstance(item, dict))
        payload["fps"] = fps
        video_jobs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[editor] 已仅同步 Beat 时长到 LiconMSR 分段：{', '.join(changed_parts)}；提示词和视频保持不变", "STEP")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _h3_aligned_frame_count(duration_sec: float) -> int:
    """H3 uses 24 fps with a 17-frame alignment rule."""
    frames = max(5, round(float(duration_sec) * 24))
    return frames + (5 - (frames % 17)) % 17


def _patch_workflow_duration(segment, duration_sec):
    """Update the Duration primitive inside a saved workflow JSON when it exists."""
    workflow_path = str(segment.get("workflow_path") or "").strip()
    if not workflow_path:
        return
    path = Path(workflow_path)
    if not path.exists():
        return
    try:
        import json as _json
        wf = _json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(wf, dict):
            return
        _update_duration_primitive(wf, float(duration_sec))
        path.write_text(_json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _update_duration_primitive(node, duration):
    """Recursively find a PrimitiveFloat 'Float (Duration)' node and set its value."""
    if isinstance(node, dict):
        meta = node.get("_meta")
        if isinstance(meta, dict) and str(meta.get("title") or "") == "Float (Duration)":
            if str(node.get("class_type") or "") == "PrimitiveFloat":
                node["inputs"] = {"value": duration}
                return True
        for _key, value in node.items():
            if _update_duration_primitive(value, duration):
                return True
    elif isinstance(node, list):
        for item in node:
            if _update_duration_primitive(item, duration):
                return True
    return False


def _beat_content_without_duration(beat: dict) -> dict:
    cloned = copy.deepcopy(beat if isinstance(beat, dict) else {})
    cloned.pop("estimated_duration_sec", None)
    cloned.pop("duration_sec", None)
    return cloned


def _first_changed_beat_index(old_beats: list, new_beats: list) -> int:
    limit = min(len(old_beats), len(new_beats))
    for index in range(1, limit + 1):
        old_beat = old_beats[index - 1] if isinstance(old_beats[index - 1], dict) else {}
        new_beat = new_beats[index - 1] if isinstance(new_beats[index - 1], dict) else {}
        if not _json_semantically_equal(_beat_content_without_duration(old_beat), _beat_content_without_duration(new_beat)):
            return index
    return limit + 1 if len(old_beats) != len(new_beats) else 0


def _changed_beat_indices(old_beats_data: dict, new_beats_data: dict) -> tuple[list[int], bool]:
    old_beats = old_beats_data.get("beats") if isinstance(old_beats_data.get("beats"), list) else []
    new_beats = new_beats_data.get("beats") if isinstance(new_beats_data.get("beats"), list) else []
    count_changed = len(old_beats) != len(new_beats)
    if count_changed:
        first_changed = _first_changed_beat_index(old_beats, new_beats)
        if first_changed <= 0:
            first_changed = min(len(old_beats), len(new_beats)) + 1
        # A manual beat count edit is ambiguous. Keep earlier aligned parts and
        # invalidate from the first divergent index so stale later parts cannot be merged.
        return list(range(first_changed, max(len(old_beats), len(new_beats)) + 1)), True

    changed: list[int] = []
    for index, (old_beat, new_beat) in enumerate(zip(old_beats, new_beats), start=1):
        old_content = _beat_content_without_duration(old_beat if isinstance(old_beat, dict) else {})
        new_content = _beat_content_without_duration(new_beat if isinstance(new_beat, dict) else {})
        if not _json_semantically_equal(old_content, new_content):
            changed.append(index)
    return changed, False


def _mark_video_jobs_for_changed_beats(
    old_beats_data: dict,
    new_beats_data: dict,
    video_jobs_path: Path,
    reason: str = "beat content changed",
) -> str:
    if not video_jobs_path.exists():
        return ""
    try:
        payload = json.loads(video_jobs_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return load_json_file_silent("video_jobs.json") or ""
    if not isinstance(payload, dict):
        return load_json_file_silent("video_jobs.json") or ""

    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    changed_indices, count_changed = _changed_beat_indices(old_beats_data, new_beats_data)
    if not changed_indices:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    changed_parts: list[str] = []
    protected_parts: list[str] = []
    for index in changed_indices:
        for segment in segments:
            if not isinstance(segment, dict) or int(segment.get("segment_index") or 0) != int(index):
                continue
            part_id = str(segment.get("part_id") or f"part_{index:03d}")
            if _segment_preserve_on_reset(segment):
                protected_parts.append(part_id)
                break
            job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
            old_video_path = (
                str(segment.get("stale_video_path") or "").strip()
                or str(job.get("stale_video_path") or "").strip()
                or str(segment.get("video_path") or "").strip()
                or str(job.get("video_path") or "").strip()
            )
            segment["status"] = "needs_regenerate"
            segment["stale_reason"] = reason
            segment["prompt_saved"] = False
            segment["submitted"] = False
            segment["backend"] = ""
            segment["prompt_id"] = ""
            segment["task_id"] = ""
            segment["output_urls"] = []
            segment["video_path"] = ""
            segment["workflow_path"] = ""
            segment["submit_result"] = {}
            segment["raw_submit"] = {}
            segment["error"] = ""
            if old_video_path:
                segment["stale_video_path"] = old_video_path
            for key in ("prompt", "director_prompt", "final_prompt", "workflow_path", "video_path"):
                job[key] = ""
            if old_video_path:
                job["stale_video_path"] = old_video_path
                job["stale_reason"] = reason
            segment["job"] = job
            changed_parts.append(part_id)
            break

    if changed_parts:
        payload["segments"] = segments
        payload["segment_count"] = len(segments)
        payload["total_duration_sec"] = sum(
            _beat_duration_seconds((item.get("job") or {}).get("duration_sec") or 0, 0)
            for item in segments
            if isinstance(item, dict)
        )
        payload["fps"] = int(payload.get("fps") or CONFIG.get("fps") or 50)
        payload.pop("final_video_path", None)
        video_jobs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from ui.render_state import mark_part_needs_regenerate

            for part_id in changed_parts:
                mark_part_needs_regenerate(part_id, reason)
        except Exception:
            pass
        log(f"[editor] beats 已修改，仅标记对应 LiconMSR 分段需补齐：{', '.join(changed_parts)}", "STEP")
        if count_changed:
            log("[editor][warn] 检测到 Beat 数量变化；如需调整序号，请优先使用拆分/删除 Beat 按钮完成迁移。", "WARN")
    if protected_parts:
        log(f"[editor] 已跳过视频OK保护分段：{', '.join(protected_parts)}", "STEP")
    return json.dumps(payload, ensure_ascii=False, indent=2)


QUOTE_RE = re.compile(r"[\"'“”‘’「」『』《》](.+?)[\"'“”‘’「」『』《》]")
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _primary_role_for_beat(beat: dict) -> str:
    for key in ("reference_roles", "visible_roles", "important_roles"):
        for role in beat.get(key) or []:
            role_text = str(role or "").strip()
            if role_text:
                return role_text
    return ""


def _role_from_context(context: str, roles: list[str], fallback: str = "") -> str:
    best_role = ""
    best_pos = -1
    for role in roles:
        for token in (role, role_display_name(role)):
            token = str(token or "").strip()
            if not token:
                continue
            pos = str(context or "").rfind(token)
            if pos > best_pos:
                best_role = role
                best_pos = pos
    return best_role or fallback


def _infer_object_from_action(action: str) -> str:
    text = str(action or "")
    for token in ("足球", "球门", "标志桶", "篮球", "车", "方向盘", "手机", "纸张"):
        if token in text:
            return token
    return ""


def _clean_action_sentence(sentence: str) -> str:
    text = re.sub(r"[\"'“”‘’「」『』《》].*?[\"'“”‘’「」『』《》]", "", str(sentence or "")).strip()
    if re.match(r"^(镜头|画面|摄像机|阳光|场景|背景|全程|所有人物)", text):
        return ""
    return text[:80].strip(" ，,。；;")


def _rebuild_beat_auxiliary_fields(beat: dict) -> dict:
    plot = str(beat.get("plot") or "").strip()
    roles = [
        str(role or "").strip()
        for role in (beat.get("visible_roles") or beat.get("reference_roles") or beat.get("important_roles") or [])
        if str(role or "").strip()
    ]
    primary_role = _primary_role_for_beat(beat)
    if primary_role and primary_role not in roles:
        roles.insert(0, primary_role)
    dialogue_units = []
    action_units = []
    event_units = []
    for match in QUOTE_RE.finditer(plot):
        line = str(match.group(1) or "").strip()
        if not line:
            continue
        context = plot[max(0, match.start() - 90):match.start()]
        speaker = _role_from_context(context, roles, primary_role)
        if not speaker:
            continue
        action = _clean_action_sentence(context.split("。")[-1] if context else "") or "准备说话"
        dialogue_units.append({"speaker_name": speaker, "line": line, "speech_type": "onscreen", "visible": speaker in roles, "target_role": ""})
        event_units.append({"type": "speech_event", "actor": speaker, "action": action, "dialogue": line, "target_role": "", "object": _infer_object_from_action(action)})
    if not dialogue_units:
        for sentence in SENTENCE_SPLIT_RE.split(plot):
            action = _clean_action_sentence(sentence)
            if not action:
                continue
            role = _role_from_context(action, roles, primary_role)
            if not role:
                continue
            obj = _infer_object_from_action(action)
            action_units.append({
                "role": role,
                "action": action,
                "target_role": "",
                "object": obj,
                "motion_scale": "medium" if any(token in action for token in ("跑", "跳", "踢", "冲", "追", "转", "射门")) else "small",
                "timing": "during",
                "visibility": "onscreen",
            })
            event_units.append({"type": "silent_action", "actor": role, "action": action, "dialogue": "", "target_role": "", "object": obj})
            if len(action_units) >= 6:
                break
    beat["dialogue_units"] = dialogue_units
    beat["action_units"] = action_units
    beat["event_units"] = event_units
    if not dialogue_units and not action_units and plot and primary_role:
        fallback_action = _clean_action_sentence(SENTENCE_SPLIT_RE.split(plot)[0] if SENTENCE_SPLIT_RE.split(plot) else plot) or plot[:80]
        beat["action_units"] = [{
            "role": primary_role,
            "action": fallback_action,
            "target_role": "",
            "object": _infer_object_from_action(fallback_action),
            "motion_scale": "medium",
            "timing": "during",
            "visibility": "onscreen",
        }]
        beat["event_units"] = [{
            "type": "silent_action",
            "actor": primary_role,
            "action": fallback_action,
            "dialogue": "",
            "target_role": "",
            "object": _infer_object_from_action(fallback_action),
        }]
    beat["offscreen_speakers"] = []
    beat["mentioned_roles"] = [role for role in beat.get("mentioned_roles") or [] if role not in set(roles)]
    locked_roles = list(dict.fromkeys([role for role in (beat.get("reference_roles") or roles) if role]))
    identity_constraints = beat.get("identity_constraints") if isinstance(beat.get("identity_constraints"), dict) else {}
    beat["identity_constraints"] = {**identity_constraints, "registered_identity_only": True, "locked_roles": locked_roles[:4]}
    return beat


def _clean_action_sentence(sentence: str) -> str:
    text = re.sub(r"[\"'“”‘’「」『』《》].*?[\"'“”‘’「」『』《》]", "", str(sentence or "")).strip()
    skip_prefixes = (
        "\u955c\u5934",
        "\u753b\u9762",
        "\u6444\u50cf\u673a",
        "\u9633\u5149",
        "\u573a\u666f",
        "\u80cc\u666f",
        "\u5168\u7a0b",
        "\u6240\u6709\u4eba\u7269",
    )
    if text.startswith(skip_prefixes) or re.match(r"^(camera|shot|scene|background|all characters|no dialogue)", text, flags=re.IGNORECASE):
        return ""
    return text[:80].strip(" ，,。；;")


def _beats_display_to_json(display_text: str) -> dict:
    existing_raw = load_json_file_silent("beats.json") or "{}"
    try:
        data = json.loads(existing_raw)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    beats = data.get("beats") if isinstance(data.get("beats"), list) else []
    blocks = _parse_beat_display_blocks(display_text)
    if not beats:
        beats = [
            {
                "id": index,
                "title": block.get("title") or f"剧情段落{index}",
                "plot": block.get("plot") or "",
                "important_roles": [],
                "visible_roles": [],
                "reference_roles": [],
                "offscreen_speakers": [],
                "mentioned_roles": [],
                "background_extras": [],
                "estimated_duration_sec": _beat_duration_seconds(block.get("duration") or 15),
            }
            for index, block in enumerate(blocks, start=1)
        ]
        data["beats"] = beats
    updated_beats = []
    for index, block in enumerate(blocks, start=1):
        has_existing = index - 1 < len(beats) and isinstance(beats[index - 1], dict)
        existing = beats[index - 1] if has_existing else {}
        beat = copy.deepcopy(existing)
        old_title = str(beat.get("title") or "").strip()
        old_plot = str(beat.get("plot") or "").strip()
        new_title = block.get("title") or old_title or f"剧情段落{index}"
        new_plot = block.get("plot") or ""
        beat["id"] = int(beat.get("id") or index)
        beat["order"] = index
        beat["node_id"] = str(beat.get("node_id") or f"beat_{index:03d}")
        beat["title"] = new_title
        beat["plot"] = new_plot
        beat["estimated_duration_sec"] = _beat_duration_seconds(block.get("duration") or beat.get("estimated_duration_sec") or beat.get("duration_sec") or 15)
        if not has_existing or old_plot != str(new_plot).strip():
            beat = _rebuild_beat_auxiliary_fields(beat)
        updated_beats.append(beat)
    beats = updated_beats
    data["beats"] = beats
    data["total_duration_sec"] = sum(_beat_duration_seconds(beat.get("estimated_duration_sec") or 0, 0) for beat in beats if isinstance(beat, dict))
    if not data.get("target_duration_sec"):
        data["target_duration_sec"] = data["total_duration_sec"]
    return data


def _json_semantically_equal(left: dict, right: dict) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(right, ensure_ascii=False, sort_keys=True)


def save_story_and_beats_edits(story_text, beats_text, topic_text=None):
    try:
        assert_current_runtime_writable("保存剧情或 Beats")
        ensure_project_dirs()
        if topic_text is not None:
            update_ui_run_config(topic=str(topic_text or "").strip())
        clean_story_text = strip_quality_annotations(story_text)
        clean_beats_text = strip_quality_annotations(beats_text)
        existing_story_text = story_json_to_display(load_json_file_silent("story.json") or "")
        story_changed = strip_quality_annotations(existing_story_text).strip() != clean_story_text.strip()
        if story_changed:
            story_marked, story_issues = validate_story_display(clean_story_text)
            beats_marked, beat_issues = "", []
            issues = story_issues
            persist_quality_issues(issues)
        else:
            story_marked, beats_marked, issues = mark_and_persist_quality_issues(clean_story_text, clean_beats_text)
        story_data = _story_display_to_json(clean_story_text)
        (get_project_dir() / "story.json").write_text(json.dumps(story_data, ensure_ascii=False, indent=2), encoding="utf-8")
        beats_path = get_project_dir() / "beats.json"
        video_jobs_path = get_project_dir() / "video_jobs.json"
        if story_changed:
            if beats_path.exists():
                beats_path.unlink()
            if video_jobs_path.exists():
                video_jobs_path.unlink()
            clean_beats_text = ""
            beats_marked = ""
            log("[editor] 剧情已更新，旧 beats/video_jobs 已清空；请重新拆分剧情 Beats", "STEP")
        elif str(clean_beats_text or "").strip():
            existing_beats_raw = load_json_file_silent("beats.json") or "{}"
            try:
                existing_beats_data = json.loads(existing_beats_raw)
                if not isinstance(existing_beats_data, dict):
                    existing_beats_data = {}
            except Exception:
                existing_beats_data = {}
            editor_beats_data = _beats_display_to_json(clean_beats_text)
            editable_content_unchanged = _editable_beat_content_is_unchanged(existing_beats_data, editor_beats_data)
            # Asset matching is only for newly generated Beats.  A manual edit
            # (especially a duration edit) must preserve the existing
            # visibility contract instead of re-inferring it from free text.
            beats_data = ensure_event_fields_on_beats(editor_beats_data)
            beats_changed = not _json_semantically_equal(existing_beats_data, beats_data)
            duration_only_changed = (
                beats_changed
                and editable_content_unchanged
            )
            if beats_changed or not beats_path.exists():
                beats_path.write_text(json.dumps(beats_data, ensure_ascii=False, indent=2), encoding="utf-8")
            if beats_changed:
                if duration_only_changed:
                    _sync_video_jobs_with_beat_durations(beats_data, video_jobs_path)
                elif video_jobs_path.exists():
                    _mark_video_jobs_for_changed_beats(existing_beats_data, beats_data, video_jobs_path)
        if not story_changed and str(clean_beats_text or "").strip() and beats_path.exists() and video_jobs_path.exists():
            try:
                current_beats_data = json.loads(load_json_file_silent("beats.json") or "{}")
                if isinstance(current_beats_data, dict):
                    _sync_video_jobs_with_beat_durations(current_beats_data, video_jobs_path)
            except Exception as exc:
                log(f"[editor][warn] 同步 Beat 时长到 LiconMSR 分段失败: {exc}", "WARN")
        video_jobs_text = load_json_file_silent("video_jobs.json") or ""
        if issues:
            log(format_quality_block_message(issues), "ERROR")
            return story_marked, beats_marked, video_jobs_text, get_logs()
        clear_quality_issues()
        if not story_changed:
            log("[editor] 已保存主题、完整剧情和 beats 修改，质检通过", "STEP")
        return story_json_to_display(json.dumps(story_data, ensure_ascii=False)), beats_json_to_display(load_json_file_silent("beats.json") or ""), video_jobs_text, get_logs()
    except Exception as exc:
        traceback.print_exc()
        log(f"[editor][error] 保存失败: {exc}", "ERROR")
        return story_text or "", beats_text or "", load_json_file_silent("video_jobs.json") or "", get_logs()


def latest_final_video_path():
    final_dir = get_project_dir() / "final"
    videos_dir = get_project_dir() / "videos"
    candidates = []
    if final_dir.exists():
        candidates.extend(final_dir.glob("final_video_*.mp4"))
        if not candidates:
            candidates.extend(final_dir.glob("*.mp4"))
    if not candidates and videos_dir.exists():
        candidates.extend(videos_dir.glob("*.mp4"))
    candidates = [p for p in candidates if p.exists() and p.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0].resolve()


def final_video_search_dirs():
    return [str((get_project_dir() / "final").resolve())]


def latest_final_video_preview_payload():
    path = latest_final_video_path()
    if not path:
        return "", "ERROR: 暂无可预览的最终合成视频"
    url = "/gradio_api/file=" + str(path).replace("\\", "/")
    return str(path), url


def _save_duration_state(duration_select, custom_duration):
    values = duration_state_values(duration_select, custom_duration)
    update_ui_run_config(**values)
    return values["final_duration_seconds"]


def save_duration_selection(duration_select, custom_duration):
    _save_duration_state(duration_select, custom_duration)


def update_custom_duration_visibility(duration_select, custom_duration):
    _save_duration_state(duration_select, custom_duration)
    return gr.update(visible=True, interactive=str(duration_select or "") == "custom")


def save_runtime_ui_options(dry_run=None, workflow_only=None, no_concat=None, auto_cleanup=None, workflow_mode=None, minimal_mode=None):
    updates = {}
    if dry_run is not None:
        updates["ui_dry_run"] = bool(dry_run)
    if workflow_only is not None:
        updates["ui_workflow_only"] = bool(workflow_only)
    if no_concat is not None:
        updates["ui_no_concat"] = bool(no_concat)
    if auto_cleanup is not None:
        updates["ui_auto_cleanup"] = bool(auto_cleanup)
    if workflow_mode is not None:
        updates["ui_workflow_mode"] = workflow_mode or CONFIG.get("output_mode", "api")
    if minimal_mode is not None:
        updates["ui_minimal_mode"] = bool(minimal_mode)
    if updates:
        update_ui_run_config(**updates)
    return None


def available_video_models() -> list[tuple[str, str]]:
    """Return registered models as Gradio-ready ``(label, id)`` choices."""
    from generation.registry import get_model_registry

    return [(spec.display_name, spec.id) for spec in get_model_registry().specs()]


def current_default_video_model() -> str:
    """Load and validate the current project's persisted default model."""
    from generation.registry import default_model_id, get_model_registry

    selected = str(load_ui_run_config().get("default_video_model_id") or "").strip()
    return selected if get_model_registry().has(selected) else default_model_id()


def save_default_video_model(model_id: str = "") -> str:
    """Persist the default model and mark incompatible Beats for regeneration."""
    from generation.registry import default_model_id, get_model_registry

    registry = get_model_registry()
    selected = str(model_id or "").strip()
    if not registry.has(selected):
        selected = default_model_id()
    previous = current_default_video_model()
    stale_for_model = str(load_ui_run_config().get("beats_stale_for_model") or "") if selected == previous else ""
    if selected != previous:
        beats_text = load_json_file_silent("beats.json") or ""
        if beats_text.strip():
            stale_for_model = selected
            log(f"[model] 已切换到 {registry.get(selected).spec.display_name}；现有 Beats 使用旧模型规则，请重新拆分 Beats", "WARN")
    update_ui_run_config(default_video_model_id=selected, beats_stale_for_model=stale_for_model)
    return selected


def workflow_model_settings(model_id: str = "") -> dict[str, object]:
    """Return model-specific workflow settings for the project settings UI."""
    from generation.registry import default_model_id, get_model_registry

    registry = get_model_registry()
    selected = str(model_id or "").strip()
    if not registry.has(selected):
        selected = default_model_id()
    pipeline = registry.get(selected)
    models = CONFIG.get("models") if isinstance(CONFIG.get("models"), dict) else {}
    model_config = models.get(selected) if isinstance(models, dict) else {}
    model_config = model_config if isinstance(model_config, dict) else {}
    configured_path = str(model_config.get("template_api_path") or "").strip()
    fallback_key = pipeline.rules.template_fallback_config_key
    if not configured_path and fallback_key:
        configured_path = str(CONFIG.get(fallback_key) or "").strip()
    template_path = Path(configured_path) if configured_path else Path()
    if configured_path and not template_path.is_absolute():
        template_path = (CONFIG_PATH.parent / template_path).resolve()
    template_value = str(template_path) if configured_path else ""
    capabilities = pipeline.spec.capabilities
    description = pipeline.rules.capability_summary or (
        f"{pipeline.spec.display_name}：{capabilities.duration_min_sec}–{capabilities.duration_max_sec} 秒、"
        f"{capabilities.default_fps}fps。"
    )
    if str(load_ui_run_config().get("beats_stale_for_model") or "") == selected:
        description += " 当前 Story/Beats 来自旧模型规则，请重新拆分 Beats 后再生成分段提示词。"
    execution_profiles = model_config.get("execution_profiles")
    execution_profiles = execution_profiles if isinstance(execution_profiles, dict) else {}
    runninghub_profile = execution_profiles.get("runninghub")
    runninghub_profile = runninghub_profile if isinstance(runninghub_profile, dict) else {}
    runninghub_visible = bool(runninghub_profile.get("workflow_id") or runninghub_profile.get("enabled"))
    return {
        "model_id": selected,
        "model_label": pipeline.spec.display_name,
        "template_label_html": f'<div class="kv-key">{escape(pipeline.spec.display_name)} API 模板</div>',
        "template_path": template_value,
        "description": description,
        "runninghub_visible": runninghub_visible,
    }


def save_default_video_model_with_workflow(model_id: str = ""):
    selected = save_default_video_model(model_id)
    settings = workflow_model_settings(selected)
    runninghub_visible = bool(settings["runninghub_visible"])
    return (
        gr.update(value=settings["template_label_html"]),
        gr.update(value=settings["template_path"]),
        gr.update(value=settings["description"]),
        gr.update(visible=runninghub_visible),
        gr.update(visible=runninghub_visible),
        gr.update(value=""),
    )


def save_submit_backend_selection(backend: str = ""):
    backend = str(backend or "").strip().lower()
    if backend not in {"comfyui", "runninghub"}:
        backend = ""
    update_ui_run_config(ui_submit_backend=backend)
    if backend:
        CONFIG["video_submit_backend"] = backend
        submit_backends = CONFIG.setdefault("submit_backends", {})
        if isinstance(submit_backends, dict):
            backend_config = submit_backends.setdefault(backend, {})
            if isinstance(backend_config, dict):
                backend_config["enabled"] = True
    CONFIG["submit_to_comfyui"] = bool(backend)
    return backend


def secret_key_placeholders() -> tuple[str, str]:
    return masked_secret(DEEPSEEK_API_KEY_ENV), masked_secret(RUNNINGHUB_API_KEY_ENV)


def _save_global_config() -> None:
    CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_workflow_config(
    model_id: str = "",
    template_api_path: str = "",
    runninghub_use_plus: bool = False,
    runninghub_concurrency: int | float | str = 3,
):
    from generation.registry import default_model_id, get_model_registry

    registry = get_model_registry()
    selected_model_id = str(model_id or "").strip()
    if not registry.has(selected_model_id):
        selected_model_id = default_model_id()
    template_api_path = str(template_api_path or "").strip()
    if not template_api_path:
        return "ERROR: API模板路径不能为空"
    try:
        concurrency = int(float(runninghub_concurrency or 3))
    except (TypeError, ValueError):
        return "ERROR: RunningHub 并发必须是数字"
    concurrency = max(1, min(concurrency, 8))
    path = Path(template_api_path)
    if path.is_absolute():
        try:
            config_value = str(path.resolve().relative_to(CONFIG_PATH.parent))
        except ValueError:
            config_value = str(path.resolve())
    else:
        config_value = str(path)
        path = CONFIG_PATH.parent / path
    if not path.exists():
        return f"ERROR: API模板不存在: {path}"
    config_value = config_value.replace("\\", "/")
    models = CONFIG.setdefault("models", {})
    model_config = models.setdefault(selected_model_id, {}) if isinstance(models, dict) else {}
    if isinstance(model_config, dict):
        model_config["template_api_path"] = config_value
    settings = workflow_model_settings(selected_model_id)
    fallback_key = registry.get(selected_model_id).rules.template_fallback_config_key
    if fallback_key:
        CONFIG[fallback_key] = config_value
    if bool(settings["runninghub_visible"]):
        CONFIG["video_generation_concurrency_limit"] = concurrency
        submit_backends = CONFIG.setdefault("submit_backends", {})
        if isinstance(submit_backends, dict):
            runninghub = submit_backends.setdefault("runninghub", {})
            if isinstance(runninghub, dict):
                runninghub["video_generation_concurrency_limit"] = concurrency
                runninghub["use_plus_instance"] = bool(runninghub_use_plus)
                if runninghub_use_plus:
                    runninghub["instanceType"] = "plus"
                else:
                    runninghub.pop("instanceType", None)
    _save_global_config()
    # Pipeline instances cache model-specific template/profile settings.  Swap
    # only the edited model in the stable registry so the next prompt/workflow
    # preparation observes this save immediately, without restarting the app.
    from generation.registry import refresh_model_pipeline

    refresh_model_pipeline(selected_model_id)
    model_label = str(settings["model_label"])
    if not bool(settings["runninghub_visible"]):
        return f"已保存 {model_label} 工作流模板: {config_value}"
    plus_text = "Plus 48G" if runninghub_use_plus else "默认实例"
    return f"已保存 {model_label} 工作流: {config_value} / RunningHub {plus_text} / 并发 {concurrency}"


def save_service_keys(
    deepseek_key: str = "",
    runninghub_key: str = "",
    comfyui_backend_url: str = "",
    deepseek_model: str = "",
    fps_value=None,
    output_dir_value: str = "",
):
    saved = []
    deepseek_key = str(deepseek_key or "").strip()
    runninghub_key = str(runninghub_key or "").strip()
    comfyui_backend_url = str(comfyui_backend_url or "").strip()
    deepseek_model = str(deepseek_model or "").strip()
    output_dir_value = str(output_dir_value or "").strip()
    placeholders = {"***", "需重新配置"}
    if deepseek_key and deepseek_key not in placeholders:
        set_secret(DEEPSEEK_API_KEY_ENV, deepseek_key)
        saved.append("DeepSeek")
    if runninghub_key and runninghub_key not in placeholders:
        set_secret(RUNNINGHUB_API_KEY_ENV, runninghub_key)
        saved.append("RunningHub")
    if comfyui_backend_url:
        submit_backends = CONFIG.setdefault("submit_backends", {})
        if not isinstance(submit_backends, dict):
            submit_backends = {}
            CONFIG["submit_backends"] = submit_backends
        comfyui_backend = submit_backends.setdefault("comfyui", {})
        if not isinstance(comfyui_backend, dict):
            comfyui_backend = {}
            submit_backends["comfyui"] = comfyui_backend
        comfyui_backend["url"] = comfyui_backend_url
        comfyui_backend["enabled"] = True
        CONFIG["comfyui_url"] = comfyui_backend_url
        CONFIG["comfy_url"] = comfyui_backend_url
        saved.append("ComfyUI backend URL")
    if deepseek_model:
        CONFIG["deepseek_model"] = deepseek_model
        saved.append("DeepSeek model")
    try:
        if fps_value is not None and str(fps_value).strip():
            CONFIG["fps"] = int(float(fps_value))
            saved.append("FPS")
    except Exception:
        log(f"[settings][warn] FPS 无效，已忽略: {fps_value}", "WARN")
    if output_dir_value:
        CONFIG["output_dir"] = output_dir_value
        saved.append("output dir")
    if saved:
        _save_global_config()
    if saved:
        log(f"[settings] 已保存 {'、'.join(saved)}", "STEP")
    else:
        log("[settings] 未输入新的 key 或 URL，保持现有配置不变", "STEP")
    return (*secret_key_placeholders(), CONFIG.get("comfyui_url", ""), get_logs())

def generate_story_with_duration(topic_text, story_input_text, duration_select, custom_duration):
    assert_current_runtime_writable("生成完整剧情")
    final_duration_seconds = _save_duration_state(duration_select, custom_duration)
    manual_story = strip_quality_annotations(story_input_text).strip()
    source_topic = manual_story or str(topic_text or "").strip()
    model_id = current_default_video_model()
    story_text, logs = generate_story(source_topic, duration_sec=final_duration_seconds, model_id=model_id)
    display = story_json_to_display(story_text)
    marked, issues = validate_story_display(display, model_id=model_id)
    if issues:
        persist_quality_issues(issues)
        log(format_quality_block_message(issues), "ERROR")
        return marked, get_logs()
    clear_quality_issues()
    return display, logs


def generate_novel_segments_with_duration(novel_text, duration_select, custom_duration, target_segment_count):
    """已有完整小说 → 生成 Beats → 再生成 Segments；覆盖 beats.json 与 video_jobs.json。"""
    assert_current_runtime_writable("小说生成 Beats / Segments")
    ensure_project_dirs()
    raw_novel_text = str(novel_text or "")
    if not raw_novel_text.strip():
        raise ValueError("完整小说不能为空")
    # The button submission is the source of truth. Persist it before any
    # destructive cleanup or remote model call so a failed generation cannot
    # discard a long-form input.
    save_novel_source_text(raw_novel_text)
    try:
        count = int(float(target_segment_count or 0))
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        update_ui_run_config(novel_segment_count=count)
    duration = _save_duration_state(duration_select, custom_duration)
    model_id = current_default_video_model()
    # 清空旧 beats 与分段信息，按新的小说链路重新生成。
    for stale_name in ("beats.json", "video_jobs.json", "novel_segments.json"):
        stale_path = get_project_dir() / stale_name
        if stale_path.exists():
            stale_path.unlink()
    log("[novel_beats] ① 把小说切分为 Beats（保留对白与原文）", "STEP")
    data = generate_novel_beats(raw_novel_text, duration, count, model_id)
    actual_count = len(data.get("beats", []))
    if count and actual_count != count:
        log(
            f"[novel_beats] 目标分段数 {count} 与 {duration}s 总时长不兼容，已按模型规则自动调整为 {actual_count} 段",
            "WARN",
        )
    (get_project_dir() / "beats.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("[novel_beats] ② 按当前模型生成视频分段任务", "STEP")
    from generation.service import get_generation_service

    jobs = get_generation_service().prepare_segments(data)
    log(f"[novel_beats] 已生成 {len(data['beats'])} 个 Segments（{model_id}）", "STEP")
    return json.dumps(jobs, ensure_ascii=False, indent=2), get_logs()


def split_beats_with_duration(story_text, duration_select, custom_duration, topic_text=None, target_beat_count=None):
    assert_current_runtime_writable("拆分剧情 Beats")
    ensure_project_dirs()
    final_duration_seconds = _save_duration_state(duration_select, custom_duration)
    if topic_text is not None:
        update_ui_run_config(topic=str(topic_text or "").strip())
    if target_beat_count is not None:
        update_ui_run_config(target_beat_count=str(target_beat_count or "").strip())
    clean_story_text = strip_quality_annotations(story_text)
    model_id = current_default_video_model()
    story_marked, story_issues = validate_story_display(clean_story_text, model_id=model_id)
    if story_issues:
        persist_quality_issues(story_issues)
        log(format_quality_block_message(story_issues), "ERROR")
        yield story_marked, "", get_logs()
        return
    story_data = _story_display_to_json(clean_story_text)
    if topic_text is not None:
        story_data["source_topic"] = str(topic_text or "").strip()
    story_json = json.dumps(story_data, ensure_ascii=False, indent=2)
    (get_project_dir() / "story.json").write_text(story_json, encoding="utf-8")
    for stale_name in ("beats.json", "video_jobs.json"):
        stale_path = get_project_dir() / stale_name
        if stale_path.exists():
            stale_path.unlink()
    log(f"[beats] requested target beat count: {target_beat_count!r}", "STEP")
    for beats_text, logs in split_beats(story_json, target_duration_sec=final_duration_seconds, target_beat_count=target_beat_count, model_id=model_id):
        display = beats_json_to_display(beats_text)
        marked, beat_issues = validate_beats_display(display, model_id=model_id)
        if beat_issues:
            persist_quality_issues(beat_issues)
            log(format_quality_block_message(beat_issues), "ERROR")
            yield clean_story_text, marked, get_logs()
            return
        update_ui_run_config(beats_stale_for_model="")
        clear_quality_issues()
        yield clean_story_text, display, logs


def save_beats_edits(beats_text):
    try:
        assert_current_runtime_writable("保存 Beats")
        ensure_project_dirs()
        if not str(beats_text or "").strip():
            raise ValueError("beats 内容为空")
        existing_raw = load_json_file_silent("beats.json") or "{}"
        try:
            existing_data = json.loads(existing_raw)
            if not isinstance(existing_data, dict):
                existing_data = {}
        except Exception:
            existing_data = {}
        data = json.loads(str(beats_text))
        editable_content_unchanged = _editable_beat_content_is_unchanged(existing_data, data)
        data = ensure_event_fields_on_beats(data)
        path = get_project_dir() / "beats.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        video_jobs_path = get_project_dir() / "video_jobs.json"
        if video_jobs_path.exists() and editable_content_unchanged:
            _sync_video_jobs_with_beat_durations(data, video_jobs_path)
        elif video_jobs_path.exists():
            _mark_video_jobs_for_changed_beats(existing_data, data, video_jobs_path)
        log("[beats] 已保存手动修改；对应变更分段已标记为需要补齐", "STEP")
        return json.dumps(data, ensure_ascii=False, indent=2), load_json_file_silent("video_jobs.json") or "", get_logs()
    except Exception as exc:
        traceback.print_exc()
        log(f"[beats][error] 保存修改失败: {exc}", "ERROR")
        return beats_text or "", load_json_file_silent("video_jobs.json") or "", get_logs()


def _duration_select_from_seconds(seconds):
    try:
        seconds = int(seconds or DEFAULT_DURATION_SECONDS)
    except Exception:
        seconds = 60
    label = f"{seconds}s"
    if label in DURATION_PRESETS:
        return label, seconds, gr.update(visible=True, interactive=False, value=seconds)
    return "custom", seconds, gr.update(visible=True, interactive=True, value=seconds)


def _runtime_topic_value(story_raw: str = "") -> str:
    """Return the topic owned by the active project or archived episode."""
    topic_value = ""
    if get_current_episode_name():
        snapshot_path = get_project_dir() / "project_meta" / "ui_run_config.json"
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig")) if snapshot_path.exists() else {}
            topic_value = str((snapshot or {}).get("topic") or "").strip()
        except Exception:
            topic_value = ""
    else:
        topic_value = str(load_ui_run_config().get("topic") or "").strip()
    if topic_value:
        return topic_value
    try:
        story_data = json.loads(story_raw or _project_file_text("story.json") or "{}")
        return str(story_data.get("title") or story_data.get("summary") or "").strip()
    except Exception:
        return ""


def _runtime_ui_config_value(key: str, default=None):
    """Read a ui_run_config value owned by the active project or archived episode."""
    if get_current_episode_name():
        snapshot_path = get_project_dir() / "project_meta" / "ui_run_config.json"
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig")) if snapshot_path.exists() else {}
            value = snapshot.get(key)
            if value not in (None, ""):
                return value
        except Exception:
            pass
    value = load_ui_run_config().get(key)
    return default if value in (None, "") else value


def _runtime_novel_segment_count() -> int:
    try:
        return int(_runtime_ui_config_value("novel_segment_count", 4) or 4)
    except (TypeError, ValueError):
        return 4


def _runtime_target_beat_count() -> str:
    return str(_runtime_ui_config_value("target_beat_count", "") or "").strip()


def restore_project_ui_state():
    ui_config = load_ui_run_config()
    duration_select_value = str(ui_config.get("duration_select") or "").strip()
    custom_duration = ui_config.get("custom_duration_seconds")
    duration_sec = ui_config.get("final_duration_seconds") or ui_config.get("duration_sec")
    story_raw = _project_file_text("story.json")
    beats_raw = _project_file_text("beats.json")
    story_text = story_json_to_display(story_raw)
    beats_text = beats_json_to_display(beats_raw)
    story_marked, beats_marked, issues = mark_and_persist_quality_issues(story_text, beats_text)
    if issues:
        story_text = story_marked
        beats_text = beats_marked
    video_jobs_text = _project_file_text("video_jobs.json")

    if duration_select_value not in DURATION_PRESETS and duration_select_value != "custom":
        duration_select_value = ""
    if not duration_sec:
        try:
            story_data = json.loads(story_raw or "{}")
            duration_sec = story_data.get("target_duration_sec")
        except Exception:
            duration_sec = None
    if not duration_select_value:
        duration_select_value, custom_duration, custom_update = _duration_select_from_seconds(duration_sec)
    else:
        final_seconds = get_final_duration_seconds(duration_select_value, custom_duration)
        if custom_duration is None:
            custom_duration = final_seconds
        custom_update = gr.update(visible=True, interactive=duration_select_value == "custom", value=custom_duration)

    topic_value = _runtime_topic_value(story_raw)

    running = one_click_is_active()
    char_update, bg_update, asset_gallery = project_bible_ui_state()
    submit_backend = str(ui_config.get("ui_submit_backend") or CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
    if submit_backend not in {"comfyui", "runninghub"}:
        submit_backend = ""
    save_submit_backend_selection(submit_backend)
    return (
        topic_value,
        duration_select_value,
        custom_update if duration_select_value == "custom" else gr.update(visible=True, interactive=False, value=custom_duration),
        story_text,
        beats_text,
        video_jobs_text,
        get_logs(),
        char_update,
        bg_update,
        asset_gallery,
        bool(ui_config.get("ui_dry_run", False)),
        bool(ui_config.get("ui_workflow_only", False)),
        bool(ui_config.get("ui_no_concat", True)),
        submit_backend == "comfyui",
        submit_backend == "runninghub",
        novel_source_ui_update(),
        _runtime_novel_segment_count(),
        _runtime_target_beat_count(),
    )


def refresh_runtime_panels(groups_text, guides_text, group_page=0):
    video_jobs_text = groups_text or _project_file_text("video_jobs.json")
    return (render_groups_panel(video_jobs_text, "", page=group_page), render_workflow_progress(workflow_status_from_run_state({})))


def _runtime_ui_state():
    project_dir = str(get_project_dir())
    char_update, bg_update, asset_gallery = project_bible_ui_state()
    video_jobs_text = _project_file_text("video_jobs.json")
    final_video = latest_final_video_path()
    story_text = story_json_to_display(load_json_file("story.json"))
    beats_text = beats_json_to_display(load_json_file("beats.json"))
    story_marked, beats_marked, issues = mark_and_persist_quality_issues(story_text, beats_text)
    if issues:
        story_text = story_marked
        beats_text = beats_marked
    return project_dir, story_text, beats_text, video_jobs_text, final_video, char_update, bg_update, asset_gallery


def set_current_project(project_name, outgoing_topic=None):
    # The topic editor is project-owned state.  Persist its latest browser
    # value before changing the global project context; otherwise a topic that
    # has not gone through the story/save actions is lost when the user moves
    # to another project.
    if outgoing_topic is not None and not get_current_episode_name():
        update_ui_run_config(topic=str(outgoing_topic or "").strip())
    project_name = _safe_project_name(project_name or "current")
    set_current_project_name(project_name)
    log(f"current project: {project_name}")
    project_dir, story_text, beats_text, video_jobs_text, final_video, char_update, bg_update, asset_gallery = _runtime_ui_state()
    novel_update = novel_source_ui_update()
    topic_value = _runtime_topic_value()
    return (
        project_dir,
        story_text,
        beats_text,
        video_jobs_text,
        get_logs(),
        project_dir,
        str(Path(project_dir) / "videos"),
        str(final_video) if final_video else "",
        char_update,
        bg_update,
        asset_gallery,
        gr.update(choices=_episode_choices(project_name), value=""),
        novel_update,
        _runtime_novel_segment_count(),
        _runtime_target_beat_count(),
        topic_value,
    )


def set_current_project_episode(episode_name):
    set_current_episode(str(episode_name or "").strip())
    label = get_current_episode_name() or "当前生成"
    log(f"current runtime: {get_current_project_name()} / {label}", "STEP")
    project_dir, story_text, beats_text, video_jobs_text, final_video, char_update, bg_update, asset_gallery = _runtime_ui_state()
    novel_update = novel_source_ui_update()
    topic_value = _runtime_topic_value()
    return (
        project_dir,
        story_text,
        beats_text,
        video_jobs_text,
        get_logs(),
        project_dir,
        str(Path(project_dir) / "videos"),
        str(final_video) if final_video else "",
        char_update,
        bg_update,
        asset_gallery,
        render_groups_panel(video_jobs_text),
        render_workflow_progress(workflow_status_from_run_state({})),
        novel_update,
        _runtime_novel_segment_count(),
        _runtime_target_beat_count(),
        topic_value,
    )


def rename_current_project(new_project_name):
    try:
        old_name = get_current_project_name() or "current"
        new_name = _safe_project_name(new_project_name)
        if new_name == old_name:
            return gr.update(choices=_project_choices(), value=old_name), gr.update(choices=_episode_choices(old_name), value=get_current_episode_name()), str(get_project_dir()), get_logs()
        old_meta = PROJECTS_DIR / old_name
        new_meta = PROJECTS_DIR / new_name
        old_output = OUTPUT_DIR / old_name
        new_output = OUTPUT_DIR / new_name
        old_episodes = EPISODES_DIR / old_name
        new_episodes = EPISODES_DIR / new_name
        if new_meta.exists() or new_output.exists() or new_episodes.exists():
            raise FileExistsError(f"项目已存在：{new_name}")
        if old_meta.exists():
            old_meta.rename(new_meta)
        else:
            new_meta.mkdir(parents=True, exist_ok=True)
        if old_output.exists():
            old_output.rename(new_output)
        if old_episodes.exists():
            old_episodes.rename(new_episodes)
        set_current_project_name(new_name)
        log(f"项目已重命名：{old_name} -> {new_name}", "STEP")
        return gr.update(choices=_project_choices(), value=new_name), gr.update(choices=_episode_choices(new_name), value=""), str(get_project_dir()), get_logs()
    except Exception as exc:
        traceback.print_exc()
        log(f"[project][rename][error] {exc}", "ERROR")
        current_name = get_current_project_name() or "current"
        return gr.update(choices=_project_choices(), value=current_name), gr.update(choices=_episode_choices(current_name), value=get_current_episode_name()), str(get_project_dir()), get_logs()


def _next_episode_name(project_name: str) -> str:
    episodes_dir = EPISODES_DIR / project_name
    episodes_dir.mkdir(parents=True, exist_ok=True)
    max_index = 0
    for item in episodes_dir.iterdir():
        if not item.is_dir():
            continue
        match = re.fullmatch(r"第(\d+)集", item.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    next_index = max_index + 1
    while (episodes_dir / f"第{next_index:03d}集").exists():
        next_index += 1
    return f"第{next_index:03d}集"


def _resolve_archive_episode_name(project_name: str, episode_name=None) -> str:
    text = str(episode_name or "").strip()
    if not text or text.startswith("留空自动生成"):
        return _next_episode_name(project_name)
    if re.fullmatch(r"第\d+集", text) and (EPISODES_DIR / project_name / text).exists():
        return _next_episode_name(project_name)
    return text


def _rebase_archive_paths(value, source_dir: Path, archive_dir: Path):
    """Rebase paths owned by the current runtime into its archive copy.

    Project-library assets live outside ``source_dir`` and intentionally keep
    their shared absolute paths. Segment uploads and generated artifacts live
    below ``source_dir`` and must not continue pointing at the mutable current
    generation after archiving.
    """
    if isinstance(value, dict):
        return {key: _rebase_archive_paths(item, source_dir, archive_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_archive_paths(item, source_dir, archive_dir) for item in value]
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        path = Path(value)
        if not path.is_absolute():
            return value
        relative = path.resolve().relative_to(source_dir.resolve())
        archived = (archive_dir / relative).resolve()
        return str(archived) if archived.exists() else value
    except (OSError, ValueError):
        return value


def _make_archive_video_jobs_portable(archive_dir: Path, source_dir: Path) -> None:
    jobs_path = archive_dir / "video_jobs.json"
    if not jobs_path.exists():
        return
    payload = json.loads(jobs_path.read_text(encoding="utf-8-sig"))
    rebased = _rebase_archive_paths(payload, source_dir.resolve(), archive_dir.resolve())
    temp_path = jobs_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(rebased, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(jobs_path)


def archive_current_generation(episode_name=None):
    try:
        assert_current_runtime_writable("归档当前生成")
        project_name = get_current_project_name() or "current"
        source_dir = OUTPUT_DIR / project_name
        if not source_dir.exists():
            raise FileNotFoundError(f"当前生成目录不存在：{source_dir}")
        episode = _resolve_archive_episode_name(project_name, episode_name)
        if any(ch in INVALID_PROJECT_NAME_CHARS for ch in episode) or episode in {".", ".."}:
            raise ValueError("归档名称非法")
        archive_dir = EPISODES_DIR / project_name / episode
        if archive_dir.exists():
            raise FileExistsError(f"归档已存在：{episode}")
        archive_dir.mkdir(parents=True, exist_ok=False)
        names = [
            "story.json",
            "beats.json",
            "video_jobs.json",
            NOVEL_SOURCE_FILE,
            "quality_issues.json",
            "video_feedback_analysis.json",
            "project_asset_candidates.json",
        ]
        for name in names:
            src = source_dir / name
            if src.exists():
                shutil.copy2(src, archive_dir / name)
        for dirname in ["videos", "final", "workflows", "logs", "temp", "media"]:
            src = source_dir / dirname
            if src.exists():
                shutil.copytree(src, archive_dir / dirname, dirs_exist_ok=True)
        _make_archive_video_jobs_portable(archive_dir, source_dir)
        sidecar = _sidecar_project_dir(project_name)
        if sidecar.exists():
            shutil.copytree(sidecar, archive_dir / "project_meta", dirs_exist_ok=True)
        log(f"当前生成已归档：{archive_dir}", "STEP")
        return "", get_logs(), str(archive_dir), gr.update(choices=_episode_choices(project_name), value=get_current_episode_name())
    except Exception as exc:
        traceback.print_exc()
        log(f"[project][archive][error] {exc}", "ERROR")
        return episode_name or "", get_logs(), "", gr.update(choices=_episode_choices(get_current_project_name() or "current"), value=get_current_episode_name())


def clean_project():
    try:
        assert_current_runtime_writable("清空项目")
        project = get_project_dir().resolve()
        expected = (OUTPUT_DIR / get_current_project_name()).resolve()
        if project != expected or project == OUTPUT_DIR.resolve():
            raise ValueError("清理目录不属于当前项目的生成目录")
        # Clear authoritative data before media: Windows may lock a video
        # while it is being previewed, which must not retain the old run.
        for name in ("video_jobs.json", "beats.json", "story.json", NOVEL_SOURCE_FILE,
                     QUALITY_ISSUES_FILE, "video_feedback_analysis.json",
                     "project_asset_candidates.json"):
            (project / name).unlink(missing_ok=True)
        update_ui_run_config(topic="")
        leftovers = []
        if project.exists():
            for item in project.iterdir():
                try:
                    if item.is_dir() and not item.is_symlink():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except OSError as exc:
                    leftovers.append(item.name)
                    log(f"[cleanup][warning] {item}: {exc}", "WARN")
        ensure_project_dirs()
        message = "已清空当前生成的主题、剧情、Beats 和 Segments"
        log(message, "STEP")
        if leftovers:
            warning = "生成信息已清空，部分产物未能删除（可能正被占用）：" + "、".join(leftovers) + "。关闭预览或占用程序后可再次清空。"
            log(warning, "WARN")
            gr.Warning(warning)
        else:
            gr.Info(message)
        char_update, bg_update, asset_gallery = project_bible_ui_state()
        return get_logs(), "", "", "", char_update, bg_update, asset_gallery
    except Exception as exc:
        traceback.print_exc()
        log(f"清空当前生成失败：{exc}", "ERROR")
        raise gr.Error(f"清空当前生成失败：{exc}") from exc
