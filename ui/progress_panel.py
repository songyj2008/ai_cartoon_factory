"""Lightweight workflow progress helpers for the model-factory UI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.context import get_project_dir
from services.file_utils import load_json_file_silent


def one_click_is_active() -> bool:
    return False


def workflow_status_from_run_state(run_state: dict[str, Any] | None = None) -> dict[str, Any]:
    project = get_project_dir()
    story_done = (project / "story.json").exists()
    beats_done = (project / "beats.json").exists()
    jobs_done = (project / "video_jobs.json").exists()
    final_done = bool(_latest_valid_final_video())
    steps = {
        "story": story_done,
        "beats": beats_done,
        "segments": jobs_done,
        "merge": final_done,
    }
    total = len(steps)
    complete = sum(1 for ok in steps.values() if ok)
    return {
        "status": "complete" if complete == total else "idle",
        "progress": int(complete * 100 / total),
        "steps": steps,
    }


def _latest_valid_final_video() -> Path | None:
    final_dir = get_project_dir() / "final"
    if not final_dir.exists():
        return None
    candidates = [p for p in final_dir.glob("final_video_*.mp4") if p.is_file() and p.stat().st_size > 0]
    if not candidates:
        candidates = [p for p in final_dir.glob("*.mp4") if p.is_file() and p.stat().st_size > 0]
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0] if candidates else None


def render_workflow_progress(status: dict[str, Any] | None = None) -> str:
    status = status or workflow_status_from_run_state({})
    steps = status.get("steps") or {}
    labels = [
        ("story", "剧情"),
        ("beats", "Beats"),
        ("segments", "分段视频"),
        ("merge", "合并"),
    ]
    progress = int(status.get("progress") or 0)
    first_pending = next((key for key, _label in labels if not steps.get(key)), "merge")
    current_label = next((label for key, label in labels if key == first_pending), "完成")
    complete = progress >= 100
    status_label = "完成" if complete else "等待中"

    pieces: list[str] = []
    for index, (key, label) in enumerate(labels):
        done = bool(steps.get(key))
        classes = ["pipeline-node"]
        if done:
            classes.append("done")
        elif key == first_pending and not complete:
            classes.append("active")
        pieces.append(f'<div class="{" ".join(classes)}" title="{label}"></div>')
        if index < len(labels) - 1:
            arrow_classes = ["pipeline-arrow"]
            if done:
                arrow_classes.append("done")
            next_key = labels[index + 1][0]
            if next_key == first_pending and not complete:
                arrow_classes.append("active")
            pieces.append(f'<div class="{" ".join(arrow_classes)}">&rarr;</div>')

    return f"""
    <div class="pipeline-status-box">
        <div class="status-center">
            <div class="pipeline-current" title="视频模型工厂主流程">
                <span>当前：</span><strong>{current_label}</strong>
            </div>
            <div class="pipeline-progress" title="视频模型工厂主流程">
                {''.join(pieces)}
            </div>
            <div class="pipeline-meta">
                <span>状态：<strong>{status_label}</strong></span>
                <span>进度：<strong>{progress}%</strong></span>
            </div>
        </div>
    </div>
    """


def render_progress_from_files() -> str:
    return render_workflow_progress(workflow_status_from_run_state({}))


def current_video_job_count() -> int:
    try:
        data = json.loads(load_json_file_silent("video_jobs.json") or "{}")
        return len(data.get("segments") or [])
    except Exception:
        return 0
