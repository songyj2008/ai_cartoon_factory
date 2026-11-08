import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from services import context
from generation import account_queue
from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.continuity.revisions import revision


def write(root, data):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'video_jobs.json').write_text(json.dumps(data), encoding='utf-8')


def test_shared_account_admission_is_atomic_across_projects(monkeypatch, tmp_path):
    roots = [tmp_path / 'outputs' / name for name in ('A', 'B')]
    monkeypatch.setattr(context, 'OUTPUT_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(context, 'EPISODES_DIR', tmp_path / 'episodes')
    pipe = MiniMaxH3Ref2VAPipeline()
    for index, root in enumerate(roots):
        row = {'segment_uid': f's{index}', 'segment_index': 1, 'model_id': pipe.spec.id,
               'generation_config': {'prompt_override': 'saved prompt', 'duration_sec': 4},
               'job': {'final_prompt': 'saved prompt'}, 'workflow_path': 'saved.json'}
        row['workflow_source_revision'] = revision(row)
        write(root, {'default_model_id': pipe.spec.id, 'segments': [row]})
    monkeypatch.setattr('workflow.video_merge.load_video_jobs',
                        lambda: json.loads((context.get_project_dir() / 'video_jobs.json').read_text()))
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda data: write(context.get_project_dir(), data))
    monkeypatch.setattr(pipe, '_submit_provider', lambda *a: SimpleNamespace(name='runninghub'))
    monkeypatch.setattr('generation.scheduler.GenerationScheduler.concurrency_limit', lambda *a: 1)
    monkeypatch.setattr('workflow.runninghub_usage.refresh_runninghub_usage_summaries', lambda *a: None)
    sent = []
    def submit(provider, row, **kwargs):
        sent.append(context.get_project_dir())
        row.update(status='running', task_id='remote-'+row['segment_uid'], backend='runninghub')
    monkeypatch.setattr(pipe, '_submit_segment', submit)
    barrier = Barrier(2)
    def run(root):
        with context.pin_runtime((root, root.name, '')):
            barrier.wait(timeout=5)
            return pipe.submit_saved({1}, wait=False)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, roots))
    rows = [json.loads((root / 'video_jobs.json').read_text())['segments'][0] for root in roots]
    assert len(sent) == 1
    assert sorted(row['status'] for row in rows) == ['queued', 'running']
    assert next(row for row in rows if row['status'] == 'queued')['video_queue_entered_at'] > 0


def test_fifo_across_drafts_counts_hidden_active_render_once(monkeypatch, tmp_path):
    current = tmp_path / 'outputs' / 'A'
    other = tmp_path / 'episodes' / 'B' / 'draft'
    monkeypatch.setattr(context, 'OUTPUT_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(context, 'EPISODES_DIR', tmp_path / 'episodes')
    other.mkdir(parents=True)
    (other / '.draft').touch()
    write(other, {'segments': [
        {'backend': '', 'status': 'needs_regenerate', 'renders': [
            {'backend': 'runninghub', 'status': 'running', 'submission': {'task_id': 'remote'}}]},
        {'segment_index': 2, 'backend': 'runninghub', 'status': 'queued', 'video_queue_entered_at': 1}]})
    row = {'segment_index': 1, 'video_queue_entered_at': 2}
    data = {'segments': [row]}
    with context.pin_runtime((current, 'A', '')):
        assert account_queue.admitted_indices(data, [row], model_id='h3', limit=2) == set()
        assert account_queue.admitted_indices(data, [row], model_id='h3', limit=3) == {1}


def test_queue_files_are_not_modified_by_admission(monkeypatch, tmp_path):
    monkeypatch.setattr(account_queue, 'runtime_paths', lambda: [])
    row = {'segment_index': 1, 'generation_config': {'prompt_override': 'unchanged'}}
    before = copy.deepcopy(row)
    assert account_queue.admitted_indices({'segments': [row]}, [row], model_id='h3', limit=1) == {1}
    row.pop('video_queue_entered_at')
    assert row == before


def test_ltx_uses_same_account_admission_and_keeps_excess_local(monkeypatch, tmp_path):
    from workflow import segment_runner
    current = tmp_path / 'outputs' / 'A'
    other = tmp_path / 'outputs' / 'B'
    monkeypatch.setattr(context, 'OUTPUT_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(context, 'EPISODES_DIR', tmp_path / 'episodes')
    write(other, {'segments': [{'backend': 'runninghub', 'status': 'running', 'task_id': 'remote-h3'}]})
    data = {'segments': [{'segment_index': 1, 'part_id': 'part_001',
                         'job': {'manual_full_prompt': True, 'final_prompt': 'saved prompt', 'duration_sec': 4}}]}
    monkeypatch.setattr(segment_runner, 'load_video_jobs', lambda: copy.deepcopy(data))
    monkeypatch.setattr(segment_runner, 'save_video_jobs', lambda value: write(current, value))
    monkeypatch.setattr(segment_runner, 'refresh_runninghub_usage_summaries', lambda *a: None)
    monkeypatch.setattr(segment_runner, '_video_submit_concurrency_limit', lambda *a: 1)
    monkeypatch.setattr(segment_runner._WORKFLOW_ADAPTER, 'build_and_optionally_submit',
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError('account already occupied')))
    with context.pin_runtime((current, 'A', '')):
        result = segment_runner.submit_saved_licon_msr_segments({1}, backend='runninghub', wait=False)
    assert result['segments'][0]['status'] == 'queued'
    assert result['segments'][0]['video_queue_entered_at'] > 0
