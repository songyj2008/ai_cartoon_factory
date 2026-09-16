"""Segment video lookup and final concat helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from services.context import BASE_DIR, get_project_dir
from services.file_utils import load_json_file_silent
from services.logger import log


_VIDEO_JOBS_LOCK = RLock()


def _even(value: Any, fallback: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = fallback
    number = max(2, number)
    return number if number % 2 == 0 else number - 1


def _media_specs(paths: list[Path]) -> list[dict[str, Any]]:
    from generation.media import inspect_media

    return [inspect_media(path).to_dict() for path in paths]


def _target_media_spec(source_specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose one durable final-media envelope from the first usable source."""
    first_video = next((item for item in source_specs if item.get("width") and item.get("height")), {})
    first_audio = next((item for item in source_specs if item.get("audio_sample_rate")), {})
    return {
        "width": _even(first_video.get("width"), 1280),
        "height": _even(first_video.get("height"), 720),
        "fps": max(1, int(round(float(first_video.get("fps") or 24)))),
        # Final outputs always get an AAC stereo track.  This lets a silent
        # source join an H3 source that has native audio without a stream-map
        # mismatch at concat time.
        "has_audio": True,
        "audio_sample_rate": max(8000, int(first_audio.get("audio_sample_rate") or 48000)),
    }


def _needs_media_normalization(
    source_specs: list[dict[str, Any]],
    source_models: list[str],
) -> bool:
    """Return true before concat when models or output envelopes differ."""
    known_models = {str(model or "").strip() for model in source_models if str(model or "").strip()}
    if len(known_models) > 1:
        return True
    known = [item for item in source_specs if item]
    if len(known) < 2:
        return False
    fields = ("width", "height", "fps", "has_audio", "audio_sample_rate")
    baseline = known[0]
    for item in known[1:]:
        for field in fields:
            left, right = baseline.get(field), item.get(field)
            if left is not None and right is not None and left != right:
                return True
    return False


def _write_concat_list(paths: list[Path], list_path: Path) -> None:
    lines = []
    for path in paths:
        safe_path = str(path).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")


def _run_ffmpeg(command: list[str], *, label: str) -> None:
    proc = subprocess.run(command, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"ffmpeg {label} failed").strip()
        raise RuntimeError(detail)


def _normalized_segment_command(
    ffmpeg: str,
    source: Path,
    target: dict[str, Any],
    destination: Path,
    *,
    use_source_audio: bool,
) -> list[str]:
    width, height = int(target["width"]), int(target["height"])
    fps, sample_rate = int(target["fps"]), int(target["audio_sample_rate"])
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
        f"fps={fps},format=yuv420p"
    )
    common = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
    ]
    if use_source_audio:
        return common + [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            video_filter,
            "-af",
            "aresample=async=1:first_pts=0,aformat=channel_layouts=stereo",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            str(sample_rate),
            "-movflags",
            "+faststart",
            str(destination),
        ]
    return common + [
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        str(sample_rate),
        "-shortest",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _normalize_segments_for_concat(
    ffmpeg: str,
    paths: list[Path],
    source_specs: list[dict[str, Any]],
    temp_dir: Path,
    stamp: str,
) -> tuple[list[Path], dict[str, Any]]:
    """Make all clips concatenable without trusting their model output spec."""
    target = _target_media_spec(source_specs)
    normalization_dir = temp_dir / f"normalized_concat_{stamp}"
    normalization_dir.mkdir(parents=True, exist_ok=True)
    normalized_paths: list[Path] = []
    for index, source in enumerate(paths, start=1):
        destination = normalization_dir / f"{index:03d}_{source.stem}.mp4"
        spec = source_specs[index - 1] if index - 1 < len(source_specs) else {}
        # If ffprobe could not inspect the file, first preserve its audio.  A
        # retry with generated silence handles a truly silent source.
        use_source_audio = spec.get("has_audio") is not False
        try:
            _run_ffmpeg(
                _normalized_segment_command(
                    ffmpeg,
                    source,
                    target,
                    destination,
                    use_source_audio=use_source_audio,
                ),
                label=f"normalize {source.name}",
            )
        except RuntimeError:
            if not use_source_audio:
                raise
            _run_ffmpeg(
                _normalized_segment_command(
                    ffmpeg,
                    source,
                    target,
                    destination,
                    use_source_audio=False,
                ),
                label=f"normalize {source.name} with silence",
            )
        normalized_paths.append(destination)
    return normalized_paths, target


@contextmanager
def video_jobs_lock():
    """Serialize read-modify-write operations on the active project's job file."""
    with _VIDEO_JOBS_LOCK:
        yield


def json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def request_video_merge(payload: dict[str, Any]) -> None:
    """Defer a requested merge while remote submissions are still outstanding."""
    segments = payload.get("segments") or []
    if any(item.get("status") in {"queued", "running", "pending"} for item in segments):
        payload["merge_when_complete"] = True
        save_video_jobs(payload)
        return
    final_path = merge_segment_videos(payload)
    payload["final_video_path"] = str(final_path) if final_path else ""
    payload["merge_when_complete"] = False
    save_video_jobs(payload)


def finish_requested_video_merge() -> None:
    with video_jobs_lock():
        payload = load_video_jobs()
        segments = payload.get("segments") or []
        if not payload.get("merge_when_complete") or not segments:
            return
        if not all(item.get("status") == "success" and item.get("video_path") for item in segments):
            return
        # Consume before merging to avoid retrying an ffmpeg failure every poll.
        payload["merge_when_complete"] = False
        save_video_jobs(payload)
        request_video_merge(payload)


def load_video_jobs() -> dict[str, Any]:
    text = load_json_file_silent("video_jobs.json")
    if not text:
        from generation.job_schema import normalize_video_jobs_payload

        return normalize_video_jobs_payload({"mode": "licon_msr_segmented", "segments": []})
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"segments": []}
        # Repair is intentionally in-memory on load.  The next normal save (or
        # the explicit repair command) persists it, after a backup is made.
        from generation.job_schema import normalize_video_jobs_payload
        from workflow.runninghub_history import normalize_runninghub_history

        normalize_runninghub_history(data)
        return normalize_video_jobs_payload(data)
    except Exception:
        from generation.job_schema import normalize_video_jobs_payload

        return normalize_video_jobs_payload({"mode": "licon_msr_segmented", "segments": []})


