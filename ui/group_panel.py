"""Model-aware segment panel rendering helpers."""
from __future__ import annotations

import html as _html
import json
import re
from pathlib import Path
from typing import Any

import gradio as gr

from services.context import CONFIG, get_current_episode_name
from services.file_utils import load_json_file_silent, short_path_name
from services.project_bible import load_project_bible
from workflow.asset_resolver import prompt_with_character_descriptions, selected_project_materials
from workflow.segment_runner import extract_director_prompt


GROUPS_PER_PAGE = 4
ROLE_ID_RE = re.compile(r"^character_\d+$", re.IGNORECASE)
SCENE_ID_RE = re.compile(r"^scene_\d+$", re.IGNORECASE)
PROMPT_PREVIEW_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M7 3.75h7l3.25 3.25v12.25A1.75 1.75 0 0 1 15.5 21h-8A1.75 1.75 0 0 1 5.75 19.25v-13.75A1.75 1.75 0 0 1 7.5 3.75Z"/>'
    '<path d="M14 3.75V7h3.25M8.75 11h4.5M8.75 14h3"/>'
    '<path d="M14.5 16.5s1.25-1.75 3.25-1.75S21 16.5 21 16.5s-1.25 1.75-3.25 1.75-3.25-1.75-3.25-1.75Z"/>'
    '<circle cx="17.75" cy="16.5" r=".65"/>'
    '</svg>'
)
SELECT_VIDEO_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/>'
    '<path d="M7 5.5v13M17 5.5v13M3.5 9h3.5M17 9h3.5M3.5 15h3.5M17 15h3.5"/>'
    '<path d="m9.25 12 1.75 1.75 3.75-4"/>'
    '</svg>'
)


def esc(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""))


def _model_id_for_segment(segment: dict[str, Any], payload: dict[str, Any]) -> str:
    from generation.job_schema import resolved_model_id

    return resolved_model_id(segment, payload)


def _model_label(model_id: str) -> str:
    try:
        from generation.registry import get_model_registry

        return get_model_registry().get(model_id).spec.display_name
    except Exception:
        return model_id or "未指定"


def _render_model_label(segment: dict[str, Any]) -> str:
    """Describe the actual playable render, not the next selected model."""
    try:
        from generation.render_history import active_render

        render = active_render(segment)
        model = render.get("model") if isinstance(render, dict) else {}
        model_id = str((model or {}).get("id") or "").strip()
        if model_id:
            return _model_label(model_id)
    except Exception:
        pass
    return ""


def _model_selector_html(segment: dict[str, Any], payload: dict[str, Any]) -> str:
    selected_id = _model_id_for_segment(segment, payload)
    try:
        from generation.registry import get_model_registry

        specs = get_model_registry().specs()
    except Exception:
        specs = []
    options = "".join(
        f'<option value="{esc(spec.id)}" {"selected" if spec.id == selected_id else ""}>{esc(spec.display_name)}</option>'
        for spec in specs
    )
    if not options:
        return ""
    return (
        '<label class="segment-model-field">'
        '<span>计划模型</span>'
        f'<select class="segment-model-select" data-current-model="{esc(selected_id)}" '
        'aria-label="选择本分段的视频模型" onchange="return window.AICF.changeSegmentModel(this)">'
        f"{options}</select>"
        '<small>切换后会重新编译提示词与工作流，旧成片会保留为历史记录。</small>'
        '<span class="segment-model-error" role="alert" aria-live="polite"></span>'
        "</label>"
    )


