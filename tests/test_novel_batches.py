import copy
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import context, novel_batches, text_jobs


@pytest.fixture
def batch_runtime(tmp_path, monkeypatch):
    from services import project_assets, project_bible, ui_run_config, web_llm_bridge
    from pipeline import novel_segments
    from generation import service
    from ui import project_handlers

    source = tmp_path / 'project'
    meta = tmp_path / 'meta'
    source.mkdir()
    meta.mkdir()
    (meta / 'ui_run_config.json').write_text('{"default_video_model_id":"minimax_h3_local_ref2va"}', encoding='utf-8')
    (meta / 'project_bible.json').write_text('{"title":"test"}', encoding='utf-8')
    routes = {'default_providers': ['deepseek'], 'overrides': {},
              'concurrency': {'total': 3, 'deepseek': 2, 'chatgpt': 2, 'doubao': 2}}
    state = SimpleNamespace(source=source, meta=meta, routes=routes, beat_calls=0,
                            segment_calls=[], fail_index=None, actual_count=3, args=['novel', '24s', 24, 3])

    def metadata(name):
        job = text_jobs.current()
        return Path(job['work']) / 'project_meta' / name if job else meta / name

    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    monkeypatch.setattr(project_assets, 'sync_generation_assets', lambda: None)
    monkeypatch.setattr(project_bible, 'project_bible_path', lambda: metadata('project_bible.json'))
    monkeypatch.setattr(ui_run_config, 'ui_run_config_path', lambda *_: metadata('ui_run_config.json'))
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'asset_index.json')
    monkeypatch.setattr(web_llm_bridge, 'get_settings', lambda: copy.deepcopy(routes))
    monkeypatch.setattr(project_handlers, '_save_duration_state', lambda *_: 24)

    def beats(*_):
        state.beat_calls += 1
        return {'beats': [{'id': str(index), 'plot': f'event {index}', 'estimated_duration_sec': 24 // state.actual_count}
                          for index in range(1, state.actual_count + 1)]}

    def prepare(data, segment_indices):
        from services.file_utils import save_json
        index = next(iter(segment_indices))
        state.segment_calls.append(index)
        if index == state.fail_index:
            raise ValueError(f'Segment {index} invalid')
        path = context.get_project_dir() / 'video_jobs.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['segments'] = [row for row in payload['segments'] if row['segment_index'] != index]
        payload['segments'].append({'segment_index': index, 'segment_uid': f'seg-{index}',
                                    'beat_uid': data['beats'][index - 1]['beat_uid'],
                                    'h3_prompt_plan': {'shots': [{'action': f'end {index}'}]},
                                    'job': {'prompt': f'prompt {index}', 'duration_sec': data['beats'][index - 1]['estimated_duration_sec']}})
        save_json('video_jobs.json', payload)

    monkeypatch.setattr(novel_segments, 'generate_novel_beats', beats)
    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(prepare_segments=prepare))
    return state


def submit(state, args=None, editors=None):
    with context.pin_runtime((state.source, 'test', '')):
        return text_jobs.submit('novel', args or state.args, editors, start=False)


def run(identifier):
    text_jobs.update(identifier, 'running')
    text_jobs._execute(text_jobs.get(identifier))
    return text_jobs.get(identifier)


def next_stage(parent):
    novel_batches.reconcile(parent['id'])
    parent = text_jobs.get(parent['id'])
    return text_jobs.get(parent['stage_tasks'][-1])


def start(state):
    parent = submit(state)
    run(parent['id'])
    return text_jobs.get(parent['id'])


def saved_project_without_tasks(state):
    batch_id = str(uuid.uuid4())
    beats = {'beats': [{'id': i, 'beat_uid': f'beat-{i}', 'plot': f'event {i}',
                        'estimated_duration_sec': 8} for i in range(1, 7)]}
    segments = [{'segment_index': i, 'segment_uid': f'seg-{i}', 'beat_uid': f'beat-{i}',
                 'novel_batch_id': batch_id, 'generation_config': {'asset_manifest': [{'id': f'ref-{i}'}]},
                 'job': {'prompt': f'edited prompt {i}', 'duration_sec': 8}}
                for i in (1, 2, 3, 4, 6)]
    (state.source / 'beats.json').write_text(json.dumps(beats), encoding='utf-8')
    (state.source / 'video_jobs.json').write_text(json.dumps(
        {'novel_batch_id': batch_id, 'segments': segments}), encoding='utf-8')
    return batch_id


