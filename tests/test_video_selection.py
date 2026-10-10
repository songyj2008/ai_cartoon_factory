from generation.render_history import begin_render, update_render
from ui.group_panel import _runninghub_usage_history, _video_path_for_segment
from workflow import segment_runner, video_merge


MODEL_ID = "minimax_h3_local_ref2va"


def _successful_render(segment: dict, task_id: str, video_path: str) -> dict:
    render = begin_render(segment, model_id=MODEL_ID, backend="runninghub", status="running")
    update_render(
        segment,
        render["render_id"],
        {
            "submitted": True,
            "task_id": task_id,
            "video_path": video_path,
            "status": "success",
        },
        status="success",
        activate_on_success=True,
        project=True,
    )
    return render


def test_select_history_video_updates_preview_and_merge_source(monkeypatch, tmp_path):
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    old_video = videos_dir / "part_001_old.mp4"
    new_video = videos_dir / "part_001_new.mp4"
    old_video.write_bytes(b"old")
    new_video.write_bytes(b"new")
    segment = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": MODEL_ID,
        "resolved_model": {"id": MODEL_ID},
        "runninghub_attempts": [
            {"task_id": "task-old", "status": "success", "video_path": str(old_video)},
            {"task_id": "task-new", "status": "success", "video_path": str(new_video)},
        ],
    }
    old_render = _successful_render(segment, "task-old", str(old_video))
    _successful_render(segment, "task-new", str(new_video))
    payload = {"default_model_id": MODEL_ID, "segments": [segment], "final_video_path": "old-final.mp4"}
    saved = {}
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "save_video_jobs", lambda value: saved.setdefault("payload", value))
    monkeypatch.setattr(segment_runner, "get_project_dir", lambda: tmp_path)

    result = segment_runner.select_runninghub_attempt_video(1, "task-old")

    selected = result["segments"][0]
    assert saved["payload"] is result
    assert selected["active_render_id"] == old_render["render_id"]
    assert selected["manual_active_render_id"] == old_render["render_id"]
    assert selected["task_id"] == "task-old"
    assert selected["video_path"] == str(old_video)
    assert "final_video_path" not in result
    assert _video_path_for_segment(selected) == str(old_video.resolve())
    assert video_merge.existing_segment_video(selected) == str(old_video.resolve())
    history_html = _runninghub_usage_history(selected)
    assert "task-old" in history_html
    assert "（当前采用）" in history_html
    assert 'aria-pressed="true"' in history_html


def test_select_history_video_does_not_replace_running_task_identity(monkeypatch, tmp_path):
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    old_video = videos_dir / "part_001_old.mp4"
    old_video.write_bytes(b"old")
    segment = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": MODEL_ID,
        "resolved_model": {"id": MODEL_ID},
        "runninghub_attempts": [
            {"task_id": "task-old", "status": "success", "video_path": str(old_video)},
            {"task_id": "task-running", "status": "running"},
        ],
    }
    old_render = _successful_render(segment, "task-old", str(old_video))
    begin_render(segment, model_id=MODEL_ID, backend="runninghub", status="running")
    segment.update(
        {
            "status": "running",
            "backend": "runninghub",
            "submitted": True,
            "task_id": "task-running",
            "video_path": "",
        }
    )
    payload = {"default_model_id": MODEL_ID, "segments": [segment]}
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "save_video_jobs", lambda value: None)
    monkeypatch.setattr(segment_runner, "get_project_dir", lambda: tmp_path)

    result = segment_runner.select_runninghub_attempt_video(1, "task-old")
    selected = result["segments"][0]

    assert selected["active_render_id"] == old_render["render_id"]
    assert selected["manual_active_render_id"] == old_render["render_id"]
    assert selected["status"] == "running"
    assert selected["task_id"] == "task-running"
    assert selected["video_path"] == ""
    assert _video_path_for_segment(selected) == str(old_video.resolve())
    assert video_merge.existing_segment_video(selected) == str(old_video.resolve())


def test_successful_new_render_overrides_manual_history_selection(tmp_path):
    old_video = tmp_path / "part_001_old.mp4"
    new_video = tmp_path / "part_001_new.mp4"
    old_video.write_bytes(b"old")
    new_video.write_bytes(b"new")
    segment = {"segment_index": 1, "part_id": "part_001"}
    old_render = _successful_render(segment, "task-old", str(old_video))
    segment["manual_active_render_id"] = old_render["render_id"]
    new_render = begin_render(segment, model_id=MODEL_ID, backend="runninghub", status="running")

    update_render(
        segment,
        new_render["render_id"],
        {
            "submitted": True,
            "task_id": "task-new",
            "video_path": str(new_video),
            "status": "success",
        },
        status="success",
        activate_on_success=True,
        project=True,
    )

    assert segment["active_render_id"] == new_render["render_id"]
    assert "manual_active_render_id" not in segment
    assert segment["video_path"] == str(new_video)
    assert _video_path_for_segment(segment) == str(new_video.resolve())


def test_prompt_change_keeps_active_render_as_merge_source(tmp_path):
    video = tmp_path / "part_001_current.mp4"
    video.write_bytes(b"current")
    segment = {"segment_index": 1, "part_id": "part_001"}
    _successful_render(segment, "task-current", str(video))
    segment.update({"status": "needs_regenerate", "video_path": ""})

    assert _video_path_for_segment(segment) == str(video.resolve())
    assert video_merge.existing_segment_video(segment) == str(video.resolve())


def test_select_rejects_deleted_history_video(monkeypatch, tmp_path):
    video = tmp_path / "part_001.mp4"
    video.write_bytes(b"video")
    segment = {
        "segment_index": 1,
        "part_id": "part_001",
        "runninghub_attempts": [
            {"task_id": "task-deleted", "status": "success", "video_path": str(video), "video_deleted": True}
        ],
    }
    _successful_render(segment, "task-deleted", str(video))
    payload = {"default_model_id": MODEL_ID, "segments": [segment]}
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "save_video_jobs", lambda value: None)

    try:
        segment_runner.select_runninghub_attempt_video(1, "task-deleted")
    except ValueError as exc:
        assert "已删除" in str(exc)
    else:
        raise AssertionError("deleted history video must not be selectable")


def test_deleting_selected_video_marks_render_and_falls_back(monkeypatch, tmp_path):
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    first_video = videos_dir / "part_001_first.mp4"
    selected_video = videos_dir / "part_001_selected.mp4"
    first_video.write_bytes(b"first")
    selected_video.write_bytes(b"selected")
    segment = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": MODEL_ID,
        "resolved_model": {"id": MODEL_ID},
        "runninghub_attempts": [
            {"task_id": "task-first", "status": "success", "video_path": str(first_video)},
            {"task_id": "task-selected", "status": "success", "video_path": str(selected_video)},
        ],
    }
    first_render = _successful_render(segment, "task-first", str(first_video))
    selected_render = _successful_render(segment, "task-selected", str(selected_video))
    segment["manual_active_render_id"] = selected_render["render_id"]
    payload = {"default_model_id": MODEL_ID, "segments": [segment]}
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "save_video_jobs", lambda value: None)
    monkeypatch.setattr(segment_runner, "get_project_dir", lambda: tmp_path)

    result = segment_runner.delete_runninghub_attempt_video(1, "task-selected")
    updated = result["segments"][0]
    deleted_output = selected_render["output"]

    assert not selected_video.exists()
    assert deleted_output["video_deleted"] is True
    assert selected_render["status"] == "deleted"
    assert updated["active_render_id"] == first_render["render_id"]
    assert "manual_active_render_id" not in updated
    assert updated["video_path"] == str(first_video)