def _h3_config_html(segment: dict[str, Any], material_payload: str = "") -> str:
    """Keep the card prompt-first and move H3-specific controls fullscreen."""
    config = segment.get("generation_config") if isinstance(segment.get("generation_config"), dict) else {}
    manifest = [item for item in config.get("asset_manifest") or [] if isinstance(item, dict)]
    by_type = {kind: [item for item in manifest if str(item.get("type") or "").lower() == kind] for kind in ("image", "video", "audio")}
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    prompt = str(config.get("prompt_override") or job.get("final_prompt") or job.get("prompt") or "").strip()
    prompt_lines = prompt.count("\n") + 1 if prompt else 0
    prompt_chars = len(prompt)
    source_excerpt = str(segment.get("source_excerpt") or "").strip()
    source_lines = source_excerpt.count("\n") + 1 if source_excerpt else 0
    source_chars = len(source_excerpt)
    index = int(segment.get("segment_index") or 0)
    rows = []
    for item in manifest:
        asset_id = str(item.get("id") or item.get("asset_id") or "")
        item_type = str(item.get("type") or "")
        label = str(item.get("label") or Path(str(item.get("path") or "")).name or asset_id)
        role = str(item.get("role") or "")
        role_control = (
            '<input type="text" class="h3-asset-role" '
            f'data-asset-id="{esc(asset_id)}" value="{esc(role)}" '
            f'placeholder="参考用途（可选）" aria-label="{esc(label)} 的参考用途" '
            f'onchange="return window.AICF.setH3AssetRole(\'{index}\', this)">'
        )
        video_audio_control = (
            '<label class="h3-video-audio-toggle">'
            f'<input type="checkbox" data-asset-id="{esc(asset_id)}" '
            f'{"checked" if item.get("use_video_audio") else ""} '
            f'onchange="return window.AICF.setH3VideoAudio(\'{index}\', this)">使用视频内嵌音频</label>'
            if item_type == "video"
            else ""
        )
        rows.append(
            '<li>'
            f'<span>{esc(item_type)} · {esc(label)}</span>'
            + role_control
            + video_audio_control
            + f'<button type="button" class="card-regen-btn action-utility" data-asset-id="{esc(asset_id)}" '
            f'onclick="return window.AICF.removeH3Asset(\'{index}\', this)">移除</button>'
            "</li>"
        )
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    source_block = ""
    if source_excerpt:
        source_block = f"""
        <details class="prompt-block segment-source-block" open>
            <summary class="prompt-summary">
                <span>原文参考</span>
                <small>{source_chars} 字符 / {source_lines} 行</small>
            </summary>
            <div class="prompt-toolbar segment-editor-toolbar" role="group" aria-label="本段原文参考操作">
                <button type="button" class="card-regen-btn action-utility" onclick="window.AICF.copySegmentSourceExcerpt(this)">复制原文</button>
            </div>
            <textarea class="segment-source-excerpt prompt-full" data-segment-index="{index}" data-render-key="segment-source-{index}" readonly spellcheck="false">{esc(source_excerpt)}</textarea>
            <pre class="segment-source-excerpt-source" hidden>{esc(source_excerpt)}</pre>
        </details>
        """
    return f"""
        <details class="prompt-block segment-editor-block h3-prompt-block" open>
            <summary class="prompt-summary">
                <span>H3 视频提示词</span>
                <small>{prompt_chars} 字符 / {prompt_lines} 行</small>
            </summary>
            <div class="prompt-toolbar segment-editor-toolbar" role="group" aria-label="本段 H3 视频提示词操作">
                <button type="button" class="card-regen-btn primary-mini h3-prompt-save" onclick="window.AICF.saveH3GenerationConfig('{index}', this).catch(function(error){{alert(error.message || '保存 H3 提示词失败')}})">保存本段提示词</button>
                <button type="button" class="card-regen-btn" onclick="window.AICF.openPromptModalFromButton(this)">全屏编辑</button>
                <button type="button" class="card-regen-btn action-utility" onclick="window.AICF.copyPromptFromButton(this)">复制提示词</button>
            </div>
            <textarea id="segment-director-editor-{index}" name="segment-director-editor-{index}" class="segment-director-editor h3-prompt-override prompt-full" data-segment-index="{index}" data-render-key="segment-{index}" spellcheck="false">{esc(prompt)}</textarea>
            <pre class="prompt-preview prompt-full prompt-modal-source" hidden>{esc(prompt)}</pre>
            <div class="h3-asset-list h3-card-config-source" data-segment-index="{index}" data-manifest="{esc(manifest_json)}" hidden></div>
            <div class="segment-material-source" data-materials="{esc(material_payload)}" hidden></div>
            <span class="h3-config-status" role="status" aria-live="polite"></span>
        </details>
        {source_block}
        <template class="h3-fullscreen-config-template">
            <div class="aicf-material-panel-title">H3 Ref2VA 参考素材</div>
            <p class="h3-config-hint" data-h3-count>图片 {len(by_type['image'])}/9 · 视频 {len(by_type['video'])}/3 · 音频 {len(by_type['audio'])}/3 · 合计 {len(manifest)}/12。音频需至少搭配一项图片或视频参考。</p>
            <section class="h3-project-image-picker" aria-labelledby="h3-project-image-title-{index}">
                <div class="h3-project-image-head">
                    <strong id="h3-project-image-title-{index}">从项目素材选择图片</strong>
                    <span>多选 · 最多 9 张</span>
                </div>
                <p>人物图和场景图都作为普通 H3 图片参考；背景图片可不选，暂不建立角色或场景绑定。</p>
                <div class="h3-project-image-group">
                    <strong>人物图片</strong>
                    <div class="aicf-material-grid h3-project-character-images"></div>
                </div>
                <div class="h3-project-image-group">
                    <strong>场景图片（可选）</strong>
                    <div class="aicf-material-grid h3-project-background-images"></div>
                </div>
            </section>
            <div class="h3-selected-preview" aria-label="已选择参考素材的排列与用途"></div>
            <div class="h3-asset-list" data-segment-index="{index}" data-manifest="{esc(manifest_json)}">
                <ul>{''.join(rows) or '<li class="muted">尚未上传专用参考素材；可直接按 Beat 生成，如需稳定人物或场景请上传参考素材。</li>'}</ul>
            </div>
            <div class="h3-upload-grid" role="group" aria-label="上传 H3 参考素材">
                <label>图片参考<input class="h3-asset-input" type="file" accept="image/png,image/jpeg,image/webp" multiple data-media-type="image"></label>
                <label>视频参考<input class="h3-asset-input" type="file" accept="video/mp4,video/webm,video/quicktime,video/x-matroska" multiple data-media-type="video"></label>
                <label>音频参考<input class="h3-asset-input" type="file" accept="audio/mpeg,audio/wav,audio/x-wav" multiple data-media-type="audio"></label>
            </div>
            <div class="h3-config-actions">
                <button type="button" class="card-regen-btn primary-mini" onclick="return window.AICF.regenerateH3Segment('{index}', this)">验证并重新生成</button>
                <span class="h3-config-status" role="status" aria-live="polite"></span>
            </div>
        </template>
    """


def _segment_video_ok(segment: dict[str, Any]) -> bool:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return bool(
        segment.get("video_ok")
        or segment.get("preserve_on_reset")
        or segment.get("protected_from_reset")
        or job.get("video_ok")
        or job.get("preserve_on_reset")
        or job.get("protected_from_reset")
    )


def _display_segments(segments: list[dict[str, Any]], unmarked_only: bool) -> list[dict[str, Any]]:
    if not unmarked_only:
        return segments
    return [segment for segment in segments if not _segment_video_ok(segment)]


