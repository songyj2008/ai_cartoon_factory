# -*- mode: python ; coding: utf-8 -*-
# Keep both historical spec entry points on the same portable build contract.
from pathlib import Path

spec_path = Path(SPECPATH) / "desktop_build.spec"
exec(compile(spec_path.read_text(encoding="utf-8"), str(spec_path), "exec"))