def test_retry_missing_segment_after_task_history_was_cleared(batch_runtime):
    state = batch_runtime
    batch_id = saved_project_without_tasks(state)
    before_beats = (state.source / 'beats.json').read_bytes()
    before = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    with context.pin_runtime((state.source, 'test', '')):
        task = novel_batches.retry_segment(batch_id, 5)
        duplicate = novel_batches.retry_segment(batch_id, 5)
    assert task['kind'] == 'novel_segment' and task['args'] == [5]
    assert task['segment']['beat_uid'] == 'beat-5'
    assert duplicate['id'] == task['id'] and duplicate['duplicate']
    assert len(text_jobs.list_jobs()) == 1
    assert run(task['id'])['state'] == 'applied'
    after = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    assert [row['segment_index'] for row in after['segments']] == [1, 2, 3, 4, 5, 6]
    for row in before['segments']:
        saved = next(item for item in after['segments'] if item['segment_index'] == row['segment_index'])
        for key in ('segment_uid', 'beat_uid', 'novel_batch_id', 'generation_config'):
            assert saved[key] == row[key]
        assert saved['job']['prompt'] == row['job']['prompt']
        assert saved['job']['duration_sec'] == row['job']['duration_sec']
    assert (state.source / 'beats.json').read_bytes() == before_beats
    assert state.segment_calls == [5] and state.beat_calls == 0


def test_detached_retry_rejects_unresolved_task(batch_runtime):
    state = batch_runtime
    batch_id = saved_project_without_tasks(state)
    with context.pin_runtime((state.source, 'test', '')):
        task = novel_batches.retry_segment(batch_id, 5)
        text_jobs.update(task['id'], 'running')
        text_jobs.update(task['id'], 'needs_attention')
        with pytest.raises(ValueError, match='等待处理'):
            novel_batches.retry_segment(batch_id, 5)
        text_jobs.cancel(task['id'])
        assert novel_batches.retry_segment(batch_id, 5)['id'] != task['id']


