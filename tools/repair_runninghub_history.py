"""Back up and repair duplicate RunningHub task history in the active project."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.context import get_project_dir
from workflow.runninghub_history import normalize_runninghub_history
from workflow.runninghub_usage import refresh_runninghub_usage_summaries
from workflow.video_merge import json_text


def main() -> int:
    project_dir = get_project_dir()
    path = project_dir / "video_jobs.json"
    if not path.exists():
        print(f"no video_jobs.json: {path}")
        return 0
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"video_jobs.before_runninghub_history_repair_{stamp}.json")
    shutil.copy2(path, backup)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("video_jobs.json must contain an object")
    report = normalize_runninghub_history(payload)
    refresh_runninghub_usage_summaries(payload)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json_text(payload), encoding="utf-8")
    temp.replace(path)
    print(json.dumps({"project": str(project_dir), "backup": str(backup), **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
