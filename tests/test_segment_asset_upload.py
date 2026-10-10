import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from ui import api_routes


@pytest.mark.parametrize("name", ["主管办公室.png", "宿舍.JPG", "走廊.jpeg", "图片.webp", "参考视频.mp4", "声音.wav"])
def test_chinese_filename_keeps_extension(name):
    safe = api_routes._safe_media_filename(name)
    assert Path(safe).suffix == Path(name).suffix
    assert Path(safe).name == safe


def test_upload_chinese_png_through_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(api_routes, "ensure_gradio_app_runtime", lambda _: None)
    monkeypatch.setattr(api_routes, "assert_current_runtime_writable", lambda _: None)
    monkeypatch.setattr(api_routes, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: {
        "segments": [{"segment_index": 1, "model_id": "minimax_h3_local_ref2va"}]
    })
    monkeypatch.setattr("generation.media.inspect_media", lambda _: SimpleNamespace(to_dict=lambda: {}))
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    with TestClient(app) as client:
        response = client.post("/api/segment/1/assets", data={"asset_type": "image"},
                               files={"files": ("主管办公室.png", buffer.getvalue(), "image/png")})
        assert response.status_code == 200, response.text
        asset = response.json()["assets"][0]
        assert Path(asset["path"]).suffix == ".png"
        assert Path(asset["path"]).read_bytes() == buffer.getvalue()
        rejected = client.post("/api/segment/1/assets", data={"asset_type": "image"},
                              files={"files": ("不支持.exe", b"bad", "application/octet-stream")})
        assert rejected.status_code == 400
