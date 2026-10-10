import copy
import json

from ui import project_handlers


LTX_MODEL_ID = "ltx23_licon_msr_v2"
H3_MODEL_ID = "minimax_h3_local_ref2va"


def test_workflow_settings_follow_the_selected_model():
    ltx = project_handlers.workflow_model_settings(LTX_MODEL_ID)
    h3 = project_handlers.workflow_model_settings(H3_MODEL_ID)

    assert ltx["model_id"] == LTX_MODEL_ID
    assert str(ltx["template_path"]).endswith("Ltx2.3 Licon-MSRV2-bfs_api.json")
    assert ltx["runninghub_visible"] is True
    assert "LiconMSR" in str(ltx["description"])

    assert h3["model_id"] == H3_MODEL_ID
    configured_h3_template = project_handlers.CONFIG["models"][H3_MODEL_ID]["template_api_path"]
    assert str(h3["template_path"]).replace("\\", "/").endswith(str(configured_h3_template).replace("\\", "/"))
    assert h3["runninghub_visible"] is True
    assert "9 张图片" in str(h3["description"])


def test_saving_h3_workflow_updates_only_the_h3_model_config(tmp_path, monkeypatch):
    config = copy.deepcopy(project_handlers.CONFIG)
    config["template_api_path"] = "templates/original-ltx.json"
    config["models"][LTX_MODEL_ID]["template_api_path"] = "templates/original-ltx.json"
    h3_template = tmp_path / "h3-api.json"
    h3_template.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"

    monkeypatch.setattr(project_handlers, "CONFIG", config)
    monkeypatch.setattr(project_handlers, "CONFIG_PATH", config_path)
    refreshed = []
    monkeypatch.setattr("generation.registry.refresh_model_pipeline", lambda model_id: refreshed.append(model_id))

    message = project_handlers.save_workflow_config(H3_MODEL_ID, str(h3_template), False, 3)

    assert "MiniMax H3" in message
    assert config["models"][H3_MODEL_ID]["template_api_path"] == "h3-api.json"
    assert config["models"][LTX_MODEL_ID]["template_api_path"] == "templates/original-ltx.json"
    assert config["template_api_path"] == "templates/original-ltx.json"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["models"][H3_MODEL_ID]["template_api_path"] == "h3-api.json"
    assert refreshed == [H3_MODEL_ID]


def test_registry_refresh_replaces_only_one_pipeline_for_existing_services():
    from generation.registry import ModelRegistry

    class Spec:
        def __init__(self, model_id):
            self.id = model_id

    class Pipeline:
        def __init__(self, model_id, template):
            self.spec = Spec(model_id)
            self.template = template

    selected_template = {"value": "old.json"}
    registry = ModelRegistry()
    old_h3 = Pipeline(H3_MODEL_ID, selected_template["value"])
    ltx = Pipeline(LTX_MODEL_ID, "ltx.json")
    registry.register(old_h3, factory=lambda: Pipeline(H3_MODEL_ID, selected_template["value"]))
    registry.register(ltx, factory=lambda: Pipeline(LTX_MODEL_ID, "ltx.json"))

    selected_template["value"] = "new.json"
    refreshed = registry.refresh(H3_MODEL_ID)

    assert refreshed.template == "new.json"
    assert registry.get(H3_MODEL_ID) is refreshed
    assert registry.get(H3_MODEL_ID) is not old_h3
    assert registry.get(LTX_MODEL_ID) is ltx
