"""HTTP API routes attached to the Gradio application."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from functools import wraps
from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from services.context import BASE_DIR, CONFIG, EPISODES_DIR, assert_current_runtime_writable, get_current_episode_name, get_current_project_name, get_project_dir
from services.file_utils import load_json_file_silent
from services.gradio_runtime import ensure_asyncio_event_loop, ensure_gradio_app_runtime
from services.logger import get_logs, log
from ui.project_handlers import final_video_search_dirs, latest_final_video_path, list_project_episodes


def _pinned_segment_route(handler):
    """Keep one request bound to its project across background LLM waits."""
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        from services.context import pin_runtime
        request = kwargs.get('request') or next((arg for arg in args if isinstance(arg, Request)), None)
        try:
            with pin_runtime():
                expected = request.headers.get('X-AICF-Runtime') if request is not None else None
                if expected and request.headers.get('X-AICF-Runtime-Encoding') == 'uri':
                    from urllib.parse import unquote
                    try:
                        expected = unquote(expected, encoding='utf-8', errors='strict')
                    except UnicodeError:
                        return JSONResponse({'ok': False, 'message': '项目路径编码无效，请刷新页面后重试。'}, status_code=400)
                if expected and Path(expected).resolve() != get_project_dir().resolve():
                    return JSONResponse({'ok': False, 'message': '项目或集数已切换，已拒绝旧页面请求；原任务继续在原项目运行。'}, status_code=409)
                return await handler(*args, **kwargs)
        except Exception as exc:
            path = request.scope.get('path', handler.__name__) if request is not None else handler.__name__
            log(f'[api][error] {path}\n{traceback.format_exc()}', 'ERROR')
            return JSONResponse({'ok': False, 'code': 'API_INTERNAL_ERROR',
                                 'message': f'后台处理失败（{path}）：{exc}'}, status_code=500)
    return wrapped


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
        "readonly": bool(get_current_episode_name()) and not (project / '.draft').is_file(),
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
    from services.browser_runtime import bind_api
    bind_api(app)

    if getattr(app.state, "aicf_api_routes_registered", False):
        return demo

    try:
        from ui.web_llm_routes import register_web_llm_routes
        register_web_llm_routes(app)
        from ui.web_video_routes import register_web_video_routes
        register_web_video_routes(app)

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
        @_pinned_segment_route
        async def api_regenerate_segment_video(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from generation.service import get_generation_service
                from generation.channels import get_channel
                from generation.job_schema import resolved_model_id
                from workflow.runninghub_sync import sync_runninghub_video_jobs
                from workflow.segment_runner import save_segment_director_prompt
                from workflow.video_merge import load_video_jobs

                segment_index = int(float((payload or {}).get("segment_index") or 0))
                channel_id = str((payload or {}).get("channel_id") or "").strip() or None
                if not channel_id:
                    channel_payload = load_video_jobs()
                    channel_segment = next((item for item in channel_payload.get('segments') or [] if int(item.get('segment_index') or 0) == segment_index), {})
                    channel_id = str((channel_segment.get('generation_config') or {}).get('channel_id') or 'runninghub')
                uses_workflow = get_channel(channel_id).uses_workflow if channel_id else True
                if segment_index <= 0:
                    raise ValueError("invalid segment index")
                assert_current_runtime_writable("重新生成分段视频")
                # A task may have been cancelled directly in RunningHub after
                # this browser loaded.  When RunningHub is the chosen backend,
                # reconcile it before the edit/save path applies its active-task
                # protection.
                if (channel_id or str(CONFIG.get("video_submit_backend") or "")).lower() == "runninghub":
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
                    has_full_prompt = "full_prompt" in (payload or {})
                    reference_roles = [str(role) for role in (payload or {}).get("reference_roles") or []] if has_reference_roles else None
                    reference_image_ids = dict((payload or {}).get("reference_image_ids") or {}) if has_reference_image_ids else None
                    scene_id = str((payload or {}).get("scene_id") or "") if has_scene_id else None
                    if director_prompt or has_reference_roles or has_reference_image_ids or has_scene_id or has_full_prompt:
                        await run_in_threadpool(save_segment_director_prompt,
                            segment_index,
                            director_prompt,
                            reference_roles=reference_roles,
                            reference_image_ids=reference_image_ids,
                            scene_id=scene_id,
                            full_prompt_override=str((payload or {}).get("full_prompt") or "") if has_full_prompt else None,
                            build_workflow=uses_workflow,
                        )
                elif uses_workflow:
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
                    # Saved submission locally rebuilds the model's workflow
                    # when needed. Regenerating a video must never replan its
                    # screenplay or extract scene state through an LLM.
                log(f"[generation] API regenerate video part_{segment_index:03d} channel={channel_id} model={model_id}", "STEP")
                result = await run_in_threadpool(service.submit_saved_segments, {segment_index}, wait=False, channel_id=channel_id)
                from ui.group_panel import render_segment_panel

                return JSONResponse(
                    {
                        "ok": True,
                        "video_jobs": result,
                        "web_video_task_id": str(next((item.get("task_id") for item in result.get("segments") or []
                            if isinstance(item, dict) and int(item.get("segment_index") or 0) == segment_index), "") or "")
                            if not uses_workflow else "",
                        "segment_panel_html": render_segment_panel(result, segment_index),
                        "log_tail": get_logs(),
                    }
                )
            except Exception as exc:
                log(f"[LICON_MSR][api_regen][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=500)

        @app.post("/api/segment/save_prompt")
        @_pinned_segment_route
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
                    result = await run_in_threadpool(get_generation_service().update_segment_generation_config,
                        segment_index,
                        prompt_override=full_prompt if full_prompt is not None else director_prompt,
                    )
                else:
                    has_roles = isinstance((payload or {}).get("reference_roles"), list)
                    has_images = isinstance((payload or {}).get("reference_image_ids"), dict)
                    has_scene = "scene_id" in (payload or {})
                    result = await run_in_threadpool(save_segment_director_prompt,
                        segment_index,
                        director_prompt,
                        reference_roles=[str(role) for role in (payload or {}).get("reference_roles") or []] if has_roles else None,
                        reference_image_ids=dict((payload or {}).get("reference_image_ids") or {}) if has_images else None,
                        scene_id=str((payload or {}).get("scene_id") or "") if has_scene else None,
                        full_prompt_override=full_prompt,
                        build_workflow=(payload or {}).get("build_workflow") is not False,
                    )
                return JSONResponse({"ok": True, "video_jobs": result, "log_tail": get_logs()})
            except Exception as exc:
                log(f"[LICON_MSR][api_save_prompt][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc), "log_tail": get_logs()}, status_code=400)

        @app.post("/api/segment/source-excerpt")
        @_pinned_segment_route
        async def api_save_segment_source_excerpt(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
                from services.context import assert_current_runtime_writable
                import json

                assert_current_runtime_writable("保存分段剧情上下文")
                index = int(float((payload or {}).get("segment_index") or 0))
                excerpt = str((payload or {}).get("source_excerpt") or "")
                if index <= 0:
                    raise ValueError("invalid segment index")
                if (payload or {}).get("regenerate_prompt") is True:
                    with video_jobs_lock():
                        jobs = load_video_jobs()
                        segment = next((item for item in jobs.get("segments") or []
                                        if isinstance(item, dict) and int(item.get("segment_index") or 0) == index), None)
                        if segment is None:
                            raise ValueError(f"segment not found: {index}")
                        beats_data = json.loads((get_project_dir() / "beats.json").read_text(encoding="utf-8-sig"))
                        beats = beats_data.get("beats") if isinstance(beats_data.get("beats"), list) else []
                        uid = str(segment.get("beat_uid") or "")
                        beat = next((item for item in beats if isinstance(item, dict) and uid and str(item.get("beat_uid") or "") == uid), None)
                        if beat is None and 0 < index <= len(beats):
                            beat = beats[index - 1]
                        if not isinstance(beat, dict):
                            raise ValueError("找不到分段对应的 Beat")
                    from services.text_jobs import submit
                    from services.web_llm_bridge import validate_routes
                    from generation.pipelines.minimax_h3.planner import H3SegmentPlanner
                    requested_duration = payload.get('duration_sec')
                    if requested_duration is None:
                        raise ValueError('页面未提交本段时长，请保留编辑内容后强制刷新页面（Ctrl+F5），重新打开本段再试')
                    if isinstance(requested_duration, bool):
                        raise ValueError('本段参考时长请输入正数')
                    try:
                        from generation.pipelines.minimax_h3.planning_drafts import positive_duration
                        positive_duration(requested_duration)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError('本段参考时长请输入正数') from exc
                    if 'providers' not in payload:
                        raise ValueError('请先勾选本页面使用的 LLM')
                    timing_api = payload.get('timing_api', False)
                    if type(timing_api) is not bool:
                        raise ValueError('规划时长 API 选项必须为布尔值')
                    providers = validate_routes(payload['providers'], inherit=True, allow_empty=timing_api)
                    if 'api' in providers:
                        raise ValueError('API 请使用“仅规划时长”复选框，不要加入网页 LLM 列表')
                    editor_prompt = str(payload.get('editor_prompt') or '')
                    if not editor_prompt.strip():
                        raise ValueError('页面未提交编辑区提示词，请保留编辑内容并刷新页面后重试')
                    options = {'duration_sec': positive_duration(requested_duration), 'providers': providers,
                               'timing_only': True, 'timing_api': timing_api, 'editor_prompt': editor_prompt}
                    if 'asset_manifest' in payload:
                        if not isinstance(payload['asset_manifest'], list):
                            raise ValueError('页面参考素材格式不正确')
                        options['asset_manifest'] = payload['asset_manifest']
                    task = submit('source_replan', [index, excerpt, options], (payload or {}).get('editors') or {})
                    return JSONResponse({'ok': True, 'source_excerpt': excerpt,
                                         'operation_id': task['id'], 'message': f'已提交重新规划时长（页面参考 {options["duration_sec"]} 秒）；仅调整现有提示词中的时间，完成后同步对应 Beat。'})
                with video_jobs_lock():
                    jobs = load_video_jobs()
                    segment = next((item for item in jobs.get("segments") or []
                                    if isinstance(item, dict) and int(item.get("segment_index") or 0) == index), None)
                    if segment is None:
                        raise ValueError(f"segment not found: {index}")
                    beats_path = get_project_dir() / "beats.json"
                    beats_data = json.loads(beats_path.read_text(encoding="utf-8-sig"))
                    beats = beats_data.get("beats") if isinstance(beats_data.get("beats"), list) else []
                    uid = str(segment.get("beat_uid") or "")
                    beat = next((item for item in beats if isinstance(item, dict) and uid and str(item.get("beat_uid") or "") == uid), None)
                    if beat is None and 0 < index <= len(beats):
                        beat = beats[index - 1]
                    if not isinstance(beat, dict):
                        raise ValueError("找不到分段对应的 Beat")
                    beat["source_excerpt"] = excerpt
                    segment["source_excerpt"] = excerpt
                    beats_path.write_text(json.dumps(beats_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    save_video_jobs(jobs)
                    config = segment.get("generation_config") if isinstance(segment.get("generation_config"), dict) else {}
                return JSONResponse({"ok": True, "source_excerpt": excerpt,
                                     "canonical_prompt": str(config.get("prompt_override") or ""),
                                     "video_jobs": jobs})
            except Exception as exc:
                log(f"[generation][api_source_excerpt][error] {exc}", "ERROR")
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        @app.post("/api/segment/model")
        @_pinned_segment_route
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
                result = await run_in_threadpool(get_generation_service().set_segment_model_override,
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

        @app.post('/api/scene-batches/{action}')
        @_pinned_segment_route
        async def api_scene_batches(action: str, request: Request):
            from services import scene_video_batches as batches
            try:
                payload = await request.json()
                if action == 'catalog': result = await run_in_threadpool(batches.catalog)
                elif action == 'start': result = await run_in_threadpool(batches.start, payload)
                elif action == 'status': result = await run_in_threadpool(batches.status, payload['id'])
                elif action == 'resume': result = await run_in_threadpool(batches.resume, payload['id'], payload.get('modes'))
                else: raise ValueError('未知场景批次操作')
                return JSONResponse({'ok': True, **result})
            except Exception as exc:
                return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

        @app.post('/api/segment/editor/{action}')
        @_pinned_segment_route
        async def api_editor_operation(action: str, request: Request):
            try:
                from generation.pipelines.minimax_h3 import editor_draft as edits
                from generation.pipelines.minimax_h3.continuity import resolve_source_snapshot
                from workflow.video_merge import load_video_jobs
                payload=await request.json()
                if action=='snapshot':
                    jobs=load_video_jobs();target=edits.locate(jobs,payload['segment_uid'])
                    index=jobs['segments'].index(target)
                    prior=jobs['segments'][index-1] if index else {}
                    result={**edits.saved_baseline(jobs,target),
                        'source_title':prior.get('title') or '无上一段',
                        'source_options':[{'render_id':r['render_id'],'label':str(r.get('created_at') or '')+' · '+r['render_id'][-8:]} for r in prior.get('renders',[]) if r.get('prompt_snapshot')]}
                    from generation.pipelines.minimax_h3.continuity.display import scene_display_contexts
                    result['scene_contexts']=scene_display_contexts(jobs)
                    from generation.pipelines.minimax_h3.continuity.constraints import DEFAULT
                    result['default_spatial_constraints']=DEFAULT
                    result['context_id']=target.get('scene_context_id')
                    first_scene_segment=next((s for s in jobs['segments']
                        if target.get('scene_context_id') and s.get('scene_context_id')==target['scene_context_id']),None)
                    result['scene_first_segment']=({k:first_scene_segment.get(k) for k in
                        ('segment_uid','segment_index','part_id','title')} if first_scene_segment else None)
                    result['scene_has_predecessor']=bool(target.get('scene_context_id') and any(
                        s.get('scene_context_id')==target['scene_context_id'] for s in jobs['segments'][:index]))
                    result['scene_initial_pending']=bool((result['scene_contexts'].get(target.get('scene_context_id')) or {}).get('initial_pending'))
                    result['scene_record']=target.get('h3_continuity') or {}
                    from generation.pipelines.minimax_h3.continuity.display import framing_display_snapshot
                    result.update(framing_display_snapshot(jobs,target))
                    start_uid=((result['scene_record'].get('config') or {}).get('start_segment_uid'))
                    result['scene_start_index']=next((i+1 for i,s in enumerate(jobs['segments']) if s['segment_uid']==start_uid),1)
                    from generation.pipelines.minimax_h3.continuity.calibration import calibration_snapshot, scene_members
                    result.update(calibration_snapshot(jobs,target))
                    result['scene_members']=scene_members(jobs)
                    from generation.pipelines.minimax_h3.continuity.state import initial_from_beat
                    beats_data=json.loads((get_project_dir()/'beats.json').read_text(encoding='utf-8-sig'))
                    beat=next((b for b in beats_data.get('beats',[]) if b.get('beat_uid')==target.get('beat_uid')),{})
                    result['initial_suggestion']=initial_from_beat(beat,target)
                    manifest=(target.get('generation_config') or {}).get('asset_manifest',[])
                    result['suggested_scene_name']=(result['scene_contexts'].get(target.get('scene_context_id')) or {}).get('display_name') or next((a.get('role') or a.get('label') for a in manifest
                        if (a.get('metadata') or {}).get('asset_kind')=='background'),None) or beat.get('title') or '新场景'
                elif action=='optimization-event':
                    from generation.pipelines.minimax_h3.optimization_history import event
                    result={'optimization_status':await run_in_threadpool(event,payload['segment_uid'],payload['stage'],
                        attempt_id=payload.get('attempt_id'),message=payload.get('message'))}
                elif action=='restore-baseline':
                    from generation.pipelines.minimax_h3.prompt_baseline import restore
                    result=await run_in_threadpool(restore,payload)
                elif action=='preview': result=await run_in_threadpool(edits.preview,payload)
                elif action=='commit':
                    result=await run_in_threadpool(edits.commit,payload)
                    from ui.group_panel import render_segment_panel
                    jobs=load_video_jobs();segment=edits.locate(jobs,payload['segment_uid'])
                    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
                    result['state_status']=state_status(jobs,payload['segment_uid'])
                    from generation.pipelines.minimax_h3.optimization_history import status
                    result['optimization_status']=status(segment)
                    from generation.pipelines.minimax_h3.continuity.display import framing_display_snapshot
                    result.update(framing_display_snapshot(jobs,segment))
                    result['groups_html']=render_segment_panel(jobs,segment['segment_index'])
                elif action=='scene-config':
                    import uuid
                    from workflow.video_merge import video_jobs_lock,save_video_jobs
                    from generation.pipelines.minimax_h3.continuity.ledger import state_before
                    # Manual calibration is a declaration, not an unconfirmed
                    # automatic suggestion awaiting an explicit extraction.
                    if isinstance(payload.get('initial_state'),dict) and payload.get('automatic') is not True:
                        for role,facts in payload['initial_state'].items():
                            present=(facts.get('present') or {}).get('value') if isinstance(facts,dict) else None
                            if type(present) is not bool:
                                raise ValueError('请明确选择人物 '+str(role)+' 在场或不在场')
                    with video_jobs_lock():
                        jobs=load_video_jobs();target=edits.locate(jobs,payload['segment_uid'])
                        old_key=target.get('scene_context_id')
                        key=payload.get('context_id') or None
                        contexts=jobs.setdefault('h3_scene_continuity',{'schema_version':3,'contexts':{}})['contexts']
                        if payload.get('name'):
                            key='scene_'+uuid.uuid4().hex
                            state=payload.get('initial_state')
                            if not isinstance(state,dict) or not state:raise ValueError('请选择初始人物并填写状态')
                            from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan
                            state=reduce_plan({role:{} for role in state},{'shots':[{'shot_uid':'initial','world_events':[{'role_id':role,'facts':facts} for role,facts in state.items()]}]},'initial',list(state))
                            contexts[key]={'name':payload['name'],'initial_state':state,'initial_pending':bool(payload.get('automatic',False)),
                                           'config_revision':uuid.uuid4().hex}
                        if key and (key not in contexts or 'initial_state' not in contexts[key]):raise ValueError('场景未初始化')
                        if key and not payload.get('name') and 'initial_state' in payload:
                            from generation.pipelines.minimax_h3.continuity.calibration import save_initial_calibration
                            save_initial_calibration(contexts[key],payload['initial_state'],payload)
                        if key:
                            from generation.pipelines.minimax_h3.continuity.constraints import save_constraints, save_scene_layout
                            save_constraints(contexts[key],payload)
                            save_scene_layout(contexts[key],payload,(target.get('generation_config') or {}).get('asset_manifest',[]))
                        if target.get('scene_context_id')!=key:
                            target.pop('h3_continuity',None)
                            target['h3_state_mode']='unset'
                        target['scene_context_id']=key
                        save_video_jobs(jobs)
                    from generation.pipelines.minimax_h3.continuity.inheritance import state_status
                    result={'context_id':key, 'state_task':None, 'state_status':state_status(jobs,payload['segment_uid'])}
                elif action=='delete-scene':
                    from generation.pipelines.minimax_h3.continuity.calibration import delete_unused_scene
                    result=await run_in_threadpool(delete_unused_scene,payload)
                elif action=='inherit-state':
                    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
                    status=await run_in_threadpool(inherit_segment_state,payload['segment_uid'])
                    result={'state_status':status}
                elif action=='extract-state':
                    from services.scene_state_jobs import request_scene_state_refresh
                    task=await run_in_threadpool(request_scene_state_refresh,payload['segment_uid'],reason='extract',source_kind='draft',providers=payload.get('providers'))
                    result={'state_task':task}
                elif action=='scene-state':
                    from services.scene_state_jobs import active_scene_state_task
                    # Legacy page start flags are read-only; only extract-state
                    # authorizes a new LLM request.
                    result={'state_task':active_scene_state_task(payload['segment_uid'],source_kind='draft')}
                elif action=='state-result':
                    from services.text_jobs import get
                    try:
                        task=get(payload['operation_id'])
                    except ValueError as exc:
                        if str(exc)!='任务不存在':raise
                        return JSONResponse({'ok':False,'code':'OPERATION_NOT_FOUND',
                            'message':'原状态提取任务已清空或删除，请重新提取本段状态。'},status_code=404)
                    if task['kind']!='scene_state' or Path(task['source']).resolve()!=get_project_dir().resolve():
                        raise ValueError('状态任务不属于当前项目')
                    result={'state':task['state'],'message':task.get('message') or task.get('phase')}
                    if task.get('children'):
                        from services.web_llm_bridge import task_status
                        try:
                            child=task_status(task['children'][-1],current_runtime=False)
                            result['web_task']={k:child.get(k) for k in ('provider','status','message')}
                        except ValueError:
                            pass
                elif action=='continuity':
                    from services.text_jobs import submit
                    if payload.get('operation_contract')!='scene_state_v3':raise ValueError('旧承接入口已停用，请刷新页面')
                    from workflow.video_merge import video_jobs_lock
                    with video_jobs_lock():
                        payload['jobs']=load_video_jobs()
                    from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
                    read_ready_context(payload['jobs'],payload['segment_uid'],target_source='draft')
                    task=await run_in_threadpool(submit,'opening_continuity',[payload],page_providers=payload.get('providers'))
                    if payload.get('optimization_attempt_id'):
                        from generation.pipelines.minimax_h3.optimization_history import event
                        await run_in_threadpool(event,payload['segment_uid'],'submitted',
                            attempt_id=payload['optimization_attempt_id'],operation_id=task['id'])
                    result={'operation_id':task['id']}
                elif action=='result':
                    from services.text_jobs import get
                    task=get(payload['operation_id'])
                    if task['kind']!='opening_continuity' or Path(task['source']).resolve()!=get_project_dir().resolve(): raise ValueError('任务不属于当前项目')
                    message = task.get('phase') or task.get('message')
                    if task['state'] in ('error','needs_attention','cancelled'):
                        message = task.get('message') or message
                    result={'state':task['state'],'candidate':task.get('result'),'message':message}
                else: raise ValueError('未知编辑操作')
                return JSONResponse({'ok':True,**result})
            except Exception as exc:
                code = getattr(exc, 'code', None)
                details = getattr(exc, 'details', {})
                if callable(details):
                    details = details()
                if not isinstance(details, dict):
                    details = {}
                if code:
                    log(f'[editor][conflict] action={action} code={code} details={details}', 'WARNING')
                else:
                    log(f'[editor][error] action={action}\n{traceback.format_exc()}', 'ERROR')
                return JSONResponse({'ok':False,'message':str(exc),'code':code,'details':details},status_code=409 if code else 400)

        @app.post("/api/segment/prompt-preview")
        @_pinned_segment_route
        async def api_preview_segment_prompt(request: Request):
            try:
                from generation.service import get_generation_service
                payload = await request.json()
                result = await run_in_threadpool(get_generation_service().preview_segment_prompt,
                    int(payload.get("segment_index") or 0), str(payload.get("prompt_override") or ""),
                    payload.get("asset_manifest"), force_save=payload.get("force_save") is True,
                    prompt_provenance=payload.get("prompt_provenance"))
                return JSONResponse({"ok": True, **result})
            except Exception as exc:
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        @app.post("/api/segment/shot-editor/preview")
        @_pinned_segment_route
        async def api_preview_shot_editor(request: Request):
            try:
                from generation.service import get_generation_service
                payload = await request.json()
                result = await run_in_threadpool(get_generation_service().preview_shot_edits, payload)
                return JSONResponse({'ok': True, **result})
            except Exception as exc:
                return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

        @app.post("/api/segment/shot-editor/commit")
        @_pinned_segment_route
        async def api_commit_shot_editor(request: Request):
            try:
                from generation.service import get_generation_service
                from workflow.video_merge import load_video_jobs
                from ui.group_panel import render_groups_panel
                assert_current_runtime_writable('保存 Shot 编辑')
                payload = await request.json()
                result = await run_in_threadpool(get_generation_service().commit_shot_edits, payload)
                from ui.project_handlers import beats_json_to_display
                result['beats_display'] = beats_json_to_display(result['beats_text'])
                result['groups_html'] = render_groups_panel(load_video_jobs())
                return JSONResponse({'ok': True, **result})
            except Exception as exc:
                return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)

        @app.post("/api/segment/generation-config")
        @_pinned_segment_route
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
                result = await run_in_threadpool(get_generation_service().update_segment_generation_config,
                    segment_index,
                    generation_mode=str((payload or {}).get("generation_mode") or "ref2va"),
                    asset_manifest=manifest,
                    prompt_override=str(payload.get("prompt_override") or "") if "prompt_override" in payload else None,
                    prompt_provenance=(payload or {}).get("prompt_provenance") if isinstance((payload or {}).get("prompt_provenance"), dict) else None,
                    force_save=(payload or {}).get("force_save") is True,
                    base_revision=(payload or {}).get('base_revision'),
                    segment_uid=(payload or {}).get('segment_uid'),
                )
                segment = next(
                    (item for item in result.get("segments") or [] if isinstance(item, dict) and int(item.get("segment_index") or 0) == segment_index),
                    None,
                )
                config = segment.get("generation_config") if isinstance(segment, dict) and isinstance(segment.get("generation_config"), dict) else {}
                scene_operation_id=None
                from generation.job_schema import resolved_model_id
                pipeline = get_generation_service()._pipeline_for(resolved_model_id(segment, result))
                from generation.pipelines.minimax_h3.editor_draft import reference_revision, saved_baseline
                from generation.pipelines.minimax_h3.continuity.inheritance import state_status
                return JSONResponse({
                    "ok": True,
                    "video_jobs": result,
                    "generation_config": config,
                    "editor_snapshot": saved_baseline(result, segment),
                    "scene_operation_id": scene_operation_id,
                    "state_status": state_status(result,segment['segment_uid']),
                    "base_revision": reference_revision(segment),
                    "canonical_prompt": str(config.get("prompt_override") or ""),
                    "prompt_chars": len(str(config.get("prompt_override") or "")),
                    "prompt_max_chars": pipeline.spec.capabilities.prompt_max_chars,
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
        @_pinned_segment_route
        async def api_set_segment_video_ok(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from workflow.segment_runner import set_segment_video_ok
                from ui.group_panel import render_segment_panel

                # Approval metadata and learning are allowed for archived episodes.
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                if segment_index <= 0:
                    raise ValueError("invalid segment index")
                result = set_segment_video_ok(segment_index, bool((payload or {}).get("video_ok")))
                from services.video_experience import on_video_ok
                try:
                    learning = await run_in_threadpool(on_video_ok, segment_index, result, bool(payload.get('video_ok')))
                except Exception as exc:
                    log(f'[video_learning] OK 标记已保留，分析未启动：{exc}', 'WARN')
                    learning = {'state':'error', 'message':f'视频 OK 标记已保存；经验分析未启动：{exc}'}
                return JSONResponse({
                    "ok": True,
                    "video_jobs": result,
                    "learning": learning,
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

        @app.post("/api/segment/reset_queued_task")
        async def api_reset_queued_task(request: Request):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            try:
                from services import web_video_bridge

                assert_current_runtime_writable("重置排队任务")
                segment_index = int(float((payload or {}).get("segment_index") or 0))
                segment_uid = str((payload or {}).get("segment_uid") or "").strip()
                if segment_index <= 0:
                    raise ValueError("segment_index 不能为空")
                result = web_video_bridge.reset_queued_segment(segment_index, segment_uid)
                return JSONResponse({"ok": True, "video_jobs": result, "log_tail": get_logs()})
            except Exception as exc:
                log(f"[api_reset_queued_task][error] {exc}", "ERROR")
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
