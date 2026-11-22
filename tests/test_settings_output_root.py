"""Saving service settings must never replace the root with a runtime path."""
import inspect
import json

from ui import project_handlers


def test_save_service_settings_preserves_output_root(tmp_path, monkeypatch):
    config = {"output_dir": "outputs", "fps": 50}
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(project_handlers, "CONFIG", config)
    monkeypatch.setattr(project_handlers, "CONFIG_PATH", config_path)
    monkeypatch.setattr(project_handlers, "secret_key_placeholders", lambda: ("", ""))
    project_handlers.save_service_keys(deepseek_model="test-model", fps_value=50)
    assert json.loads(config_path.read_text(encoding="utf-8"))["output_dir"] == "outputs"
    assert "output_dir_value" not in inspect.signature(project_handlers.save_service_keys).parameters


def test_service_settings_binding_excludes_runtime_directory():
    from pathlib import Path
    import ast

    source = Path(project_handlers.__file__).with_name("bindings.py").read_text(encoding="utf-8-sig")
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
             and any(kw.arg == "fn" and isinstance(kw.value, ast.Name)
                     and kw.value.id == "save_service_keys" for kw in node.keywords)]
    assert len(calls) == 1
    inputs = next(kw.value for kw in calls[0].keywords if kw.arg == "inputs")
    assert [item.id for item in inputs.elts] == [
        "deepseek_api_key_box", "runninghub_api_key_box", "comfyui_backend_url_box",
        "deepseek_model_box", "fps_box",
        "runninghub_key_selection_box", "runninghub_key_alias_box",
    ]


def test_runninghub_page_saves_only_to_private_config(tmp_path, monkeypatch):
    from services import runninghub_keys

    settings = {}
    config_path = tmp_path / "config.json"
    environment = {runninghub_keys.RUNNINGHUB_API_KEYS_ENV: '{"env-account":"test-environment-key"}'}
    monkeypatch.setattr(project_handlers, "CONFIG", settings)
    monkeypatch.setattr(project_handlers, "CONFIG_PATH", config_path)
    monkeypatch.setattr(runninghub_keys, "CONFIG", settings)
    monkeypatch.setattr(runninghub_keys, "secret_value", lambda name, **kwargs: environment.get(name, ""))

    def unexpected_environment_write(*args):
        raise AssertionError("RunningHub settings attempted to write an environment variable")

    monkeypatch.setattr(project_handlers, "set_secret", unexpected_environment_write)
    project_handlers.save_service_keys(runninghub_key="test-page-default")
    result = project_handlers.save_service_keys(runninghub_key="test-page-extra", runninghub_alias="extra")
    assert "test-page-extra" not in repr(result)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved[runninghub_keys.CONFIG_KEYS_FIELD] == {
        "default": "test-page-default", "extra": "test-page-extra",
    }
    assert runninghub_keys.resolve_runninghub_key().alias == "env-account"
    environment.clear()
    project_handlers.save_service_keys(runninghub_selection="extra")
    assert runninghub_keys.resolve_runninghub_key().alias == "extra"
