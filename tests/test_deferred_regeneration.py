from types import SimpleNamespace

from generation.deferred_regeneration import (
    DEFERRED_REGENERATION_KEY,
    finalize_deferred_regeneration,
)
from generation.render_history import begin_render, update_render
from generation.service import GenerationService
from workflow import runninghub_sync, segment_runner, video_merge


H3_MODEL_ID = "minimax_h3_local_ref2va"


def test_local_runninghub_queue_dispatches_through_the_segment_model(monkeypatch):
    payload = {
        "default_model_id": H3_MODEL_ID,
        "segments": [
            {
                "segment_index": 2,
                "part_id": "part_002",
                "model_id": H3_MODEL_ID,
                "status": "queued",
                "backend": "runninghub",
                "job": {"model_id": H3_MODEL_ID},
            }
        ],
    }
    calls = []

    class FakeService:
        def _submit_saved_direct(self, indices, *, merge_after, wait, backend, expected_backend):
            calls.append((indices, merge_after, wait, backend, expected_backend))
            return payload

    monkeypatch.setitem(segment_runner.CONFIG, "video_submit_backend", "runninghub")
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(segment_runner, "_video_submit_concurrency_limit", lambda: 3)
    monkeypatch.setattr("generation.service.get_generation_service", lambda: FakeService())

    result = segment_runner.dispatch_queued_runninghub_segments()

    assert result is payload
    assert calls == [({2}, False, False, "runninghub", "runninghub")]


def test_runninghub_queue_does_not_claim_web_video_tasks(monkeypatch):
    payload = {
        "segments": [{"segment_index": 4, "status": "queued", "backend": "doubao_web",
                      "model_id": H3_MODEL_ID, "workflow_path": "", "task_id": "web-task"}],
    }
    monkeypatch.setitem(segment_runner.CONFIG, "video_submit_backend", "runninghub")
    monkeypatch.setattr(segment_runner, "load_video_jobs", lambda: payload)
    monkeypatch.setattr("generation.service.get_generation_service",
                        lambda: (_ for _ in ()).throw(AssertionError("web task was claimed")))

    assert segment_runner.dispatch_queued_runninghub_segments() is None
    assert payload["segments"][0]["task_id"] == "web-task"


def _running_segment() -> dict:
    return {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": H3_MODEL_ID,
        "resolved_model": {"id": H3_MODEL_ID},
        "status": "running",
        "backend": "runninghub",
        "submitted": True,
        "task_id": "task-active",
        "workflow_path": "workflows/active.json",
        "generation_config": {"prompt_override": "old prompt"},
        "job": {"prompt": "submitted prompt", "duration_sec": 5},
    }


