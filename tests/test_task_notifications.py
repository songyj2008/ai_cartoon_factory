import asyncio
import threading
from unittest.mock import patch
from fastapi import FastAPI
from services.task_notifications import ChangeSignal, operations
from ui.web_llm_routes import register_web_llm_routes


def test_publish_before_wait_is_not_lost():
    signal = ChangeSignal()
    revision = signal.revision()
    signal.publish()
    assert signal.wait(revision, 0) == revision + 1


def test_publish_wakes_waiter():
    signal = ChangeSignal()
    revision = signal.revision()
    result = []
    thread = threading.Thread(target=lambda: result.append(signal.wait(revision, 2)))
    thread.start()
    signal.publish()
    thread.join(1)
    assert result == [revision + 1]


def test_sse_initial_resync_change_and_disconnect():
    app = FastAPI()
    with patch('services.text_jobs.ensure_worker'):
        register_web_llm_routes(app)
    endpoint = next(route.endpoint for route in app.routes if route.path == '/api/web-llm/operations/events')
    class Request:
        url = 'http://testserver/api/web-llm/operations/events'
        headers = {}
        disconnected = False
        async def is_disconnected(self):
            return self.disconnected
    async def run():
        request = Request()
        response = await endpoint(request)
        stream = response.body_iterator
        assert 'event: changed' in await anext(stream)
        with patch.object(operations, 'wait', side_effect=lambda revision, timeout: revision + 1):
            assert 'event: changed' in await anext(stream)
        request.disconnected = True
        try:
            await anext(stream)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError('Stream did not close')
    asyncio.run(run())
