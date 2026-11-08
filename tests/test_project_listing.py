"""Project discovery is read-only and tests never own real project storage."""
from services import context


def test_listing_does_not_create_default_project():
    from ui import project_handlers
    from services.project_assets import list_asset_project_names

    project = context.PROJECTS_DIR / 'my-project'
    project.mkdir(parents=True)
    with context.pin_runtime((context.OUTPUT_DIR / 'my-project', 'my-project', '')):
        assert project_handlers.list_projects() == ['my-project']
        assert list_asset_project_names() == ['my-project']
    assert not (context.PROJECTS_DIR / 'current').exists()
    assert not (context.OUTPUT_DIR / 'current').exists()


def test_selection_writes_only_to_temporary_storage(tmp_path):
    with context.pin_runtime():
        context.set_current_project_name('test')
        context.set_current_episode('episode-001')
    assert context.PROJECTS_DIR == tmp_path / 'projects'
    assert (tmp_path / 'projects' / 'test').is_dir()
    assert (tmp_path / 'episodes' / 'test' / 'episode-001').is_dir()
    assert context.LAST_PROJECT_PATH.is_relative_to(tmp_path)


def test_late_imported_path_alias_is_isolated(tmp_path):
    from services import project_assets
    assert project_assets.PROJECTS_DIR == tmp_path / 'projects'
