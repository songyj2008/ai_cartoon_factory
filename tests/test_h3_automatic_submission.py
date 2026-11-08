"""Saved submission preserves authored content independently of scene analysis."""
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.continuity import preparation
from generation.pipelines.minimax_h3.continuity.revisions import revision
from workflow import video_merge


@pytest.fixture
def saved_scene(monkeypatch, tmp_path):
    # Load route aliases before this fixture replaces context.get_project_dir;
    # otherwise a first API import can retain this test's temporary path.
    import ui.api_routes
    pipe = MiniMaxH3Ref2VAPipeline()
    segment = {'segment_index': 1, 'segment_uid': 'segment_1', 'part_id': 'part_001',
               'model_id': pipe.spec.id, 'scene_context_id': None, 'status': 'success',
               'workflow_path': 'saved.json', 'video_path': 'good.mp4',
               'active_render_id': 'good_render', 'renders': [{'render_id': 'good_render'}],
               'generation_config': {'prompt_override': 'fixed screenplay', 'asset_manifest': [], 'duration_sec': 4},
               'job': {'final_prompt': 'fixed screenplay', 'duration_sec': 4}}
    segment['workflow_source_revision'] = revision(segment)
    payload = {'default_model_id': pipe.spec.id, 'segments': [segment],
               'h3_scene_continuity': {'contexts': {'room': {'initial_state': {}}}}}
    writes, submissions = [], []
    lock = {'depth': 0}
    @contextmanager
    def tracked_lock(*args, **kwargs):
        lock['depth'] += 1
        try:
            yield
        finally:
            lock['depth'] -= 1
    def save(value):
        snapshot = copy.deepcopy(value)
        payload.clear(); payload.update(snapshot); writes.append(snapshot)
    def submit(provider, row, *, wait):
        assert lock['depth'] > 0
        assert row['generation_config']['prompt_override'] == 'fixed screenplay'
        submissions.append(copy.deepcopy(row))
        row.update(status='running', task_id='accepted', backend='runninghub', submitted=True)
    def forbidden(*args, **kwargs):
        pytest.fail('saved video submission must not invoke an LLM or scene preparation')
    monkeypatch.setattr(video_merge, 'video_jobs_lock', tracked_lock)
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: copy.deepcopy(payload))
    monkeypatch.setattr(video_merge, 'save_video_jobs', save)
    monkeypatch.setattr(preparation, 'ensure_scene_state', forbidden)
    monkeypatch.setattr('services.llm.call_llm', forbidden)
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_planner._call_model', forbidden)
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('workflow.runninghub_usage.refresh_runninghub_usage_summaries', lambda value: value)
    monkeypatch.setattr('generation.scheduler.GenerationScheduler.concurrency_limit', lambda *args: 3)
    monkeypatch.setattr(pipe, '_submit_provider', lambda *args: SimpleNamespace(name='runninghub'))
    monkeypatch.setattr(pipe, '_submit_segment', submit)
    return SimpleNamespace(pipe=pipe, payload=payload, writes=writes, submissions=submissions, lock=lock)


@pytest.mark.parametrize('record', [None, {'schema_version':3,'text_pending':True,'source_revision':'stale'},
                                   {'status':'needs_analysis','state_pending':True},
                                   {'record_kind':'planned','schema_version':3,'source_revision':'stale'}])
def test_independent_submission_ignores_unassigned_scene_metadata(saved_scene, record):
    state=saved_scene
    row=state.payload['segments'][0]
    if record is not None: row['h3_continuity']=record
    original=copy.deepcopy(row)
    state.pipe.submit_saved({1},wait=False)
    assert len(state.submissions)==1
    sent=state.submissions[0]
    for key in ('generation_config','job','video_path','renders','h3_continuity'):
        assert sent.get(key)==original.get(key)


@pytest.mark.parametrize('workflow_revision', [None,'stale'])
def test_missing_or_stale_workflow_rebuilds_only_saved_inputs(saved_scene,monkeypatch,workflow_revision):
    state=saved_scene;row=state.payload['segments'][0]
    row.update(workflow_source_revision=workflow_revision,h3_force_replan=True,h3_auto_duration=True)
    if workflow_revision is None:row.pop('workflow_path')
    monkeypatch.setattr(state.pipe,'_load_template',lambda:{})
    def build(template,beat,index,previous,**kwargs):
        assert state.lock['depth']==0
        assert kwargs.get('previous_segment_context') is None
        assert previous['h3_editor_prompt']=='fixed screenplay'
        assert not previous.get('h3_force_replan') and not previous.get('h3_auto_duration')
        assert previous['generation_config']['prompt_override']=='fixed screenplay'
        return {**copy.deepcopy(previous),'workflow_path':'rebuilt.json'}
    monkeypatch.setattr(state.pipe,'_prepare_segment',build)
    state.pipe.submit_saved({1},wait=False)
    assert state.submissions[0]['workflow_path']=='rebuilt.json'
    assert state.submissions[0]['workflow_source_revision']==revision(state.submissions[0])


