"""Platform-specific subprocess helpers."""
import os
import subprocess


def popen_platform_kwargs(log_fn=None):
    """Return Windows-only kwargs for isolated subprocess startup.

    Non-Windows platforms intentionally return an empty dict so Linux/macOS
    subprocess and Ctrl+C behavior stays exactly the same.
    """
    if os.name != "nt":
        return {}

    flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if log_fn:
        try:
            log_fn("[windows][subprocess] hidden subprocess mode enabled", "STEP")
        except TypeError:
            log_fn("[windows][subprocess] hidden subprocess mode enabled")
        except Exception:
            pass
    return {"creationflags": flags}
