import inspect
import json

from ui import group_panel
from ui.scripts import APP_JS


def test_workflow_prompt_text_reads_exact_node_5_content(tmp_path, monkeypatch):
    workflow_path = tmp_path / "LTX-2_MSR_segments_part_001_api.json"
    workflow_prompt = "【人物参考图身份描述】\n角色 A\n\n导演正文"
    workflow_path.write_text(
        json.dumps({"5": {"inputs": {"text": workflow_prompt}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setitem(group_panel.CONFIG, "licon_prompt_node", "5")

    assert group_panel._workflow_prompt_text(str(workflow_path), "fallback") == workflow_prompt


def test_workflow_prompt_text_falls_back_when_workflow_is_unavailable(tmp_path):
    missing_path = tmp_path / "missing.json"

    assert group_panel._workflow_prompt_text(str(missing_path), "fallback prompt") == "fallback prompt"


def test_workflow_prompt_prefix_preserves_existing_compiled_prompt():
    director_prompt = "镜头从人物正面缓慢推近。"
    compiled_prompt = "【人物参考图身份描述】\n角色 A\n\n" + director_prompt

    assert group_panel._workflow_prompt_prefix(compiled_prompt, director_prompt, "new prefix") == (
        "【人物参考图身份描述】\n角色 A"
    )


def test_prompt_modal_uses_same_director_source_as_card_and_live_reference_preview():
    assert "workflow-prompt-source" in APP_JS
    assert "/api/segment/preview_prompt" in APP_JS
    assert "workflowPrompt: directorPrompt || ''" in APP_JS
    assert "导演分镜全屏编辑" in APP_JS
    assert "仅在此处编辑本段提示词" in APP_JS


def test_episode_switch_invalidates_prompt_editors_and_closes_stale_modal():
    from ui.bindings import bind_events

    source = inspect.getsource(bind_events)
    assert "promptRuntimeRevision" in source
    assert "closePromptModal" in source


def test_initial_page_restore_maps_every_return_value_to_an_output():
    from ui.bindings import _restore_direct_state, bind_events

    assert len(_restore_direct_state()) == 25
    source = inspect.getsource(bind_events)
    load_outputs = source.split("demo.load(", 1)[1].split(")\n\n    return demo", 1)[0]
    assert "default_video_model_box" in load_outputs


def test_h3_card_keeps_prompt_actions_and_original_but_hides_prompt_editor():
    html = group_panel._h3_config_html(
        {
            "segment_index": 2,
            "job": {"final_prompt": "H3 compiled prompt"},
            "generation_config": {
                "asset_manifest": [
                    {"id": "image-1", "type": "image", "path": "image.png", "label": "image"},
                    {"id": "video-1", "type": "video", "path": "video.mp4", "label": "video"},
                ]
            },
        }
    )

    assert "H3 compiled prompt</pre>" in html
    assert 'class="h3-prompt-open"' in html
    assert "复制提示词" in html
    assert "复制原文" in html
    assert "剧情上下文 / 原文参考" in html
    assert "segment-director-editor-2" not in html
    assert "保存本段提示词" not in html
    assert "h3-prompt-save" not in html
    assert "promptSource = !promptField ? root.querySelector('.h3-card-prompt-source') : null" in APP_JS
    assert "return card && card.dataset ? String(card.dataset.segmentIndex || '') : ''" in APP_JS
    assert "h3-fullscreen-config-template" in html
    assert "h3-card-config-source" in html
    assert '<details class="h3-config-block"' not in html
    assert "aicf-h3-config-panel" in APP_JS
    assert "h3-project-image-picker" in html
    assert "场景图片（可选）" in html
    assert "背景图片可不选" in html
    assert 'data-media-type="video"' in html
    assert 'data-media-type="audio"' in html
    assert "renderH3ProjectImages" in APP_JS
    assert "source: 'project'" in APP_JS
    assert "asset_kind: 'character'" in APP_JS
    assert "asset_kind: 'background'" in APP_JS
    assert "subject_description" in APP_JS
    assert "scheduleH3ConfigAutosave" in APP_JS
    assert "h3AutosavePending" in APP_JS
    assert "archive_generation_btn" in APP_JS


def test_h3_source_excerpt_is_restored_from_authoritative_non_form_text():
    html = group_panel._h3_config_html(
        {
            "segment_index": 10,
            "source_excerpt": "吴天刚想说什么，视线被窗外吸住了。",
            "job": {"final_prompt": "【主体定义】\n提示词正文"},
            "generation_config": {},
        }
    )

    assert 'class="segment-source-excerpt prompt-full" data-segment-index="10"' in html
    assert '<pre class="segment-source-excerpt-source" hidden>吴天刚想说什么，视线被窗外吸住了。</pre>' in html
    assert "window.AICF.syncSegmentSourceExcerpts = function()" in APP_JS
    assert "textarea.value = sourceText" in APP_JS
    assert "window.AICF.syncSegmentSourceExcerpts();" in APP_JS


def test_h3_fullscreen_receives_project_material_choices_without_ltx_bindings():
    material_payload = json.dumps(
        {
            "characters": [{"id": "role-1", "images": [{"id": "image-1", "image_path": "role.png"}]}],
            "backgrounds": [{"id": "scene-1", "image_path": "scene.png"}],
        },
        ensure_ascii=False,
    )
    html = group_panel._h3_config_html(
        {"segment_index": 2, "job": {"final_prompt": "H3 prompt"}},
        material_payload,
    )

    assert "segment-material-source" in html
    assert "role-1" in html
    assert "scene-1" in html
    assert "reference_image_ids" not in html
    assert "scene_id" not in html


def test_h3_fullscreen_autosave_is_locked_to_the_opened_segment_session():
    open_modal = APP_JS.split("window.AICF.openPromptModalFromButton = function(button)", 1)[1].split(
        "window.AICF.savePromptModal = async function", 1
    )[0]
    autosave = APP_JS.split("function _scheduleH3ConfigAutosave(root)", 1)[1].split(
        "window.AICF.scheduleH3ConfigAutosave", 1
    )[0]
    save_config = APP_JS.split("window.AICF.saveH3GenerationConfig = async function", 1)[1].split(
        "window.AICF.regenerateH3Segment", 1
    )[0]
    save_modal = APP_JS.split("window.AICF.savePromptModal = async function", 1)[1].split(
        "window.AICF.closePromptModal", 1
    )[0]

    assert "window.clearTimeout(ov._h3AutosaveTimer)" in open_modal
    assert "ov.dataset.h3Session" in open_modal
    assert "saveRoot.dataset.h3Session" in autosave
    assert "saveRoot.dataset.segmentIndex" in autosave
    assert "rootIndex !== requestedIndex" in save_config
    assert "rootStillMatches" in save_config
    assert "prompt_override: submittedPrompt" in save_config
    assert "modalSession" in save_modal
    assert "ov.dataset.segmentIndex" in save_modal


def test_retained_modal_button_cannot_select_an_editor_from_another_segment():
    editor_lookup = APP_JS.split("window.AICF.segmentEditorFromButton = function", 1)[1].split(
        "window.AICF.promptSourceTextForEditor", 1
    )[0]

    assert "Never return that card's editor for a different index" in editor_lookup
    assert "if (!segmentIndex) return localTextarea" in editor_lookup


def test_h3_selected_images_have_accessible_up_down_order_controls():
    assert "window.AICF.moveH3ImageAsset" in APP_JS
    assert "h3-preview-move-group" in APP_JS
    assert "上移 " in APP_JS
    assert "下移 " in APP_JS
    assert "item.order = index + 1" in APP_JS
    assert "图片已上移，正在同步提示词并自动保存。" in APP_JS


def test_segment_model_switch_saves_without_a_confirmation_dialog():
    change_model_source = APP_JS.split("window.AICF.changeSegmentModel = function(select)", 1)[1].split(
        "window.AICF.removeH3Asset", 1
    )[0]

    assert "window.confirm" not in change_model_source
    assert "正在切换模型" in change_model_source
    assert "window.location.reload" not in change_model_source
    assert "segment_panel_html" in change_model_source
    assert "card.replaceWith(replacement)" in change_model_source


def test_render_segment_panel_returns_only_the_changed_card():
    html = group_panel.render_segment_panel(
        {
            "default_model_id": "ltx23_licon_msr_v2",
            "segments": [
                {
                    "segment_index": 1,
                    "part_id": "part_001",
                    "title": "First",
                    "model_id": "ltx23_licon_msr_v2",
                    "job": {"duration_sec": 5, "prompt": "First prompt"},
                },
                {
                    "segment_index": 2,
                    "part_id": "part_002",
                    "title": "Second",
                    "model_id": "minimax_h3_local_ref2va",
                    "job": {"duration_sec": 5, "prompt": "Second prompt"},
                },
            ],
        },
        2,
    )

    assert 'data-segment-index="2"' in html
    assert 'data-segment-index="1"' not in html
    assert 'data-model-id="minimax_h3_local_ref2va"' in html
    assert "H3 视频提示词" in html


def test_regenerate_and_h3_fullscreen_save_preserve_page_state():
    ltx_regenerate = APP_JS.split("window.AICF.triggerSegmentVideoRegen = async function", 1)[1].split(
        "window.AICF.triggerSegmentVideoRegenViaGradio = function", 1
    )[0]
    h3_regenerate = APP_JS.split("window.AICF.regenerateH3Segment = async function", 1)[1].split(
        "window.__AICF_READY__", 1
    )[0]
    fullscreen_save = APP_JS.split("window.AICF.savePromptModal = async function", 1)[1].split(
        "window.AICF.closePromptModal = function", 1
    )[0]

    assert "window.location.reload" not in ltx_regenerate
    assert "window.location.reload" not in h3_regenerate
    assert "window.location.reload" not in fullscreen_save
    assert "replaceSegmentCardFromResponse" in ltx_regenerate
    assert "replaceSegmentCardFromResponse" in h3_regenerate
    assert "closePromptModal" in fullscreen_save


def test_running_segment_card_exposes_local_update_targets_and_disables_duplicate_submit():
    html = group_panel.render_segment_panel(
        {
            "default_model_id": "minimax_h3_local_ref2va",
            "segments": [
                {
                    "segment_index": 3,
                    "part_id": "part_003",
                    "model_id": "minimax_h3_local_ref2va",
                    "status": "running",
                    "task_id": "task-123",
                    "backend": "runninghub",
                    "job": {"duration_sec": 5, "prompt": "H3 prompt"},
                }
            ],
        },
        3,
    )

    assert "data-segment-status" in html
    assert "data-segment-backend" in html
    assert "data-segment-task" in html
    assert 'disabled aria-disabled="true"' in html
    assert "task-123" in html


def test_h3_segment_card_exposes_regenerate_prompt_action():
    html = group_panel.render_segment_panel(
        {
            "default_model_id": "minimax_h3_local_ref2va",
            "segments": [
                {
                    "segment_index": 1,
                    "part_id": "part_001",
                    "model_id": "minimax_h3_local_ref2va",
                    "job": {"duration_sec": 5, "prompt": "H3 prompt"},
                }
            ],
        },
        1,
    )

    assert "重新生成提示词" in html
    assert "window.AICF.triggerSegmentRegen('1', this)" in html
