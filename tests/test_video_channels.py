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
        "comfyui", "runninghub", "doubao_web", "runninghub_web", "runninghub_international_web"
    }


def test_web_quota_is_atomic_and_claim_is_once_only(tmp_path, monkeypatch):
    from services import web_llm_bridge
    monkeypatch.setattr(web_llm_bridge, 'BRIDGE_DIR', tmp_path / 'clients')
    web_llm_bridge.heartbeat('browser-profile', ['deepseek'], browser={'name':'Chrome', 'channels':['doubao_web']})
    web_llm_bridge.heartbeat('edge', ['deepseek'], browser={'name':'Edge', 'channels':['doubao_web']})
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
    assert bridge.claim('edge', ['doubao_web']) is None
    queued = bridge.list_tasks()[0]
    bridge.retarget(queued['id'], 'edge')
    targeted = bridge.claim('edge', ['doubao_web'])
    assert targeted['id'] == queued['id']
    with pytest.raises(ValueError, match='尚未领取'):
        bridge.retarget(targeted['id'], 'browser-profile')
    first = bridge.claim("browser-profile", ["doubao_web"])
    assert first and first["lease"]
    assert bridge.asset(first["id"], "0", first["lease"]) == image
    with pytest.raises(PermissionError):
        bridge.asset(first["id"], "0", "wrong-lease")
    assert bridge.claim("browser-profile", ["doubao_web"]) is None
    bridge.report(first["id"], first["lease"], "error", error="参考图上传前失败")
    second = bridge.claim("browser-profile", ["doubao_web"])
    assert second and second["id"] != first["id"]
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
    assert response.json()['selected'] == 'runninghub'
    doubao = next(item for item in response.json()["channels"] if item["id"] == "doubao_web")
    assert doubao["available"] is True
    assert doubao["uses_workflow"] is False
    listing = TestClient(app).get("/api/web-video/tasks")
    assert listing.status_code == 200
    assert listing.json() == {"ok": True, "tasks": []}


def test_video_claim_reserves_browser_channel_independently(tmp_path, monkeypatch):
    from services import web_llm_bridge
    monkeypatch.setattr(web_llm_bridge, 'BRIDGE_DIR', tmp_path / 'clients')
    monkeypatch.setattr(bridge, '_DB', tmp_path / 'queue.sqlite3')
    monkeypatch.setattr(bridge, '_public', lambda row, **kwargs: dict(row))
    monkeypatch.setattr(web_llm_bridge, 'get_settings', lambda: {})
    monkeypatch.setattr(web_llm_bridge, 'browser_clients', lambda: [
        {'id': browser, 'online': True, 'channels': ['doubao_web', 'runninghub_web']}
        for browser in ('edge', 'chrome')])
    with bridge._connect() as conn:
        for identifier, browser, channel in [('a','edge','doubao_web'), ('b','edge','doubao_web'),
                                             ('c','edge','runninghub_web'), ('d','chrome','doubao_web')]:
            conn.execute("""INSERT INTO tasks
                (id,channel,account,quota_day,state,runtime,project,episode,segment_index,
                 segment_uid,render_id,input_json,created_at,updated_at,target_client_id)
                VALUES (?,?,? ,?,'queued','','','',1,'','','{}',1,1,?)""",
                (identifier, channel, 'default', bridge._today(), browser))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bridge.claim('edge', ['doubao_web']), range(2)))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)['id'] == 'a'
    assert bridge.claim('edge', ['doubao_web', 'runninghub_web'])['id'] == 'c'
    assert bridge.claim('chrome', ['doubao_web'])['id'] == 'd'
    with bridge._connect() as conn:
        conn.execute("UPDATE tasks SET state='submitted' WHERE id='a'")
    assert bridge.claim('edge', ['doubao_web']) is None
    with bridge._connect() as conn:
        conn.execute("UPDATE tasks SET state='uncertain' WHERE id='a'")
    assert bridge.claim('edge', ['doubao_web'])['id'] == 'b'


def test_offline_video_browser_prefers_online_same_type():
    browsers = [
        {'id': 'old', 'type': 'Edge', 'online': False, 'channels': ['runninghub_web']},
        {'id': 'chrome', 'type': 'Chrome', 'online': True, 'seen': 20, 'channels': ['runninghub_web']},
        {'id': 'edge', 'type': 'Edge', 'online': True, 'seen': 10, 'channels': ['runninghub_web']},
    ]
    assert bridge._online_video_browser('old', 'runninghub_web', browsers) == 'edge'
    assert bridge._online_video_browser('chrome', 'runninghub_web', browsers) == 'chrome'
    assert bridge._online_video_browser('old', 'unsupported', browsers) == ''


def test_queued_offline_target_is_reassigned_on_claim(tmp_path, monkeypatch):
    from services import web_llm_bridge
    monkeypatch.setattr(bridge, '_DB', tmp_path / 'queue.sqlite3')
    monkeypatch.setattr(bridge, '_public', lambda row, **kwargs: dict(row))
    monkeypatch.setattr(web_llm_bridge, 'get_settings', lambda: {})
    monkeypatch.setattr(web_llm_bridge, 'browser_clients', lambda: [
        {'id': 'old', 'type': 'Edge', 'online': False},
        {'id': 'new', 'type': 'Edge', 'online': True, 'channels': ['runninghub_web']},
    ])
    with bridge._connect() as conn:
        conn.execute("""INSERT INTO tasks
            (id,channel,account,quota_day,state,runtime,project,episode,segment_index,
             segment_uid,render_id,input_json,created_at,updated_at,target_client_id)
            VALUES ('queued','runninghub_web','default',?,'queued','','','',1,'','','{}',1,1,'old')""",
            (bridge._today(),))
    claimed = bridge.claim('new', ['runninghub_web'])
    assert claimed['id'] == 'queued'
    assert claimed['target_client_id'] == 'new'
    assert claimed['client_id'] == 'new'
    assert bridge.claim('new', ['runninghub_web']) is None
