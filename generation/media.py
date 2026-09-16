"""Best-effort media inspection used by model-neutral result records."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from generation.contracts import MediaSpec


def inspect_media(path_value: str | Path) -> MediaSpec:
    """Read output facts when ffprobe is available; never fail a render for it."""
    path = Path(path_value)
    ffprobe = shutil.which("ffprobe")
    if not path.exists() or not ffprobe:
        return MediaSpec()
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,avg_frame_rate,width,height,sample_rate", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        data = json.loads(proc.stdout or "{}")
        streams = [item for item in data.get("streams") or [] if isinstance(item, dict)]
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        rate = str(video.get("avg_frame_rate") or "")
        fps = None
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            if float(denominator):
                fps = float(numerator) / float(denominator)
        elif rate:
            fps = float(rate)
        duration = (data.get("format") or {}).get("duration")
        return MediaSpec(
            duration_sec=float(duration) if duration not in (None, "") else None,
            fps=fps,
            width=int(video["width"]) if video.get("width") else None,
            height=int(video["height"]) if video.get("height") else None,
            has_audio=bool(audio),
            audio_sample_rate=int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        )
    except Exception:
        return MediaSpec()
