"""Application context: paths, config, and project/episode directory management."""
import json
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

FPS = int(CONFIG.get("fps", 24))
MAX_GROUP_DURATION_SEC = 12
MAX_KEY_IMAGES_PER_GROUP = 6
OUTPUT_DIR = BASE_DIR / CONFIG.get("output_dir", "outputs")
PROJECTS_DIR = BASE_DIR / "projects"
LAST_PROJECT_PATH = PROJECTS_DIR / "last_project.json"
EPISODES_DIR = BASE_DIR / "episodes"
CURRENT_PROJECT_NAME = "current"
CURRENT_EPISODE_NAME = ""
PROJECT_DIR = OUTPUT_DIR / CURRENT_PROJECT_NAME
_PINNED_RUNTIME = ContextVar("pinned_runtime", default=None)
_ACTIVE_RUNTIME = (PROJECT_DIR, CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME)
ASSETS_DIR = BASE_DIR / "assets"
ASSET_INDEX_PATH = ASSETS_DIR / "asset_index.json"
PYTHON_EXE = CONFIG.get("python_executable") or sys.executable or "python3"
LICON_MSR_API_TEMPLATE = BASE_DIR / CONFIG.get("template_api_path", "templates/Ltx2.3_Licon-MSRV1_api.json")

_RUNTIME_SUBDIRS = [
    "images/generated",
    "images/cache",
    "images/references",
    "workflows/licon_msr",
    "workflows/minimax_h3",
    "media/h3",
    "temp",
    "videos",
    "logs",
    "final",
]


def _ensure_runtime_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for sub in _RUNTIME_SUBDIRS:
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def _ensure_project_meta(name: str) -> Path:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROJECTS_DIR / str(name or "current")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runtime_dir(project_name: str, episode_name: str = "") -> Path:
    project_name = str(project_name or "current").strip() or "current"
    episode_name = str(episode_name or "").strip()
    if episode_name:
        return EPISODES_DIR / project_name / episode_name
    return OUTPUT_DIR / project_name


def _activate(project_name: str, episode_name: str = "") -> Path:
    global CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME, PROJECT_DIR, _ACTIVE_RUNTIME
    CURRENT_PROJECT_NAME = str(project_name or "current").strip() or "current"
    CURRENT_EPISODE_NAME = str(episode_name or "").strip()
    _ensure_project_meta(CURRENT_PROJECT_NAME)
    PROJECT_DIR = _ensure_runtime_dir(_runtime_dir(CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME))
    _ACTIVE_RUNTIME = (PROJECT_DIR, CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME)
    return PROJECT_DIR


def _load_last_project_name() -> str:
    try:
        if LAST_PROJECT_PATH.exists():
            data = json.loads(LAST_PROJECT_PATH.read_text(encoding="utf-8-sig"))
            name = str((data or {}).get("project_name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return "current"


def save_last_project_name(project_name: str) -> None:
    name = str(project_name or "current").strip() or "current"
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        LAST_PROJECT_PATH.write_text(json.dumps({"project_name": name}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
EPISODES_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_PROJECT_NAME = _load_last_project_name()
_activate(CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME)

# Migrate from old projects/current runtime data if outputs/current is empty.
_OLD_PROJECT_DIR = BASE_DIR / "projects" / "current"
if _OLD_PROJECT_DIR.exists() and not (PROJECT_DIR / "story.json").exists():
    import shutil as _shutil
    try:
        for _item in _OLD_PROJECT_DIR.iterdir():
            if _item.name in {"ui_run_config.json", "project_bible.json", "industry_profile.json"}:
                continue
            _dst = PROJECT_DIR / _item.name
            if _item.is_dir():
                if not _dst.exists():
                    _shutil.copytree(_item, _dst)
            else:
                if not _dst.exists():
                    _shutil.copy2(_item, _dst)
    except Exception:
        pass


def get_project_dir():
    """Return the active runtime directory: outputs/<project> or episodes/<project>/<episode>."""
    return (_PINNED_RUNTIME.get() or _ACTIVE_RUNTIME)[0]


def get_current_project_name() -> str:
    return (_PINNED_RUNTIME.get() or _ACTIVE_RUNTIME)[1]


def get_current_episode_name() -> str:
    return (_PINNED_RUNTIME.get() or _ACTIVE_RUNTIME)[2]


@contextmanager
def pin_runtime():
    """Keep file ownership stable for an operation even if the UI switches project."""
    token = _PINNED_RUNTIME.set(_PINNED_RUNTIME.get() or _ACTIVE_RUNTIME)
    try:
        yield
    finally:
        _PINNED_RUNTIME.reset(token)


def is_archive_episode_active() -> bool:
    return bool(get_current_episode_name())


def assert_current_runtime_writable(action: str = "修改"):
    if is_archive_episode_active():
        raise PermissionError(f"已归档的集数只能查看，不能{action}：{CURRENT_PROJECT_NAME} / {CURRENT_EPISODE_NAME}")


def set_current_project_name(name: str):
    path = _activate(name or "current", "")
    save_last_project_name(CURRENT_PROJECT_NAME)
    return path


def set_current_episode(name_or_empty: str = ""):
    return _activate(CURRENT_PROJECT_NAME, name_or_empty or "")


def set_project_dir(path_or_name):
    """Compatibility wrapper.

    Plain names switch to outputs/<name>. Absolute paths are accepted as a legacy
    direct runtime directory, while project name is inferred from the path name.
    """
    global PROJECT_DIR, CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME, _ACTIVE_RUNTIME
    path = Path(path_or_name)
    if path.is_absolute():
        PROJECT_DIR = _ensure_runtime_dir(path)
        CURRENT_PROJECT_NAME = path.name or "current"
        CURRENT_EPISODE_NAME = ""
        _ACTIVE_RUNTIME = (PROJECT_DIR, CURRENT_PROJECT_NAME, CURRENT_EPISODE_NAME)
        _ensure_project_meta(CURRENT_PROJECT_NAME)
        return PROJECT_DIR
    return set_current_project_name(str(path_or_name or "current"))


def get_output_dir():
    return OUTPUT_DIR


def get_projects_dir():
    return PROJECTS_DIR


def get_episodes_dir():
    return EPISODES_DIR


def get_config():
    return CONFIG
