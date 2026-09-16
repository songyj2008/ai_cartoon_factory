"""HTTP API routes attached to the Gradio application."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from services.context import BASE_DIR, CONFIG, EPISODES_DIR, assert_current_runtime_writable, get_current_episode_name, get_current_project_name, get_project_dir
from services.file_utils import load_json_file_silent
from services.gradio_runtime import ensure_asyncio_event_loop, ensure_gradio_app_runtime
from services.logger import get_logs, log
from ui.project_handlers import final_video_search_dirs, latest_final_video_path, list_project_episodes


_H3_MEDIA_SUFFIXES = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".webm", ".mov", ".mkv", ".avi"},
    "audio": {".mp3", ".wav"},
}


def _safe_media_filename(value: str) -> str:
    name = Path(str(value or "").replace("\\", "/")).name
    # Sanitize the stem separately so an all-Chinese name cannot lose
    # its extension separator ("主管办公室.png" previously became "png").
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    safe_suffix = re.sub(r"[^A-Za-z0-9.-]+", "_", suffix)
    return (cleaned or "reference") + (safe_suffix or (".bin" if not name else ""))


def _latest_runtime_video(project_name: str, episode_name: str = ""):
    from services.context import OUTPUT_DIR

    root = (EPISODES_DIR / project_name / episode_name) if episode_name else (OUTPUT_DIR / project_name)
    candidates = []
    final_dir = root / "final"
    videos_dir = root / "videos"
    if final_dir.exists():
        candidates.extend(p for p in final_dir.glob("final_video_*.mp4") if p.is_file())
        if not candidates:
            candidates.extend(p for p in final_dir.glob("*.mp4") if p.is_file())
    if not candidates and videos_dir.exists():
        candidates.extend(p for p in videos_dir.glob("*.mp4") if p.is_file())
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0].resolve()


def _runtime_video_url(project_name: str, episode_name: str = "") -> str:
    path = _latest_runtime_video(project_name, episode_name)
    return "/gradio_api/file=" + str(path).replace("\\", "/") if path else ""


def _current_state():
    project = get_project_dir()
    project_name = get_current_project_name() or "current"
    episode_choices = list_project_episodes(project_name)
    episodes = [episode for _label, episode in episode_choices if episode]
    return {
        "project_name": project_name,
        "current_episode": get_current_episode_name() or "",
        "readonly": bool(get_current_episode_name()),
        "episodes": episodes,
        "episode_items": [
            {"label": label, "value": episode, "video_url": _runtime_video_url(project_name, episode)}
            for label, episode in episode_choices
        ],
        "project_dir": str(project.resolve()),
        "story_text": load_json_file_silent("story.json"),
        "beats_text": load_json_file_silent("beats.json"),
        "video_jobs_text": load_json_file_silent("video_jobs.json"),
        "log_tail": get_logs(),
    }


def register_api_routes(demo):
    ensure_asyncio_event_loop()
    app = getattr(demo, "app", None)
    if app is None:
        from gradio.routes import App

        app = App()
        demo.app = app
    ensure_gradio_app_runtime(demo)

    if getattr(app.state, "aicf_api_routes_registered", False):
        return demo

    try:
        @app.get("/api/project/current_state")
        def api_project_current_state():
            return JSONResponse(_current_state())

        @app.get("/api/run_state")
        def api_run_state():
            data = {}
            try:
                data = json.loads(load_json_file_silent("video_jobs.json") or "{}")
            except Exception:
                data = {}
            return JSONResponse({"status": "IDLE", "video_jobs": data})

        @app.post("/api/project/open_videos_dir")
        def api_open_videos_dir():
            try:
                final_dir = get_project_dir() / "final"
                final_dir.mkdir(parents=True, exist_ok=True)
                if sys.platform.startswith("win"):
                    os.startfile(str(final_dir.resolve()))  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(final_dir.resolve())])
                else:
                    subprocess.Popen(["xdg-open", str(final_dir.resolve())])
                return JSONResponse({"ok": True, "path": str(final_dir.resolve())})
            except Exception as exc:
                log(f"[api][open_videos_dir][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

        @app.post("/api/segment/regenerate_video")
        async def api_regenerate_segment_video(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.service import get_generation_service
                from generation.job_schema import resolved_model_id
                from workflow.runninghub_sync import sync_runninghub_video_jobs
                from workflow.segment_runner import save_segment_director_prompt
                from workflow.video_merge import load_video_jobs

                segment_index = int(float((payload or {}).get("segment_index") or 0))
                if segment_index <= 0:
                    raise ValueError("invalid segment index")
                # A task may have been cancelled directly in RunningHub after
                # this browser loaded.  When RunningHub is the chosen backend,
                # reconcile it before the edit/save path applies its active-task
                # protection.
                if str(CONFIG.get("video_submit_backend") or "").strip().lower() == "runninghub":
                    log("[LICON_MSR][runninghub][regen] checking remote task status before retry", "STEP")
                    sync_runninghub_video_jobs()
                service = get_generation_service()
                jobs = load_video_jobs()
                segment = next(
                    (item for item in jobs.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == segment_index),
                    None,
                )
                if segment is None:
                    raise ValueError(f"segment not found: {segment_index}")
                model_id = resolved_model_id(segment, jobs)
                if model_id == "ltx23_licon_msr_v2":
                    director_prompt = str((payload or {}).get("director_prompt") or "").strip()
                    has_reference_roles = isinstance((payload or {}).get("reference_roles"), list)
                    has_reference_image_ids = isinstance((payload or {}).get("reference_image_ids"), dict)
                    has_scene_id = "scene_id" in (payload or {})
                    reference_roles = [str(role) for role in (payload or {}).get("reference_roles") or []] if has_reference_roles else None
                    reference_image_ids = dict((payload or {}).get("reference_image_ids") or {}) if has_reference_image_ids else None
                    scene_id = str((payload or {}).get("scene_id") or "") if has_scene_id else None
                    if director_prompt or has_reference_roles or has_reference_image_ids or has_scene_id:
                        save_segment_director_prompt(
                            segment_index,
                            director_prompt,
                            reference_roles=reference_roles,
                            reference_image_ids=reference_image_ids,
                            scene_id=scene_id,
                        )
                else:
                    validation = service.validate_segment_generation_config(segment_index)
                    if validation.get("errors"):
                        raise ValueError(
                            "；".join(
                                str(item.get("message") or item.get("code") or item)
                                if isinstance(item, dict)
                                else str(item)
                                for item in validation["errors"]
                            )
                        )
                    beats_text = load_json_file_silent("beats.json")
                    try:
                        beats_data = json.loads(beats_text or "{}")
                    except json.JSONDecodeError as exc:
                        raise ValueError("beats.json is invalid; regenerate or save Beats before rendering this segment") from exc
                    if not isinstance(beats_data, dict) or not isinstance(beats_data.get("beats"), list):
                        raise ValueError("beats.json is missing; generate or save Beats before rendering this segment")
                    # H3 owns a different prompt compiler, asset binder and
                    # graph.  Always rebuild its selected segment before a
                    # retry; an LTX workflow must never be submitted as H3.
                    service.prepare_segments(beats_data, segment_indices={segment_index})
                log(f"[generation] API regenerate video part_{segment_index:03d} model={model_id}", "STEP")
                result = service.submit_saved_segments({segment_index}, wait=False)
                from ui.group_panel import render_segment_panel

                return JSONResponse(
                    {
                        "ok": True,
                        "video_jobs": result,
                        "segment_panel_html": render_segment_panel(result, segment_index),
                        "log_tail": get_logs(),
                    }
                )
            except Exception as exc:
                log(f"[LICON_MSR][api_regen][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=500)

        @app.post("/api/segment/save_prompt")
        async def api_save_segment_prompt(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.job_schema import resolved_model_id
                from generation.service import get_generation_service
                from workflow.segment_runner import save_segment_director_prompt
                from workflow.video_merge import load_video_jobs

                segment_index = int(float((payload or {}).get("segment_index") or 0))
                director_prompt = str((payload or {}).get("director_prompt") or "").strip()
                full_prompt = str((payload or {}).get("full_prompt") or "").strip() if "full_prompt" in (payload or {}) else None
                if segment_index <= 0 or (not director_prompt and not full_prompt):
                    raise ValueError("segment_index 和完整提示词不能为空")
                jobs = load_video_jobs()
                segment = next(
                    (item for item in jobs.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == segment_index),
                    None,
                )
                if segment is None:
                    raise ValueError(f"segment not found: {segment_index}")
                if resolved_model_id(segment, jobs) != "ltx23_licon_msr_v2":
                    result = get_generation_service().update_segment_generation_config(
                        segment_index,
                        prompt_override=full_prompt if full_prompt is not None else director_prompt,
                    )
                else:
                    has_roles = isinstance((payload or {}).get("reference_roles"), list)
                    has_images = isinstance((payload or {}).get("reference_image_ids"), dict)
                    has_scene = "scene_id" in (payload or {})
                    result = save_segment_director_prompt(
                        segment_index,
                        director_prompt,
                        reference_roles=[str(role) for role in (payload or {}).get("reference_roles") or []] if has_roles else None,
                        reference_image_ids=dict((payload or {}).get("reference_image_ids") or {}) if has_images else None,
                        scene_id=str((payload or {}).get("scene_id") or "") if has_scene else None,
                        full_prompt_override=full_prompt,
                    )
                return JSONResponse({"ok": True, "video_jobs": result, "log_tail": get_logs()})
            except Exception as exc:
                log(f"[LICON_MSR][api_save_prompt][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/model")
        async def api_set_segment_model(request: Request):
            """Model-neutral endpoint used when a future model is registered in the UI."""
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.service import get_generation_service

                assert_current_runtime_writable("切换分段视频模型")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                result = get_generation_service().set_segment_model_override(
                    segment_index,
                    str((payload or {}).get("model_id") or ""),
                )
                from ui.group_panel import render_segment_panel

                return JSONResponse(
                    {
                        "ok": True,
                        "video_jobs": result,
                        "segment_panel_html": render_segment_panel(result, segment_index),
                        "log_tail": get_logs(),
                    }
                )
            except Exception as exc:
                log(f"[generation][api_model][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/generation-config")
        async def api_save_segment_generation_config(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.service import get_generation_service

                assert_current_runtime_writable("保存分段模型配置")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                if segment_index <= 0:
                    raise ValueError("invalid segment index")
                manifest = (payload or {}).get("asset_manifest")
                if manifest is not None and not isinstance(manifest, list):
                    raise ValueError("asset_manifest must be a list")
                result = get_generation_service().update_segment_generation_config(
                    segment_index,
                    generation_mode=str((payload or {}).get("generation_mode") or "ref2va"),
                    asset_manifest=manifest,
                    prompt_override=str((payload or {}).get("prompt_override") or ""),
                    force_save=(payload or {}).get("force_save") is True,
                )
                segment = next(
                    (item for item in result.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == segment_index),
                    None,
                )
                config = segment.get("generation_config") if isinstance(segment, dict) and isinstance(segment.get("generation_config"), dict) else {}
                return JSONResponse({
                    "ok": True,
                    "video_jobs": result,
                    "generation_config": config,
                    "canonical_prompt": str(config.get("prompt_override") or ""),
                    "log_tail": get_logs(),
                })
            except Exception as exc:
                log(f"[generation][api_config][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/validate")
        async def api_validate_segment_generation_config(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.service import get_generation_service

                segment_index = int(float((payload or {}).get("segment_index") or 0))
                validation = get_generation_service().validate_segment_generation_config(segment_index)
                return JSONResponse({"ok": True, **validation})
            except Exception as exc:
                log(f"[generation][api_validate][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "errors": [str(exc)]}, status_code=400)

        @app.post("/api/segment/{segment_index}/assets")
        async def api_upload_segment_generation_assets(
            segment_index: int,
            asset_type: str = Form(...),
            files: list[UploadFile] = File(...),
        ):
            try:
                assert_current_runtime_writable("上传分段参考素材")
                index = int(segment_index)
                if index <= 0:
                    raise ValueError("invalid segment index")
                from generation.job_schema import resolved_model_id
                from workflow.video_merge import load_video_jobs

                jobs = load_video_jobs()
                segment = next(
                    (
                        item
                        for item in jobs.get("segments") or []
                        if isinstance(item, dict) and int(item.get("segment_index") or 0) == index
                    ),
                    None,
                )
                if segment is None:
                    raise ValueError(f"segment not found: {index}")
                if resolved_model_id(segment, jobs) != "minimax_h3_local_ref2va":
                    raise ValueError("this upload endpoint is only available for a MiniMax H3 Ref2VA segment")
                media_type = str(asset_type or "").strip().lower()
                allowed = _H3_MEDIA_SUFFIXES.get(media_type)
                if not allowed:
                    raise ValueError("asset_type must be image, video, or audio")
                if not files:
                    raise ValueError("no files uploaded")
                target_dir = get_project_dir() / "media" / "h3" / f"segment_{index:03d}"
                target_dir.mkdir(parents=True, exist_ok=True)
                assets: list[dict[str, object]] = []
                for order, upload in enumerate(files, start=1):
                    filename = _safe_media_filename(upload.filename or "")
                    suffix = Path(filename).suffix.lower()
                    if suffix not in allowed:
                        raise ValueError(f"unsupported {media_type} file: {filename}")
                    dest = target_dir / f"{uuid.uuid4().hex}_{filename}"
                    with dest.open("wb") as target:
                        shutil.copyfileobj(upload.file, target)
                    await upload.close()
                    from generation.media import inspect_media

                    metadata = {"size_bytes": dest.stat().st_size}
                    metadata.update(inspect_media(dest).to_dict())
                    assets.append(
                        {
                            "id": f"upload_{uuid.uuid4().hex}",
                            "type": media_type,
                            "path": str(dest.resolve()),
                            "label": filename,
                            "role": "",
                            "order": order,
                            "source": "segment_upload",
                            "metadata": metadata,
                        }
                    )
                return JSONResponse({"ok": True, "assets": assets})
            except Exception as exc:
                log(f"[generation][api_asset_upload][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        @app.post("/api/segment/preview_prompt")
        async def api_preview_segment_prompt(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from workflow.asset_resolver import prompt_with_character_descriptions

                director_prompt = str((payload or {}).get("director_prompt") or "").strip()
                reference_roles = list(
                    dict.fromkeys(
                        str(role).strip()
                        for role in (payload or {}).get("reference_roles") or []
                        if str(role or "").strip()
                    )
                )
                if len(reference_roles) > 4:
                    raise ValueError("人物参考图最多选择 4 个角色")
                raw_image_ids = (
                    (payload or {}).get("reference_image_ids")
                    if isinstance((payload or {}).get("reference_image_ids"), dict)
                    else {}
                )
                reference_image_ids = {
                    str(role): str(image_id).strip()
                    for role, image_id in raw_image_ids.items()
                    if str(role).strip() and str(image_id).strip()
                }
                prompt = prompt_with_character_descriptions(
                    director_prompt,
                    reference_roles,
                    reference_image_ids,
                )
                prefix = prompt_with_character_descriptions(
                    "",
                    reference_roles,
                    reference_image_ids,
                )
                return JSONResponse(
                    {
                        "ok": True,
                        "prompt": prompt,
                        "prefix": prefix,
                        "director_prompt": director_prompt,
                    }
                )
            except Exception as exc:
                log(f"[LICON_MSR][api_preview_prompt][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        @app.post("/api/segment/video_ok")
        async def api_set_segment_video_ok(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from workflow.segment_runner import set_segment_video_ok
                from ui.group_panel import render_segment_panel

                assert_current_runtime_writable("标记分段视频OK")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                if segment_index <= 0:
                    raise ValueError("invalid segment index")
                result = set_segment_video_ok(segment_index, bool((payload or {}).get("video_ok")))
                return JSONResponse({
                    "ok": True,
                    "video_jobs": result,
                    "segment_panel_html": render_segment_panel(result, segment_index),
                    "log_tail": get_logs(),
                })
            except Exception as exc:
                log(f"[LICON_MSR][api_video_ok][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/delete_task_video")
        async def api_delete_task_video(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from workflow.segment_runner import delete_runninghub_attempt_video

                assert_current_runtime_writable("删除任务视频")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                task_id = str((payload or {}).get("task_id") or "").strip()
                if segment_index <= 0 or not task_id:
                    raise ValueError("segment_index 和 task_id 不能为空")
                result = delete_runninghub_attempt_video(segment_index, task_id)
                return JSONResponse({"ok": True, "video_jobs": result, "log_tail": get_logs()})
            except Exception as exc:
                log(f"[LICON_MSR][api_delete_task_video][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/select_task_video")
        async def api_select_task_video(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from ui.group_panel import render_segment_panel
                from workflow.segment_runner import select_runninghub_attempt_video

                assert_current_runtime_writable("选择分段合成视频")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                task_id = str((payload or {}).get("task_id") or "").strip()
                if segment_index <= 0 or not task_id:
                    raise ValueError("segment_index 和 task_id 不能为空")
                result = select_runninghub_attempt_video(segment_index, task_id)
                return JSONResponse(
                    {
                        "ok": True,
                        "video_jobs": result,
                        "segment_panel_html": render_segment_panel(result, segment_index),
                        "log_tail": get_logs(),
                    }
                )
            except Exception as exc:
                log(f"[LICON_MSR][api_select_task_video][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.get("/api/one_click/status")
        def api_one_click_status():
            return JSONResponse({"running": False, "paused": False, "status": "IDLE", "step": ""})

        @app.post("/api/one_click/pause")
        def api_one_click_pause():
            return JSONResponse({"ok": True, "pause_requested": False, "status": "IDLE"})

        @app.post("/api/one_click/continue")
        def api_one_click_continue():
            return JSONResponse({"ok": True, "pause_requested": False, "status": "IDLE", "resume_pipeline": False})

        @app.get("/static/codemirror/{filename}")
        def api_static_codemirror(filename: str):
            safe_name = filename.replace("\\", "/").split("/")[-1]
            path = BASE_DIR / "web" / "codemirror" / safe_name
            if not path.exists() or path.suffix.lower() not in {".js", ".css"}:
                return JSONResponse({"ok": False, "message": "static file not found"}, status_code=404)
            media_type = "text/javascript" if path.suffix.lower() == ".js" else "text/css"
            return FileResponse(path, media_type=media_type)

        @app.get("/api/final_video/latest")
        def api_latest_final_video():
            path = latest_final_video_path()
            if not path:
                return JSONResponse(
                    {
                        "ok": False,
                        "message": "暂无可预览的最终合成视频",
                        "project_dir": str(get_project_dir().resolve()),
                        "search_dirs": final_video_search_dirs(),
                    }
                )
            return JSONResponse(
                {
                    "ok": True,
                    "path": str(path),
                    "url": "/gradio_api/file=" + str(path).replace("\\", "/"),
                    "project_dir": str(get_project_dir().resolve()),
                    "search_dirs": final_video_search_dirs(),
                }
            )

        app.state.aicf_api_routes_registered = True
        log("[api] custom routes registered", "STEP")
    except Exception as exc:
        log(f"[api][error] custom routes registration failed: {exc}", "ERROR")
        raise

    return demo
