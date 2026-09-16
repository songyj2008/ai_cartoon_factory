"""Story-to-beats generation for the LiconMSR direct workflow."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import traceback
from typing import Any

from asset_index import load_asset_index
from pipeline.events import ensure_event_fields_on_beats
from services.context import BASE_DIR, CONFIG, CONFIG_PATH, PYTHON_EXE, get_project_dir
from services.file_utils import get_project_temp_dir, load_json_file_silent, safe_json_loads
from services.industry_profile import load_industry_profile
from services.logger import get_logs, log
from services.project_bible import ensure_project_bible, load_project_bible
from services.subprocess_utils import popen_platform_kwargs
from workflow.asset_matcher import match_beats_assets


def _normalise_target_beat_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        count = int(float(value))
    except Exception:
        return None
    return count if count > 0 else None


def build_beat_generation_input(story: dict[str, Any], target_duration_sec: int, target_beat_count: Any = None, model_id: str | None = None) -> dict[str, Any]:
    from generation.model_rules import rules_for_model

    model_rules = rules_for_model(model_id)
    payload = dict(story or {})
    payload["target_duration_sec"] = int(target_duration_sec or CONFIG.get("default_video_duration", 60) or 60)
    count = _normalise_target_beat_count(target_beat_count)
    count_source = "user" if count else ""
    if not count:
        count = model_rules.beat_planning.default_target_count(payload["target_duration_sec"])
        if count:
            count_source = "model_default"
    if count:
        payload["target_beat_count"] = count
        payload["target_beat_count_source"] = count_source
    payload["project_bible"] = load_project_bible() or ensure_project_bible(
        topic=payload.get("source_topic") or payload.get("summary") or payload.get("title") or "",
        asset_index=load_asset_index(),
    )
    payload["industry_profile"] = load_industry_profile(get_project_dir().name)
    payload["generation_policy"] = {"model_id": model_rules.model_id, "rules_revision": model_rules.revision}
    return payload


def split_beats(story_text, target_duration_sec=None, target_beat_count=None, model_id=None):
    """Generate beats.json from story.json text.

    This is now the only intermediate story structure used by runtime video
    generation. It preserves important_roles and background_extras because the
    direct prompt compiler needs them.
    """
    try:
        story = safe_json_loads(story_text, "story")
        if target_duration_sec is None:
            target_duration_sec = int(story.get("target_duration_sec") or CONFIG.get("default_video_duration", 60) or 60)
        requested_count = _normalise_target_beat_count(target_beat_count)
        payload = build_beat_generation_input(story, int(target_duration_sec or 60), requested_count, model_id=model_id)
        count = _normalise_target_beat_count(payload.get("target_beat_count"))
        resolved_model_id = str((payload.get("generation_policy") or {}).get("model_id") or "")

        log("=" * 60)
        log("[beats] story -> filmable beats", "STEP")
        if count:
            source = str(payload.get("target_beat_count_source") or "user")
            log(f"[beats] target beat count: {count} ({source})", "STEP")
        # A new story invalidates any previously generated beats. Yield an
        # empty editor state while generation is running so the UI never shows
        # stale beats that belong to an older story.
        yield "", get_logs()

        input_path = get_project_temp_dir() / "beats_generation_input.json"
        output_path = get_project_dir() / "beats.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        script_path = BASE_DIR / "scripts" / "story_script" / "generate_beats.py"
        cmd = [
            PYTHON_EXE,
            str(script_path),
            "--config",
            str(CONFIG_PATH),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--target-duration-sec",
            str(int(target_duration_sec or 60)),
            "--model-id",
            resolved_model_id,
        ]
        if count:
            cmd.extend(["--target-beat-count", str(count)])
        log("[beats] calling generate_beats.py", "STEP")
        yield "", get_logs()

        if getattr(sys, "frozen", False):
            from scripts.story_script.generate_beats import run_generation

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                returncode = run_generation(
                    config_path=str(CONFIG_PATH),
                    input_path=str(input_path),
                    output_path=str(output_path),
                    target_duration_sec=int(target_duration_sec or 60),
                    target_beat_count=count,
                    model_id=resolved_model_id,
                )
            output_lines = [line.rstrip() for line in buffer.getvalue().splitlines()]
            for line in output_lines:
                if line:
                    level = "ERROR" if "[beats][error]" in line else "STEP"
                    log(line, level)
                    yield "", get_logs()
            if returncode != 0:
                if output_lines:
                    log("[beats][error] script output:\n" + "\n".join(output_lines), "ERROR")
                raise RuntimeError(f"generate_beats.py failed, returncode={returncode}")
        else:
            env = os.environ.copy()
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUTF8", "1")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(BASE_DIR),
                bufsize=1,
                env=env,
                **popen_platform_kwargs(log),
            )
            output_lines: list[str] = []
            if proc.stdout:
                for line in proc.stdout:
                    line = line.rstrip()
                    output_lines.append(line)
                    if line:
                        level = "ERROR" if "[beats][error]" in line else "STEP"
                        log(line, level)
                        yield "", get_logs()
            returncode = proc.wait()
            if returncode != 0:
                if output_lines:
                    log("[beats][error] script output:\n" + "\n".join(output_lines), "ERROR")
                raise RuntimeError(f"generate_beats.py failed, returncode={returncode}")

        data = json.loads(output_path.read_text(encoding="utf-8"))
        data = match_beats_assets(data)
        data = ensure_event_fields_on_beats(data)
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        beats_count = len(data.get("beats", []))
        log(f"[beats] generated beats: {beats_count}", "STEP")
        yield json.dumps(data, ensure_ascii=False, indent=2), get_logs()

    except Exception as exc:
        traceback.print_exc()
        log("[beats][error] generation failed", "ERROR")
        log(f"[beats][error] {exc}", "ERROR")
        yield "", get_logs()
