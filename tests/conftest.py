"""Never let tests inherit the desktop app's active output directory."""
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    import sys
    from services import context
    from workflow import video_merge
    from services import text_jobs
    from services import web_llm_bridge, web_video_bridge
    # Route registration must not start a daemon that consumes another test's
    # monkeypatched queue. Tests drive claims/execution explicitly.
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'text_operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    # Browser queue recovery/cleanup must never connect to the running app's DB.
    monkeypatch.setattr(web_llm_bridge, 'BRIDGE_DIR', tmp_path / 'web_llm_bridge')
    monkeypatch.setattr(web_video_bridge, '_DB', tmp_path / 'web_video_bridge.sqlite3')
    # Runtime pinning alone does not isolate metadata created for that runtime.
    # Update already imported aliases as well as the source module.
    isolated_paths = {
        'PROJECTS_DIR': tmp_path / 'projects',
        'OUTPUT_DIR': tmp_path / 'outputs',
        'EPISODES_DIR': tmp_path / 'episodes',
        'LAST_PROJECT_PATH': tmp_path / 'projects' / 'last_project.json',
    }
    for name, module in list(sys.modules.items()):
        if name.startswith(('services.', 'ui.', 'workflow.')):
            for attribute, path in isolated_paths.items():
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, path)
    # Selection APIs use the active globals, even while a worker is pinned.
    # Restore them on teardown so selection tests cannot leak into later tests.
    monkeypatch.setattr(context, 'CURRENT_PROJECT_NAME', '__pytest__')
    monkeypatch.setattr(context, 'CURRENT_EPISODE_NAME', '')
    monkeypatch.setattr(context, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (tmp_path, '__pytest__', ''))
    # Resolve dynamically: per-test monkeypatches must affect imported aliases.
    monkeypatch.setattr(video_merge, 'get_project_dir', lambda: context.get_project_dir())
    with context.pin_runtime((tmp_path, '__pytest__', '')):
        yield
