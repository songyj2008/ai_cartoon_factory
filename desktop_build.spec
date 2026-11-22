# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import shutil

from PyInstaller.utils.hooks import collect_all, collect_submodules


PROJECT_DIR = Path(SPECPATH)
APP_NAME = "AI_Cartoon_Factory"
WEBVIEW2_INSTALLER = Path(os.environ.get(
    "AICF_WEBVIEW2_INSTALLER",
    str(PROJECT_DIR / ".local" / "packaging" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"),
))

datas = [
    (str(PROJECT_DIR / "config.example.json"), "."),
    (str(PROJECT_DIR / "templates"), "templates"),
    (str(PROJECT_DIR / "resources" / "brand"), "resources/brand"),
    (str(WEBVIEW2_INSTALLER), "runtime"),
    (str(PROJECT_DIR / "web"), "web"),
    # Gradio inspects custom component source when constructing its metadata.
    (str(PROJECT_DIR / "ui" / "gradio_components.py"), "ui"),
    (str(PROJECT_DIR / "profiles"), "profiles"),
    (str(PROJECT_DIR / "prompt_profiles"), "prompt_profiles"),
    (str(PROJECT_DIR / "generation" / "channels" / "registry.json"), "generation/channels"),
]
for source in (PROJECT_DIR / "tools" / "chrome_prompt_bridge").rglob("*"):
    relative = source.relative_to(PROJECT_DIR)
    if source.is_file() and not {"tests", "__pycache__"}.intersection(relative.parts):
        datas.append((str(source), str(relative.parent)))

for executable in ("ffmpeg", "ffprobe"):
    source = shutil.which(executable)
    if not source:
        raise RuntimeError(f"Install {executable} and add it to PATH before packaging")
    datas.append((source, "runtime"))
binaries = []
# Conda keeps these Python extension dependencies outside its DLLs directory.
import sys
for name in ("ffi.dll", "sqlite3.dll", "libmpdec-4.dll"):
    source = Path(sys.prefix) / "Library" / "bin" / name
    if source.is_file():
        binaries.append((str(source), "."))
hiddenimports = [
    "uvicorn",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
]
hiddenimports += collect_submodules("generation.channels")

for package in [
    "gradio",
    "fastapi",
    "starlette",
    "pydantic",
    "gradio_client",
    "safehttpx",
    "groovy",
    "webview",
]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


def prepare_binaries(toc):
    blocked = {"ucrtbase.dll"}
    seen = set()
    filtered = []
    for item in toc:
        dest = str(item[0]).lower().replace("\\", "/")
        dest_name = Path(dest).name
        source_name = Path(str(item[1])).name.lower() if len(item) > 1 else ""
        if dest_name in blocked or source_name in blocked:
            continue
        if dest in seen:
            continue
        seen.add(dest)
        filtered.append(item)
    return filtered


a = Analysis(
    [str(PROJECT_DIR / "desktop_app.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(PROJECT_DIR / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_DIR / "pyinstaller_hooks" / "desktop_runtime.py")],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "diffusers",
        "timm",
        "peft",
        "scipy",
        "cv2",
    ],
    noarchive=False,
    optimize=0,
)

a.binaries = prepare_binaries(a.binaries)
# Omit generated media and dependency demo/test assets from distributable data.
video_suffixes = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
a.datas = [
    item for item in a.datas
    if Path(item[0]).suffix.lower() not in video_suffixes
    and not {"media_assets", "test_data", "tests", "__pycache__"}.intersection(
        str(item[0]).replace("\\", "/").split("/")
    )
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=str(PROJECT_DIR / "resources" / "brand" / "desktop-icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
