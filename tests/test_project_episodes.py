import json
import pytest
from pathlib import Path

from ui import project_handlers


def test_novel_source_round_trip_preserves_verbatim_text(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "outputs" / "project"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: runtime_dir)
    monkeypatch.setattr(project_handlers, "assert_current_runtime_writable", lambda action="": None)
    monkeypatch.setattr(project_handlers, "ensure_project_dirs", lambda: runtime_dir.mkdir(parents=True, exist_ok=True))
    novel = "  第一段。\n\n第二段，保留原始换行。  "

    project_handlers.save_novel_source_text(novel)

    assert project_handlers.load_novel_source_text() == novel
    payload = json.loads((runtime_dir / "novel_source.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "novel_source.v1"
    assert payload["source"] == "generate_novel_segments"


def test_novel_submission_is_saved_before_downstream_generation_failure(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "outputs" / "project"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: runtime_dir)
    monkeypatch.setattr(project_handlers, "assert_current_runtime_writable", lambda action="": None)
    monkeypatch.setattr(project_handlers, "ensure_project_dirs", lambda: runtime_dir.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(
        project_handlers,
        "_save_duration_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("duration failure")),
    )

    try:
        project_handlers.generate_novel_segments_with_duration("失败也不能丢的小说", "60s", 60, 4)
    except RuntimeError as exc:
        assert str(exc) == "duration failure"
    else:
        raise AssertionError("expected downstream generation failure")

    assert project_handlers.load_novel_source_text() == "失败也不能丢的小说"


def test_archived_novel_source_is_loaded_but_not_editable(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "episodes" / "project" / "第001集"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "novel_source.json").write_text(
        json.dumps({"schema": "novel_source.v1", "text": "第一集小说"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: runtime_dir)
    monkeypatch.setattr(project_handlers, "get_current_episode_name", lambda: "第001集")

    update = project_handlers.novel_source_ui_update()

    assert update["value"] == "第一集小说"
    assert update["interactive"] is False


def test_archiving_current_generation_copies_novel_source(tmp_path, monkeypatch):
    outputs_root = tmp_path / "outputs"
    episodes_root = tmp_path / "episodes"
    projects_root = tmp_path / "projects"
    source = outputs_root / "project"
    source.mkdir(parents=True)
    projects_root.mkdir(parents=True)
    expected = {"schema": "novel_source.v1", "text": "随集数归档的小说"}
    (source / "novel_source.json").write_text(
        json.dumps(expected, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_handlers, "OUTPUT_DIR", outputs_root)
    monkeypatch.setattr(project_handlers, "EPISODES_DIR", episodes_root)
    monkeypatch.setattr(project_handlers, "PROJECTS_DIR", projects_root)
    monkeypatch.setattr(project_handlers, "get_current_project_name", lambda: "project")
    monkeypatch.setattr(project_handlers, "assert_current_runtime_writable", lambda action="": None)
    monkeypatch.setattr(project_handlers, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_handlers, "get_logs", lambda: "")

    project_handlers.archive_current_generation("第001集")

    archived = episodes_root / "project" / "第001集" / "novel_source.json"
    assert json.loads(archived.read_text(encoding="utf-8")) == expected


def test_archive_video_jobs_rebases_segment_media_but_keeps_shared_assets(tmp_path):
    source = tmp_path / "outputs" / "project"
    archive = tmp_path / "episodes" / "project" / "第001集"
    upload = source / "media" / "h3" / "segment_001" / "voice.mp3"
    shared = tmp_path / "assets" / "character.png"
    upload.parent.mkdir(parents=True)
    archive.mkdir(parents=True)
    shared.parent.mkdir(parents=True)
    upload.write_bytes(b"audio")
    shared.write_bytes(b"image")
    archived_upload = archive / upload.relative_to(source)
    archived_upload.parent.mkdir(parents=True)
    archived_upload.write_bytes(upload.read_bytes())
    (archive / "video_jobs.json").write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "generation_config": {
                            "asset_manifest": [
                                {"id": "voice", "type": "audio", "path": str(upload)},
                                {"id": "person", "type": "image", "path": str(shared)},
                            ]
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    project_handlers._make_archive_video_jobs_portable(archive, source)

    payload = json.loads((archive / "video_jobs.json").read_text(encoding="utf-8"))
    manifest = payload["segments"][0]["generation_config"]["asset_manifest"]
    assert Path(manifest[0]["path"]) == archived_upload.resolve()
    assert Path(manifest[1]["path"]) == shared.resolve()


def test_episode_choices_include_every_archive_with_archive_names(tmp_path, monkeypatch):
    episodes_root = tmp_path / "episodes"
    project_root = episodes_root / "功夫小女足"
    for name in ("第003集", "第001集", "第004集", "第002集"):
        (project_root / name).mkdir(parents=True)
    monkeypatch.setattr(project_handlers, "EPISODES_DIR", episodes_root)

    assert project_handlers.list_project_episodes("功夫小女足") == [
        ("当前生成", ""),
        ("第001集", "第001集"),
        ("第002集", "第002集"),
        ("第003集", "第003集"),
        ("第004集", "第004集"),
    ]


def test_project_switch_payload_includes_the_selected_projects_topic(monkeypatch):
    monkeypatch.setattr(project_handlers, "set_current_project_name", lambda name: None)
    monkeypatch.setattr(project_handlers, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        project_handlers,
        "_runtime_ui_state",
        lambda: ("F:/project", "story", "beats", "jobs", None, None, None, []),
    )
    monkeypatch.setattr(project_handlers, "load_ui_run_config", lambda: {"topic": "selected project topic"})
    monkeypatch.setattr(project_handlers, "_episode_choices", lambda project_name=None: [("current", "")])

    result = project_handlers.set_current_project("selected-project")

    assert result[-1] == "selected project topic"


def test_project_switch_saves_outgoing_topic_before_loading_selected_topic(monkeypatch):
    active_project = {"name": "project-a"}
    saved_topics = {"project-a": "old topic", "project-b": "project b topic"}

    def save_outgoing(**updates):
        saved_topics[active_project["name"]] = updates["topic"]

    def activate(name):
        active_project["name"] = name

    monkeypatch.setattr(project_handlers, "get_current_episode_name", lambda: "")
    monkeypatch.setattr(project_handlers, "update_ui_run_config", save_outgoing)
    monkeypatch.setattr(project_handlers, "set_current_project_name", activate)
    monkeypatch.setattr(project_handlers, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        project_handlers,
        "_runtime_ui_state",
        lambda: ("F:/project", "story", "beats", "jobs", None, None, None, []),
    )
    monkeypatch.setattr(
        project_handlers,
        "load_ui_run_config",
        lambda: {"topic": saved_topics[active_project["name"]]},
    )
    monkeypatch.setattr(project_handlers, "_episode_choices", lambda project_name=None: [("current", "")])

    result = project_handlers.set_current_project("project-b", "edited project a topic")

    assert saved_topics["project-a"] == "edited project a topic"
    assert result[-1] == "project b topic"


def test_archived_episode_topic_comes_from_its_snapshot(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "episode"
    snapshot_dir = runtime_dir / "project_meta"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "ui_run_config.json").write_text(
        '{"topic": "archived episode topic"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(project_handlers, "get_current_episode_name", lambda: "episode-001")
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: runtime_dir)
    monkeypatch.setattr(project_handlers, "load_ui_run_config", lambda: {"topic": "current project topic"})

    assert project_handlers._runtime_topic_value() == "archived episode topic"


@pytest.mark.parametrize("episode", ["", "episode-001"])
def test_empty_topic_is_not_replaced_by_story(tmp_path, monkeypatch, episode):
    monkeypatch.setattr(project_handlers, "get_current_episode_name", lambda: episode)
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr(project_handlers, "load_ui_run_config", lambda: {"topic": ""})
    story = '{"title":"generated title","summary":"generated summary"}'
    (tmp_path / "story.json").write_text(story, encoding="utf-8")
    assert project_handlers._runtime_topic_value(story) == ""
    assert project_handlers._runtime_topic_value() == ""


def test_generation_persists_topic_even_if_model_fails(tmp_path, monkeypatch):
    from services import ui_run_config
    monkeypatch.setattr(ui_run_config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ui_run_config, "get_current_project_name", lambda: "topic-test")
    ui_run_config.update_ui_run_config(topic="old topic")
    monkeypatch.setattr(project_handlers, "assert_current_runtime_writable", lambda *_: None)
    monkeypatch.setattr(project_handlers, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr(project_handlers, "get_current_episode_name", lambda: "")
    monkeypatch.setattr(project_handlers, "_save_duration_state", lambda *_: 60)
    monkeypatch.setattr(project_handlers, "current_default_video_model", lambda: "test")
    monkeypatch.setattr(project_handlers, "clear_quality_issues", lambda: None)

    def fail_model(topic, **kwargs):
        assert topic == "new topic"
        assert ui_run_config.load_ui_run_config()["topic"] == topic
        raise RuntimeError("model timeout")

    monkeypatch.setattr(project_handlers, "generate_story", fail_model)
    with pytest.raises(RuntimeError, match="model timeout"):
        project_handlers.generate_story_with_duration(" new topic ", "60", None)
    assert project_handlers._runtime_topic_value() == "new topic"
