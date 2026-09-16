"""Current workflow JSON viewer helpers."""
from __future__ import annotations

from pathlib import Path

from services.context import get_project_dir


def workflow_label(path):
    name = Path(str(path or "")).name.lower()
    if "minimax" in name or "h3" in name:
        return "MiniMax H3 Ref2VA API workflow"
    if "licon" in name or "msr" in name:
        return "LiconMSR API workflow"
    return "Workflow JSON"


def workflow_candidates():
    project_dir = get_project_dir()
    candidates = []
    for directory in (project_dir / "workflows" / "licon_msr", project_dir / "workflows" / "minimax_h3"):
        if directory.exists():
            candidates.extend(path for path in directory.glob("*_api.json") if path.is_file())
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def latest_workflow_path():
    candidates = workflow_candidates()
    return candidates[-1] if candidates else None


def latest_workflow_text():
    path = latest_workflow_path()
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig")
