"""Gradio application assembly."""
from ui.bindings import bind_events
from ui.layout import build_layout


def build_app():
    demo, components = build_layout()
    with demo:
        bind_events(demo, components)
    return demo