def test_detached_retry_still_checks_source_changes_before_apply(batch_runtime):
    state = batch_runtime
    batch_id = saved_project_without_tasks(state)
    with context.pin_runtime((state.source, 'test', '')):
        task = novel_batches.retry_segment(batch_id, 5)
    beats = json.loads((state.source / 'beats.json').read_text(encoding='utf-8'))
    beats['beats'][4]['plot'] = 'edited while request was running'
    (state.source / 'beats.json').write_text(json.dumps(beats), encoding='utf-8')
    result = run(task['id'])
    assert result['state'] == 'ready' and '已变化' in result['apply_error']
    assert len(json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']) == 5


def test_detached_retry_validates_project_index_and_active_video(batch_runtime, monkeypatch):
    state = batch_runtime
    batch_id = saved_project_without_tasks(state)
    with context.pin_runtime((state.source, 'test', '')):
        with pytest.raises(ValueError, match='不匹配'):
            novel_batches.retry_segment(str(uuid.uuid4()), 5)
        with pytest.raises(ValueError, match='Beat 不存在'):
            novel_batches.retry_segment(batch_id, 7)
        monkeypatch.setattr('workflow.runninghub_queue.has_active_submission', lambda row: bool(row))
        with pytest.raises(ValueError, match='视频正在生成'):
            novel_batches.retry_segment(batch_id, 4)


def test_publish_each_stage_and_resume_only_failed_segment(batch_runtime):
    state = batch_runtime
    parent = start(state)
    assert parent['state'] == 'waiting_children'
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    assert (state.source / 'beats.json').is_file()
    beats_task_id = text_jobs.get(parent['id'])['stage_records']['beats']
    assert text_jobs.result(beats_task_id)['published_beats_text']
    assert json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))['segments'] == []
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    assert len(json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']) == 1
    state.fail_index = 2
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    assert text_jobs.get(parent['id'])['state'] == 'error'
    assert text_jobs.get(parent['id'])['segment_processed'] == 3
    assert state.beat_calls == 1

    state.fail_index = None
    resumed = submit(state, editors={'beats_editor': 'newly displayed Beats'})
    assert resumed['id'] == parent['id'] and resumed['resumed']
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    assert len(json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']) == 3
    novel_batches.reconcile(parent['id'])
    final = text_jobs.get(parent['id'])
    assert final['state'] == 'applied' and final['segment_completed'] == 3
    assert state.beat_calls == 1 and state.segment_calls == [1, 2, 3, 2]
    assert submit(state)['already_complete']


def test_waiting_batch_uses_no_worker_or_web_queue_slot(batch_runtime):
    parent = start(batch_runtime)
    child = next_stage(parent)
    assert child['kind'] == 'novel_beats'
    tasks = text_jobs.list_jobs()
    assert sum(item['state'] == 'queued' for item in tasks) == 1
    assert sum(item['kind'] == 'novel_segment' for item in tasks) == 0
    assert text_jobs.get(parent['id'])['children'] == []
    assert submit(batch_runtime)['duplicate']


def test_large_budget_deviation_is_advisory_and_schedules_all_segments(batch_runtime):
    state = batch_runtime
    state.actual_count = 6
    parent = start(state)
    run(next_stage(parent)['id'])
    novel_batches.reconcile(parent['id'])
    parent = text_jobs.get(parent['id'])
    assert parent['state'] == 'waiting_children'
    assert '参考预算提示' in parent['message'] and '实际 6 段' in parent['message']
    assert state.beat_calls == 1 and state.segment_calls == []
    assert len(json.loads((state.source / 'beats.json').read_text(encoding='utf-8'))['beats']) == 6
    for _ in range(6):
        assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    assert text_jobs.get(parent['id'])['state'] == 'applied'
    assert state.segment_calls == [1, 2, 3, 4, 5, 6]


def test_segment_retiming_reports_total_budget_without_stopping(batch_runtime):
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    run(next_stage(parent)['id'])
    path = state.source / 'video_jobs.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['segments'][0]['job']['duration_sec'] = 15
    path.write_text(json.dumps(saved), encoding='utf-8')
    novel_batches.reconcile(parent['id'])
    stopped = text_jobs.get(parent['id'])
    assert stopped['state'] == 'waiting_children' and '预计 31 秒' in stopped['message']
    assert next_stage(parent)['batch_index'] == 2
    assert state.segment_calls == [1]


def test_explicit_retry_only_replaces_target_and_preserves_later_segments(batch_runtime):
    state = batch_runtime
    parent = start(state)
    for _ in range(4):
        run(next_stage(parent)['id'])
    novel_batches.reconcile(parent['id'])
    old_records = copy.deepcopy(text_jobs.get(parent['id'])['stage_records'])
    before = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    with context.pin_runtime((state.source, 'test', '')):
        retried = novel_batches.retry_segment(parent['id'], 2)
    assert retried['batch_index'] == 2
    assert retried['id'] != old_records['segment:2']
    during = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    assert during == before
    assert run(retried['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    records = text_jobs.get(parent['id'])['stage_records']
    assert records['segment:1'] == old_records['segment:1']
    assert records['segment:3'] == old_records['segment:3']
    assert state.segment_calls == [1, 2, 3, 2]


def test_uncertain_original_request_does_not_replay_or_block_other_beats(batch_runtime, monkeypatch):
    from generation import service
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    original = service.get_generation_service().prepare_segments
    def prepare(data, segment_indices):
        if segment_indices == {2}:
            raise text_jobs.NeedsAttentionError('原网页请求状态不明')
        return original(data, segment_indices)
    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(prepare_segments=prepare))
    run(next_stage(parent)['id'])
    uncertain = run(next_stage(parent)['id'])
    assert uncertain['state'] == 'needs_attention'
    third = next_stage(parent)
    assert third['batch_index'] == 3
    run(third['id'])
    novel_batches.reconcile(parent['id'])
    assert text_jobs.get(parent['id'])['state'] == 'needs_attention'
    with context.pin_runtime((state.source, 'test', '')):
        with pytest.raises(ValueError, match='等待处理'):
            novel_batches.retry_segment(parent['id'], 2)


def test_segment_tasks_follow_actual_beat_count_not_reference(batch_runtime):
    state = batch_runtime
    state.actual_count = 2
    parent = start(state)
    for _ in range(3):
        assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    finished = text_jobs.get(parent['id'])
    assert finished['state'] == 'applied'
    assert finished['segment_total'] == finished['segment_completed'] == 2
    assert state.segment_calls == [1, 2]


def test_recovery_does_not_recreate_completed_stages(batch_runtime):
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    first = next_stage(parent)
    run(first['id'])
    text_jobs.recover_interrupted()
    second = next_stage(parent)
    assert second['batch_index'] == 2
    assert state.beat_calls == 1 and state.segment_calls == [1]
    assert text_jobs.get(first['id'])['state'] == 'applied'


def test_changed_novel_or_model_creates_new_batch(batch_runtime):
    state = batch_runtime
    first = start(state)
    assert submit(state, args=['different', '24s', 24, 3])['id'] != first['id']
    (state.meta / 'ui_run_config.json').write_text('{"default_video_model_id":"ltx2.3"}', encoding='utf-8')
    assert submit(state)['id'] != first['id']


def test_editing_target_beat_during_generation_prevents_publish(batch_runtime):
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    child = next_stage(parent)
    beats_path = state.source / 'beats.json'
    data = json.loads(beats_path.read_text(encoding='utf-8'))
    data['beats'][0]['plot'] = 'user edit'
    beats_path.write_text(json.dumps(data), encoding='utf-8')
    result = run(child['id'])
    assert result['state'] == 'ready' and '已变化' in result['apply_error']
    assert json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))['segments'] == []


def test_cancel_stops_children_and_new_click_resumes_without_redoing_beats(batch_runtime):
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    pending = next_stage(parent)
    text_jobs.cancel(parent['id'])
    assert text_jobs.get(pending['id'])['state'] == 'cancelled'
    resumed = submit(state)
    assert resumed['id'] == parent['id']
    assert next_stage(parent)['id'] != pending['id']
    assert state.beat_calls == 1


def test_changed_completed_beat_replans_only_target_and_keeps_following(batch_runtime):
    state = batch_runtime
    parent = start(state)
    for _ in range(4):
        assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    path = state.source / 'beats.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['beats'][1]['plot'] = 'edited second event'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert submit(state)['resumed']
    assert next_stage(parent)['batch_index'] == 2
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    assert state.beat_calls == 1 and state.segment_calls == [1, 2, 3, 2]
    jobs = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    assert jobs['segments'][2]['job']['prompt'] == 'prompt 3'
    assert 'continuity_note' not in jobs['segments'][2]


def test_clearing_finished_history_keeps_stages_needed_by_live_batch(batch_runtime):
    parent = start(batch_runtime)
    beats = run(next_stage(parent)['id'])
    text_jobs.clear_finished()
    assert text_jobs.get(beats['id'])['state'] == 'applied'
    assert next_stage(parent)['batch_index'] == 1


def test_old_failed_snapshot_recovers_beats_without_model_call(batch_runtime):
    state = batch_runtime
    parent = start(state)
    old_work = text_jobs.root() / str(uuid.uuid4()) / 'work'
    old_work.mkdir(parents=True)
    old = copy.deepcopy(parent)
    old.pop('batch_version')
    old['work'] = str(old_work)
    old['id'] = old_work.parent.name
    (old_work / 'beats.json').write_text(json.dumps({
        'generation_source': 'novel_beats',
        'beats': [{'id': 'saved', 'plot': 'already generated', 'estimated_duration_sec': 8}]
    }), encoding='utf-8')
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (old['id'], 'error', time.time()-100, time.time()-100, json.dumps(old)))
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    assert state.beat_calls == 0
    assert text_jobs.get(parent['id'])['adopted_beats_from'] == old['id']
    assert json.loads((state.source / 'beats.json').read_text(encoding='utf-8'))['beats'][0]['id'] == 'saved'


