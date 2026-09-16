"""Centralised logging: now(), log(), get_logs(), clear_logs()."""
import os
from datetime import datetime

LOG_LINES = []


def is_debug_log_enabled():
    return str(os.environ.get("DEBUG_LOG", "")).strip().lower() in ("1", "true", "yes", "on")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, level="INFO"):
    line = f"[{level}] [{now()}] {msg}"
    print(line)
    LOG_LINES.append(line)
    try:
        from services.run_state import append_run_log
        append_run_log(line)
    except Exception:
        pass
    if len(LOG_LINES) > 2000:
        del LOG_LINES[:500]
    return "\n".join(LOG_LINES)


def debug_log(msg):
    if is_debug_log_enabled():
        return log(msg, "DEBUG")
    return "\n".join(LOG_LINES)


def get_logs():
    if LOG_LINES:
        return "\n".join(LOG_LINES)
    try:
        from services.run_state import read_recent_run_log
        return read_recent_run_log(200)
    except Exception:
        return ""


def clear_logs():
    try:
        from services.windows_runtime_guard import is_long_job_active
        if is_long_job_active():
            return get_logs()
    except Exception:
        pass
    LOG_LINES.clear()
    return ""
