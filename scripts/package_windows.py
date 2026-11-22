"""Build and verify the Windows x64 portable release in the active environment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "AI_Cartoon_Factory"
INSTALLER_URL = "https://go.microsoft.com/fwlink/?linkid=2124701"


def build_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key.upper() not in {"RUNNINGHUB_API_KEY", "RUNNINGHUB_API_KEYS"}}


def verify_example_config(path: Path) -> None:
    example = json.loads(path.read_text(encoding="utf-8-sig"))
    if example.get("runninghub_api_keys"):
        raise RuntimeError("Release example configuration contains RunningHub keys")


def main() -> None:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Build this release on Windows x64")
    verify_example_config(ROOT / "config.example.json")
    installer = ROOT / ".local" / "packaging" / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    if not installer.is_file():
        import urllib.request

        temporary = installer.with_suffix(".download")
        urllib.request.urlretrieve(INSTALLER_URL, temporary)
        temporary.replace(installer)
    # Require Microsoft's signed full installer, including on subsequent builds.
    subprocess.run(
        [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-Command",
         "$s = Get-AuthenticodeSignature -LiteralPath $env:AICF_WEBVIEW2_INSTALLER; "
         "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { exit 1 }"],
        env={**{key: value for key, value in build_environment().items() if key.upper() != "PSMODULEPATH"},
             "AICF_WEBVIEW2_INSTALLER": str(installer)}, check=True,
    )
    if installer.stat().st_size < 50_000_000:
        raise RuntimeError("A full offline WebView2 installer is required")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "desktop_build.spec"],
        cwd=ROOT, check=True,
        env={**build_environment(), "PATH": str(Path(sys.prefix) / "Library" / "bin")
             + os.pathsep + os.environ.get("PATH", "")},
    )
    verify_and_archive(ROOT / "dist" / APP_NAME)


def verify_and_archive(release: Path) -> None:
    for name in ("config.json", "projects", "episodes", "outputs", "assets", "logs"):
        if (release / name).exists():
            raise RuntimeError(f"Release contains local state: {name}")
    video_suffixes = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    for source in release.rglob("*"):
        relative = source.relative_to(release)
        if source.is_file() and (
            source.name.lower() in {"config.json", "config.local.json", ".env"}
            or source.name.lower().startswith("config.json.")
            or (source.name.lower().startswith(".env.") and source.name.lower() != ".env.example")
        ):
            raise RuntimeError(f"Release contains private configuration: {relative}")
        if source.is_file() and source.name.lower() == "config.example.json":
            verify_example_config(source)
        if source.is_file() and (
            source.suffix.lower() in video_suffixes
            or {"media_assets", "test_data"}.intersection(relative.parts)
        ):
            raise RuntimeError(f"Release contains video or sample media: {relative}")
    # Exercise a relocated copy with spaces and Chinese characters and no conda PATH.
    with tempfile.TemporaryDirectory(prefix="aicf_发布测试 ", ignore_cleanup_errors=True) as temp:
        test_app = Path(temp) / APP_NAME
        shutil.copytree(release, test_app)
        windows = Path(os.environ["SystemRoot"])
        env = {key: value for key, value in build_environment().items()
               if not key.startswith(("PYTHON", "CONDA", "AI_CARTOON_FACTORY", "GRADIO"))}
        env.update(PATH=str(windows / "System32"), AI_CARTOON_FACTORY_SMOKE="1",
                   LOCALAPPDATA=str(Path(temp) / "localappdata"))
        subprocess.run([str(test_app / f"{APP_NAME}.exe")], cwd=temp, env=env,
                       check=True, timeout=180)
        log = (test_app / "logs" / "desktop.log").read_text(encoding="utf-8")
        if "[desktop] smoke ok:" not in log:
            raise RuntimeError(f"Packaged startup check failed\n{log[-8000:]}")
        for binary in ("ffmpeg", "ffprobe"):
            subprocess.run([str(test_app / "runtime" / f"{binary}.exe"), "-version"],
                           cwd=temp, env=env, check=True, capture_output=True, timeout=30)
        print("Verified relocated EXE startup, application API, ffmpeg and ffprobe", flush=True)
    shutil.copy2(ROOT / "docs" / "portable_windows.txt", release / "使用说明.txt")
    archive = ROOT / "dist" / f"{APP_NAME}_Windows_x64.zip"
    temporary_archive = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for source in sorted(release.rglob("*")):
            if source.is_file():
                bundle.write(source, source.relative_to(release.parent))
    temporary_archive.replace(archive)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    print(f"Verified portable release: {archive}")


if __name__ == "__main__":
    main()
