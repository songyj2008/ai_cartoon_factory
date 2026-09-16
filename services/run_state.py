"""Minimal run-state compatibility for the LiconMSR direct workflow."""
from __future__ import annotations

import json
from typing import Any

from services.context import EPISODES_DIR, get_current_episode_name, get_current_project_name, get_project_dir
from services.file_utils import load_json_file_silent
from services.logger import get_logs


def load_run_state() -> dict[str, Any]:
    return {"status": "IDLE", "unfinished": False, "current_step": "", "stage": ""}


def save_run_state(state: dict[str, Any] | None = None) -> dict[str, Any]:
    return load_run_state()


def project_current_state() -> dict[str, Any]:
    project = get_project_dir()
    try:
        video_jobs = json.loads(load_json_file_silent("video_jobs.json") or "{}")
    except Exception:
        video_jobs = {}
    project_name = get_current_project_name() or "current"
    episodes_root = EPISODES_DIR / project_name
    episodes = sorted([p.name for p in episodes_root.iterdir() if p.is_dir()]) if episodes_root.exists() else []
    return {
        "project_name": project_name,
        "current_episode": get_current_episode_name() or "",
        "episodes": episodes,
        "project_dir": str(project.resolve()),
        "story_text": load_json_file_silent("story.json"),
        "beats_text": load_json_file_silent("beats.json"),
        "video_jobs_text": load_json_file_silent("video_jobs.json"),
        "video_jobs": video_jobs,
        "log_tail": get_logs(),
        "run_state": load_run_state(),
    }
