import json
import runpy
import shutil
from pathlib import Path

import pytest

from services import context


@pytest.fixture
def isolated_selection(tmp_path, monkeypatch):
    # These tests exercise live selection changes rather than a pinned worker.
    runtime_token = context._PINNED_RUNTIME.set(None)
    for name, value in {'PROJECTS_DIR': tmp_path / 'projects', 'OUTPUT_DIR': tmp_path / 'outputs',
                        'EPISODES_DIR': tmp_path / 'episodes',
                        'LAST_PROJECT_PATH': tmp_path / 'projects' / 'last_project.json',
                        'CURRENT_PROJECT_NAME': 'current', 'CURRENT_EPISODE_NAME': '',
                        'PROJECT_DIR': tmp_path / 'outputs' / 'current',
                        '_ACTIVE_RUNTIME': (tmp_path / 'outputs' / 'current', 'current', '')}.items():
        monkeypatch.setattr(context, name, value)
    try:
        yield tmp_path
    finally:
        context._PINNED_RUNTIME.reset(runtime_token)


def test_episode_selection_is_saved_per_project_and_current_generation_is_explicit(isolated_selection):
    context.set_current_project_name('project-a')
    context.set_current_episode('episode-003')
    context.set_current_project_name('project-b')
    context.set_current_episode('episode-002')
    context.set_current_project_name('project-a')
    assert context.get_current_episode_name() == 'episode-003'
    context.set_current_project_name('project-b')
    assert context.get_current_episode_name() == 'episode-002'
    context.set_current_episode('')
    context.set_current_project_name('project-a')
    context.set_current_project_name('project-b')
    assert context.get_current_episode_name() == ''
    saved = json.loads(context.LAST_PROJECT_PATH.read_text(encoding='utf-8'))
    assert saved['project_episodes'] == {'project-a': 'episode-003', 'project-b': ''}


def test_startup_restores_saved_episode_and_its_files(tmp_path):
    app = tmp_path / 'app'
    services = app / 'services'
    services.mkdir(parents=True)
    script = services / 'context.py'
    shutil.copyfile(context.__file__, script)
    (app / 'config.json').write_text('{}', encoding='utf-8')
    projects = app / 'projects'
    projects.mkdir()
    (projects / 'last_project.json').write_text(json.dumps({
        'project_name': 'test', 'project_episodes': {'test': 'episode-003'},
    }), encoding='utf-8')
    episode = app / 'episodes' / 'test' / 'episode-003'
    episode.mkdir(parents=True)
    (episode / '.draft').touch()
    (episode / 'story.json').write_text('{"title":"episode story"}', encoding='utf-8')
    restored = runpy.run_path(str(script))
    assert restored['get_current_episode_name']() == 'episode-003'
    assert restored['get_project_dir']() == episode
    assert json.loads((restored['get_project_dir']() / 'story.json').read_text())['title'] == 'episode story'


def test_removed_episode_is_not_recreated_on_restore(isolated_selection):
    context.set_current_project_name('test')
    context.set_current_episode('removed')
    path = context.get_project_dir()
    path.rename(path.with_name('backup'))
    context.set_current_project_name('test')
    assert context.get_current_episode_name() == ''
    assert not path.exists()


@pytest.mark.parametrize('saved', [
    {'project_name': 'test'},
    {'project_name': 'test', 'episode_name': '../outside'},
    {'project_name': 'test', 'project_episodes': {'test': 'missing'}},
])
def test_old_or_invalid_selection_safely_uses_current_generation(isolated_selection, saved):
    context.PROJECTS_DIR.mkdir(parents=True)
    context.LAST_PROJECT_PATH.write_text(json.dumps(saved), encoding='utf-8')
    context.set_current_project_name('test')
    assert context.get_current_episode_name() == ''


def test_background_runtime_pin_does_not_change_saved_ui_selection(isolated_selection):
    context.set_current_project_name('test')
    context.set_current_episode('episode-003')
    before = context.LAST_PROJECT_PATH.read_bytes()
    with context.pin_runtime((Path(isolated_selection) / 'worker', 'other-project', 'other-episode')):
        assert context.get_current_episode_name() == 'other-episode'
    assert context.get_current_episode_name() == 'episode-003'
    assert context.LAST_PROJECT_PATH.read_bytes() == before


def test_page_load_restores_selectors_with_the_runtime_contents(monkeypatch):
    from ui import bindings, project_handlers
    monkeypatch.setattr(bindings, '_restore_direct_state', lambda: tuple(range(25)))
    monkeypatch.setattr(context, 'get_current_project_name', lambda: 'test')
    monkeypatch.setattr(bindings, 'get_current_episode_name', lambda: 'episode-003')
    monkeypatch.setattr(project_handlers, '_project_choices', lambda: ['test'])
    monkeypatch.setattr(bindings, '_episode_choices', lambda *_: [('draft', 'episode-003')])
    restored = bindings._restore_page_state()
    assert restored[:25] == tuple(range(25))
    assert restored[-2]['value'] == 'test'
    assert restored[-1]['value'] == 'episode-003'
