from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from generation.channels import all_channels, options_for
from services import web_video_bridge as bridge


def _payload(image, durations):
    return {
        "default_model_id": "minimax_h3_local_ref2va",
        "segments": [
            {"segment_index": index, "segment_uid": f"segment-{index}", "model_id": "minimax_h3_local_ref2va",
             "status": "workflow_ready", "job": {"duration_sec": duration, "final_prompt": "镜头缓慢推进",
                "asset_manifest": [{"id": "image-1", "type": "image", "path": str(image)}]}}
            for index, duration in enumerate(durations, 1)
        ],
    }


def test_doubao_free_duration_filters_without_changing_model_or_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_DB", tmp_path / "queue.sqlite3")
    image = tmp_path / "reference.png"
    image.write_bytes(b"image")
    payload = _payload(image, [3, 4, 10, 11])
    channel = all_channels()["doubao_web"]
    eligible = [not channel.unavailable_reason(segment, payload) for segment in payload["segments"]]
    assert eligible == [False, True, True, False]
    assert [segment["job"]["duration_sec"] for segment in payload["segments"]] == [3, 4, 10, 11]
    assert all(segment["model_id"] == "minimax_h3_local_ref2va" for segment in payload["segments"])
    assert {option["id"] for option in options_for(payload["segments"][1], payload)} == {
        "comfyui", "runninghub", "doubao_web"
    }


def test_web_quota_is_atomic_and_claim_is_once_only(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_DB", tmp_path / "queue.sqlite3")
    monkeypatch.setattr(bridge, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "get_current_project_name", lambda: "test")
    monkeypatch.setattr(bridge, "get_current_episode_name", lambda: "")
    image = tmp_path / "reference.png"
    image.write_bytes(b"image")
    payload = _payload(image, [5] * 11)
    monkeypatch.setattr(bridge, "load_video_jobs", lambda: payload)
    monkeypatch.setattr(bridge, "save_video_jobs", lambda value: None)
    channel = all_channels()["doubao_web"]

    def enqueue(index):
        try:
            bridge.enqueue_segment(index, channel)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=11) as pool:
        results = list(pool.map(enqueue, range(1, 12)))
    assert results.count(True) == 10
    assert results.count(False) == 1
    assert bridge.remaining_quota("doubao_web") == 0
    first = bridge.claim("browser-profile", ["doubao_web"])
    assert first and first["lease"]
    assert bridge.asset(first["id"], "0", first["lease"]) == image
    with pytest.raises(PermissionError):
        bridge.asset(first["id"], "0", "wrong-lease")
    second = bridge.claim("browser-profile", ["doubao_web"])
    assert second and second["id"] != first["id"]
    bridge.report(first["id"], first["lease"], "error", error="参考图上传前失败")
    assert bridge.remaining_quota("doubao_web") == 1
    bridge.report(second["id"], second["lease"], "submitted")
    visible = bridge.list_tasks()
    assert any(item["id"] == second["id"] and item["state"] == "submitted" for item in visible)
    assert all("lease" not in item and "runtime" not in item and "input_json" not in item for item in visible)
    with pytest.raises(ValueError, match="不能释放额度"):
        bridge.report(second["id"], second["lease"], "error")
    from generation.contracts import MediaSpec

    monkeypatch.setattr("generation.media.inspect_media", lambda _path: MediaSpec(duration_sec=5, width=1600, height=900))
    downloaded = tmp_path / "downloaded.mp4"
    downloaded.write_bytes(b"video bytes")
    bridge.report(second["id"], second["lease"], "complete", output_path=str(downloaded))
    segment = next(item for item in payload["segments"] if item["task_id"] == second["id"])
    assert segment["status"] == "success"
    assert segment["model_id"] == "minimax_h3_local_ref2va"
    assert segment["video_path"] and (tmp_path / "videos").is_dir()
    assert segment["active_render_id"] == next(render["render_id"] for render in segment["renders"]
        if render["status"] == "success")


def test_explicit_direct_channel_does_not_mutate_project_default(monkeypatch):
    from workflow import submit_factory
    from workflow.submitters import comfyui_provider

    sentinel = object()
    monkeypatch.setattr(submit_factory, "CONFIG", {
        "video_submit_backend": "runninghub", "submit_backends": {"comfyui": {"enabled": True}}
    })
    monkeypatch.setattr(comfyui_provider, "ComfyUISubmitProvider", lambda: sentinel)
    assert submit_factory.get_submit_provider("ltx23_licon_msr_v2", backend="comfyui") is sentinel


def test_channel_options_route_reports_web_capability(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ui import web_video_routes

    monkeypatch.setattr(bridge, "_DB", tmp_path / "queue.sqlite3")
    image = tmp_path / "reference.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(web_video_routes, "load_video_jobs", lambda: _payload(image, [5]))
    app = FastAPI()
    web_video_routes.register_web_video_routes(app)
    response = TestClient(app).get("/api/video-channels/segments/1")
    assert response.status_code == 200
    doubao = next(item for item in response.json()["channels"] if item["id"] == "doubao_web")
    assert doubao["available"] is True
    assert doubao["uses_workflow"] is False
    listing = TestClient(app).get("/api/web-video/tasks")
    assert listing.status_code == 200
    assert listing.json() == {"ok": True, "tasks": []}
