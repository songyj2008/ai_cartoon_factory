"""Compile story beats into direct LiconMSR video jobs."""
from __future__ import annotations

import re
from typing import Any

from prompt_builder.beat_prompt_expander import expand_beat_to_licon_prompt


MIN_SEGMENT_SEC = 15
MAX_SEGMENT_SEC = 30


def _text(value: Any) -> str:
    return str(value or "").strip()


BEAT_HEADING_RE = re.compile(r"^\s*(?:【\s*)?\[?\s*Beat\s*\d+\s*(?:】|\])?.*$", re.IGNORECASE)
METADATA_LINE_RE = re.compile(
    r"^\s*(?:标题|简介|摘要|summary|title|时长|持续时长|预计时长|estimated_duration_sec|duration_sec|duration)\s*[:：=].*$",
    re.IGNORECASE,
)
DURATION_ONLY_RE = re.compile(r"^\s*(?:约\s*)?\d+(?:\.\d+)?\s*(?:秒|s|sec|seconds)\s*$", re.IGNORECASE)


def clean_beat_video_prompt(text: str) -> str:
    """Remove UI-only beat labels and duration metadata from the video prompt."""
    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if BEAT_HEADING_RE.match(stripped):
            continue
        if METADATA_LINE_RE.match(stripped):
            continue
        if DURATION_ONLY_RE.match(stripped):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def beat_duration(beat: dict[str, Any]) -> int:
    try:
        value = int(round(float(beat.get("estimated_duration_sec") or beat.get("duration_sec") or MIN_SEGMENT_SEC)))
    except Exception:
        value = MIN_SEGMENT_SEC
    return max(1, value)


def compile_beat_video_prompt(beat: dict[str, Any], segment_index: int, duration_sec: int | None = None) -> str:
    """Expand the beat plot into a full LiconMSR cinematic video prompt."""
    plot = clean_beat_video_prompt(_text(beat.get("plot")))
    if not plot:
        raise ValueError(f"Beat {segment_index} plot is empty; cannot build video prompt")
    return expand_beat_to_licon_prompt(beat, plot, segment_index, int(duration_sec or beat_duration(beat)))


def compile_beats_to_video_jobs(beats_data: dict[str, Any]) -> list[dict[str, Any]]:
    beats = beats_data.get("beats") if isinstance(beats_data, dict) else []
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats 数据为空，无法生成 LiconMSR 视频任务")
    jobs: list[dict[str, Any]] = []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        duration = beat_duration(beat)
        jobs.append(
            {
                "segment_index": index,
                "segment_id": f"segment_{index:03d}",
                "part_id": f"part_{index:03d}",
                "title": _text(beat.get("title")) or f"剧情段落{index}",
                "duration_sec": duration,
                "prompt": compile_beat_video_prompt(beat, index, duration),
                "beat": beat,
                "scene_id": _text(beat.get("scene_id")),
                "important_roles": list(beat.get("important_roles") or []),
                "visible_roles": list(beat.get("visible_roles") or []),
                "reference_roles": list(beat.get("reference_roles") or []),
                "reference_image_ids": {
                    str(role): str(image_id).strip()
                    for role, image_id in (beat.get("reference_image_ids") or {}).items()
                    if str(role).strip() and str(image_id).strip()
                },
                "offscreen_speakers": list(beat.get("offscreen_speakers") or []),
                "mentioned_roles": list(beat.get("mentioned_roles") or []),
                "background_extras": list(beat.get("background_extras") or []),
            }
        )
    if not jobs:
        raise ValueError("没有有效 beat 可生成 LiconMSR 视频任务")
    return jobs
