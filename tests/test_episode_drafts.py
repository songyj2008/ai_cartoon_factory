import json
from pathlib import Path

from services import context, ui_run_config
from ui import project_handlers


def test_drafts_are_independent_and_archive_in_place(tmp_path, monkeypatch):
    episodes = tmp_path / 'episodes'
    monkeypatch.setattr(project_handlers, 'EPISODES_DIR', episodes)
    monkeypatch.setattr(context, 'EPISODES_DIR', episodes)
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (tmp_path / 'current', 'test', ''))
    monkeypatch.setattr(project_handlers, '_sidecar_project_dir', lambda _: tmp_path / 'meta')
    first = project_handlers.create_episode_draft('first')['value']
    second = project_handlers.create_episode_draft('second')['value']
    with context.pin_runtime((episodes / 'test' / first, 'test', first)):
        assert not context.is_archive_episode_active()
        ui_run_config.update_ui_run_config(topic='first topic')
        result = project_handlers.archive_current_generation()
        assert result[2] == str(episodes / 'test' / first)
        assert context.is_archive_episode_active()
    with context.pin_runtime((episodes / 'test' / second, 'test', second)):
        assert not context.is_archive_episode_active()
        assert ui_run_config.load_ui_run_config()['topic'] == ''
    assert (episodes / 'test' / first / 'project_meta' / 'ui_run_config.json').is_file()


def test_named_draft_remains_writable_and_frontend_uses_server_readonly_flag(tmp_path):
    draft = tmp_path / '.draft'
    draft.write_text('draft', encoding='utf-8')
    with context.pin_runtime((tmp_path, 'test', 'episode-draft')):
        assert not context.is_archive_episode_active()
        context.assert_current_runtime_writable('生成内容')

    script = Path(__file__).resolve().parents[1] / 'ui' / 'scripts.py'
    source = script.read_text(encoding='utf-8')
    assert 'setArchiveReadOnly(Boolean(data.readonly), current)' in source
    assert 'setArchiveReadOnly(Boolean(data.readonly || current), current)' not in source


def test_running_draft_cannot_be_archived(tmp_path, monkeypatch):
    (tmp_path / '.draft').write_text('draft')
    (tmp_path / 'video_jobs.json').write_text(json.dumps({'segments': [{'status': 'running'}]}))
    monkeypatch.setattr(project_handlers, 'EPISODES_DIR', tmp_path / 'episodes')
    with context.pin_runtime((tmp_path, 'test', 'first')):
        assert project_handlers.archive_current_generation()[2] == ''
        assert (tmp_path / '.draft').exists()


def test_worker_polls_unselected_drafts_without_switching_ui(tmp_path, monkeypatch):
    from services import runninghub_worker
    draft = tmp_path / 'episodes' / 'test' / 'second'
    draft.mkdir(parents=True)
    (draft / '.draft').write_text('draft')
    (draft / 'video_jobs.json').write_text('{}')
    current = (tmp_path / 'current', 'test', '')
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', current)
    monkeypatch.setattr(runninghub_worker, 'OUTPUT_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(runninghub_worker, 'EPISODES_DIR', tmp_path / 'episodes')
    observed = []
    monkeypatch.setattr('workflow.runninghub_sync.sync_runninghub_video_jobs', lambda: observed.append(context.get_project_dir()))
    monkeypatch.setattr('workflow.video_merge.finish_requested_video_merge', lambda: None)
    runninghub_worker.RunningHubWorker().tick()
    assert observed == [draft]
    assert context.get_project_dir() == current[0]
