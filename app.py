"""AI Cartoon Factory application entry point."""
import os
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

from services.gradio_runtime import ensure_asyncio_event_loop, ensure_gradio_app_runtime

ensure_asyncio_event_loop()

import gradio as gr

from asset_index import configure_asset_index
from services.context import (
    ASSET_INDEX_PATH,
    ASSETS_DIR,
    BASE_DIR,
    CONFIG,
    get_project_dir,
)
from services.file_utils import ensure_project_dirs
from services.logger import log
from services.brand import load_brand_info
from services.windows_runtime_guard import install_windows_signal_guard
from ui.api_routes import register_api_routes
from ui.app_ui import build_app
from ui.scripts import APP_JS_FUNCTION
from ui.styles import CSS


def configure_services():
    configure_asset_index(BASE_DIR, ASSETS_DIR, ASSET_INDEX_PATH, log)
    ensure_project_dirs()
    brand = load_brand_info()
    if brand.get("signature_valid"):
        log(f"[brand] official info verified: {brand.get('brand') or ''}", "STEP")
    else:
        log(f"[brand][warn] {brand.get('status_text') or 'official info signature verification failed'}", "WARN")


def allowed_paths():
    comfy_input = Path(CONFIG.get("comfy_input_dir") or "../ComfyUI/input")
    comfy_output = Path(CONFIG.get("comfy_output_dir") or "../ComfyUI/output")
    if not comfy_input.is_absolute():
        comfy_input = (BASE_DIR / comfy_input).resolve()
    if not comfy_output.is_absolute():
        comfy_output = (BASE_DIR / comfy_output).resolve()
    return [
        str(BASE_DIR.resolve()),
        str(comfy_input),
        str(comfy_input.parent),
        str(comfy_output),
        str(comfy_output.parent),
    ]


def create_app():
    configure_services()
    app_demo = build_app()
    ensure_gradio_app_runtime(app_demo)
    return app_demo


demo = create_app()


def ui_launch_kwargs():
    return {
        "css": CSS,
        "js": APP_JS_FUNCTION,
        "theme": gr.themes.Base(),
    }


def main():
    install_windows_signal_guard()
    ensure_gradio_app_runtime(demo)
    demo.queue()
    server_port = int(os.environ.get("AI_CARTOON_FACTORY_PORT") or CONFIG.get("server_port", 7860))
    demo.launch(
        server_name=os.environ.get("AI_CARTOON_FACTORY_HOST", "127.0.0.1"),
        server_port=server_port,
        allowed_paths=allowed_paths(),
        prevent_thread_lock=True,
        **ui_launch_kwargs(),
    )
    # Gradio 6 creates its real FastAPI app during launch(). Register custom
    # routes afterwards so they are attached to the serving app, not a
    # temporary pre-launch instance.
    register_api_routes(demo)
    ensure_gradio_app_runtime(demo)
    from services.runninghub_worker import runninghub_worker
    runninghub_worker.start()
    try:
        demo.block_thread()
    finally:
        runninghub_worker.stop()


if __name__ == "__main__":
    main()
