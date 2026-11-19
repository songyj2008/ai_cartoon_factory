"""Initialize the portable release before importing application modules."""
import os
from pathlib import Path
import sys


base = Path(sys.executable).resolve().parent
os.chdir(base)
os.environ["PATH"] = str(base / "runtime") + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
log_dir = base / "logs"
log_dir.mkdir(exist_ok=True)
log_stream = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)
sys.stdout = log_stream
sys.stderr = log_stream
config = base / "config.json"
try:
    with config.open("x", encoding="utf-8") as target:
        target.write((base / "config.example.json").read_text(encoding="utf-8"))
except FileExistsError:
    pass
