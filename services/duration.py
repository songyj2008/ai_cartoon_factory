"""Shared duration parsing and persistence helpers."""

DEFAULT_DURATION_SECONDS = 60
MIN_CUSTOM_DURATION_SECONDS = 10
MAX_CUSTOM_DURATION_SECONDS = 600
DURATION_PRESETS = {
    "30s": 30,
    "60s": 60,
    "90s": 90,
    "120s": 120,
}


def get_final_duration_seconds(duration_select, custom_duration):
    selection = str(duration_select or "60s").strip()
    if selection == "custom":
        try:
            seconds = int(custom_duration)
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_DURATION_SECONDS
        if seconds < MIN_CUSTOM_DURATION_SECONDS:
            return DEFAULT_DURATION_SECONDS
        return min(seconds, MAX_CUSTOM_DURATION_SECONDS)
    return DURATION_PRESETS.get(selection, DEFAULT_DURATION_SECONDS)


def duration_state_values(duration_select, custom_duration):
    selection = str(duration_select or "60s").strip()
    if selection not in DURATION_PRESETS and selection != "custom":
        selection = "60s"
    final_seconds = get_final_duration_seconds(selection, custom_duration)
    try:
        custom_seconds = int(custom_duration)
    except (TypeError, ValueError, OverflowError):
        custom_seconds = DEFAULT_DURATION_SECONDS
    return {
        "duration_select": selection,
        "custom_duration_seconds": custom_seconds,
        "final_duration_seconds": final_seconds,
        "duration_sec": final_seconds,
    }
