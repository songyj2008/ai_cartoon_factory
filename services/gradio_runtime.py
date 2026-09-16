"""Runtime guards for Gradio when launched from the packaged desktop app."""
from __future__ import annotations

import asyncio


def ensure_asyncio_event_loop() -> asyncio.AbstractEventLoop:
    """Ensure the current thread has an asyncio event loop."""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def ensure_gradio_app_runtime(demo) -> None:
    """Repair Gradio ASGI primitives that can be None in frozen thread startup."""
    ensure_asyncio_event_loop()
    app = getattr(demo, "app", None)
    if app is None:
        return
    if getattr(app, "stop_event", None) is None:
        app.stop_event = asyncio.Event()
    if getattr(app, "lock", None) is None:
        app.lock = asyncio.Lock()
