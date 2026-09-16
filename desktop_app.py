"""Desktop entry point for AI Cartoon Factory.

This wraps the existing local Gradio app in a native desktop window.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

from services.gradio_runtime import ensure_asyncio_event_loop, ensure_gradio_app_runtime

ensure_asyncio_event_loop()

import webview

from app import allowed_paths, demo, ui_launch_kwargs
from services.context import CONFIG
from services.windows_runtime_guard import install_windows_signal_guard


WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _app_base_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _message_box(title: str, message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)


def _webview2_registry_version() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        roots = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        ]
        for root, subkey in roots:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                    value = str(value or "").strip()
                    if value:
                        return value
            except OSError:
                continue
    except Exception:
        return ""
    return ""


def _webview2_binary_exists() -> bool:
    if os.name != "nt":
        return True
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "EdgeWebView" / "Application",
    ]
    for base in candidates:
        try:
            if base.exists() and any(base.glob("*/msedgewebview2.exe")):
                return True
        except Exception:
            continue
    return False


def _webview2_is_installed() -> bool:
    if os.name != "nt":
        return True
    return bool(_webview2_registry_version() or _webview2_binary_exists())


def _installer_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ensure_webview2_runtime() -> None:
    if _webview2_is_installed():
        return
    installer = _app_base_dir() / "runtime" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    if not installer.exists():
        message = (
            "当前电脑缺少 Microsoft Edge WebView2 Runtime，桌面 UI 无法显示。\n\n"
            f"未找到安装器：{installer}\n"
            "请重新使用完整发布包，或手动安装 WebView2 Runtime 后再运行。"
        )
        _message_box("AI Cartoon Factory", message)
        raise RuntimeError(message)

    print("[desktop] WebView2 Runtime missing; installing...")
    result = subprocess.run(
        [str(installer), "/silent", "/install"],
        cwd=str(_app_base_dir()),
        capture_output=True,
        text=True,
        timeout=600,
        creationflags=_installer_creationflags(),
    )
    if result.returncode != 0 or not _webview2_is_installed():
        message = (
            "Microsoft Edge WebView2 Runtime 自动安装失败，桌面 UI 无法显示。\n\n"
            f"安装器：{installer}\n"
            f"退出码：{result.returncode}\n"
            "请右键以管理员身份运行，或手动安装 WebView2 Runtime 后再打开。"
        )
        _message_box("AI Cartoon Factory", message)
        raise RuntimeError(message)
    print("[desktop] WebView2 Runtime installed")


def _configured_host() -> str:
    return os.environ.get("AI_CARTOON_FACTORY_HOST", "127.0.0.1")


def _configured_port() -> int:
    return int(os.environ.get("AI_CARTOON_FACTORY_PORT") or CONFIG.get("server_port", 7860))


def _port_is_free(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, int(port)))
            return True
    except OSError:
        return False


def _select_server_port() -> int:
    host = _configured_host()
    preferred = _configured_port()
    if _port_is_free(host, preferred):
        return preferred
    for port in range(max(1, preferred + 1), max(preferred + 1, 7900)):
        if _port_is_free(host, port):
            print(f"[desktop] port {preferred} is busy; using {port}")
            return port
    raise RuntimeError(f"Cannot find empty port in range: {preferred}-7899")


def _redirect_console_output() -> None:
    if os.environ.get("AI_CARTOON_FACTORY_SMOKE") == "1":
        return
    try:
        base_dir = _app_base_dir()
        log_dir = base_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
    except Exception:
        pass


def _server_url() -> str:
    host = _configured_host()
    port = _configured_port()
    return f"http://{host}:{port}"


def _run_gradio() -> None:
    ensure_asyncio_event_loop()
    ensure_gradio_app_runtime(demo)
    demo.queue()
    ensure_gradio_app_runtime(demo)
    demo.launch(
        server_name=_configured_host(),
        server_port=_configured_port(),
        allowed_paths=allowed_paths(),
        prevent_thread_lock=True,
        quiet=True,
        show_api=False,
        **ui_launch_kwargs(),
    )


def _wait_for_server(url: str, timeout_sec: int = 90) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if 200 <= response.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> None:
    _redirect_console_output()
    os.environ["AI_CARTOON_FACTORY_PORT"] = str(_select_server_port())
    os.environ["GRADIO_SERVER_PORT"] = os.environ["AI_CARTOON_FACTORY_PORT"]
    url = _server_url()
    install_windows_signal_guard()
    ensure_gradio_app_runtime(demo)
    thread = threading.Thread(target=_run_gradio, name="gradio-server", daemon=True)
    thread.start()

    if not _wait_for_server(url):
        raise RuntimeError(f"Gradio server did not start: {url}")

    if os.environ.get("AI_CARTOON_FACTORY_SMOKE") == "1":
        print(f"[desktop] smoke ok: {url}")
        os._exit(0)

    from services.runninghub_worker import runninghub_worker
    runninghub_worker.start()
    _ensure_webview2_runtime()
    webview.create_window(
        "AI Cartoon Factory",
        url,
        width=1440,
        height=960,
        min_size=(1120, 720),
        text_select=True,
    )
    try:
        webview.start()
    finally:
        runninghub_worker.stop()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[desktop][error] {exc}", file=sys.stderr)
        raise
