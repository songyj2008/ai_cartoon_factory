"""Task-scoped Runow preparation heartbeat, using the paired extension auth."""
from fastapi import Request

from services import runow_web
from ui.web_llm_routes import _error, _extension_auth


def register_runow_web_routes(app):
    @app.post('/api/runow-web/tasks/{task_id}/progress')
    async def progress(task_id: str, request: Request):
        try:
            _extension_auth(request)
            data = await request.json()
            runow_web.progress(task_id, str(data.get('lease') or ''))
            return {'ok': True}
        except Exception as exc:
            return _error(exc)
