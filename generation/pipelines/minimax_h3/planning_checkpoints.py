"""Validated plan checkpoints, confined to a text operation's private work tree."""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path

_LOCK = threading.RLock()
_RELATIVE_PATH = Path("temp") / "h3_planning_checkpoint.json"
_logger = logging.getLogger(__name__)


def _work_path():
    from services.context import get_project_dir
    from services.text_jobs import current

    operation = current()
    if not operation or not operation.get("work"):
        return None
    work = Path(operation["work"]).resolve()
    if Path(get_project_dir()).resolve() != work or not (work / ".draft").is_file():
        return None
    return work / _RELATIVE_PATH


def _read(path):
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        return {}
    if not isinstance(value.get("plans", {}), dict) or not isinstance(value.get("novel_beats", {}), dict):
        raise ValueError("Invalid planning checkpoint sections")
    return {"plans": {key: plan for key, plan in (value.get("plans") or {}).items()
                      if isinstance(plan, dict) and plan.get("planning_fingerprint") == key},
            "novel_beats": value.get("novel_beats") or {}}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, **data}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _load(section, fingerprint):
    path = _work_path()
    if path is None:
        return None
    with _LOCK:
        try:
            return copy.deepcopy(_read(path).get(section, {}).get(fingerprint))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            _logger.warning("H3 planning checkpoint unreadable: %s", exc)
            return None


def _save(section, fingerprint, value):
    path = _work_path()
    if path is None:
        return
    with _LOCK:
        try:
            data = _read(path)
        except (OSError, ValueError, TypeError, AttributeError):
            data = {}
        data.setdefault(section, {})[fingerprint] = copy.deepcopy(value)
        try:
            _write(path, data)
        except OSError as exc:
            _logger.warning("H3 planning checkpoint could not be saved: %s", exc)


def load_plan(fingerprint):
    return _load("plans", fingerprint)


def save_plan(fingerprint, plan):
    _save("plans", fingerprint, plan)


def load_novel_beats(fingerprint):
    data = _load("novel_beats", fingerprint)
    return data if isinstance(data, dict) and isinstance(data.get("beats"), list) and data["beats"] else None


def save_novel_beats(fingerprint, data):
    _save("novel_beats", fingerprint, data)


def inherit_plans(source_work, target_work):
    """Caller must verify operation inputs before transferring private drafts."""
    with _LOCK:
        try:
            plans = _read(Path(source_work) / _RELATIVE_PATH)
            if not any(plans.values()):
                return False
            _write(Path(target_work) / _RELATIVE_PATH, plans)
            return True
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            _logger.warning("H3 planning checkpoint could not be inherited: %s", exc)
            return False
