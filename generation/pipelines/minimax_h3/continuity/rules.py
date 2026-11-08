"""Load creative instructions separately from structural runtime checks."""
from pathlib import Path

def load_rules():
    return (Path(__file__).resolve().parents[4]/"profiles"/"h3_opening_continuity_rules.md").read_text(encoding="utf-8").strip()

def load_source_rules():
    return (Path(__file__).resolve().parents[4]/"profiles"/"h3_continuity_source_rules.md").read_text(encoding="utf-8").strip()