def _format_usage_number(value: Any, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待同步"
    if number.is_integer():
        return f"{int(number)}{suffix}"
    return f"{number:.2f}{suffix}"


def _format_task_duration(value: Any) -> str:
    try:
        seconds = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return "待同步"
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _runninghub_usage_history(segment: dict[str, Any]) -> str:
    from workflow.runninghub_usage import runninghub_attempt_model_id

    attempts = [item for item in segment.get("runninghub_attempts") or [] if isinstance(item, dict)]
    if not attempts:
        return ""
    usage = segment.get("runninghub_usage") if isinstance(segment.get("runninghub_usage"), dict) else {}
    total_task_cost = _format_task_duration(usage.get("total_task_cost_seconds")) if usage.get("task_cost_seconds_known") else "待同步"
    total_coins = _format_usage_number(usage.get("total_consume_coins"), " RH币") if usage.get("consume_coins_known") else "待同步"
    segment_index = int(segment.get("segment_index") or 0)
    active_task_id = str(segment.get("task_id") or "").strip()
    selected_task_id = ""
    try:
        from generation.render_history import active_render

        selected_render = active_render(segment)
        selected_submission = (
            selected_render.get("submission")
            if isinstance(selected_render, dict) and isinstance(selected_render.get("submission"), dict)
            else {}
        )
        selected_task_id = str(selected_submission.get("task_id") or "").strip()
    except Exception:
        selected_task_id = ""
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    rows: list[str] = []
    for index, attempt in enumerate(attempts, start=1):
        task_id = str(attempt.get("task_id") or "").strip()
        attempt_model_id = runninghub_attempt_model_id(segment, attempt)
        attempt_model_label = _model_label(attempt_model_id) if attempt_model_id else "未标注"
        video_path = "" if attempt.get("video_deleted") else str(attempt.get("video_path") or "").strip()
        # Attempts created before task-video history was introduced can still
        # preview/delete the currently active completed task.
        if not video_path and task_id and task_id == active_task_id and not attempt.get("video_deleted"):
            video_path = str(segment.get("video_path") or job.get("video_path") or "").strip()
        has_video = bool(video_path and Path(video_path).is_file())
        video_url = ""
        if has_video:
            resolved_video = str(Path(video_path).resolve()).replace("\\", "/")
            video_url = f"/gradio_api/file={resolved_video}"
        prompt_text = str(attempt.get("submitted_prompt") or "").strip()
        prompt_button = (
            f'<button type="button" class="usage-icon-btn usage-prompt-btn" data-submitted-prompt="{esc(prompt_text)}" '
            'aria-label="预览本次提交提示词" title="预览本次提交提示词" '
            'onclick="event.stopPropagation(); window.AICF.openUsageAttemptPrompt(this)">'
            f"{PROMPT_PREVIEW_ICON}</button>"
            if prompt_text
            else f'<button type="button" class="usage-icon-btn usage-prompt-btn" aria-label="该旧记录未保存提示词" title="该旧记录未保存提示词" disabled>{PROMPT_PREVIEW_ICON}</button>'
        )
        delete_disabled = "" if has_video else "disabled"
        delete_title = "删除该次任务生成的视频" if has_video else "该任务没有可删除的本地视频"
        is_selected = bool(has_video and task_id and task_id == selected_task_id)
        select_disabled = "" if has_video else "disabled"
        select_title = "当前采用的视频" if is_selected else ("设为本分段最终合成视频" if has_video else "该任务没有可用的本地视频")
        select_class = " is-active" if is_selected else ""
        select_pressed = "true" if is_selected else "false"
        task_id_js = esc(json.dumps(task_id, ensure_ascii=False))
        status_text = str(attempt.get("status") or "submitted")
        if attempt.get("video_deleted"):
            status_text += "（视频已删除）"
        elif is_selected:
            status_text += "（当前采用）"
        row_class = "usage-history-row usage-history-row-playable" if has_video else "usage-history-row"
        rows.append(
            f'<tr class="{row_class}" data-video-url="{esc(video_url)}" '
            f'title="{esc("双击播放该次任务生成的视频" if has_video else "该任务视频尚不可用")}" '
            'ondblclick="return window.AICF.playUsageAttemptVideo(this)">'
            f"<td>{esc(attempt.get('attempt') or index)}</td>"
            f'<td class="usage-model-label" title="{esc(attempt_model_id)}">{esc(attempt_model_label)}</td>'
            f"<td class=\"usage-task-id\">{esc(task_id or '—')}</td>"
            f"<td>{esc(status_text)}</td>"
            f"<td>{esc(_format_task_duration(attempt.get('task_cost_seconds')))}</td>"
            f"<td>{esc(_format_usage_number(attempt.get('consume_coins'), ' RH币'))}</td>"
            '<td class="usage-delete-cell">'
            '<div class="usage-row-actions">'
            f"{prompt_button}"
            f'<button type="button" class="usage-icon-btn usage-select-btn{select_class}" aria-label="{esc(select_title)}" '
            f'title="{esc(select_title)}" aria-pressed="{select_pressed}" {select_disabled} '
            f'onclick="event.stopPropagation(); return window.AICF.selectUsageAttemptVideo({segment_index}, {task_id_js}, this)">{SELECT_VIDEO_ICON}</button>'
            f'<button type="button" class="usage-icon-btn usage-delete-btn" aria-label="删除任务 {esc(task_id)} 的视频" title="{esc(delete_title)}" {delete_disabled} '
            f'onclick="event.stopPropagation(); return window.AICF.confirmDeleteTaskVideo({segment_index}, {task_id_js}, this)">&#215;</button>'
            "</div>"
            "</td>"
            "</tr>"
        )
    return f"""
        <details class="usage-history">
            <summary>生成与计费明细（生成 {len(attempts)} 次，重试 {max(0, len(attempts) - 1)} 次，累计 {total_coins}，耗时 {total_task_cost}）</summary>
            <div class="usage-table-wrap">
                <table>
                    <thead><tr><th>次数</th><th>生成模型</th><th>任务 ID</th><th>状态</th><th>任务耗时</th><th>RH币</th><th aria-label="任务操作"></th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            </div>
        </details>
    """


def _retired_runninghub_history(data: dict[str, Any]) -> str:
    """Render retired history separately so it never looks like an active part retry."""
    rows: list[tuple[str, dict[str, Any], str]] = []
    for item in data.get("retired_runninghub_attempts") or []:
        if isinstance(item, dict):
            owners = ", ".join(str(value) for value in item.get("legacy_owner_parts") or [] if str(value))
            rows.append((owners or "历史重复记录", item, str(item.get("retired_reason") or "历史归档")))
    for segment in data.get("retired_segments") or []:
        if not isinstance(segment, dict):
            continue
        label = str(segment.get("former_part_id") or segment.get("part_id") or "历史分段")
        reason = str(segment.get("retired_reason") or "已归档")
        for attempt in segment.get("runninghub_attempts") or []:
            if isinstance(attempt, dict):
                rows.append((label, attempt, reason))
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{esc(owner)}</td><td class=\"usage-task-id\">{esc(attempt.get('task_id') or '—')}</td>"
        f"<td>{esc(attempt.get('status') or 'submitted')}</td><td>{esc(reason)}</td></tr>"
        for owner, attempt, reason in rows
    )
    return f"""
        <details class="usage-history history-archive">
            <summary>已归档任务明细（{len(rows)} 条，不计入当前视频分段）</summary>
            <p class="usage-history-note">删除或拆分前的任务保留在这里，避免复制到当前 Part。</p>
            <div class="usage-table-wrap"><table>
                <thead><tr><th>原归属</th><th>任务 ID</th><th>状态</th><th>归档原因</th></tr></thead>
                <tbody>{body}</tbody>
            </table></div>
        </details>
    """