def test_uncertain_web_stage_is_never_automatically_resent(batch_runtime):
    parent = start(batch_runtime)
    child = next_stage(parent)
    text_jobs.update(child['id'], 'needs_attention', attention_reason='web_timeout')
    novel_batches.reconcile(parent['id'])
    again = submit(batch_runtime)
    assert again['duplicate']
    assert again['state'] == 'needs_attention'
    assert text_jobs.get(parent['id'])['stage_tasks'] == [child['id']]


def test_same_input_resumes_after_beats_publish_changes_its_own_metadata(batch_runtime, monkeypatch):
    from pipeline import novel_segments
    from services.project_bible import project_bible_path
    original = novel_segments.generate_novel_beats

    def generate(*args):
        result = original(*args)
        project_bible_path().write_text('{"title":"generated bible"}', encoding='utf-8')
        return result

    monkeypatch.setattr(novel_segments, 'generate_novel_beats', generate)
    parent = start(batch_runtime)
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    # No dispatcher tick between publish and this click.
    assert submit(batch_runtime)['id'] == parent['id']
    assert batch_runtime.beat_calls == 1


def test_real_h3_compiler_can_publish_segments_incrementally(batch_runtime, monkeypatch):
    from generation import service
    from generation.pipelines.minimax_h3 import prompt_planner
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline

    monkeypatch.setattr('workflow.asset_matcher.match_beat_assets', lambda beat, **_: beat)
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.auto_bind_asset_manifest', lambda beat: [])
    monkeypatch.setattr('generation.pipelines.minimax_h3.auto_binder.enrich_asset_manifest_metadata', lambda values: values)
    monkeypatch.setattr('services.character_voice.attach_character_voices', lambda values, *_: values)
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_compiler.load_project_bible', lambda: {})
    pipeline = MiniMaxH3Ref2VAPipeline()
    pipeline._config = {'video_submit_backend': 'runninghub'}

    def model(system, user, **_):
        payload = json.loads(user)
        return {'shot_mode': 'one_take', 'shots': [{'start_sec': 0, 'end_sec': payload['duration_sec'],
                                                   'action': '人物保持站位。', 'subject': '人物'}]}, 0

    monkeypatch.setattr(prompt_planner, '_call_model', model)
    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(
        prepare_segments=lambda data, segment_indices: pipeline.generate(data, submit=False, workflow_only=True,
                                                                          segment_indices=segment_indices)))
    parent = start(batch_runtime)
    for _ in range(4):
        child = run(next_stage(parent)['id'])
        assert child['state'] == 'applied', child.get('message') or child.get('apply_error')
    novel_batches.reconcile(parent['id'])
    assert text_jobs.get(parent['id'])['state'] == 'applied'
    payload = json.loads((batch_runtime.source / 'video_jobs.json').read_text(encoding='utf-8'))
    assert [row['segment_index'] for row in payload['segments']] == [1, 2, 3]
    assert all(row['beat_uid'] for row in payload['segments'])