def test_no_saved_prompt_fails_without_model(saved_scene):
    state=saved_scene
    row=state.payload['segments'][0];row['generation_config'].pop('prompt_override');row['job'].pop('final_prompt')
    with pytest.raises(RuntimeError,match='没有已保存的视频提示词'):
        state.pipe.submit_saved({1},wait=False)
    assert not state.submissions and not state.writes


def test_repeat_submit_keeps_accepted_task(saved_scene):
    state=saved_scene
    state.pipe.submit_saved({1},wait=False)
    accepted=copy.deepcopy(state.payload)
    state.pipe.submit_saved({1},wait=False)
    assert len(state.submissions)==1 and state.payload==accepted


def test_batch_submits_fixed_prompts_without_scene_analysis(saved_scene):
    state=saved_scene;second=copy.deepcopy(state.payload['segments'][0])
    second.update(segment_index=2,segment_uid='segment_2',part_id='part_002')
    second['workflow_source_revision']=revision(second)
    state.payload['segments'].append(second)
    state.pipe.submit_saved({1,2},wait=False)
    assert len(state.submissions)==2


def test_edit_during_compile_is_preserved(saved_scene,monkeypatch):
    state=saved_scene;row=state.payload['segments'][0];row.pop('workflow_path')
    monkeypatch.setattr(state.pipe,'_load_template',lambda:{})
    def build(template,beat,index,previous,**kwargs):
        state.payload['segments'][0]['generation_config']['prompt_override']='new editor content'
        return {**copy.deepcopy(previous),'workflow_path':'rebuilt.json'}
    monkeypatch.setattr(state.pipe,'_prepare_segment',build)
    with pytest.raises(ValueError,match='准备期间变化'):
        state.pipe.submit_saved({1},wait=False)
    assert state.payload['segments'][0]['generation_config']['prompt_override']=='new editor content'
    assert not state.submissions


def test_final_submission_guard_keeps_later_edit(saved_scene):
    state=saved_scene;guard=state.pipe._prepare_saved_submission_state({1})
    state.payload['segments'][0]['generation_config']['prompt_override']='later saved draft'
    with pytest.raises(ValueError,match='提交前稿件'):
        state.pipe._submit_prepared_saved({1},wait=False,preparation_guard=guard)
    assert not state.submissions


def test_service_dispatch_uses_direct_saved_submission(saved_scene):
    from generation.service import GenerationService
    state=saved_scene;service=object.__new__(GenerationService)
    service.registry=SimpleNamespace(has=lambda model:model==state.pipe.spec.id)
    service._pipeline_for=lambda model:state.pipe
    service.submit_saved_segments({1},wait=False)
    assert len(state.submissions)==1


def test_workflow_preparation_does_not_extract_or_repair_scene_state(saved_scene, monkeypatch):
    state = saved_scene
    row = state.payload['segments'][0]
    row['h3_continuity'] = {'status': 'needs_analysis', 'source_revision': 'old'}
    original = copy.deepcopy(row['h3_continuity'])
    monkeypatch.setattr(state.pipe, '_load_template', lambda: {})
    def build(template, beat, index, previous, **kwargs):
        assert kwargs['previous_segment_context'] is None
        return copy.deepcopy(previous)
    monkeypatch.setattr(state.pipe, '_prepare_segment', build)
    result = state.pipe.generate({'beats': [{'id': 1}]}, submit=False, workflow_only=True)
    assert result['segments'][0]['h3_continuity'] == original
    assert not state.submissions


@pytest.mark.parametrize('scene_id', [None, 'room'])
def test_regenerate_api_skips_segment_generation(saved_scene, monkeypatch, scene_id):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ui import api_routes
    from generation.service import GenerationService
    state = saved_scene
    state.payload['segments'][0].update(scene_context_id=scene_id, h3_state_mode='unset',
                                       h3_continuity={'source_revision': 'stale'})
    service = object.__new__(GenerationService)
    service.registry = SimpleNamespace(has=lambda model: model == state.pipe.spec.id)
    service._pipeline_for = lambda model: state.pipe
    monkeypatch.setattr('generation.service.get_generation_service', lambda: service)
    monkeypatch.setattr(service, 'prepare_segments', lambda *a, **k: pytest.fail('video retry must not prepare Segment'))
    monkeypatch.setattr(service, 'validate_segment_generation_config', lambda *a, **k: {'errors': []})
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    monkeypatch.setattr(api_routes, 'assert_current_runtime_writable', lambda *a: None)
    monkeypatch.setattr('workflow.runninghub_sync.sync_runninghub_video_jobs', lambda: None)
    monkeypatch.setattr('ui.group_panel.render_segment_panel', lambda *a: '<div>submitted</div>')
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    response = TestClient(app).post('/api/segment/regenerate_video', json={'segment_index': 1})
    assert response.status_code == 200, response.text
    assert response.json()['ok'] and len(state.submissions) == 1


