"""Local ComfyUI input staging for MiniMax H3 reference assets.

The core ``LoadImage``, ``LoadVideo`` and ``LoadAudio`` nodes resolve their
file names beneath ComfyUI's configured ``input`` directory.  H3 therefore
cannot safely reuse the project's absolute asset paths in a saved API graph.
This module copies each selected source into a namespaced input subdirectory
and returns the relative name understood by those core nodes.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _safe_token(value: str, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return token or fallback


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class H3ComfyInputStager:
    """Stage H3 reference files and return ComfyUI-input-relative paths.

    Staging is content-versioned by source location, size and modification
    time.  A changed source gets a new filename, so ComfyUI will not reuse a
    stale cached reference.  This class only creates/copies files; it never
    cleans the input directory, because those files may still be referenced by
    a queued ComfyUI prompt.
    """

    input_dir: Path
    base_dir: Path
    scope: str
    namespace: str = "ai_cartoon_factory/minimax_h3"
    _staged: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.input_dir = Path(self.input_dir).resolve()
        self.base_dir = Path(self.base_dir).resolve()
        if not self.input_dir.exists() or not self.input_dir.is_dir():
            raise FileNotFoundError(
                "configured ComfyUI input directory does not exist: "
                f"{self.input_dir}"
            )

    def resolve_source(self, value: str) -> Path:
        """Resolve a persisted project path without treating it as a graph path."""

        raw = str(value or "").strip()
        if not raw:
            raise ValueError("H3 reference asset path is empty")
        candidate = Path(raw)
        if candidate.is_absolute():
            source = candidate.resolve()
        else:
            project_candidate = (self.base_dir / candidate).resolve()
            if project_candidate.exists():
                source = project_candidate
            else:
                # A previously staged relative value is still a valid source
                # when the workflow is re-submitted after an application
                # restart.  Keep it constrained to the configured input root.
                input_candidate = (self.input_dir / candidate).resolve()
                source = input_candidate if _is_within(input_candidate, self.input_dir) else project_candidate
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"H3 reference asset does not exist: {source}")
        return source

    def stage(self, value: str) -> str:
        """Copy one source when needed and return its input-relative filename."""

        source = self.resolve_source(value)
        cache_key = str(source)
        cached = self._staged.get(cache_key)
        if cached:
            return cached

        source_stat = source.stat()
        version = hashlib.sha256(
            f"{source}|{source_stat.st_size}|{source_stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        filename = _safe_token(source.name, f"asset{source.suffix.lower()}")
        relative = self._relative_root() / f"{version}_{filename}"
        target = (self.input_dir / relative).resolve()
        if not _is_within(target, self.input_dir):
            raise ValueError(f"refusing to stage H3 asset outside ComfyUI input: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self._matches_source(target, source_stat):
            shutil.copy2(source, target)
        staged = relative.as_posix()
        self._staged[cache_key] = staged
        return staged

    def _relative_root(self) -> Path:
        parts = [
            _safe_token(part, "scope")
            for part in str(self.namespace or "").replace("\\", "/").split("/")
            if str(part).strip()
        ]
        return Path(*parts, _safe_token(self.scope, "default"))

    @staticmethod
    def _matches_source(target: Path, source_stat: object) -> bool:
        if not target.exists() or not target.is_file():
            return False
        try:
            target_stat = target.stat()
            return (
                target_stat.st_size == getattr(source_stat, "st_size")
                and target_stat.st_mtime_ns == getattr(source_stat, "st_mtime_ns")
            )
        except OSError:
            return False