def test_segment_input_does_not_depend_on_scene_group_or_predecessor(tmp_path):
    beats = {'beats': [{'plot': 'first'}, {'plot': 'second'}],
             'scene_contexts': [{'id': 'office', 'scene_id': 'room'}]}
    (tmp_path / 'beats.json').write_text(json.dumps(beats), encoding='utf-8')
    path = tmp_path / 'video_jobs.json'
    path.write_text(json.dumps({'segments': []}), encoding='utf-8')
    before = novel_batches._stage_input(tmp_path, 2)
    path.write_text(json.dumps({'segments': [{'segment_index': 1, 'scene_context_id': 'office',
        'h3_prompt_plan': {'shots': [{'action': 'changed'}]},
        'generation_config': {'prompt_override': 'edited previous prompt'},
        'h3_continuity': {'end_state': 'changed'}}],
        'h3_scene_continuity': {'contexts': {'office': {'initial_pending': True}}}}), encoding='utf-8')
    assert novel_batches._stage_input(tmp_path, 2) == before
    beats['beats'][1]['plot'] = 'edited target'
    (tmp_path / 'beats.json').write_text(json.dumps(beats), encoding='utf-8')
    assert novel_batches._stage_input(tmp_path, 2) != before


def test_replacement_preserves_old_results_until_each_new_prompt_succeeds(batch_runtime, monkeypatch):
    from generation import service
    state = batch_runtime
    saved_project_without_tasks(state)
    path = state.source / 'video_jobs.json'
    old = json.loads(path.read_text(encoding='utf-8'))
    for row in old['segments']:
        row['segment_uid'] = 'old-' + row['segment_uid']
    path.write_text(json.dumps(old), encoding='utf-8')
    parent = start(state)
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    published = json.loads(path.read_text(encoding='utf-8'))
    assert published['segments'] == old['segments']
    original = service.get_generation_service().prepare_segments

    def prepare(data, segment_indices):
        index = next(iter(segment_indices))
        private = json.loads((context.get_project_dir() / 'video_jobs.json').read_text(encoding='utf-8'))
        # A different Beat must not inherit the old slot's prompt or references.
        assert not any(row['segment_index'] == index for row in private['segments'])
        return original(data, segment_indices)

    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(prepare_segments=prepare))
    state.fail_index = 2
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    after_first = json.loads(path.read_text(encoding='utf-8'))
    assert next(row for row in after_first['segments'] if row['segment_index'] == 1)['job']['prompt'] == 'prompt 1'
    assert after_first['retired_segments'][0]['job']['prompt'] == 'edited prompt 1'
    failed = run(next_stage(parent)['id'])
    assert failed['result']['previous_preserved'] is True
    assert failed['result']['planning_state'] == 'invalid'
    assert json.loads(path.read_text(encoding='utf-8')) == after_first
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    assert text_jobs.get(parent['id'])['state'] == 'error'
    assert text_jobs.get(parent['id'])['segment_failed'] == 1
    assert any(row['segment_index'] == 6 for row in json.loads(path.read_text(encoding='utf-8'))['segments'])
    state.fail_index = None
    assert submit(state)['resumed']
    assert next_stage(parent)['batch_index'] == 2
    assert run(next_stage(parent)['id'])['state'] == 'applied'
    novel_batches.reconcile(parent['id'])
    final = json.loads(path.read_text(encoding='utf-8'))
    assert text_jobs.get(parent['id'])['state'] == 'applied'
    assert [row['segment_index'] for row in final['segments']] == [1, 2, 3]
    assert {row['job']['prompt'] for row in final['retired_segments']} == {
        'edited prompt 1', 'edited prompt 2', 'edited prompt 3', 'edited prompt 4', 'edited prompt 6'}


