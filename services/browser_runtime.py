"""Bind Gradio callbacks and custom API calls to the same browser session."""
import inspect
from functools import wraps
from urllib.parse import unquote

from services import context


def bind_demo(demo):
    if getattr(demo, '_aicf_browser_runtime_bound', False) or not hasattr(demo, 'process_api'):
        return
    original = demo.process_api
    signature = inspect.signature(original)

    @wraps(original)
    async def process(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        session = bound.arguments.get('session_hash')
        with context.browser_session(session), context.pin_runtime():
            return await original(*args, **kwargs)

    demo.process_api = process
    demo._aicf_browser_runtime_bound = True


class BrowserRuntimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/'):
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        from pathlib import Path
        headers = dict(scope.get('headers', []))
        session = headers.get(b'x-aicf-session', b'').decode('ascii', errors='ignore')
        with context.browser_session(session), context.pin_runtime():
            expected = headers.get(b'x-aicf-runtime')
            if expected:
                try:
                    value = expected.decode('utf-8')
                    if headers.get(b'x-aicf-runtime-encoding') == b'uri':
                        value = unquote(value, encoding='utf-8', errors='strict')
                    if Path(value).resolve() != context.get_project_dir().resolve():
                        response = JSONResponse({'ok': False, 'message': '当前窗口的项目或草稿已切换，旧页面请求已停止；请刷新后重试。'}, status_code=409)
                        return await response(scope, receive, send)
                except (UnicodeError, ValueError):
                    return await JSONResponse({'ok': False, 'message': '项目路径编码无效'}, status_code=400)(scope, receive, send)
            return await self.app(scope, receive, send)


def bind_api(app):
    if getattr(app.state, 'aicf_browser_runtime_bound', False):
        return
    if app.middleware_stack is None:
        app.add_middleware(BrowserRuntimeMiddleware)
    else:
        # Gradio creates the serving app during launch; routes are attached
        # afterwards, when Starlette's add_middleware is already unavailable.
        app.middleware_stack = BrowserRuntimeMiddleware(app.middleware_stack)
    app.state.aicf_browser_runtime_bound = True
