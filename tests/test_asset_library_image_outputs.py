import gradio as gr

from ui import asset_library_panel


def test_save_selected_asset_failure_returns_empty_single_image(monkeypatch):
    monkeypatch.setattr(
        asset_library_panel,
        "_path_from_text",
        lambda _value: (_ for _ in ()).throw(ValueError("missing asset")),
    )
    monkeypatch.setattr(asset_library_panel, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(asset_library_panel, "get_logs", lambda: "missing asset")
    monkeypatch.setattr(
        asset_library_panel,
        "project_bible_ui_state",
        lambda: (gr.update(), gr.update(), []),
    )

    result = asset_library_panel.save_selected_asset_metadata(
        "", "background", "", "", "", "", "", "", "", ""
    )

    assert result[0] is None
    assert gr.Image(type="filepath").postprocess(result[0]) is None
    assert result[-1] == "missing asset"
