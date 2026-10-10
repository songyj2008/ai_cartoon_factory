from ui import beat_split_migration
from ui.beat_split_migration import _retarget_segment, _split_placeholder
from ui.group_panel import _segment_status
from workflow.runninghub_history import normalize_runninghub_history, retire_segment


def _attempt(task_id: str) -> dict:
    return {"task_id": task_id, "status": "success", "consume_coins": 12}


def test_legacy_duplicate_attempt_is_archived_once_and_removed_from_active_segments():
    first = {"part_id": "part_005", "segment_index": 5, "runninghub_attempts": [_attempt("old-task")]}
    second = {"part_id": "part_006", "segment_index": 6, "runninghub_attempts": [_attempt("old-task"), _attempt("new-task")]}
    payload = {"segments": [first, second]}

    report = normalize_runninghub_history(payload)

    assert report["repaired_duplicate_task_ids"] == 1
    assert "runninghub_attempts" not in first
    assert [item["task_id"] for item in second["runninghub_attempts"]] == ["new-task"]
    assert payload["retired_runninghub_attempts"][0]["task_id"] == "old-task"
    assert payload["retired_runninghub_attempts"][0]["legacy_owner_parts"] == ["part_005", "part_006"]
    assert first["segment_uid"] != second["segment_uid"]


def test_split_placeholder_never_inherits_runninghub_history_or_artifacts():
    source = {
        "segment_uid": "source-uid",
        "runninghub_attempts": [_attempt("old-task")],
        "task_id": "old-task",
        "video_path": "F:/project/videos/part_005.mp4",
        "workflow_path": "F:/project/workflows/part_005.json",
        "video_ok": True,
        "job": {"duration_sec": 3, "video_path": "F:/project/videos/part_005.mp4"},
    }

    created = _split_placeholder(source, {"title": "new beat", "estimated_duration_sec": 3}, 5, {})

    assert created["segment_uid"] != "source-uid"
    assert "runninghub_attempts" not in created
    assert "task_id" not in created
    assert "video_path" not in created
    assert "video_ok" not in created


def test_split_placeholder_does_not_inherit_asset_manifest():
    source = {
        "segment_uid": "source-uid",
        "compiled_prompt_snapshot": "旧快照",
        "generation_config": {
            "generation_mode": "ref2va",
            "duration_sec": 8,
            "asset_manifest": [
                {"id": "old-char", "type": "image", "path": "old.png", "role": "旧角色"},
            ],
            "prompt_override": "旧提示词",
        },
    }

    created = _split_placeholder(source, {"title": "new beat", "estimated_duration_sec": 3}, 5, {})

    config = created["generation_config"]
    assert "asset_manifest" not in config
    assert "prompt_override" not in config
    assert "compiled_prompt_snapshot" not in created
    assert config["generation_mode"] == "ref2va"
    assert config["duration_sec"] == 8


def test_retarget_changes_only_display_number_and_keeps_artifact_identity():
    original = {
        "segment_uid": "stable-id",
        "segment_index": 6,
        "part_id": "part_006",
        "video_path": "F:/project/videos/part_006_runninghub_task.mp4",
        "workflow_path": "F:/project/workflows/part_006.json",
        "job": {"part_id": "part_006", "video_path": "F:/project/videos/part_006_runninghub_task.mp4"},
    }

    moved = _retarget_segment(original, 5, {})

    assert moved["segment_uid"] == "stable-id"
    assert moved["part_id"] == "part_005"
    assert moved["video_path"] == original["video_path"]
    assert moved["workflow_path"] == original["workflow_path"]


def test_retire_segment_preserves_history_without_removing_artifacts():
    payload = {"segments": []}
    segment = {"segment_uid": "stable-id", "part_id": "part_005", "video_path": "F:/project/videos/source.mp4", "runninghub_attempts": [_attempt("task")]}

    archived = retire_segment(payload, segment, "beat deleted")

    assert archived["video_path"] == segment["video_path"]
    assert archived["runninghub_attempts"][0]["task_id"] == "task"
    assert payload["retired_segments"][0]["retired_reason"] == "beat deleted"