def test_failed_retry_preserves_usable_prompt_and_is_not_counted_as_success(batch_runtime):
    state = batch_runtime
    batch_id = saved_project_without_tasks(state)
    path = state.source / 'video_jobs.json'
    before = json.loads(path.read_text(encoding='utf-8'))
    state.fail_index = 2
    with context.pin_runtime((state.source, 'test', '')):
        task = novel_batches.retry_segment(batch_id, 2)
    result = run(task['id'])
    assert result['state'] == 'applied'
    assert result['result']['planning_state'] == 'invalid'
    assert result['result']['previous_preserved'] is True
    assert '已保留原提示词' in result['message']
    assert json.loads(path.read_text(encoding='utf-8')) == before


def test_replacement_candidate_cannot_overwrite_concurrent_old_prompt_edit(batch_runtime, monkeypatch):
    from generation import service
    state = batch_runtime
    saved_project_without_tasks(state)
    parent = start(state)
    run(next_stage(parent)['id'])
    task = next_stage(parent)
    original = service.get_generation_service().prepare_segments
    path = state.source / 'video_jobs.json'

    def prepare(data, segment_indices):
        result = original(data, segment_indices)
        live = json.loads(path.read_text(encoding='utf-8'))
        live['segments'][0]['job']['prompt'] = 'concurrent manual edit'
        path.write_text(json.dumps(live), encoding='utf-8')
        return result

    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(prepare_segments=prepare))
    result = run(task['id'])
    assert result['state'] == 'ready' and result['apply_error']
    assert json.loads(path.read_text(encoding='utf-8'))['segments'][0]['job']['prompt'] == 'concurrent manual edit'


def test_empty_prompt_is_a_failed_segment_even_if_provider_reports_ready(batch_runtime, monkeypatch):
    from generation import service
    state = batch_runtime
    parent = start(state)
    run(next_stage(parent)['id'])
    original = service.get_generation_service().prepare_segments
    def prepare(data, segment_indices):
        original(data, segment_indices)
        path = context.get_project_dir() / 'video_jobs.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['segments'][0].update(planning_state='ready', job={'prompt': '', 'duration_sec': 8})
        path.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(service, 'get_generation_service', lambda: SimpleNamespace(prepare_segments=prepare))
    result = run(next_stage(parent)['id'])
    assert result['result']['planning_state'] == 'invalid'
    saved = json.loads((state.source / 'video_jobs.json').read_text(encoding='utf-8'))
    assert saved['segments'][0]['planning_state'] == 'invalid'
    assert saved['segments'][0]['prompt_saved'] is False
    assert next_stage(parent)['batch_index'] == 2