def save_video_jobs(payload: dict[str, Any]) -> None:
    path = get_project_dir() / "video_jobs.json"
    temp_path = path.with_suffix(".json.tmp")
    with _VIDEO_JOBS_LOCK:
        from generation.job_schema import normalize_video_jobs_payload
        from workflow.runninghub_history import normalize_runninghub_history

        normalize_runninghub_history(payload)
        normalize_video_jobs_payload(payload)
        temp_path.write_text(json_text(payload), encoding="utf-8")
        temp_path.replace(path)


def existing_segment_video(segment: dict[str, Any]) -> str:
    segment_status = str(segment.get("status") or "").strip()
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    stale_reason = str(segment.get("stale_reason") or job.get("stale_reason") or "").strip().casefold()

    # An explicit history selection is the source-of-truth for the next merge,
    # even while a replacement task is queued or running.  A successful new
    # render clears ``manual_active_render_id`` and promotes itself.
    manual_render_id = str(segment.get("manual_active_render_id") or "").strip()
    if manual_render_id and manual_render_id == str(segment.get("active_render_id") or "").strip():
        try:
            from generation.render_history import active_render_video_path

            active_path = active_render_video_path(segment)
            if active_path and Path(active_path).exists():
                return str(Path(active_path).resolve())
        except Exception:
            pass

    # Prompt edits and ordinary retries keep using the selected artifact until
    # a replacement succeeds.  Explicit structural invalidations such as a
    # changed Beat or model still block automatic reuse.
    if (
        segment_status in {"workflow_ready", "needs_regenerate", "stale", "pending", "queued", "running"}
        and stale_reason in {"", "prompt changed"}
        and isinstance(segment.get("renders"), list)
    ):
        try:
            from generation.render_history import active_render_video_path

            active_path = active_render_video_path(segment)
            if active_path and Path(active_path).exists():
                return str(Path(active_path).resolve())
        except Exception:
            pass

    # Saving an edited prompt intentionally preserves the previous render in
    # ``stale_video_path``.  The user may have changed wording without
    # changing the actual shot, so that render remains a valid merge fallback.
    # Do not apply this to other stale causes (for example a changed beat,
    # scene, or material binding): those can describe a different shot.
    if stale_reason == "prompt changed":
        for value in (segment.get("stale_video_path"), job.get("stale_video_path")):
            path_text = str(value or "").strip()
            if path_text and Path(path_text).exists():
                return str(Path(path_text).resolve())

    if segment_status in {"workflow_ready", "needs_regenerate", "stale", "pending", "queued", "running"}:
        return ""
    # Model-factory records own a selected, immutable render.  Never scan the
    # project's videos directory once history exists: a later LTX/H3 retry can
    # have the same part id but be a different shot and model.
    if isinstance(segment.get("renders"), list):
        try:
            from generation.render_history import active_render_video_path

            active_path = active_render_video_path(segment)
            if active_path and Path(active_path).exists():
                return str(Path(active_path).resolve())
        except Exception:
            pass
        return ""
    direct = str(segment.get("video_path") or "").strip()
    if direct and Path(direct).exists():
        return direct
    job_video = str(job.get("video_path") or "").strip()
    if job_video and Path(job_video).exists():
        return job_video
    part_id = str(segment.get("part_id") or "").strip()
    if part_id:
        videos = sorted(
            [p for p in (get_project_dir() / "videos").glob(f"{part_id}_*.mp4") if p.is_file()],
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        if videos:
            return str(videos[0].resolve())
    return ""


def merge_segment_videos(video_jobs: dict[str, Any] | None = None) -> Path | None:
    data = video_jobs or load_video_jobs()
    paths: list[Path] = []
    source_manifest: list[dict[str, Any]] = []
    missing: list[str] = []
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        path_text = existing_segment_video(segment)
        if path_text:
            paths.append(Path(path_text).resolve())
            render_id = ""
            model_id = ""
            try:
                from generation.render_history import active_render, render_model_id

                render = active_render(segment)
                render_id = str((render or {}).get("render_id") or "")
                model_id = render_model_id(segment)
            except Exception:
                pass
            source_manifest.append(
                {
                    "segment_uid": str(segment.get("segment_uid") or ""),
                    "part_id": str(segment.get("part_id") or segment.get("segment_id") or ""),
                    "render_id": render_id,
                    "model_id": model_id,
                    "video_path": str(Path(path_text).resolve()),
                }
            )
            job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
            stale_reason = str(segment.get("stale_reason") or job.get("stale_reason") or "").strip().casefold()
            if stale_reason == "prompt changed":
                part_id = str(segment.get("part_id") or segment.get("segment_id") or "?")
                log(f"[FINAL VIDEO] reusing latest video for {part_id}; prompt changed", "STEP")
        else:
            missing.append(str(segment.get("part_id") or segment.get("segment_id") or "?"))
    if missing:
        raise RuntimeError(
            "cannot merge; missing segment videos: "
            + ", ".join(missing)
            + ". Generate these parts, or mark an existing render as video OK."
        )
    if not paths:
        raise RuntimeError("cannot merge; no segment videos found")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH; cannot merge videos")

    final_dir = get_project_dir() / "final"
    temp_dir = get_project_dir() / "temp"
    final_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    list_path = temp_dir / f"concat_{stamp}.txt"
    final_path = final_dir / f"final_video_{stamp}.mp4"
    source_specs = _media_specs(paths)
    for entry, media_spec in zip(source_manifest, source_specs):
        entry["actual_media_spec"] = media_spec
    source_models = [str(entry.get("model_id") or "") for entry in source_manifest]
    merge_paths = paths
    normalization = {
        "applied": False,
        "source_media_specs": source_specs,
    }
    if _needs_media_normalization(source_specs, source_models):
        log("[FINAL VIDEO] normalizing mixed model output specs before merge", "STEP")
        merge_paths, target = _normalize_segments_for_concat(ffmpeg, paths, source_specs, temp_dir, stamp)
        normalization = {
            "applied": True,
            "source_media_specs": source_specs,
            "target_media_spec": target,
            "normalized_paths": [str(path.resolve()) for path in merge_paths],
        }
    _write_concat_list(merge_paths, list_path)

    log(f"[FINAL VIDEO] merging {len(merge_paths)} segments", "STEP")
    try:
        _run_ffmpeg(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(final_path)],
            label="concat",
        )
    except RuntimeError:
        if normalization["applied"]:
            raise
        # A single-model project can still contain historical clips with
        # inconsistent codecs.  Normalize only after the lossless path fails,
        # preserving LTX's existing fast concat behavior whenever possible.
        log("[FINAL VIDEO] lossless concat failed; normalizing output specs", "WARN")
        merge_paths, target = _normalize_segments_for_concat(ffmpeg, paths, source_specs, temp_dir, stamp)
        normalization = {
            "applied": True,
            "source_media_specs": source_specs,
            "target_media_spec": target,
            "normalized_paths": [str(path.resolve()) for path in merge_paths],
        }
        _write_concat_list(merge_paths, list_path)
        _run_ffmpeg(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(final_path)],
            label="normalized concat",
        )
    if not final_path.exists():
        raise RuntimeError("ffmpeg merge finished without creating a final video")
    log(f"[FINAL VIDEO] saved {final_path}", "STEP")
    from generation.media import inspect_media

    data["final_video_path"] = str(final_path.resolve())
    data["final_video_source_manifest"] = source_manifest
    data["final_video_normalization"] = normalization
    data["final_video_media_spec"] = inspect_media(final_path).to_dict()
    save_video_jobs(data)
    return final_path.resolve()