def test_workflow_files_are_immutable_by_content(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from generation.pipelines.minimax_h3.continuity.checkpoints import digest

    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    pipe = MiniMaxH3Ref2VAPipeline()
    first = {'1': {'class_type': 'Prompt', 'inputs': {'text': 'first good screenplay'}}}
    second = {'1': {'class_type': 'Prompt', 'inputs': {'text': 'second screenplay'}}}
    with ThreadPoolExecutor(2) as pool:
        paths = list(pool.map(lambda graph: pipe._save_workflow(graph, 'part_003'), [first, second]))
    assert paths[0] != paths[1]
    assert digest(first)[:24] in paths[0].name and digest(second)[:24] in paths[1].name
    assert json.loads(paths[0].read_text(encoding='utf-8')) == first
    assert json.loads(paths[1].read_text(encoding='utf-8')) == second
    timestamp = paths[0].stat().st_mtime_ns
    assert pipe._save_workflow(copy.deepcopy(first), 'part_003') == paths[0]
    assert paths[0].stat().st_mtime_ns == timestamp
    assert not list(paths[0].parent.glob('*.tmp'))


@pytest.mark.parametrize('asset_key', ['id', 'asset_id'])
def test_real_workflow_build_records_final_screenplay_revision(tmp_path, monkeypatch, asset_key):
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('workflow.asset_matcher.match_beat_assets', lambda beat, **kwargs: beat)
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_planner._call_model',
                        lambda *args, **kwargs: pytest.fail('a fixed screenplay must not be replanned'))
    pipe = MiniMaxH3Ref2VAPipeline()
    monkeypatch.setattr(pipe, '_submit_backend_name', lambda: 'runninghub')
    monkeypatch.setattr(pipe, '_validate_source_assets', lambda *args, **kwargs: None)
    text = '[reference generation] 4 秒。\n[Shot 1]（0–4 秒）：人物坐在桌边。'
    saved = {'segment_uid': 'stable', 'generation_config': {'prompt_override': text,
             'generation_mode': 'ref2va', 'duration_sec': 4, 'asset_manifest': [
                 {asset_key: 'stable-image', 'type': 'image', 'path': str(tmp_path / 'actor.png'),
                  'order': 1, 'label': 'actor', 'metadata': {'asset_kind': 'character', 'role_id': 'actor'}}]}}
    before = revision(saved)
    result = pipe._prepare_segment(pipe._load_template(), {'id': 1, 'estimated_duration_sec': 4},
                                   1, saved, check_files=False)
    assert result['workflow_source_revision'] == revision(result)
    assert result['generation_config']['prompt_override'] == text
    assert result['job']['final_prompt'] == text
    assert result['generation_config']['asset_manifest'] == saved['generation_config']['asset_manifest']
    assert revision(result) == before


@pytest.mark.parametrize('scene', [None, 'room'])
def test_saved_submission_does_not_require_ready_scene_state(saved_scene, monkeypatch, scene):
    state = saved_scene
    row = state.payload['segments'][0]
    row.update(scene_context_id=scene, h3_continuity={'record_kind': 'planned', 'source_revision': 'stale'})
    before = copy.deepcopy(row)
    def forbidden(*args, **kwargs):
        pytest.fail('saved submission must not validate optional scene records')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context', forbidden)
    state.pipe.submit_saved({1}, wait=False)
    assert len(state.submissions) == 1
    for key in ('generation_config', 'job', 'h3_continuity', 'scene_context_id'):
        assert state.submissions[0].get(key) == before.get(key)


def test_edited_saved_prompt_rebuilds_without_refreshing_planned_state(saved_scene, monkeypatch):
    state = saved_scene
    row = state.payload['segments'][0]
    row['h3_continuity'] = {'record_kind': 'planned', 'source_revision': 'old_prompt'}
    row['workflow_source_revision'] = 'old_workflow'
    row['generation_config']['asset_manifest'] = [{'asset_id': 'edited', 'type': 'image', 'path': 'edited.png'}]
    before = copy.deepcopy(row)
    monkeypatch.setattr(state.pipe, '_load_template', lambda: {})
    def forbidden(*args, **kwargs):
        pytest.fail('manual edits must not require scene extraction before submission')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context', forbidden)
    def rebuild(template, beat, index, previous, **kwargs):
        assert previous['generation_config'] == before['generation_config']
        return {**copy.deepcopy(previous), 'workflow_path': 'edited_saved.json'}
    monkeypatch.setattr(state.pipe, '_prepare_segment', rebuild)
    state.pipe.submit_saved({1}, wait=False)
    assert len(state.submissions) == 1
    assert state.submissions[0]['generation_config'] == before['generation_config']
    assert state.submissions[0]['h3_continuity'] == before['h3_continuity']
    assert state.submissions[0]['workflow_path'] == 'edited_saved.json'