def test_saving_h3_config_preserves_the_active_submission(monkeypatch):
    payload = {"segments": [_running_segment()]}
    saved = {}
    monkeypatch.setattr(video_merge, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(video_merge, "save_video_jobs", lambda value: saved.setdefault("payload", value))
    monkeypatch.setattr("generation.service.normalize_video_jobs_payload", lambda value: value)

    service = object.__new__(GenerationService)
    result = service.update_segment_generation_config(1, prompt_override="new prompt")
    segment = result["segments"][0]

    assert saved["payload"] is result
    assert segment["generation_config"]["prompt_override"] == "new prompt"
    assert segment["status"] == "running"
    assert segment["submitted"] is True
    assert segment["task_id"] == "task-active"
    assert segment["workflow_path"] == "workflows/active.json"
    assert segment[DEFERRED_REGENERATION_KEY] == {
        "reason": "prompt changed",
        "task_id": "task-active",
        "prompt_id": "",
    }


def test_deferred_regeneration_is_bound_to_the_task_that_owned_the_saved_edit():
    segment = _running_segment()
    segment[DEFERRED_REGENERATION_KEY] = {
        "reason": "prompt changed",
        "task_id": "task-active",
        "prompt_id": "",
    }

    assert finalize_deferred_regeneration(segment, task_id="different-task") is False
    assert segment["status"] == "running"
    assert DEFERRED_REGENERATION_KEY in segment


def test_runninghub_completion_applies_saved_edits_after_recording_the_result(monkeypatch, tmp_path):
    video_path = tmp_path / "part_001.mp4"
    video_path.write_bytes(b"video")
    # This is the broken shape persisted by the previous implementation:
    # saving hid the active task behind needs_regenerate, so polling stopped.
    segment = _running_segment()
    segment["status"] = "needs_regenerate"
    segment["submitted"] = False
    segment["runninghub_attempts"] = [{"task_id": "task-active", "status": "running"}]
    payload = {"default_model_id": H3_MODEL_ID, "segments": [segment]}
    saved = {}

    class FakeClient:
        def get_task_status(self, task_id):
            assert task_id == "task-active"
            return {"status": "success"}

        def get_task_outputs(self, task_id):
            assert task_id == "task-active"
            return [{"fileUrl": "https://example.invalid/video.mp4", "consumeCoins": 12}]

    class FakeProvider:
        def __init__(self, model_id):
            assert model_id == H3_MODEL_ID
            self.client = FakeClient()

        def _download_first_video(self, outputs, part_id, task_id):
            assert outputs and part_id == "part_001" and task_id == "task-active"
            return str(video_path), ["https://example.invalid/video.mp4"]

    monkeypatch.setattr(runninghub_sync, "RunningHubSubmitProvider", FakeProvider)
    monkeypatch.setattr(runninghub_sync, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(runninghub_sync, "save_video_jobs", lambda value: saved.setdefault("payload", value))
    monkeypatch.setattr(runninghub_sync, "inspect_media", lambda path: SimpleNamespace(to_dict=lambda: {"path": str(path)}))
    monkeypatch.setattr(runninghub_sync, "mark_part_success", lambda *args: None)
    monkeypatch.setattr(segment_runner, "dispatch_queued_runninghub_segments", lambda: None)

    result, changed = runninghub_sync.sync_runninghub_video_jobs()
    completed = result["segments"][0]

    assert changed is True
    assert saved["payload"] is result
    assert completed["status"] == "needs_regenerate"
    assert completed["submitted"] is False
    assert completed["video_path"] == str(video_path)
    assert completed["stale_video_path"] == str(video_path)
    assert completed["stale_reason"] == "prompt changed"
    assert DEFERRED_REGENERATION_KEY not in completed
    assert completed["runninghub_attempts"][0]["status"] == "success"
    assert completed["runninghub_attempts"][0]["video_path"] == str(video_path)


def test_runninghub_completion_promotes_the_render_owned_by_the_latest_task(monkeypatch, tmp_path):
    old_video = tmp_path / "part_001_old.mp4"
    new_video = tmp_path / "part_001_new.mp4"
    old_video.write_bytes(b"old video")
    new_video.write_bytes(b"new video")
    segment = _running_segment()

    old_render = begin_render(segment, model_id=H3_MODEL_ID, backend="runninghub", status="running")
    update_render(
        segment,
        old_render["render_id"],
        {"submitted": True, "task_id": "task-old", "video_path": str(old_video), "status": "success"},
        status="success",
        activate_on_success=True,
    )
    new_render = begin_render(segment, model_id=H3_MODEL_ID, backend="runninghub", status="running")
    update_render(
        segment,
        new_render["render_id"],
        {"submitted": True, "task_id": "task-active", "status": "running"},
        status="running",
        activate_on_success=True,
    )
    segment.update({"task_id": "task-active", "video_path": "", "status": "running"})
    payload = {"default_model_id": H3_MODEL_ID, "segments": [segment]}

    class FakeClient:
        def get_task_status(self, task_id):
            return {"status": "success"}

        def get_task_outputs(self, task_id):
            return [{"fileUrl": "https://example.invalid/new.mp4"}]

    class FakeProvider:
        def __init__(self, model_id):
            self.client = FakeClient()

        def _download_first_video(self, outputs, part_id, task_id):
            return str(new_video), ["https://example.invalid/new.mp4"]

    monkeypatch.setattr(runninghub_sync, "RunningHubSubmitProvider", FakeProvider)
    monkeypatch.setattr(runninghub_sync, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(runninghub_sync, "save_video_jobs", lambda value: None)
    monkeypatch.setattr(runninghub_sync, "inspect_media", lambda path: SimpleNamespace(to_dict=lambda: {"path": str(path)}))
    monkeypatch.setattr(runninghub_sync, "mark_part_success", lambda *args: None)
    monkeypatch.setattr(segment_runner, "dispatch_queued_runninghub_segments", lambda: None)

    result, changed = runninghub_sync.sync_runninghub_video_jobs()
    completed = result["segments"][0]

    assert changed is True
    assert completed["task_id"] == "task-active"
    assert completed["video_path"] == str(new_video)
    assert completed["active_render_id"] == new_render["render_id"]
    assert new_render["status"] == "success"
    assert new_render["output"]["video_path"] == str(new_video)


def test_runninghub_sync_repairs_an_already_completed_task_still_pointing_at_old_render(monkeypatch, tmp_path):
    old_video = tmp_path / "part_001_old.mp4"
    new_video = tmp_path / "part_001_new.mp4"
    old_video.write_bytes(b"old video")
    new_video.write_bytes(b"new video")
    segment = _running_segment()

    old_render = begin_render(segment, model_id=H3_MODEL_ID, backend="runninghub", status="running")
    update_render(
        segment,
        old_render["render_id"],
        {"submitted": True, "task_id": "task-old", "video_path": str(old_video), "status": "success"},
        status="success",
        activate_on_success=True,
    )
    new_render = begin_render(segment, model_id=H3_MODEL_ID, backend="runninghub", status="running")
    update_render(
        segment,
        new_render["render_id"],
        {"submitted": True, "task_id": "task-active", "video_path": str(new_video), "status": "success"},
        status="success",
        activate_on_success=False,
    )
    segment.update({"task_id": "task-active", "video_path": str(new_video), "status": "success"})
    payload = {"default_model_id": H3_MODEL_ID, "segments": [segment]}
    saved = {}

    monkeypatch.setattr(runninghub_sync, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(runninghub_sync, "save_video_jobs", lambda value: saved.setdefault("payload", value))
    monkeypatch.setattr(segment_runner, "dispatch_queued_runninghub_segments", lambda: None)

    result, changed = runninghub_sync.sync_runninghub_video_jobs()

    assert changed is True
    assert saved["payload"] is result
    assert result["segments"][0]["active_render_id"] == new_render["render_id"]
    assert result["segments"][0]["task_id"] == "task-active"
    assert result["segments"][0]["video_path"] == str(new_video)