def _parse_payload(text: str | dict[str, Any] | None) -> dict[str, Any]:
    if text is None:
        text = load_json_file_silent("video_jobs.json")
    if isinstance(text, dict):
        return text
    if not text:
        return {"segments": []}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"segments": []}
    except Exception:
        return {"segments": []}


def _entry_names(entry: dict[str, Any]) -> set[str]:
    names = {
        str(entry.get("id") or "").strip(),
        str(entry.get("display_name") or "").strip(),
        str(entry.get("name") or "").strip(),
        str(entry.get("name_cn") or "").strip(),
        str(entry.get("asset_identity_id") or "").strip(),
        str(entry.get("asset_background_id") or "").strip(),
    }
    aliases = entry.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = [aliases]
    names.update(str(alias or "").strip() for alias in aliases)
    return {name for name in names if name}


def _display_name(entry: dict[str, Any], fallback: str) -> str:
    return str(
        entry.get("display_name")
        or entry.get("name_cn")
        or entry.get("name")
        or fallback
    ).strip()


def _display_context() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bible = load_project_bible() or {}
    characters = [
        item
        for item in list(bible.get("core_characters") or []) + list(bible.get("supporting_characters") or [])
        if isinstance(item, dict)
    ]
    backgrounds = [item for item in bible.get("allowed_backgrounds") or [] if isinstance(item, dict)]
    return characters, backgrounds


def _current_beats() -> list[dict[str, Any]]:
    raw = load_json_file_silent("beats.json") or ""
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return [item for item in data.get("beats") or [] if isinstance(item, dict)] if isinstance(data, dict) else []


def _workflow_prompt_text(workflow_path: str, fallback: str = "") -> str:
    """Read the exact prompt currently stored in the LiconMSR workflow node."""
    path_text = str(workflow_path or "").strip()
    if not path_text:
        return str(fallback or "")
    try:
        path = Path(path_text)
        if not path.is_file():
            return str(fallback or "")
        workflow = json.loads(path.read_text(encoding="utf-8-sig"))
        node_id = str(CONFIG.get("licon_prompt_node") or "5")
        node = workflow.get(node_id) if isinstance(workflow, dict) else None
        inputs = node.get("inputs") if isinstance(node, dict) else None
        prompt = inputs.get("text") if isinstance(inputs, dict) else None
        return str(prompt) if isinstance(prompt, str) else str(fallback or "")
    except Exception:
        return str(fallback or "")


def _workflow_prompt_prefix(
    workflow_prompt: str,
    director_prompt: str,
    fallback_prefix: str = "",
) -> str:
    """Return the generated prefix while preserving an existing workflow verbatim."""
    full_text = str(workflow_prompt or "")
    director_text = str(director_prompt or "")
    if director_text and full_text.endswith(director_text):
        return full_text[: -len(director_text)].rstrip()
    return str(fallback_prefix or "").rstrip()


