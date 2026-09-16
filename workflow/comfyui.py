"""Small ComfyUI HTTP helpers for direct video generation."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests


VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def comfy_url() -> str:
    from services.context import CONFIG

    backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
    comfy_config = backends.get("comfyui") if isinstance(backends, dict) else {}
    url = (
        (comfy_config or {}).get("url")
        or CONFIG.get("comfyui_url")
        or CONFIG.get("comfy_url")
        or "http://127.0.0.1:8188"
    )
    return str(url).rstrip("/")


def comfy_output_dir() -> Path:
    from services.context import BASE_DIR, CONFIG

    backends = CONFIG.get("submit_backends") if isinstance(CONFIG.get("submit_backends"), dict) else {}
    comfy_config = backends.get("comfyui") if isinstance(backends, dict) else {}
    out = (comfy_config or {}).get("output_dir") or CONFIG.get("comfy_output_dir")
    if out:
        path = Path(out)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    inp = Path((comfy_config or {}).get("input_dir") or CONFIG.get("comfy_input_dir") or "../ComfyUI/input")
    if not inp.is_absolute():
        inp = (BASE_DIR / inp).resolve()
    return inp.parent / "output"


def wait_for_prompt(prompt_id: str, poll_seconds: float = 5.0, timeout_seconds: int = 24 * 3600) -> dict[str, Any]:
    from services.logger import log

    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        raise ValueError("ComfyUI prompt_id is empty")
    started = time.time()
    url = comfy_url()
    log(f"[comfyui] waiting for prompt_id={prompt_id}", "STEP")
    while True:
        if time.time() - started > timeout_seconds:
            raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")
        try:
            response = requests.get(f"{url}/history/{prompt_id}", timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and prompt_id in data:
                return data[prompt_id]
            if isinstance(data, dict) and data.get("outputs"):
                return data
        except requests.RequestException as exc:
            log(f"[comfyui][warn] history poll failed: {exc}", "WARN")
        time.sleep(float(poll_seconds or 5.0))


def extract_output_filenames(history: dict[str, Any], extensions: set[str] | None = None) -> list[str]:
    extensions = {ext.lower() for ext in (extensions or VIDEO_EXTS)}
    found: list[str] = []

    def add_file(payload: dict[str, Any]) -> None:
        filename = str(payload.get("filename") or payload.get("name") or "").strip()
        if not filename:
            return
        if Path(filename).suffix.lower() not in extensions:
            return
        subfolder = str(payload.get("subfolder") or "").strip().replace("\\", "/")
        rel = f"{subfolder}/{filename}" if subfolder else filename
        rel = rel.replace("\\", "/")
        if rel not in found:
            found.append(rel)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("gifs", "videos", "images", "files"):
                items = value.get(key)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            add_file(item)
                        elif isinstance(item, str):
                            add_file({"filename": item})
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(history or {})
    return found


def locate_output_video(history: dict[str, Any], filename_prefix: str = "") -> Path | None:
    root = comfy_output_dir()
    prefix = str(filename_prefix or "").replace("\\", "/").strip()
    candidates: list[Path] = []
    for rel in extract_output_filenames(history):
        rel_norm = rel.replace("\\", "/")
        if prefix and prefix not in rel_norm:
            continue
        path = root / rel_norm
        if path.exists() and path.is_file():
            candidates.append(path.resolve())
    if candidates:
        return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0]
    return None


def extract_errors(history: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            message = value.get("exception_message") or value.get("message") or value.get("error")
            node = value.get("node_id") or value.get("node")
            if message:
                prefix = f"node {node}: " if node is not None else ""
                errors.append(prefix + str(message))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            if len(value) >= 2 and value[0] in {"execution_error", "execution_interrupted"}:
                walk(value[1])
            for item in value:
                walk(item)

    walk(history or {})
    result: list[str] = []
    for error in errors:
        if error not in result:
            result.append(error)
    return result
