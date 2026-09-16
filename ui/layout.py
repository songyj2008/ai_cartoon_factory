"""Gradio component layout for the LiconMSR-only pipeline."""
from __future__ import annotations

from html import escape
from pathlib import Path

import gradio as gr

from services.context import CONFIG, EPISODES_DIR, FPS, OUTPUT_DIR, get_current_episode_name, get_current_project_name, get_project_dir
from services.file_utils import load_json_file_silent
from services.brand import is_brand_verified, render_brand_card
from services.ui_run_config import load_ui_run_config
from ui.scripts import APP_JS_FUNCTION
from ui.styles import CSS
from ui.progress_panel import render_workflow_progress
from ui.project_bible_panel import project_bible_asset_choices, project_bible_selected_asset_ids, selected_project_asset_gallery
from ui.project_handlers import (
    _episode_choices,
    _project_choices,
    available_video_models,
    current_default_video_model,
    latest_final_video_path,
    list_projects,
    secret_key_placeholders,
    workflow_model_settings,
)
from ui.video_feedback import ISSUE_TYPES, video_part_choices
from ui.asset_library_panel import asset_library_choices, asset_target_dir_choices
from ui.group_panel import group_total_pages
from ui.gradio_components import null_safe_multiselect_dropdown


def _latest_runtime_video(project_name: str, episode_name: str = "") -> Path | None:
    root = (EPISODES_DIR / project_name / episode_name) if episode_name else (OUTPUT_DIR / project_name)
    candidates = []
    final_dir = root / "final"
    videos_dir = root / "videos"
    if final_dir.exists():
        candidates.extend(p for p in final_dir.glob("final_video_*.mp4") if p.is_file())
        if not candidates:
            candidates.extend(p for p in final_dir.glob("*.mp4") if p.is_file())
    if not candidates and videos_dir.exists():
        candidates.extend(p for p in videos_dir.glob("*.mp4") if p.is_file())
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0].resolve()


def _video_url(path: Path | None) -> str:
    return "/gradio_api/file=" + str(path).replace("\\", "/") if path else ""


def _episode_strip_html() -> str:
    project_name = get_current_project_name() or "current"
    current_episode = get_current_episode_name() or ""
    items = []
    for index, (label, value) in enumerate(_episode_choices(project_name)):
        episode = str(value or "")
        if episode:
            digits = "".join(ch for ch in episode if ch.isdigit())
            display = str(int(digits)) if digits else str(index)
        else:
            display = "当前生成"
        video_url = _video_url(_latest_runtime_video(project_name, episode))
        active = " active" if episode == current_episode else ""
        title = "当前生成（未归档）" if not episode else (label or episode)
        items.append(
            '<button type="button" class="episode-cell{active}" data-episode="{episode}" data-video-url="{video}" title="{title}"><b>{display}</b></button>'.format(
                active=active,
                episode=escape(episode, quote=True),
                video=escape(video_url, quote=True),
                title=escape(str(title), quote=True),
                display=escape(display),
            )
        )
    return "".join(items)


