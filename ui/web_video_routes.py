"""HTTP API for video-channel selection and the paired Chrome extension."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from generation.channels import options_for
from services import web_video_bridge
from ui.web_llm_routes import _error, _extension_auth, _ui_origin
from workflow.video_merge import load_video_jobs


def register_web_video_routes(app):
    @app.get("/api/video-channels/segments/{index}/panel")
    def current_panel(index: int, request: Request):
        try:
            _ui_origin(request)
            from ui.group_panel import render_segment_panel

            payload = load_video_jobs()
            return {"ok": True, "segment_panel_html": render_segment_panel(payload, index)}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/video-channels/segments/{index}")
    def channel_options(index: int, request: Request):
        try:
            _ui_origin(request)
            payload = load_video_jobs()
            segment = next((item for item in payload.get("segments") or [] if isinstance(item, dict)
                            and int(item.get("segment_index") or 0) == index), None)
            if segment is None:
                raise ValueError("分段不存在")
            return {"ok": True, "channels": options_for(segment, payload),
                    "selected": str((segment.get("generation_config") or {}).get("channel_id") or "runninghub")}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/video-channels/segments/{index}")
    async def save_channel(index: int, request: Request):
        try:
            _ui_origin(request)
            from services.context import assert_current_runtime_writable
            from generation.channels import get_channel
            from workflow.video_merge import save_video_jobs, video_jobs_lock
            assert_current_runtime_writable('保存分段视频渠道')
            data = await request.json()
            channel_id = str(data.get('channel_id') or '')
            get_channel(channel_id)
            with video_jobs_lock():
                payload = load_video_jobs()
                segment = next((item for item in payload.get('segments') or [] if int(item.get('segment_index') or 0) == index), None)
                if segment is None:
                    raise ValueError('分段不存在')
                config = dict(segment.get('generation_config') or {})
                config['channel_id'] = channel_id
                segment['generation_config'] = config
                save_video_jobs(payload)
            return {'ok': True, 'selected': channel_id}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-video/bridge/claim")
    async def claim(request: Request):
        try:
            _extension_auth(request)
            data = await request.json()
            return {"ok": True, "task": web_video_bridge.claim(str(data.get("client_id") or ""),
                                                                 [str(value) for value in data.get("channels") or []])}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-video/bridge/tasks/{task_id}/assets/{asset_id}")
    def asset(task_id: str, asset_id: str, lease: str, request: Request):
        try:
            _extension_auth(request)
            path = web_video_bridge.asset(task_id, asset_id, lease)
            return FileResponse(path, filename=path.name)
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-video/bridge/tasks/{task_id}/assets/{asset_id}/local-path")
    def asset_local_path(task_id: str, asset_id: str, lease: str, request: Request):
        """Return a leased task asset path for the browser's native file chooser."""
        try:
            _extension_auth(request)
            path = web_video_bridge.asset(task_id, asset_id, lease)
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-video/bridge/tasks/{task_id}/report")
    async def report(task_id: str, request: Request):
        try:
            _extension_auth(request)
            data = await request.json()
            return {"ok": True, "task": web_video_bridge.report(task_id, str(data.get("lease") or ""),
                str(data.get("state") or ""), output_path=str(data.get("output_path") or ""),
                error=str(data.get("error") or ""))}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-video/tasks/clear")
    def clear_video_tasks(request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "dismissed": web_video_bridge.dismiss_tasks()}
        except Exception as exc:
            return _error(exc)

    @app.delete("/api/web-video/tasks/{task_id}")
    def dismiss_video_task(task_id: str, request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "dismissed": web_video_bridge.dismiss_tasks(task_id)}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-video/tasks/{task_id}")
    def task_status(task_id: str, request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "task": web_video_bridge.task_status(task_id)}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-video/tasks")
    def video_tasks(request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "tasks": web_video_bridge.list_tasks()}
        except Exception as exc:
            return _error(exc)
