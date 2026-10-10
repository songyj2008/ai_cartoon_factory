"""Clearing run data must survive Windows video preview locks."""
import pytest

from ui import project_handlers as handlers


def setup_project(monkeypatch, tmp_path):
    output = tmp_path / "outputs"
    project = output / "demo"
    project.mkdir(parents=True)
    for name in ("story.json", "beats.json", "video_jobs.json", "novel_source.json"):
        (project / name).write_text("{}", encoding="utf-8")
    config = {"topic": "old topic"}
    monkeypatch.setattr(handlers, "OUTPUT_DIR", output)
    monkeypatch.setattr(handlers, "get_project_dir", lambda: project)
    monkeypatch.setattr(handlers, "get_current_project_name", lambda: "demo")
    monkeypatch.setattr(handlers, "assert_current_runtime_writable", lambda _: None)
    monkeypatch.setattr(handlers, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(handlers, "update_ui_run_config", lambda **kw: config.update(kw))
    monkeypatch.setattr(handlers, "project_bible_ui_state", lambda: (None, None, []))
    monkeypatch.setattr(handlers, "log", lambda *args: None)
    monkeypatch.setattr(handlers, "get_logs", lambda: "logs")
    return project, config


def test_clear_data_even_when_final_video_is_locked(monkeypatch, tmp_path):
    project, config = setup_project(monkeypatch, tmp_path)
    final = project / "final"
    final.mkdir()
    video = final / "video.mp4"
    video.write_bytes(b"video")
    warnings = []
    monkeypatch.setattr(handlers.gr, "Warning", warnings.append)

    def locked(path):
        assert path == final
        assert not (project / "video_jobs.json").exists()
        raise PermissionError("[WinError 32] video in use")

    monkeypatch.setattr(handlers.shutil, "rmtree", locked)
    result = handlers.clean_project()
    assert result[1:4] == ("", "", "")
    assert config["topic"] == ""
    assert not list(project.glob("*.json"))
    assert video.exists()
    assert len(warnings) == 1 and "final" in warnings[0]


def test_data_deletion_failure_is_reported(monkeypatch, tmp_path):
    project, config = setup_project(monkeypatch, tmp_path)
    original = type(project).unlink

    def locked(path, *args, **kwargs):
        if path.name == "video_jobs.json":
            raise PermissionError("data in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(project), "unlink", locked)
    with pytest.raises(handlers.gr.Error, match="清空当前生成失败"):
        handlers.clean_project()
    assert (project / "beats.json").exists()
    assert config["topic"] == "old topic"
