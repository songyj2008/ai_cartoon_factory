# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


PROJECT_DIR = Path(r"F:\work\comfyui\ai_cartoon_factory")
APP_NAME = "AI_Cartoon_Factory"

datas = [
    (str(PROJECT_DIR / "config.json"), "."),
    (str(PROJECT_DIR / "templates"), "templates"),
    (str(PROJECT_DIR / "resources"), "resources"),
    (str(PROJECT_DIR / "web"), "web"),
    (str(PROJECT_DIR / "profiles"), "profiles"),
    (str(PROJECT_DIR / "prompt_profiles"), "prompt_profiles"),
    (str(PROJECT_DIR / "projects"), "projects"),
    (str(PROJECT_DIR / "generation" / "channels" / "registry.json"), "generation/channels"),
    (str(PROJECT_DIR / "tools" / "chrome_prompt_bridge"), "tools/chrome_prompt_bridge"),
]
binaries = []
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
    runtime_hooks=[],
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
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