def build_layout():
    brand_ok = is_brand_verified()
    ui_config = load_ui_run_config()
    initial_video_jobs_text = load_json_file_silent("video_jobs.json") or ""
    initial_group_total_pages = group_total_pages(initial_video_jobs_text)
    # Themes are deliberately page-scoped.  A refresh must start each project
    # from the neutral default instead of inheriting an earlier project's look.
    initial_theme = "linear"
    with gr.Blocks() as demo:
        runtime_refresh_timer = gr.Timer(value=5.0, active=True)
        gr.HTML(
            """
            <script>
            (function() {
                try {
                    var allowed = {'linear':1,'cursor':1,'vercel':1,'glass':1,'ocean':1,'purple':1,'cyber':1,'classic':1};
                    var theme = %r;
                    if (!allowed[theme]) theme = 'linear';
                    document.documentElement.setAttribute('data-aicf-theme', theme);
                    if (document.body) document.body.setAttribute('data-aicf-theme', theme);
                    window.__AICF_INITIAL_THEME__ = theme;
                    window.__AICF_SERVER_THEME__ = theme;
                    try { localStorage.removeItem('aicf_theme'); } catch (error) {}
                } catch (error) {}
            })();
            </script>
            """ % initial_theme
        )
        with gr.Row(elem_classes=["app-header"]):
            gr.HTML(
                """
                <div class="brand-lockup">
                    <div class="brand-mark">AC</div>
                    <div>
                        <div class="brand-title">AI Cartoon Factory</div>
                        <div class="brand-subtitle">Story → Beats → Video</div>
                    </div>
                </div>
                """
            )
            workflow_progress_panel = gr.HTML(render_workflow_progress())
            gr.HTML(
                '<div class="top-actions">'
                '<div class="episode-strip" id="episode-picker" title="选择当前生成或归档集数">'
                f'<div class="episode-grid" id="episode-grid">{_episode_strip_html()}</div>'
                '</div>'
                '<button type="button" class="open-videos-dir-btn" onclick="openCurrentVideosDir()" aria-label="打开视频目录" title="打开视频目录">'
                '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-9Z"/></svg>'
                '</button>'
                '<button type="button" class="preview-final-btn" onclick="previewLatestFinalVideo()" title="预览最终视频">&#9654;</button>'
                "</div>"
            )
        gr.HTML(render_brand_card(), elem_id="official_platform_section")
        segment_regen_payload = gr.Textbox(elem_id="segment_regen_payload", elem_classes=["aicf-hidden-trigger"])
        segment_regen_btn = gr.Button("segment regen", elem_id="segment_regen_btn", elem_classes=["aicf-hidden-trigger"])
        segment_video_regen_payload = gr.Textbox(elem_id="segment_video_regen_payload", elem_classes=["aicf-hidden-trigger"])
        segment_video_regen_btn = gr.Button("segment video regen", elem_id="segment_video_regen_btn", elem_classes=["aicf-hidden-trigger"])
        segment_video_ok_payload = gr.Textbox(elem_id="segment_video_ok_payload", elem_classes=["aicf-hidden-trigger"])
        segment_video_ok_btn = gr.Button("segment video ok", elem_id="segment_video_ok_btn", elem_classes=["aicf-hidden-trigger"])
        segment_ok_filter_btn = gr.Button("toggle segment OK filter", elem_id="segment_ok_filter_btn", elem_classes=["aicf-hidden-trigger"])
        segment_prompt_save_payload = gr.Textbox(elem_id="segment_prompt_save_payload", elem_classes=["aicf-hidden-trigger"])
        segment_prompt_save_btn = gr.Button("save segment prompt", elem_id="segment_prompt_save_btn", elem_classes=["aicf-hidden-trigger"])
        segment_prompt_batch_payload = gr.Textbox(elem_id="segment_prompt_batch_payload", elem_classes=["aicf-hidden-trigger"])
        episode_select_payload = gr.Textbox(elem_id="episode_select_payload", elem_classes=["aicf-hidden-trigger"])
        episode_select_btn = gr.Button("select episode", elem_id="episode_select_btn", elem_classes=["aicf-hidden-trigger"])
        open_videos_dir_btn = gr.Button("open videos dir", elem_id="open_videos_dir_btn", elem_classes=["aicf-hidden-trigger"])

        main_page = gr.Radio(
            label=None,
            choices=[("创作工作台", "workbench"), ("素材库", "assets"), ("设置", "settings")],
            value="workbench",
            show_label=False,
            elem_id="main_page_tabs",
            elem_classes=["main-page-tabs"],
        )

        with gr.Group(elem_id="main_pages"):
            with gr.Column(visible=True, elem_id="workbench_page", elem_classes=["page-panel"]) as workbench_page:
                with gr.Row(elem_classes=["app-shell"]):
                    with gr.Column(scale=1, min_width=260, elem_classes=["sidebar-column"]):
                        gr.HTML('<div class="side-card"><div class="side-title">项目管理</div><div class="side-copy">选择项目并管理当前运行目录。</div>')
                        project_name = gr.Dropdown(
                            label="当前项目",
                            choices=_project_choices(),
                            value=get_current_project_name() or "current",
                        )
                        project_episode = gr.Dropdown(
                            label="生成 / 归档",
                            choices=_episode_choices(get_current_project_name() or "current"),
                            value=get_current_episode_name(),
                            visible=True,
                        )
                        project_rename_text = gr.Textbox(label="重命名项目", placeholder="输入新项目名称", lines=1)
                        rename_project_btn = gr.Button("重命名项目")
                        archive_episode_name = gr.Textbox(label="归档名称", placeholder="留空自动生成：自动顺延到下一集", lines=1)
                        archive_generation_btn = gr.Button("归档当前生成", elem_id="archive_generation_btn")
                        archive_path_box = gr.Textbox(value="", visible=False)
                        clean_btn = gr.Button("清空当前生成")
                        gr.HTML("</div>")

                        gr.HTML('<div class="side-card"><div class="side-title">项目素材选择</div><div class="side-copy">为角色、物品和场景绑定参考素材。</div>')
                        character_choices, background_choices = project_bible_asset_choices()
                        selected_character_ids, selected_background_ids = project_bible_selected_asset_ids()
                        bible_character_select = null_safe_multiselect_dropdown(
                            label="核心人物 / 物品参考图",
                            choices=character_choices,
                            value=selected_character_ids,
                        )
                        bible_background_select = null_safe_multiselect_dropdown(
                            label="背景参考图",
                            choices=background_choices,
                            value=selected_background_ids,
                        )
                        manual_bible_btn = gr.Button("保存已选素材")
                        selected_asset_gallery = gr.Gallery(
                            label="已选参考图 / 背景图",
                            value=selected_project_asset_gallery(selected_character_ids, selected_background_ids),
                            columns=2,
                            height=300,
                            object_fit="contain",
                        )
                        gr.HTML("</div>")

                    with gr.Column(scale=3, min_width=640, elem_classes=["workspace-column"]):
                        gr.HTML('<div class="workspace-card hero-card"><div class="section-kicker">创作控制台</div><div class="section-title">从主题到成片的一键流水线</div><div class="section-subtitle">保留原有流程，优化输入、执行与反馈的视觉层级。</div>')
                        with gr.Row(elem_classes=["topic-row"]):
                            topic = gr.Textbox(label="主题", placeholder="输入视频主题", value="", lines=1, scale=8, elem_id="topic_editor")
                            with gr.Column(scale=0, min_width=260, elem_classes=["duration-beat-stack"]):
                                with gr.Row(elem_classes=["compact-field-row"]):
                                    gr.HTML('<div class="compact-field-label">总时长：</div>')
                                    duration_select = gr.Dropdown(
                                        label=None,
                                        show_label=False,
                                        container=False,
                                        choices=["30s", "60s", "90s", "120s", ("自定义", "custom")],
                                        value="60s",
                                        scale=0,
                                        min_width=128,
                                        elem_classes=["compact-field-input", "duration-control"],
                                    )
                                with gr.Row(
                                    elem_classes=["compact-field-row", "custom-duration-row"],
                                ):
                                    gr.HTML('<div class="compact-field-label">自定义时长：</div>')
                                    custom_duration_box = gr.Number(
                                        label=None,
                                        show_label=False,
                                        container=False,
                                        value=60,
                                        step=1,
                                        precision=0,
                                        interactive=False,
                                        scale=0,
                                        min_width=120,
                                        elem_classes=["compact-field-input", "custom-duration-control"],
                                    )
                                target_beat_count_box = gr.Textbox(
                                    label=None,
                                    show_label=False,
                                    value="",
                                    elem_id="target_beat_count_box",
                                    elem_classes=["target-beat-count-hidden"],
                                )
                                with gr.Row(elem_classes=["compact-field-row"]):
                                    gr.HTML('<div class="compact-field-label">目标 Beat 数：</div>')
                                    gr.HTML(
                                        '<input id="target_beat_count_proxy" class="target-beat-count-proxy" '
                                        'type="number" min="1" step="1" inputmode="numeric" aria-label="目标 Beat 数">',
                                        elem_classes=["compact-field-input", "beat-count-control"],
                                    )

                        with gr.Row(elem_classes=["main-action-row"]):
                            gen_story_btn = gr.Button("生成完整剧情", elem_classes=["primary-btn", "step-action"])
                            split_beats_btn = gr.Button("拆分剧情 Beats", elem_classes=["primary-btn", "step-action"])
                            generate_segment_prompts_btn = gr.Button("生成分段提示词", elem_classes=["quick-btn", "quick-blue", "step-action"])
                            render_video_btn = gr.Button("生成视频", elem_classes=["primary-btn", "step-action"])
                            merge_video_btn = gr.Button("合并最终视频", elem_classes=["primary-btn", "step-action"])
                        with gr.Row(elem_classes=["one-click-action-row"]):
                            one_click_full_btn = gr.Button("一键全流程生成", elem_classes=["primary-btn", "one-click-main"])
                        gr.HTML("</div>")

                        with gr.Accordion("已有完整小说？生成 Beats 再生成 Segments", open=False, elem_classes=["novel-segments-panel"]):
                            gr.HTML('<div class="section-subtitle">先把小说切分为 Beats（保留对白与原文），再按当前模型生成分段。会覆盖已有 Beats 与分段信息。</div>')
                            novel_text = gr.Textbox(label="完整小说段", lines=12, max_lines=36, placeholder="粘贴已有小说内容（支持几千字）。系统将保留小说对白与原文，按模型规则切分 Beats 后生成视频 Segments。", elem_id="novel_text_editor")
                            novel_segment_count = gr.Number(label="目标视频分段数", value=4, minimum=1, step=1, precision=0)
                            generate_novel_segments_btn = gr.Button("生成 Beats → Segments", elem_classes=["primary-btn", "step-action"])

                        with gr.Accordion("剧情简介（可编辑）", open=True):
                            story_json = gr.Textbox(
                                label="剧情简介 / 完整剧情输入",
                                value="",
                                lines=10,
                                max_lines=24,
                                interactive=True,
                                placeholder="请输入剧情简介，或修改 AI 生成后的完整剧情。生成完整剧情、拆分 Beats、生成分段提示词都会使用这里的最新内容。",
                                elem_id="story_editor",
                            )

                        with gr.Accordion("Beats", open=True):
                            beats_json = gr.Textbox(
                                label="具体剧情 Beats（可编辑，Ctrl+S 保存）",
                                value="",
                                lines=18,
                                max_lines=36,
                                interactive=True,
                                elem_id="beats_editor",
                            )
                            save_beats_btn = gr.Button("保存剧情修改", elem_id="save_story_beats_btn")
                            with gr.Accordion("Beat 风险检测 / 复杂 Beat 拆分", open=False):
                                beat_risk_notice = gr.HTML("")
                                with gr.Row(elem_classes=["beat-risk-row"]):
                                    complex_beat_index = gr.Number(
                                        label="指定 Beat",
                                        value=1,
                                        step=1,
                                        precision=0,
                                        scale=0,
                                        min_width=188,
                                        elem_id="complex_beat_index",
                                        elem_classes=["beat-split-index"],
                                    )
                                    detect_beat_risk_btn = gr.Button("检测 Beat 风险", elem_classes=["quick-btn", "beat-risk-detect-btn"])
                                    split_complex_beat_btn = gr.Button("继续拆分指定 Beat", elem_classes=["quick-btn", "quick-blue", "beat-split-submit-btn"])
                                    add_beat_btn = gr.Button("新增 Beat", elem_classes=["quick-btn", "beat-add-submit-btn"])
                                    delete_beat_btn = gr.Button("删除指定 Beat", elem_classes=["quick-btn", "beat-delete-submit-btn"])
                                with gr.Row():
                                    copy_beat_source = gr.Number(label="复制源 Beat 序号", value=1, minimum=1, precision=0, step=1)
                                    copy_beat_target = gr.Number(label="插入为 Beat 序号", value=2, minimum=1, precision=0, step=1)
                                    copy_beat_btn = gr.Button("复制 Beat", elem_classes=["quick-btn", "quick-blue"])
                                gr.Markdown("复制剧情、绑定、已保存的提示词和配置；不复制小说原文和生成记录。目标位置及后续 Beat 顺延，填总数 + 1 可追加到末尾。提示词修改后请先保存。")
                                ignore_beat_risk_box = gr.Checkbox(
                                    label="忽略 Beat 风险，继续生成分段提示词",
                                    value=False,
                                    elem_id="ignore_beat_risk_box",
                                )
                                beat_risk_report = gr.Code(
                                    label="Beat 风险报告",
                                    value="",
                                    language="json",
                                    lines=10,
                                    visible=False,
                                )

                        video_jobs_json = gr.Code(
                            label="video_jobs.json",
                            value=initial_video_jobs_text,
                            language="json",
                            lines=1,
                            visible=False,
                        )

                        clear_log_btn = gr.Button("清空日志", elem_classes=["ghost-btn"], visible=False)
                        status = gr.Textbox(
                            label="日志",
                            lines=13,
                            max_lines=13,
                            autoscroll=True,
                            elem_id="status_log",
                            elem_classes=["scroll-log"],
                            visible=False,
                        )

                    with gr.Column(scale=2, min_width=460, elem_classes=["output-column"]):
                        groups_panel = gr.HTML("<div class='muted'>生成分段提示词后显示可编辑的视频分段。</div>")
                        with gr.Row(elem_id="segment_pager", elem_classes=["segment-pager"]):
                            group_prev_page_btn = gr.Button("上一页", elem_classes=["pager-btn"], interactive=True)
                            group_page_label = gr.HTML(f"<span class='pager-label'>第 1 / {initial_group_total_pages} 页</span>")
                            group_next_page_btn = gr.Button("下一页", elem_classes=["pager-btn"], interactive=True)

                        with gr.Column(visible=False, elem_id="project_output_section", elem_classes=["output-card"]):
                            gr.HTML('<div class="panel-title">项目输出</div><div class="panel-subtitle">当前项目生成目录与最终文件。</div>')
                            project_output_dir_box = gr.Textbox(label="输出目录", value=str(get_project_dir()), interactive=False)
                            project_videos_dir_box = gr.Textbox(label="视频目录", value=str(get_project_dir() / "videos"), interactive=False)
                            project_final_video_box = gr.Textbox(label="最终视频", value="", interactive=False)

                        with gr.Column(visible=False, elem_id="final_video_section", elem_classes=["output-card", "final-card"]):
                            gr.HTML('<div class="panel-title">Final 最终视频</div><div class="panel-subtitle">预览最终合成结果。</div>')
                            final_path = latest_final_video_path()
                            final_video_path_display = gr.Textbox(
                                label="最终视频路径",
                                value=str(final_path) if final_path else "",
                                interactive=False,
                            )
                            final_video_preview_btn = gr.Button("预览最终视频", elem_id="final_video_preview_btn")
                            final_video_refresh_btn = gr.Button("refresh final video", elem_id="final_video_refresh_btn", visible=False)
                            final_video_preview_payload = gr.Textbox(elem_id="final_video_preview_payload", visible=False)

                        with gr.Accordion("视频问题反馈 → 质检规则", open=False):
                            feedback_part_select = gr.CheckboxGroup(
                                label="选择问题视频 Part",
                                choices=video_part_choices(),
                                value=[],
                            )
                            feedback_refresh_btn = gr.Button("刷新 Part 列表")
                            feedback_issue_types = gr.CheckboxGroup(
                                label="问题类型",
                                choices=ISSUE_TYPES,
                                value=[],
                            )
                            feedback_note = gr.Textbox(
                                label="补充描述",
                                lines=4,
                                placeholder="例如：part_003 里说话人变成了旁边的人，背景里多出一个清晰人物……",
                            )
                            feedback_analyze_btn = gr.Button("提交问题并分析")
                            feedback_analysis = gr.Code(
                                label="LLM 归因与候选质检规则（可编辑）",
                                value="",
                                language="json",
                                lines=14,
                            )
                            feedback_apply_rules_btn = gr.Button("将候选规则加入质检函数")

            with gr.Column(visible=False, elem_id="asset_library_page", elem_classes=["page-panel", "asset-library-tab"]) as asset_library_page:
                with gr.Column(elem_classes=["asset-library-workspace"]):
                    with gr.Row(elem_classes=["asset-library-grid"]):
                        with gr.Column(scale=1, min_width=280, elem_classes=["asset-panel", "asset-upload-panel"]):
                            gr.HTML('<div class="asset-panel-title">选择 / 上传图片</div><div class="asset-panel-subtitle">先选 assets 里的现有素材；只有外部新图片才需要上传。</div>')
                            asset_existing_select = gr.Dropdown(
                                label="现有素材",
                                choices=asset_library_choices(),
                                value=None,
                                elem_classes=["asset-existing-select"],
                            )
                            asset_type = gr.Radio(
                                label="素材类型",
                                choices=[("人物/物品", "character"), ("背景", "background")],
                                value="character",
                                elem_classes=["asset-type-switch"],
                            )
                            asset_upload_file = gr.File(
                                label="上传外部新图片",
                                file_types=["image"],
                                type="filepath",
                                elem_classes=["asset-upload-dropzone"],
                            )
                            asset_id = gr.Textbox(
                                label="图片 ID / 文件名",
                                placeholder="支持中文，如张明_正面或 front；留空则使用原文件名",
                                lines=1,
                                elem_classes=["asset-text-field", "asset-id-field"],
                            )
                            asset_target_dir = gr.Dropdown(
                                label="目标目录",
                                choices=asset_target_dir_choices(),
                                allow_custom_value=True,
                                elem_classes=["asset-path-box"],
                            )
                            asset_target_path = gr.Textbox(
                                label="当前/保存路径",
                                value="",
                                interactive=False,
                                lines=2,
                                elem_classes=["asset-path-box"],
                            )
                        with gr.Column(scale=2, min_width=480, elem_classes=["asset-panel", "asset-preview-panel"]):
                            gr.HTML('<div class="asset-panel-title">素材预览</div><div class="asset-panel-subtitle">这里只显示当前选择的图片。</div>')
                            asset_current_path = gr.Textbox(
                                label="当前素材路径",
                                value="",
                                interactive=False,
                                lines=2,
                                elem_classes=["asset-path-box"],
                            )
                            asset_gallery = gr.Image(
                                label="",
                                value=None,
                                height=560,
                                type="filepath",
                                elem_classes=["asset-preview-gallery"],
                            )
                        with gr.Column(scale=1, min_width=340, elem_classes=["asset-panel", "asset-meta-panel"]):
                            gr.HTML('<div class="asset-panel-title">当前素材描述</div><div class="asset-panel-subtitle">这里编辑的是中间正在预览的素材。</div>')
                            asset_metadata_paste = gr.Textbox(
                                label="粘贴素材信息", lines=4, max_lines=8,
                                placeholder="粘贴显示名、别名、中文标签、描述、适用场景、姿态/用途；支持 Markdown 格式。",
                                elem_classes=["asset-text-field"],
                            )
                            asset_metadata_parse_btn = gr.Button("解析并填入下方字段", elem_classes=["quick-btn"])
                            asset_metadata_parse_status = gr.Markdown("")
                            asset_display_name = gr.Textbox(label="显示名", placeholder="蓝衣小男孩 / 露天足球场", lines=1, elem_classes=["asset-text-field"])
                            asset_aliases = gr.Textbox(label="别名", placeholder="用逗号分隔", lines=2, elem_classes=["asset-text-field"])
                            asset_tags_cn = gr.Textbox(label="中文标签", placeholder="卡通, 小孩, 正面", lines=2, elem_classes=["asset-text-field"])
                            asset_description_cn = gr.Textbox(label="描述", lines=5, max_lines=8, elem_classes=["asset-text-field"])
                            asset_use_for = gr.Textbox(label="适用场景", placeholder="用逗号分隔", lines=2, elem_classes=["asset-text-field"])
                            asset_pose = gr.Textbox(label="姿态/用途", placeholder="front / fullbody / in_car / outdoor scene", lines=1, elem_classes=["asset-text-field"])
                            asset_save_meta_btn = gr.Button("保存当前素材描述", elem_classes=["quick-btn"])
                    asset_library_log = gr.State("")

            with gr.Column(visible=False, elem_id="settings_page", elem_classes=["page-panel", "settings-panel"]) as settings_page:
                with gr.Column(elem_id="settings-panel", elem_classes=["settings-panel-inner"]):
                    gr.HTML(
                        """
                        <div class="dev-drawer-header">
                            <span class="dev-drawer-title">&#9881; 设置</span>
                            
                        </div>
                        <div class="theme-switcher">
                            <div class="theme-switcher-title">
                                <span>Appearance</span>
                                <span class="theme-switcher-caption">Token themes</span>
                            </div>
                            <div class="theme-switcher-grid">
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-linear" value="linear">
                                <label class="theme-choice" for="aicf-theme-linear" data-theme="linear" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('linear')}else{document.documentElement.setAttribute('data-aicf-theme','linear');document.body&&document.body.setAttribute('data-aicf-theme','linear')}">Linear</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-cursor" value="cursor">
                                <label class="theme-choice" for="aicf-theme-cursor" data-theme="cursor" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('cursor')}else{document.documentElement.setAttribute('data-aicf-theme','cursor');document.body&&document.body.setAttribute('data-aicf-theme','cursor')}">Cursor</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-vercel" value="vercel">
                                <label class="theme-choice" for="aicf-theme-vercel" data-theme="vercel" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('vercel')}else{document.documentElement.setAttribute('data-aicf-theme','vercel');document.body&&document.body.setAttribute('data-aicf-theme','vercel')}">Vercel</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-glass" value="glass">
                                <label class="theme-choice" for="aicf-theme-glass" data-theme="glass" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('glass')}else{document.documentElement.setAttribute('data-aicf-theme','glass');document.body&&document.body.setAttribute('data-aicf-theme','glass')}">Glass</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-ocean" value="ocean">
                                <label class="theme-choice" for="aicf-theme-ocean" data-theme="ocean" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('ocean')}else{document.documentElement.setAttribute('data-aicf-theme','ocean');document.body&&document.body.setAttribute('data-aicf-theme','ocean')}">Ocean</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-purple" value="purple">
                                <label class="theme-choice" for="aicf-theme-purple" data-theme="purple" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('purple')}else{document.documentElement.setAttribute('data-aicf-theme','purple');document.body&&document.body.setAttribute('data-aicf-theme','purple')}">Purple AI</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-cyber" value="cyber">
                                <label class="theme-choice" for="aicf-theme-cyber" data-theme="cyber" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('cyber')}else{document.documentElement.setAttribute('data-aicf-theme','cyber');document.body&&document.body.setAttribute('data-aicf-theme','cyber')}">Cyber</label>
                                <input class="theme-choice-input" type="radio" name="aicf-theme-choice" id="aicf-theme-classic" value="classic">
                                <label class="theme-choice" for="aicf-theme-classic" data-theme="classic" onclick="if(window.AICF&&window.AICF.theme){window.AICF.theme.apply('classic')}else{document.documentElement.setAttribute('data-aicf-theme','classic');document.body&&document.body.setAttribute('data-aicf-theme','classic')}">Classic</label>
                            </div>
                        </div>
                        """
                    )
                    settings_subpage = gr.Radio(
                        label="",
                        choices=[("模型配置", "model"), ("工作流配置", "workflow"), ("开发者选项", "developer")],
                        value="model",
                        elem_id="settings_subpage_tabs",
                        elem_classes=["settings-subpage-tabs"],
                    )
                    with gr.Column(visible=True, elem_id="dev-tab1", elem_classes=["dev-tab-content"]) as settings_model_page:
                        deepseek_key_value, runninghub_key_value = secret_key_placeholders()
                        with gr.Column(elem_classes=["kv-settings-form"]):
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">帧率</div>')
                                fps_box = gr.Number(value=FPS, step=1, precision=0)
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">LLM 模型</div>')
                                deepseek_model_box = gr.Dropdown(
                                    choices=["deepseek_v4-flash", "deepseek-v4-pro"],
                                    value=CONFIG.get("deepseek_model", "deepseek_v4-flash"),
                                    allow_custom_value=True,
                                )
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">LLM API Key</div>')
                                deepseek_api_key_box = gr.Textbox(
                                    value=deepseek_key_value,
                                    placeholder="输入 LLM API Key",
                                    type="password",
                                    lines=1,
                                )
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">输出目录</div>')
                                output_dir = gr.Textbox(value=str(get_project_dir()), lines=1)
                        submit_backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
                        comfy_backend = submit_backends.get("comfyui") if isinstance(submit_backends, dict) else {}
                        runninghub_backend = submit_backends.get("runninghub") if isinstance(submit_backends, dict) else {}
                        with gr.Column(elem_classes=["kv-settings-form"]):
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">ComfyUI URL</div>')
                                comfyui_backend_url_box = gr.Textbox(
                                    value=(comfy_backend or {}).get("url", CONFIG.get("comfyui_url", "")),
                                    lines=1,
                                )
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">RunningHub API Key</div>')
                                runninghub_api_key_box = gr.Textbox(
                                    value=runninghub_key_value,
                                    placeholder="输入 RunningHub API Key",
                                    type="password",
                                    lines=1,
                                )
                            with gr.Row():
                                save_service_keys_btn = gr.Button("保存模型配置", elem_classes=["quick-btn", "quick-blue"])

                    with gr.Column(visible=False, elem_id="dev-tab2", elem_classes=["dev-tab-content"]) as settings_workflow_page:
                        with gr.Column(elem_classes=["kv-settings-form"]):
                            workflow_mode_box = gr.State("api")
                            workflow_only_initial = bool(ui_config.get("ui_workflow_only"))
                            submit_backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
                            runninghub_backend = submit_backends.get("runninghub") if isinstance(submit_backends, dict) else {}
                            runninghub_concurrency_value = (
                                CONFIG.get("video_generation_concurrency_limit")
                                or (runninghub_backend or {}).get("video_generation_concurrency_limit")
                                or 3
                            )
                            model_choices = available_video_models()
                            configured_model_id = current_default_video_model()
                            valid_model_ids = {model_id for _label, model_id in model_choices}
                            if configured_model_id not in valid_model_ids and model_choices:
                                configured_model_id = model_choices[0][1]
                            default_video_model_box = gr.Dropdown(
                                choices=model_choices,
                                value=configured_model_id,
                                label="默认视频模型",
                                info="当前项目的新分段将使用此模型；每段也可在分段卡片中单独覆盖。",
                                interactive=len(model_choices) > 1,
                            )
                            workflow_settings = workflow_model_settings(configured_model_id)
                            with gr.Row(elem_classes=["kv-row"]):
                                workflow_template_label = gr.HTML(str(workflow_settings["template_label_html"]))
                                model_workflow_template_box = gr.Textbox(
                                    value=str(workflow_settings["template_path"]),
                                    lines=1,
                                    interactive=True,
                                    show_label=False,
                                )
                            with gr.Row(elem_classes=["kv-row"]):
                                gr.HTML('<div class="kv-key">工作流能力</div>')
                                workflow_description_box = gr.Textbox(
                                    value=str(workflow_settings["description"]),
                                    interactive=False,
                                    show_label=False,
                                )
                            with gr.Row(
                                elem_classes=["kv-row"],
                                visible=bool(workflow_settings["runninghub_visible"]),
                            ) as runninghub_plus_row:
                                gr.HTML('<div class="kv-key">RunningHub Plus</div>')
                                runninghub_plus_box = gr.Checkbox(
                                    label="使用 48G Plus 实例",
                                    value=bool((runninghub_backend or {}).get("use_plus_instance")),
                                    show_label=False,
                                )
                            with gr.Row(
                                elem_classes=["kv-row"],
                                visible=bool(workflow_settings["runninghub_visible"]),
                            ) as runninghub_concurrency_row:
                                gr.HTML('<div class="kv-key">RunningHub 并发</div>')
                                runninghub_concurrency_box = gr.Number(
                                    value=runninghub_concurrency_value,
                                    minimum=1,
                                    maximum=8,
                                    step=1,
                                    precision=0,
                                    show_label=False,
                                )
                            with gr.Row():
                                save_workflow_config_btn = gr.Button("保存工作流配置", elem_classes=["quick-btn", "quick-blue"])
                            workflow_config_status = gr.HTML("", elem_classes=["inline-save-status"])

                    with gr.Column(visible=False, elem_id="dev-tab3", elem_classes=["dev-tab-content"]) as settings_developer_page:
                        dry_run_box = gr.State(False)
                        workflow_only_box = gr.Checkbox(label="仅生成 Workflow", value=workflow_only_initial)
                        default_backend = str(ui_config.get("ui_submit_backend") or CONFIG.get("video_submit_backend") or "comfyui").strip().lower()
                        submit_comfyui_box = gr.Checkbox(label="提交到 ComfyUI", value=default_backend == "comfyui", interactive=not workflow_only_initial)
                        submit_runninghub_box = gr.Checkbox(label="提交到 RunningHub", value=default_backend == "runninghub", interactive=not workflow_only_initial)
                        no_concat_box = gr.Checkbox(label="生成后不自动合并", value=bool(ui_config.get("ui_no_concat", True)))
                        minimal_mode_box = gr.Checkbox(label="极简模式", value=bool(ui_config.get("ui_minimal_mode", False)), elem_id="minimal_mode_box")
                        auto_cleanup_box = gr.Checkbox(label="自动清理临时目录", value=bool(ui_config.get("ui_auto_cleanup", True)), visible=False)
                        one_click_resume_mode = gr.State("restart")
                        group_page_state = gr.State(0)
                        segment_ok_filter_state = gr.State(False)
                        guide_refs_gallery = gr.Gallery(label="当前参考图", value=[], visible=False)
                        guides_json = gr.Code(label="legacy guides removed", value="", visible=False)
                        groups_json = gr.State("")

                        if not brand_ok:
                            for locked_component in (
                                project_name,
                                project_episode,
                                project_rename_text,
                                rename_project_btn,
                                archive_episode_name,
                                archive_generation_btn,
                                clean_btn,
                                bible_character_select,
                                bible_background_select,
                                manual_bible_btn,
                                topic,
                                duration_select,
                                custom_duration_box,
                                target_beat_count_box,
                                novel_text,
                                novel_segment_count,
                                generate_novel_segments_btn,
                                gen_story_btn,
                                split_beats_btn,
                                generate_segment_prompts_btn,
                                render_video_btn,
                                merge_video_btn,
                                one_click_full_btn,
                                story_json,
                                beats_json,
                                save_beats_btn,
                                detect_beat_risk_btn,
                                complex_beat_index,
                                split_complex_beat_btn,
                                add_beat_btn,
                                delete_beat_btn,
                                copy_beat_source,
                                copy_beat_target,
                                copy_beat_btn,
                                ignore_beat_risk_box,
                                workflow_only_box,
                                submit_comfyui_box,
                                submit_runninghub_box,
                                no_concat_box,
                                segment_regen_payload,
                                segment_regen_btn,
                                segment_video_regen_payload,
                                segment_video_regen_btn,
                                segment_video_ok_payload,
                                segment_video_ok_btn,
                                segment_prompt_save_payload,
                                segment_prompt_save_btn,
                                segment_prompt_batch_payload,
                                episode_select_payload,
                                episode_select_btn,
                                open_videos_dir_btn,
                                feedback_part_select,
                                feedback_refresh_btn,
                                feedback_issue_types,
                                feedback_note,
                                feedback_analyze_btn,
                                feedback_apply_rules_btn,
                                asset_existing_select,
                                asset_type,
                                asset_upload_file,
                                asset_id,
                                asset_target_dir,
                                asset_target_path,
                                asset_current_path,
                                asset_metadata_paste,
                                asset_metadata_parse_btn,
                                asset_display_name,
                                asset_aliases,
                                asset_tags_cn,
                                asset_description_cn,
                                asset_use_for,
                                asset_pose,
                                asset_save_meta_btn,
                            ):
                                try:
                                    locked_component.interactive = False
                                except Exception:
                                    pass
    return demo, locals()
