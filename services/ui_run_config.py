"""Persistent UI run configuration stored outside runtime run_state.json."""
import json
import os
import time
from pathlib import Path

from services.context import BASE_DIR, CONFIG, get_current_project_name

UI_RUN_CONFIG_NAME = "ui_run_config.json"
UI_RUN_CONFIG_KEYS = {
    "topic",
    "duration_select",
    "custom_duration_seconds",
    "final_duration_seconds",
    "duration_sec",
    "ui_dry_run",
    "ui_workflow_only",
    "ui_no_concat",
    "ui_minimal_mode",
    "ui_auto_cleanup",
    "ui_workflow_mode",
    "ui_submit_backend",
    "default_video_model_id",
    "beats_stale_for_model",
    "novel_segment_count",
    "target_beat_count",
}

DEFAULT_UI_RUN_CONFIG = {
    "topic": "",
    "duration_select": "",
    "custom_duration_seconds": None,
    "final_duration_seconds": None,
    "duration_sec": None,
    "novel_segment_count": 4,
    "target_beat_count": "",
    "ui_dry_run": False,
    "ui_workflow_only": False,
    "ui_no_concat": True,
    "ui_minimal_mode": False,
    "ui_auto_cleanup": True,
    "ui_workflow_mode": CONFIG.get("output_mode", "api"),
    "ui_submit_backend": CONFIG.get("video_submit_backend", "comfyui"),
}


def _now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def project_name():
    return get_current_project_name() or "current"


def ui_run_config_path(project=None):
    name = str(project or project_name() or "current").strip() or "current"
    return BASE_DIR / "projects" / name / UI_RUN_CONFIG_NAME


def _normalise_config(data):
    config = dict(DEFAULT_UI_RUN_CONFIG)
    if isinstance(data, dict):
        for key in UI_RUN_CONFIG_KEYS:
            if key in data:
                config[key] = data.get(key)
        if data.get("created_at"):
            config["created_at"] = data.get("created_at")
    config.setdefault("created_at", _now_text())
    config["updated_at"] = _now_text()
    config["version"] = 1
    return config


def extract_ui_run_config(data):
    if not isinstance(data, dict):
        return {}
    return {
        key: data.get(key)
        for key in UI_RUN_CONFIG_KEYS
        if key in data
    }


def strip_ui_run_config(data):
    if not isinstance(data, dict):
        return data
    for key in UI_RUN_CONFIG_KEYS:
        data.pop(key, None)
    return data


def load_ui_run_config(project=None):
    path = ui_run_config_path(project)
    if not path.exists():
        return _normalise_config({})
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}
    return _normalise_config(data)


def save_ui_run_config(config, project=None):
    path = ui_run_config_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _normalise_config(config)
    text = json.dumps(data, ensure_ascii=False, indent=2)

    last_error = None
    for _ in range(3):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return data
        except OSError as exc:
            last_error = exc
            time.sleep(0.15)

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
        return data
    except OSError as exc:
        last_error = exc
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        from services.logger import log
        log(f"[ui_config][warn] cannot save {path}: {last_error}", "WARN")
    except Exception:
        pass
    return data


def update_ui_run_config(**updates):
    ui_updates = {
        key: value
        for key, value in (updates or {}).items()
        if key in UI_RUN_CONFIG_KEYS
    }
    if not ui_updates:
        return load_ui_run_config()
    data = load_ui_run_config()
    data.update(ui_updates)
    return save_ui_run_config(data)
