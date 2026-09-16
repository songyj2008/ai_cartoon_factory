"""Windows-only signal guard for long-running one-click jobs."""
import os
import signal
import time

_ACTIVE_LONG_JOB = False
_PENDING_SIGNAL_NOTICE = False
_LAST_INTERRUPT_AT = 0.0
_LAST_LONG_JOB_ENDED_AT = 0.0
_END_COOLDOWN_SECONDS = 15.0


def set_long_job_active(active: bool):
    global _ACTIVE_LONG_JOB, _PENDING_SIGNAL_NOTICE, _LAST_INTERRUPT_AT, _LAST_LONG_JOB_ENDED_AT
    was_active = _ACTIVE_LONG_JOB
    _ACTIVE_LONG_JOB = bool(active)
    if not _ACTIVE_LONG_JOB:
        _PENDING_SIGNAL_NOTICE = False
        _LAST_INTERRUPT_AT = 0.0
        if was_active:
            _LAST_LONG_JOB_ENDED_AT = time.time()


def is_long_job_active() -> bool:
    return bool(_ACTIVE_LONG_JOB)


def pop_pending_signal_notice() -> bool:
    global _PENDING_SIGNAL_NOTICE
    pending = bool(_PENDING_SIGNAL_NOTICE)
    _PENDING_SIGNAL_NOTICE = False
    return pending


def clear_pending_signal_notice():
    global _PENDING_SIGNAL_NOTICE
    _PENDING_SIGNAL_NOTICE = False


def install_windows_signal_guard():
    if os.name != "nt":
        return

    def _handler(signum, frame):
        global _PENDING_SIGNAL_NOTICE, _LAST_INTERRUPT_AT

        if _ACTIVE_LONG_JOB:
            _PENDING_SIGNAL_NOTICE = True
            _LAST_INTERRUPT_AT = time.time()
            return
        if time.time() - _LAST_LONG_JOB_ENDED_AT < _END_COOLDOWN_SECONDS:
            _PENDING_SIGNAL_NOTICE = True
            _LAST_INTERRUPT_AT = time.time()
            return

        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)

    try:
        signal.signal(signal.SIGBREAK, _handler)
    except Exception:
        pass
