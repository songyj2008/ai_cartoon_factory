"""H3 continuity API: frozen sources and reviewable standalone prompt candidates."""
from .source import resolve_source_snapshot
from .service import build_opening_candidate

__all__ = ["resolve_source_snapshot", "build_opening_candidate"]
