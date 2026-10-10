"""HTTP boundary for local UI and the explicitly paired Chrome extension."""
from __future__ import annotations

import asyncio
import secrets
import uuid
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from services import web_llm_bridge as bridge
from services.context import assert_current_runtime_writable, pin_runtime


def _ui_origin(request):
    host = urlsplit(str(request.url)).hostname
    if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        raise PermissionError("浏览器扩展配置仅允许从本机访问")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc):
        raise PermissionError("不允许跨站访问扩展配置或任务")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise PermissionError("不允许跨站请求")


def _extension_auth(request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not supplied or not secrets.compare_digest(supplied, bridge.get_settings()["token"]):
        raise PermissionError("扩展配对码不正确，请重新配对")


def _error(exc):
    return JSONResponse({"ok": False, "message": str(exc)}, status_code=403 if isinstance(exc, PermissionError) else 400)


def register_web_llm_routes(app):
    from services import text_jobs
    # Gradio's wrapped App does not expose FastAPI's event registration API.
    # Start recovery while routes are registered, before the server begins
    # accepting requests; the worker is idempotent within this process.
    text_jobs.ensure_worker()

    @app.get('/api/web-llm/operations')
    def operations(request: Request):
        try:
            _ui_origin(request)
            return {'ok':True,'tasks':text_jobs.list_jobs()}
        except Exception as exc:
            return _error(exc)

    @app.get('/api/web-llm/operations/events')
    async def operation_events(request: Request):
        try:
            _ui_origin(request)
        except Exception as exc:
            return _error(exc)
        from services.task_notifications import operations as signal
        async def events():
            revision = signal.revision()
            yield 'event: changed\ndata: {}\n\n'
            while not await request.is_disconnected():
                updated = await asyncio.to_thread(signal.wait, revision, 15)
                if updated != revision:
                    revision = updated
                    yield 'event: changed\ndata: {}\n\n'
                else:
                    yield ': heartbeat\n\n'
        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control':'no-cache', 'X-Accel-Buffering':'no'})

    @app.get('/api/web-llm/operations/{identifier}')
    def operation_result(identifier: str, request: Request):
        try:
            _ui_origin(request)
            return {'ok':True,'result':text_jobs.result(identifier)}
        except Exception as exc:
            return _error(exc)

    @app.delete('/api/web-llm/operations/{identifier}')
    def delete_operation(identifier: str, request: Request):
        try:
            _ui_origin(request)
            with pin_runtime():
                result = text_jobs.delete(identifier)
            return {'ok':True,**result}
        except Exception as exc:
            return _error(exc)

    @app.post('/api/web-llm/operations/clear')
    async def clear_finished_operations(request: Request):
        try:
            _ui_origin(request)
            payload = await request.json()
            if payload.get('confirm') is not True:
                raise ValueError('请确认清空已结束的文字任务')
            with pin_runtime():
                result = text_jobs.clear_finished()
            return {'ok':True,**result}
        except Exception as exc:
            return _error(exc)

    @app.post('/api/web-llm/operations/{identifier}/{action}')
    async def operation_action(identifier: str, action: str, request: Request):
        try:
            _ui_origin(request)
            payload = await request.json()
            with pin_runtime():
                if action == 'cancel':
                    item = text_jobs.cancel(identifier)
                elif action == 'apply':
                    try:
                        item = text_jobs.apply(identifier,payload.get('editors') or {})
                    except Exception as exc:
                        text_jobs.record_apply_error(identifier,str(exc))
                        raise
                elif action == 'retry':
                    old = text_jobs.get(identifier)
                    if old['state'] not in ('error','cancelled'):
                        raise ValueError('仅已失败或已取消的任务可以重新提交')
                    if old['kind'] == 'revision_wait':
                        raise ValueError('修正任务不可重发原网页请求；请从分段修正入口重新提交')
                    from pathlib import Path
                    with pin_runtime((Path(old['source']),old['project'],old['episode'])):
                        item = text_jobs.submit(old['kind'],old['args'],old['editors'])
                elif action == 'inspect':
                    item = text_jobs.inspect(identifier)
                else:
                    raise ValueError('未知任务操作')
            return {'ok':True,'id':item['id'],'state':item['state']}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-llm/settings")
    def settings(request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "settings": bridge.get_settings(), "providers": bridge.PROVIDERS,
                    "entries": bridge.ENTRIES, "connection": bridge.connection_status()}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/settings")
    async def save_settings(request: Request):
        try:
            _ui_origin(request)
            return {"ok": True, "settings": bridge.save_settings(await request.json())}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-llm/segments/{index}")
    def segment(index: int, request: Request):
        try:
            _ui_origin(request)
            with pin_runtime():
                web_task = bridge.latest_task(index)
                if web_task:
                    operation_id = str((web_task.get('metadata') or {}).get('operation_id') or '')
                    if operation_id:
                        try:
                            operation = text_jobs.get(operation_id)
                            web_task['business_operation'] = {'id':operation_id,'state':operation['state'],
                                                              'message':operation.get('message','')}
                        except ValueError:
                            pass
                return {"ok": True, "snapshot": bridge.segment_snapshot(index), "task": web_task}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/segments/{index}/duration")
    async def duration(index: int, request: Request):
        try:
            _ui_origin(request)
            payload = await request.json()
            with pin_runtime():
                assert_current_runtime_writable("修改分段时长")
                snapshot = bridge.segment_snapshot(index)
                if payload.get("runtime") != snapshot["runtime"] or payload.get("fingerprint") != snapshot["fingerprint"]:
                    raise ValueError("分段已变化，请刷新预览后重试")
                from generation.service import get_generation_service
                from starlette.concurrency import run_in_threadpool
                await run_in_threadpool(get_generation_service().update_segment_duration, index, payload.get("duration_sec"))
                return {"ok": True, "snapshot": bridge.segment_snapshot(index)}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/segments/{index}/revisions")
    async def revise(index: int, request: Request):
        try:
            _ui_origin(request)
            payload = await request.json()
            with pin_runtime():
                assert_current_runtime_writable("提交提示词修正")
                snapshot = bridge.segment_snapshot(index)
                if payload.get('runtime') != snapshot['runtime'] or payload.get('fingerprint') != snapshot['fingerprint']:
                    raise ValueError('项目、视频或保存的提示词已变化，请刷新预览后重新提交')
                selected_providers = bridge.routes_for('revision',payload.get('providers') or payload.get('provider') or 'inherit')
                if selected_providers == ['api']:
                    raise ValueError('视频反馈修正当前使用网页扩展，请选择 DeepSeek、ChatGPT 或豆包')
                operation = text_jobs.submit('revision_wait',[None,index,payload,snapshot],start=False,hold=True)
                is_duplicate = bool(operation.get('duplicate'))
                if is_duplicate:
                    if not operation.get('children'):
                        raise ValueError('相同输入的修正任务正在准备，请稍后查看任务列表')
                    web_task = bridge.task_status(operation['children'][0],current_runtime=False)
                else:
                    try:
                        text_jobs.update(operation['id'],provider=selected_providers[0],provider_candidates=selected_providers)
                        web_task = bridge.revision_task(index,payload,operation_id=operation['id'],
                                                        provider_override=selected_providers,
                                                        snapshot_override=snapshot)
                        text_jobs.attach_child(operation['id'],web_task['id'])
                        web_task = bridge.attach_operation(web_task['id'],operation['id'])
                        text_jobs.update(operation['id'],args=[web_task['id'],index,payload,snapshot],
                                         provider=web_task['provider'],provider_candidates=selected_providers)
                        text_jobs.release_prepared(operation['id'])
                    except Exception:
                        # A deterministic ID also covers failure after the web task
                        # was inserted but before its association was persisted.
                        child_id = str(uuid.uuid5(uuid.UUID(operation['id']),'revision'))
                        try:
                            bridge.task_status(child_id,current_runtime=False)
                        except ValueError:
                            child_id = ''
                        try:
                            if child_id:
                                text_jobs.attach_child(operation['id'],child_id)
                                bridge.cancel_task(child_id,current_runtime=False)
                        finally:
                            text_jobs.cancel(operation['id'])
                        if not child_id:
                            text_jobs.delete(operation['id'])
                        raise
                operation = text_jobs.get(operation['id'])
                web_task['business_operation'] = {'id':operation['id'],'state':operation['state'],
                                                  'message':operation.get('message','')}
                return {"ok": True, "task": web_task, "operation_id": operation['id']}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-llm/tasks/{identifier}")
    def task(identifier: str, request: Request):
        try:
            _ui_origin(request)
            value = bridge.task_status(identifier, current_runtime=True)
            operation_id = str((value.get('metadata') or {}).get('operation_id') or '')
            if operation_id:
                try:
                    operation = text_jobs.get(operation_id)
                    value['business_operation'] = {'id':operation_id,'state':operation['state'],
                                                   'message':operation.get('message','')}
                except ValueError:
                    pass
            return {"ok": True, "task": value}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/tasks/{identifier}/cancel")
    def cancel(identifier: str, request: Request):
        try:
            _ui_origin(request)
            value = bridge.task_status(identifier)
            operation_id = str((value.get('metadata') or {}).get('operation_id') or '')
            if operation_id:
                try:
                    operation = text_jobs.cancel(operation_id)
                    value = bridge.cancel_task(identifier,current_runtime=False)
                    value['business_operation'] = {'id':operation_id,'state':operation['state'],
                                                   'message':operation.get('message','')}
                except ValueError:
                    value = bridge.cancel_task(identifier)
            else:
                value = bridge.cancel_task(identifier)
            return {"ok": True, "task": value}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/bridge/ping")
    async def ping(request: Request):
        try:
            _extension_auth(request)
            payload = await request.json()
            bridge.heartbeat(str(payload.get("client_id") or ""), payload.get("providers") or [])
            return {"ok": True, **bridge.pool_status()}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/bridge/claim")
    async def claim(request: Request):
        try:
            _extension_auth(request)
            payload = await request.json()
            return {"ok": True, "task": bridge.claim(str(payload.get("client_id") or ""),
                                                      payload.get("providers") or [],tab_id=payload.get('tab_id'))}
        except Exception as exc:
            return _error(exc)

    @app.get("/api/web-llm/bridge/tasks/{identifier}")
    def bridge_task(identifier: str, request: Request):
        try:
            _extension_auth(request)
            return {"ok": True, "task": bridge.task_status(identifier)}
        except Exception as exc:
            return _error(exc)

    @app.post("/api/web-llm/bridge/tasks/{identifier}")
    async def report(identifier: str, request: Request):
        try:
            _extension_auth(request)
            payload = await request.json()
            return {"ok": True, "task": bridge.report(identifier, str(payload.get("lease") or ""), str(payload.get("status") or ""),
                      result=payload.get("result"), message=payload.get("message", ""), raw=payload.get("raw", ""),
                      diagnostics=payload.get("diagnostics"))}
        except Exception as exc:
            return _error(exc)