def test_split_retires_original_and_creates_clean_replacement_segments(monkeypatch):
    original = {
        "segment_uid": "source-uid",
        "segment_index": 2,
        "part_id": "part_002",
        "video_path": "F:/project/videos/part_002.mp4",
        "runninghub_attempts": [_attempt("source-task")],
        "job": {"duration_sec": 6, "part_id": "part_002"},
    }
    later = {
        "segment_uid": "later-uid",
        "segment_index": 3,
        "part_id": "part_003",
        "video_path": "F:/project/videos/part_003.mp4",
        "runninghub_attempts": [_attempt("later-task")],
        "job": {"duration_sec": 3, "part_id": "part_003"},
    }
    source = {"segments": [{"segment_index": 1, "part_id": "part_001", "job": {"duration_sec": 3}}, original, later]}
    saved = {}
    monkeypatch.setattr(beat_split_migration, "load_video_jobs", lambda: source)
    monkeypatch.setattr(beat_split_migration, "save_video_jobs", lambda payload: saved.setdefault("payload", payload))
    monkeypatch.setattr(beat_split_migration, "_update_render_log", lambda *args: None)

    payload = beat_split_migration.migrate_after_complex_beat_split(
        2,
        2,
        {"beats": [{"title": "one", "estimated_duration_sec": 3}, {"title": "two", "estimated_duration_sec": 3}, {"title": "later", "estimated_duration_sec": 3}, {"title": "last", "estimated_duration_sec": 3}]},
    )

    assert payload is saved["payload"]
    assert len(payload["retired_segments"]) == 1
    assert payload["retired_segments"][0]["segment_uid"] == "source-uid"
    assert [item.get("runninghub_attempts") for item in payload["segments"][1:3]] == [None, None]
    assert payload["segments"][3]["segment_uid"] == "later-uid"
    assert payload["segments"][3]["video_path"] == "F:/project/videos/part_003.mp4"


def test_delete_retires_deleted_segment_and_keeps_later_task_owner(monkeypatch):
    deleted = {"segment_uid": "deleted", "segment_index": 2, "part_id": "part_002", "runninghub_attempts": [_attempt("deleted-task")], "job": {"duration_sec": 3}}
    later = {"segment_uid": "later", "segment_index": 3, "part_id": "part_003", "runninghub_attempts": [_attempt("later-task")], "video_path": "F:/project/videos/part_003.mp4", "job": {"duration_sec": 3}}
    source = {"segments": [{"segment_uid": "first", "segment_index": 1, "part_id": "part_001", "job": {"duration_sec": 3}}, deleted, later]}
    monkeypatch.setattr(beat_split_migration, "load_video_jobs", lambda: source)
    monkeypatch.setattr(beat_split_migration, "save_video_jobs", lambda payload: None)
    monkeypatch.setattr(beat_split_migration, "_update_render_log_after_delete", lambda *args: None)

    payload = beat_split_migration.migrate_after_beat_delete(2, {"beats": [{}, {}]})

    assert payload["retired_segments"][0]["segment_uid"] == "deleted"
    assert payload["segments"][1]["segment_uid"] == "later"
    assert payload["segments"][1]["part_id"] == "part_002"
    assert payload["segments"][1]["runninghub_attempts"][0]["task_id"] == "later-task"
    assert payload["segments"][1]["video_path"] == "F:/project/videos/part_003.mp4"


def test_insert_creates_clean_segment_and_shifts_later_task_owner(monkeypatch):
    first = {
        "segment_uid": "first",
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": "minimax_h3_local_ref2va",
        "visible_roles": ["old-role"],
        "generation_config": {
            "generation_mode": "ref2va",
            "prompt_override": "old prompt",
            "asset_manifest": [{"id": "old-image", "type": "image", "path": "old.png"}],
        },
        "job": {"duration_sec": 8, "prompt": "old prompt"},
    }
    later = {
        "segment_uid": "later",
        "segment_index": 2,
        "part_id": "part_002",
        "video_path": "F:/project/videos/part_002.mp4",
        "runninghub_attempts": [_attempt("later-task")],
        "job": {"duration_sec": 5, "part_id": "part_002"},
    }
    source = {"segments": [first, later]}
    monkeypatch.setattr(beat_split_migration, "load_video_jobs", lambda: source)
    monkeypatch.setattr(beat_split_migration, "save_video_jobs", lambda payload: None)

    payload = beat_split_migration.migrate_after_beat_insert(
        1,
        {
            "beats": [
                {"title": "first", "estimated_duration_sec": 8},
                {"title": "new", "scene_id": "scene-1", "estimated_duration_sec": 8},
                {"title": "later", "estimated_duration_sec": 5},
            ]
        },
    )

    inserted = payload["segments"][1]
    shifted = payload["segments"][2]
    assert inserted["part_id"] == "part_002"
    assert inserted["title"] == "new"
    assert inserted["visible_roles"] == []
    assert inserted["generation_config"] == {"generation_mode": "ref2va"}
    assert inserted["job"]["prompt"] == ""
    assert shifted["segment_uid"] == "later"
    assert shifted["part_id"] == "part_003"
    assert shifted["video_path"] == "F:/project/videos/part_002.mp4"


def test_active_failed_segment_is_never_labeled_deleted_from_legacy_render_log():
    assert _segment_status("part_008", "", "failed") == ("生成失败", "status-failed")
