import json
from pathlib import Path

import gradio as gr
import pytest

from test_asset_metadata_paste import setup_assets
from ui import asset_library_panel as panel


FIELDS = ("测试人物", "", "", "完整描述", "", "正面")


@pytest.mark.parametrize("selection", [None, "", "character::missing::missing"])
def test_empty_selection_then_upload_passes_dropdown_validation(monkeypatch, tmp_path, selection):
    source = setup_assets(monkeypatch, tmp_path)
    dropdown = gr.Dropdown(choices=panel.asset_target_dir_choices(), value=panel.NEW_ASSET)
    cleared = panel.load_existing_asset_selection(selection)
    destination = dropdown.preprocess(cleared[2])
    assert destination == panel.NEW_ASSET
    uploaded = panel.preview_selected_asset_for_upload("character", str(source), "", destination)
    assert dropdown.preprocess(uploaded[2]) == panel.NEW_ASSET
    assert uploaded[10] == str(source)


@pytest.mark.parametrize("kind", ["character", "background"])
def test_clear_upload_and_failed_preview_keep_valid_destination(monkeypatch, tmp_path, kind):
    source = setup_assets(monkeypatch, tmp_path)
    dropdown = gr.Dropdown(choices=panel.asset_target_dir_choices(), value=panel.NEW_ASSET)
    cleared = panel.preview_selected_asset_for_upload(kind, None, "", "")
    assert dropdown.preprocess(cleared[2])
    failed = panel.preview_selected_asset(kind, str(source), "bad/id", "")
    assert dropdown.preprocess(failed[2]) == panel.NEW_ASSET


def test_new_asset_created_only_on_save(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    preview = panel.preview_selected_asset_for_upload("character", str(source), "old", "old")
    assert preview[1] == ""
    assert preview[2] == panel.NEW_ASSET
    assert not panel.ASSETS_DIR.exists()
    result = panel.save_selected_asset_metadata(str(source), "character", "", panel.NEW_ASSET, *FIELDS)
    image = Path(result[1])
    assert image.parent.parent == panel.ASSETS_DIR / "characters/identities"
    assert image.name.startswith("image_")
    assert image.read_bytes() == source.read_bytes()
    assert json.loads((image.parent / "asset_meta.json").read_text(encoding="utf-8"))["identity"]["display_name"] == FIELDS[0]
    assert any(FIELDS[0] in label for label, value in panel.asset_target_dir_choices())


def test_add_reference_and_duplicate_name_do_not_merge(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    first = Path(panel.save_selected_asset_metadata(str(source), "character", "", panel.NEW_ASSET, *FIELDS)[1])
    second = Path(panel.save_selected_asset_metadata(str(source), "character", "", str(first.parent), *FIELDS)[1])
    assert first.parent == second.parent
    assert first != second
    third = Path(panel.save_selected_asset_metadata(str(source), "character", "", panel.NEW_ASSET, *FIELDS)[1])
    assert third.parent != first.parent
    # Matching a filename must not turn an external upload into an existing asset.
    external = tmp_path / first.name
    external.write_bytes(b"different")
    assert panel._managed_asset_path_for_selection(external) == external


def test_background_ignores_previous_character_destination(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    result = panel.save_selected_asset_metadata(str(source), "background", "", "old-character", *FIELDS)
    assert Path(result[1]).parent == panel.ASSETS_DIR / "backgrounds"


def test_existing_image_keeps_path_when_renamed(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    first = panel.save_selected_asset_metadata(str(source), "character", "", panel.NEW_ASSET, *FIELDS)
    second = panel.save_selected_asset_metadata(first[1], "character", "", panel.NEW_ASSET, "改名", *FIELDS[1:])
    assert first[1] == second[1]
