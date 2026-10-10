from ui import project_handlers


H3_MODEL_ID = "minimax_h3_local_ref2va"
LTX_MODEL_ID = "ltx23_licon_msr_v2"


def test_current_default_video_model_restores_persisted_project_choice(monkeypatch):
    monkeypatch.setattr(
        project_handlers,
        "load_ui_run_config",
        lambda: {"default_video_model_id": H3_MODEL_ID},
    )

    assert project_handlers.current_default_video_model() == H3_MODEL_ID


def test_current_default_video_model_falls_back_only_for_invalid_choice(monkeypatch):
    monkeypatch.setattr(
        project_handlers,
        "load_ui_run_config",
        lambda: {"default_video_model_id": "removed-model"},
    )

    assert project_handlers.current_default_video_model() == H3_MODEL_ID