def _material_selector_payload(
    segment: dict[str, Any],
    beat: dict[str, Any],
    materials: dict[str, list[dict[str, Any]]],
) -> str:
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    roles_source = (
        segment.get("reference_roles")
        if "reference_roles" in segment
        else job.get("reference_roles") if "reference_roles" in job else beat.get("reference_roles")
    )
    image_ids_source = (
        segment.get("reference_image_ids")
        if isinstance(segment.get("reference_image_ids"), dict)
        else job.get("reference_image_ids")
        if isinstance(job.get("reference_image_ids"), dict)
        else beat.get("reference_image_ids")
    )
    scene_source = (
        segment.get("scene_id")
        if "scene_id" in segment
        else job.get("scene_id") if "scene_id" in job else beat.get("scene_id")
    )
    visible_roles_source = (
        segment.get("visible_roles")
        if "visible_roles" in segment
        else job.get("visible_roles") if "visible_roles" in job else beat.get("visible_roles")
    )
    payload = {
        "characters": list(materials.get("characters") or []),
        "backgrounds": list(materials.get("backgrounds") or []),
        "reference_roles": [str(role) for role in roles_source or [] if str(role or "").strip()],
        "reference_image_ids": {
            str(role): str(image_id).strip()
            for role, image_id in (image_ids_source or {}).items()
            if str(role).strip() and str(image_id).strip()
        },
        "visible_roles": [str(role) for role in visible_roles_source or [] if str(role or "").strip()],
        "scene_id": str(scene_source or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _role_display(role: Any, characters: list[dict[str, Any]]) -> str:
    role_text = str(role or "").strip()
    if not role_text:
        return ""
    for entry in characters:
        if role_text in _entry_names(entry):
            return _display_name(entry, role_text)
    if ROLE_ID_RE.match(role_text):
        return "未绑定角色"
    return role_text


def _scene_display(scene: Any, backgrounds: list[dict[str, Any]]) -> str:
    scene_text = str(scene or "").strip()
    if not scene_text:
        return ""
    for entry in backgrounds:
        if scene_text in _entry_names(entry):
            return _display_name(entry, scene_text)
    if SCENE_ID_RE.match(scene_text):
        return "未绑定场景"
    return scene_text


def _role_list_display(roles: Any, characters: list[dict[str, Any]]) -> str:
    names = [_role_display(role, characters) for role in roles or []]
    return "、".join(name for name in names if name)


def groups_panel_value(guides_text=None):
    return render_groups_panel(load_json_file_silent("video_jobs.json"))


def render_segment_panel(groups_text, segment_index: int) -> str:
    """Render one updated segment card for a local, state-preserving UI swap."""
    data = _parse_payload(groups_text)
    target_index = int(segment_index or 0)
    target = next(
        (
            item
            for item in data.get("segments") or []
            if isinstance(item, dict) and int(item.get("segment_index") or 0) == target_index
        ),
        None,
    )
    if target is None:
        return ""
    isolated = dict(data)
    isolated["segments"] = [target]
    return render_groups_panel(isolated, page=0)


def render_guide_reference_gallery(guides_text=None):
    """Return selected reference/background images for the active direct workflow."""
    try:
        data = _parse_payload(load_json_file_silent("video_jobs.json"))
        items = []
        seen = set()
        for segment in data.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            part_id = str(segment.get("part_id") or segment.get("segment_id") or "")
            job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
            for index, path in enumerate(job.get("reference_images") or [], start=1):
                if path and Path(path).exists() and path not in seen:
                    seen.add(path)
                    items.append((str(Path(path).resolve()), f"{part_id} Reference {index}"))
            bg = job.get("background_image")
            if bg and Path(bg).exists() and bg not in seen:
                seen.add(bg)
                items.append((str(Path(bg).resolve()), f"{part_id} Background"))
        return items
    except Exception:
        return []


def _video_path_for_segment(segment: dict[str, Any]) -> str:
    segment_status = str(segment.get("status") or "").strip()
    # Render history separates the selected playable artifact from the newest
    # provider task.  Keep the selected video visible while a replacement is
    # prepared, queued or running; a successful replacement promotes itself.
    if isinstance(segment.get("renders"), list):
        try:
            from generation.render_history import active_render_video_path

            path_text = active_render_video_path(segment)
            if path_text and Path(path_text).exists():
                return str(Path(path_text).resolve())
        except Exception:
            pass
        if segment_status in {"workflow_ready", "needs_regenerate", "stale", "pending", "queued", "running"}:
            return ""
        return ""
    if segment_status in {"workflow_ready", "needs_regenerate", "stale", "pending", "queued", "running"}:
        stale_path = _stale_video_path_for_segment(segment)
        stale_reason = str(
            segment.get("stale_reason")
            or ((segment.get("job") or {}).get("stale_reason") if isinstance(segment.get("job"), dict) else "")
            or ""
        ).strip().casefold()
        if stale_path and stale_reason == "prompt changed":
            return stale_path
        return ""
    direct = str(segment.get("video_path") or "").strip()
    if direct and Path(direct).exists():
        return str(Path(direct).resolve())
    job_path = str((segment.get("job") or {}).get("video_path") or "").strip()
    if job_path and Path(job_path).exists():
        return str(Path(job_path).resolve())
    part_id = str(segment.get("part_id") or "").strip()
    if part_id:
        from services.context import get_project_dir

        candidates = sorted(
            [p for p in (get_project_dir() / "videos").glob(f"{part_id}_*.mp4") if p.is_file()],
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        if candidates:
            return str(candidates[0].resolve())
    return ""


def _stale_video_path_for_segment(segment: dict[str, Any]) -> str:
    for raw in (
        segment.get("stale_video_path"),
        (segment.get("job") or {}).get("stale_video_path") if isinstance(segment.get("job"), dict) else "",
    ):
        path_text = str(raw or "").strip()
        if path_text and Path(path_text).exists():
            return str(Path(path_text).resolve())
    return ""


def _render_video_preview_btn(video_path: str, label: str) -> str:
    if not video_path:
        return ""
    src = str(Path(video_path).resolve()).replace("\\", "/")
    url = f"/gradio_api/file={src}"
    return (
        f'<button class="card-regen-btn" data-video-url="{esc(url)}" '
        "onclick=\"if(window.AICFPreview&&window.AICFPreview.video)"
        "{window.AICFPreview.video(this.dataset.videoUrl)}\" "
        f'title="预览 {esc(label)}">预览</button>'
    )


def _segment_status(part_id: str, video_path: str, segment_status: str = "") -> tuple[str, str]:
    segment_status = str(segment_status or "").strip()
    if segment_status == "workflow_ready":
        return "未生成", "status-pending"
    if segment_status in {"needs_regenerate", "stale"}:
        return "需要重新生成", "status-stale"
    if segment_status == "running":
        return "生成中", "status-running"
    if segment_status == "queued":
        return "排队中", "status-pending"
    if segment_status in {"failed", "error"}:
        # A failed re-generation may intentionally retain a playable stale
        # video.  Do not fall back to render_log here: that legacy log only
        # knows the current path is empty and can incorrectly call this active
        # segment "deleted".
        return "生成失败", "status-failed"
    if segment_status == "pending":
        return "等待生成", "status-pending"
    if segment_status == "success":
        return "视频已生成", "status-success"
    stale_statuses = {"needs_regenerate", "stale", "workflow_ready", "pending"}
    try:
        from ui.render_state import part_status_info

        label, css, _entry = part_status_info(part_id)
        status = str(_entry.get("status") or "").strip()
        if status in stale_statuses:
            return label, css
        return label, css
    except Exception:
        return ("视频已生成", "status-success") if video_path else ("未生成", "status-pending")


def render_groups_panel(groups_text, guides_text="", page=0, fast=False, unmarked_only=False):
    data = _parse_payload(groups_text)
    segments = [item for item in data.get("segments") or [] if isinstance(item, dict)]
    viewing_label = get_current_episode_name() or "当前生成"
    if not segments:
        return """
        <div class="right-panel">
            <div class="panel-heading">
                <div>
                    <div class="section-kicker">Segments</div>
                    <div class="panel-title">视频分段</div>
                </div>
            </div>
            <div class="empty-card">还没有视频分段。先生成 Beats，再生成对应模型的视频。</div>
        </div>
        """

    displayed_segments = _display_segments(segments, bool(unmarked_only))
    total_pages = max((len(displayed_segments) + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE, 1)
    page = max(0, min(int(page or 0), total_pages - 1))
    page_segments = displayed_segments[page * GROUPS_PER_PAGE:(page + 1) * GROUPS_PER_PAGE]
    characters, backgrounds = _display_context()
    project_materials = selected_project_materials()
    beats = _current_beats()

    cards: list[str] = []
    total_duration = 0
    for segment in page_segments:
        idx = int(segment.get("segment_index") or 0)
        part_id = str(segment.get("part_id") or f"part_{idx:03d}")
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        title = str(segment.get("title") or job.get("title") or f"Segment {idx}")
        duration = int(float(job.get("duration_sec") or 0))
        total_duration += duration
        usage_history = _runninghub_usage_history(segment)
        roles = _role_list_display(segment.get("reference_roles"), characters)
        visible_roles = _role_list_display(segment.get("visible_roles"), characters)
        offscreen = _role_list_display(segment.get("offscreen_speakers"), characters)
        mentioned = _role_list_display(segment.get("mentioned_roles"), characters)
        scene_name = _scene_display(segment.get("scene_id"), backgrounds)
        workflow_path = str(segment.get("workflow_path") or job.get("workflow_path") or "")
        backend = str(segment.get("backend") or "")
        task_id = str(segment.get("task_id") or "")
        prompt_id = str(segment.get("prompt_id") or "")
        video_path = _video_path_for_segment(segment)
        stale_video_path = _stale_video_path_for_segment(segment)
        status_label, status_class = _segment_status(part_id, video_path, str(segment.get("status") or ""))
        segment_status = str(segment.get("status") or "").strip().lower()
        generation_active = segment_status in {"running", "queued", "pending"}
        regen_disabled = 'disabled aria-disabled="true"' if generation_active else ""
        regen_label = "排队中…" if segment_status == "queued" else "生成中…" if generation_active else "重新生成视频"
        protected_video_ok = _segment_video_ok(segment)
        protected_badge = '<span class="status-badge status-success">视频OK保护</span>' if protected_video_ok else ""
        protected_button_label = "取消保护" if protected_video_ok else "标记视频OK"
        protected_button_pressed = "true" if protected_video_ok else "false"
        preview_btn = _render_video_preview_btn(video_path, part_id)
        stale_preview_btn = _render_video_preview_btn(stale_video_path, f"{part_id} old")
        can_toggle_video_ok = protected_video_ok or bool(video_path or stale_video_path)
        protected_button_disabled = "" if can_toggle_video_ok else "disabled"
        protected_button_title = "取消视频OK保护" if protected_video_ok else (
            "标记该分段视频OK" if can_toggle_video_ok else "本段还没有生成视频，不能标记视频OK"
        )
        video_display = short_path_name(video_path) or "未生成"
        if stale_video_path and not video_path:
            video_display = "待重新生成"
        show_stale_video = bool(
            stale_video_path
            and (
                not video_path
                or Path(stale_video_path).resolve() != Path(video_path).resolve()
            )
        )
        stale_video_html = (
            f'<span class="stale-video-line">旧版本视频：<b>{esc(short_path_name(stale_video_path))}</b> {stale_preview_btn}</span>'
            if show_stale_video
            else ""
        )
        director_prompt = str(job.get("director_prompt") or "").strip()
        prompt = str(job.get("prompt") or "")
        final_prompt = str(job.get("final_prompt") or "").strip()
        if not director_prompt:
            director_prompt = extract_director_prompt(prompt or final_prompt)
        if not final_prompt:
            final_prompt = prompt_with_character_descriptions(
                director_prompt or prompt,
                [str(role) for role in segment.get("reference_roles") or []],
                segment.get("reference_image_ids") if isinstance(segment.get("reference_image_ids"), dict) else {},
            )
        reference_roles = [str(role) for role in segment.get("reference_roles") or []]
        reference_image_ids = (
            segment.get("reference_image_ids")
            if isinstance(segment.get("reference_image_ids"), dict)
            else job.get("reference_image_ids")
            if isinstance(job.get("reference_image_ids"), dict)
            else {}
        )
        workflow_prompt = _workflow_prompt_text(workflow_path, final_prompt)
        generated_prefix = prompt_with_character_descriptions("", reference_roles, reference_image_ids)
        workflow_prompt_prefix = _workflow_prompt_prefix(workflow_prompt, director_prompt, generated_prefix)
        prompt_lines = director_prompt.count("\n") + 1 if director_prompt else 0
        prompt_chars = len(director_prompt)
        saved_label = "已保存" if segment.get("prompt_saved", True) else "未保存"
        saved_class = "status-success" if segment.get("prompt_saved", True) else "status-pending"
        beat = beats[idx - 1] if 0 < idx <= len(beats) else {}
        material_payload = _material_selector_payload(segment, beat, project_materials)
        desired_model_id = _model_id_for_segment(segment, data)
        desired_model_html = _model_selector_html(segment, data)
        actual_model = _render_model_label(segment)
        actual_model_html = (
            f'<div class="detail-item"><span>当前成片模型</span><b>{esc(actual_model)}</b></div>'
            if actual_model
            else '<div class="detail-item"><span>当前成片模型</span><b>尚未生成</b></div>'
        )
        if desired_model_id == "minimax_h3_local_ref2va":
            editor_html = _h3_config_html(segment, material_payload)
            video_regen_action_html = (
                f'<button type="button" class="card-regen-btn primary-mini" {regen_disabled} '
                f'onclick="return window.AICF.regenerateH3Segment(\'{idx}\', this)">{regen_label}</button>'
            )
            prompt_regen_action_html = f"""
                <button type="button" class="card-regen-btn" onclick="return window.AICF.triggerSegmentRegen('{idx}', this)">
                    重新生成提示词
                </button>
            """
        else:
            editor_html = f"""
                <details class="prompt-block segment-editor-block" open>
                    <summary class="prompt-summary">
                        <span>导演分镜</span>
                        <small>{prompt_chars} 字符 / {prompt_lines} 行，只编辑这一部分</small>
                    </summary>
                    <div class="prompt-toolbar segment-editor-toolbar" role="group" aria-label="{esc(part_id)} 导演分镜操作">
                        <button type="button" class="card-regen-btn primary-mini" onclick="window.AICF.saveSegmentPrompt('{idx}', this).catch(function(error){{alert(error.message || '保存分段提示词失败')}})">保存本段提示词</button>
                        <button type="button" class="card-regen-btn" onclick="window.AICF.openPromptModalFromButton(this)">全屏编辑</button>
                        <button type="button" class="card-regen-btn action-utility" onclick="window.AICF.copyPromptFromButton(this)">复制导演分镜</button>
                    </div>
                    <textarea id="segment-director-editor-{idx}" name="segment-director-editor-{idx}" class="segment-director-editor prompt-full" data-segment-index="{idx}" data-render-key="segment-{idx}" spellcheck="false">{esc(director_prompt)}</textarea>
                    <pre class="prompt-preview prompt-full prompt-modal-source" hidden>{esc(director_prompt)}</pre>
                    <pre class="prompt-preview prompt-full final-prompt-source" hidden>{esc(final_prompt)}</pre>
                    <pre class="workflow-prompt-source" hidden>{esc(workflow_prompt)}</pre>
                    <pre class="workflow-prompt-prefix-source" hidden>{esc(workflow_prompt_prefix)}</pre>
                    <div class="segment-material-source" data-materials="{esc(material_payload)}" hidden></div>
                </details>
            """
            video_regen_action_html = f"""
                <button type="button" class="card-regen-btn primary-mini" {regen_disabled} onclick="return window.AICF.triggerSegmentVideoRegen('{idx}', this)">
                    {regen_label}
                </button>
            """
            prompt_regen_action_html = f"""
                <button type="button" class="card-regen-btn" onclick="return window.AICF.triggerSegmentRegen('{idx}', this)">
                    重新生成提示词
                </button>
            """

        cards.append(
            f"""
            <div class="group-card" data-segment-index="{idx}" data-model-id="{esc(desired_model_id)}" data-video-ok="{str(protected_video_ok).lower()}">
                <div class="group-head">
                    <div class="segment-title-block">
                        <div class="segment-id">{esc(part_id)}</div>
                        <div class="segment-title">{esc(title)}</div>
                    </div>
                    <div class="group-actions">
                        <div class="segment-status-row">
                            <span class="status-badge {esc(status_class)}" data-segment-status>{esc(status_label)}</span>
                            {protected_badge}
                            <span class="status-badge {esc(saved_class)}">提示词{esc(saved_label)}</span>
                        </div>
                        <div class="segment-action-row" role="group" aria-label="{esc(part_id)} 分段操作">
                            {video_regen_action_html}
                            {prompt_regen_action_html}
                            <button type="button" class="card-regen-btn action-utility" aria-pressed="{protected_button_pressed}" title="{esc(protected_button_title)}" {protected_button_disabled} onclick="return window.AICF.toggleSegmentVideoOk('{idx}', {str(protected_video_ok).lower()}, this)">
                                {protected_button_label}
                            </button>
                        </div>
                        <div class="segment-action-feedback" role="status" aria-live="polite"></div>
                    </div>
                </div>
                <div class="segment-meta-grid single-metric">
                    <div class="metric-card"><span>时长</span><b>{duration} 秒</b></div>
                </div>
                <div class="segment-model-row">{desired_model_html}</div>
                <div class="segment-detail-grid">
                    <div class="detail-item"><span>参考人物</span><b>{esc(roles or "按参考图")}</b></div>
                    <div class="detail-item"><span>入画人物</span><b>{esc(visible_roles or roles or "按参考图")}</b></div>
                    <div class="detail-item"><span>画外声音</span><b>{esc(offscreen or "无")}</b></div>
                    <div class="detail-item"><span>仅提及</span><b>{esc(mentioned or "无")}</b></div>
                    <div class="detail-item"><span>场景</span><b>{esc(scene_name or "默认背景")}</b></div>
                    <div class="detail-item" data-segment-backend><span>后端</span><b>{esc(backend or "未提交")}</b></div>
                    {actual_model_html}
                    <div class="detail-item path-line" data-segment-task><span>任务</span><b>{esc(task_id or prompt_id or "无")}</b></div>
                    <div class="detail-item path-line"><span>Workflow</span><b>{esc(short_path_name(workflow_path))}</b></div>
                    <div class="detail-item path-line detail-wide"><span>视频</span><b>{esc(video_display)}</b> {preview_btn}{stale_video_html}</div>
                </div>
                {usage_history}
                {editor_html}
            </div>
            """
        )

    final_video = str(data.get("final_video_path") or "")
    final_preview = _render_video_preview_btn(final_video, "final") if final_video and Path(final_video).exists() else ""
    final_usage = data.get("final_video_usage") if isinstance(data.get("final_video_usage"), dict) else {}
    final_coins = (
        _format_usage_number(final_usage.get("total_consume_coins"), " RH币")
        if int(final_usage.get("consume_coins_known_segment_count") or 0) > 0
        else "待同步"
    )
    final_task_cost = (
        _format_task_duration(final_usage.get("total_task_cost_seconds"))
        if int(final_usage.get("task_cost_seconds_known_segment_count") or 0) > 0
        else "待同步"
    )
    final_generation_count = int(final_usage.get("generation_count") or 0)
    final_retry_count = max(0, final_generation_count - int(final_usage.get("segment_count") or 0))
    shown_count = len(displayed_segments)
    count_label = f"未标记OK：{shown_count} / 总分段：{len(segments)}" if unmarked_only else f"总分段：{len(segments)}"
    stats = f"当前查看：{viewing_label}　·　第 {page + 1} / {total_pages} 页　{count_label}　当前页时长：{total_duration} 秒"
    filter_class = " is-filtering" if unmarked_only else ""
    filter_pressed = "true" if unmarked_only else "false"
    filter_label = "仅显示未标记视频OK分段；点击显示所有视频分段" if unmarked_only else "显示所有视频分段；点击仅显示未标记视频OK分段"
    empty_filtered = (
        '<div class="empty-card segment-filter-empty">所有视频分段均已标记 OK。</div>'
        if unmarked_only and not displayed_segments
        else ""
    )
    retired_history = _retired_runninghub_history(data)

    return f"""
    <div class="right-panel">
        <div class="panel-heading">
            <div>
                <div class="section-kicker">Segments</div>
                <div class="panel-title">视频分段</div>
            </div>
            <div class="stats-pill">{esc(stats)}</div>
        </div>
        <div class="panel-subtitle-row">
            <div class="panel-subtitle">每个 Beat 由所选模型独立编译；可单段重新生成、预览并最终合并。各模型的时长、参考素材和音频能力以其自身契约为准。</div>
            <button type="button" class="segment-ok-filter{filter_class}" aria-pressed="{filter_pressed}" aria-label="{filter_label}" title="{filter_label}" onclick="return window.AICF.toggleVideoOkSegmentVisibility(this)">OK</button>
        </div>
        <div class="usage-summary-line" role="status">本集最终视频累计：<b>{esc(final_coins)}</b><span>（共 {final_generation_count} 次生成，重试 {final_retry_count} 次，任务累计耗时 {esc(final_task_cost)}）</span></div>
        {empty_filtered}
        {"".join(cards)}
        {retired_history}
        <div class="final-output-line">最终视频：<b>{esc(short_path_name(final_video) or "未合并")}</b> {final_preview}<span class="final-usage">RH币总消耗：<b>{esc(final_coins)}</b></span></div>
    </div>
    """


def group_count_from_text(groups_text, unmarked_only=False):
    segments = [item for item in _parse_payload(groups_text).get("segments") or [] if isinstance(item, dict)]
    return len(_display_segments(segments, bool(unmarked_only)))


def group_total_pages(groups_text, unmarked_only=False):
    return max((group_count_from_text(groups_text, unmarked_only) + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE, 1)


def group_page_label(page, groups_text, unmarked_only=False):
    total = group_total_pages(groups_text, unmarked_only)
    page = max(0, min(int(page or 0), total - 1))
    return f'<span class="pager-label">第 {page + 1} / {total} 页</span>'


def group_part_ids_for_page(groups_text, page, unmarked_only=False):
    segments = [item for item in _parse_payload(groups_text).get("segments") or [] if isinstance(item, dict)]
    displayed_segments = _display_segments(segments, bool(unmarked_only))
    page = max(0, min(int(page or 0), group_total_pages(groups_text, unmarked_only) - 1))
    page_segments = displayed_segments[page * GROUPS_PER_PAGE:(page + 1) * GROUPS_PER_PAGE]
    return [str(segment.get("part_id") or f"part_{int(segment.get('segment_index') or 0):03d}") for segment in page_segments]


def group_regen_button_updates(groups_text, page, unmarked_only=False):
    updates = []
    for part_id in group_part_ids_for_page(groups_text, page, unmarked_only):
        if part_id:
            updates.append(gr.update(value=f"重新生成 {part_id}", visible=True))
        else:
            updates.append(gr.update(value="重新生成", visible=False))
    return updates


def change_group_page(groups_text, guides_text, page, delta):
    total = group_total_pages(groups_text)
    page = max(0, min(int(page or 0) + int(delta), total - 1))
    return (page, render_groups_panel(groups_text, "", page, fast=True), group_page_label(page, groups_text), *group_regen_button_updates(groups_text, page))


def rerender_group_page(groups_text, guides_text, page):
    return (render_groups_panel(groups_text, "", page), group_page_label(page, groups_text), *group_regen_button_updates(groups_text, page))


def parse_group_guide_ids_text(text):
    raise ValueError("Guide 分组编辑已移除；请按 LiconMSR segment 重新生成。")
