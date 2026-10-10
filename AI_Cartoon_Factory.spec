# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [('F:\\work\\comfyui\\ai_cartoon_factory\\config.json', '.'), ('F:\\work\\comfyui\\ai_cartoon_factory\\templates', 'templates'), ('F:\\work\\comfyui\\ai_cartoon_factory\\resources', 'resources'), ('F:\\work\\comfyui\\ai_cartoon_factory\\web', 'web'), ('F:\\work\\comfyui\\ai_cartoon_factory\\profiles', 'profiles'), ('F:\\work\\comfyui\\ai_cartoon_factory\\prompt_profiles', 'prompt_profiles'), ('F:\\work\\comfyui\\ai_cartoon_factory\\projects', 'projects'), ('F:\\work\\comfyui\\ai_cartoon_factory\\generation\\channels\\registry.json', 'generation/channels'), ('F:\\work\\comfyui\\ai_cartoon_factory\\tools\\chrome_prompt_bridge', 'tools/chrome_prompt_bridge')]
binaries = []
hiddenimports = ['uvicorn', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'webview.platforms.edgechromium', 'webview.platforms.mshtml']
hiddenimports += collect_submodules('generation.channels')
tmp_ret = collect_all('gradio')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('gradio_client')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['F:\\work\\comfyui\\ai_cartoon_factory\\desktop_app.py'],
    pathex=['F:\\work\\comfyui\\ai_cartoon_factory'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['F:\\work\\comfyui\\ai_cartoon_factory\\pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'transformers', 'diffusers', 'timm', 'peft', 'scipy', 'cv2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AI_Cartoon_Factory',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
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
    upx=True,
    upx_exclude=[],
    name='AI_Cartoon_Factory',
)
