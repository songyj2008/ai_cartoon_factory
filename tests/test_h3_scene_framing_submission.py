"""Video retry submits saved inputs without requiring optional scene analysis."""
import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.editor_draft import snapshot
from generation.pipelines.minimax_h3.continuity.framing import build_framing_record
from generation.pipelines.minimax_h3.continuity.inheritance import inherited_record
from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
from generation.pipelines.minimax_h3.continuity.revisions import revision


@pytest.fixture
def scene(monkeypatch, tmp_path):
    pipe = MiniMaxH3Ref2VAPipeline()
    assets = [{'asset_id': role, 'type': 'image', 'path': str(tmp_path / (role + '.png')),
               'order': index, 'role': role,
               'metadata': {'asset_kind': 'character', 'role_id': role}}
              for index, role in enumerate(('a', 'b'), 1)]
    prompt = ('【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；'
              '人物调度：<Subject 1>坐在桌前；镜头构图：正面特写，朝向侧墙，'
              '<Subject 2>所在桌后位置位于画框外；声音：本镜无对白。')
    row = {'segment_uid': 'selected', 'segment_index': 1, 'part_id': 'part_001',
           'model_id': pipe.spec.id, 'scene_context_id': 'room', 'h3_state_mode': 'inherit',
           'generation_config': {'prompt_override': prompt, 'asset_manifest': assets, 'duration_sec': 4},
           'h3_shot_editor': {'shot_uids': ['s1']}, 'workflow_path': 'fixed.json',
           'job': {'final_prompt': prompt, 'asset_manifest': copy.deepcopy(assets), 'duration_sec': 4},
           'status': 'success', 'video_path': 'previous-good.mp4', 'active_render_id': 'previous',
           'renders': [{'render_id': 'previous', 'status': 'success', 'prompt_snapshot': prompt,
                        'asset_manifest': copy.deepcopy(assets), 'requested_media_spec': {'duration_sec': 4},
                        'output': {'video_path': 'previous-good.mp4'}}]}
    data = {'default_model_id': pipe.spec.id, 'segments': [row],
            'h3_scene_continuity': {'contexts': {'room': {'initial_state': {
                'a': {'position': {'value': '桌前'}, 'posture': {'value': '坐着'}, 'present': {'value': True}},
                'b': {'position': {'value': '桌后'}, 'posture': {'value': '坐着'}, 'present': {'value': True}}
            }}}}}
    ctx = read_ready_context(data, row['segment_uid'], target_source='draft')
    row['h3_continuity'] = inherited_record(row, ctx)
    patch = {'shots': [{'shot_uid': 's1', 'visible_roles': ['a'], 'edits': [], 'world_events': [],
                       'role_coverage': [
                           {'role_id': 'a', 'visibility': 'visible', 'reason': '朝向a的正面特写'},
                           {'role_id': 'b', 'visibility': 'out_of_frame', 'reason': '镜头朝侧墙，b的桌后位置在画框外'}]}],
             'conflicts': [], 'unresolved': []}
    row['h3_scene_framing'] = build_framing_record(snapshot(row), ctx, patch, prompt, assets)
    row['workflow_source_revision'] = revision(row)
    depth = {'value': 0}
    writes, sent = [], []

    @contextmanager
    def lock():
        depth['value'] += 1
        try:
            yield
        finally:
            depth['value'] -= 1

    def save(value):
        new = copy.deepcopy(value)
        data.clear(); data.update(new); writes.append(new)

    def submit(provider, segment, **kwargs):
        assert depth['value'] > 0
        sent.append(copy.deepcopy(segment))
        segment.update(status='running', task_id='accepted', submitted=True, backend='runninghub')

    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: copy.deepcopy(data))
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', save)
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock', lock)
    monkeypatch.setattr(pipe, '_submit_provider', lambda *a: SimpleNamespace(name='runninghub'))
    monkeypatch.setattr(pipe, '_submit_segment', submit)
    monkeypatch.setattr('generation.scheduler.GenerationScheduler.concurrency_limit', lambda *a: 3)
    monkeypatch.setattr('workflow.runninghub_usage.refresh_runninghub_usage_summaries', lambda x: None)
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **k: pytest.fail('valid coverage must not call a model'))
    return SimpleNamespace(pipe=pipe, data=data, writes=writes, sent=sent, depth=depth, patch=patch)


def test_current_coverage_is_reused_without_touching_unchecked_members(scene):
    unchecked = {'segment_uid': 'unchecked', 'segment_index': 2, 'model_id': scene.pipe.spec.id,
                 'scene_context_id': 'room', 'h3_state_mode': 'unset', 'status': 'success'}
    scene.data['segments'].append(copy.deepcopy(unchecked))
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1
    assert scene.data['segments'][1] == unchecked
    assert scene.sent[0]['h3_scene_framing']['tracked_role_ids'] == ['a', 'b']


def test_stale_job_is_rebuilt_locally_even_when_revision_stamp_matches(scene, monkeypatch):
    row = scene.data['segments'][0]
    row['job']['final_prompt'] = 'stale cached screenplay'
    assert row['workflow_source_revision'] == revision(row)
    calls = []
    monkeypatch.setattr(scene.pipe, '_load_template', lambda: {})
    def rebuild(template, beat, index, source, **kwargs):
        calls.append(index)
        fresh = copy.deepcopy(source)
        fresh['job']['final_prompt'] = source['generation_config']['prompt_override']
        fresh['job']['asset_manifest'] = copy.deepcopy(source['generation_config']['asset_manifest'])
        return fresh
    monkeypatch.setattr(scene.pipe, '_prepare_segment', rebuild)
    scene.pipe.submit_saved({1}, wait=False)
    assert calls == [1] and len(scene.sent) == 1
    assert scene.sent[0]['job']['final_prompt'] == snapshot(scene.sent[0])['prompt']


def test_changed_target_source_never_runs_llm_during_video_retry(scene, monkeypatch):
    row = scene.data['segments'][0]
    row['h3_continuity'].update(record_kind='optimized', shots=[{'shot_uid':'s1','world_events':[]}], source_revision='old')
    row['h3_state_mode'] = 'extract'
    original = copy.deepcopy(scene.data)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record',
                        lambda *a, **k: pytest.fail('video retry cannot extract state'))
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1
    assert scene.sent[0]['h3_continuity'] == original['segments'][0]['h3_continuity']


def test_unready_unchecked_predecessor_is_not_automatically_prepared(scene, monkeypatch):
    previous = copy.deepcopy(scene.data['segments'][0])
    previous.update(segment_uid='unchecked-predecessor', segment_index=0, h3_state_mode='unset')
    previous.pop('h3_continuity', None)
    scene.data['segments'].insert(0, previous)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record',
                        lambda *a, **k: pytest.fail('unchecked member cannot receive extraction task'))
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1
    assert scene.data['segments'][0] == previous


def test_missing_framing_is_reported_without_running_llm(scene, monkeypatch):
    scene.data['segments'][0].pop('h3_scene_framing')
    original = copy.deepcopy(scene.data)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.framing.review_existing_framing',
                        lambda *a, **k: pytest.fail('video retry cannot review or replan prose'))
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1 and 'h3_scene_framing' not in scene.sent[0]
    assert scene.sent[0]['renders'] == original['segments'][0]['renders']


def test_missing_proof_allows_provider_submission_and_preserves_good_video(scene, monkeypatch):
    scene.data['segments'][0].pop('h3_scene_framing')
    original = copy.deepcopy(scene.data)
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1
    assert scene.sent[0]['video_path'] == original['segments'][0]['video_path']


def test_changed_scene_does_not_block_saved_video_submission(scene, monkeypatch):
    scene.data['h3_scene_continuity']['contexts']['room']['initial_state']['b']['position']['value'] = '新座位'
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.framing.review_existing_framing',
                        lambda *a, **k: pytest.fail('must use explicit optimization'))
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1


def test_scene_analysis_edit_between_preparation_and_acceptance_does_not_block(scene):
    guard = scene.pipe._prepare_saved_submission_state({1})
    scene.data['h3_scene_continuity']['contexts']['room']['initial_state']['b']['position']['value'] = '另一个座位'
    scene.data['segments'][0]['h3_continuity']['source_revision'] = 'old'
    scene.data['segments'][0]['h3_scene_framing']['status'] = 'stale'
    scene.pipe._submit_prepared_saved({1}, wait=False, preparation_guard=guard)
    assert len(scene.sent) == 1


def test_video_retry_does_not_require_matching_framing_and_end_state(scene):
    row = scene.data['segments'][0]
    prompt = row['generation_config']['prompt_override'].replace(
        '<Subject 1>坐在桌前', '<Subject 1>从桌前站起')
    row['generation_config']['prompt_override'] = row['job']['final_prompt'] = prompt
    patch = copy.deepcopy(scene.patch)
    patch['shots'][0]['world_events'] = [{'role_id': 'a', 'source_quote': '<Subject 1>从桌前站起',
                                        'facts': {'posture': {'value': '站着'}}}]
    context = read_ready_context(scene.data, 'selected', target_source='draft')
    row['h3_scene_framing'] = build_framing_record(snapshot(row), context, patch, prompt,
                                                 row['generation_config']['asset_manifest'])
    row['workflow_source_revision'] = revision(row)
    scene.pipe.submit_saved({1}, wait=False)
    assert len(scene.sent) == 1


@pytest.mark.parametrize('changed', ['prompt', 'assets'])
def test_saved_job_edit_during_submission_is_rejected(scene, changed):
    row = scene.data['segments'][0]
    guard = scene.pipe._prepare_saved_submission_state({1})
    if changed == 'prompt':
        row['job']['final_prompt'] += '摄影机改成全景。'
    else:
        row['job']['asset_manifest'][1]['path'] = 'another-person.png'
    with pytest.raises(ValueError, match='提交前稿件'):
        scene.pipe._submit_prepared_saved({1}, wait=False, preparation_guard=guard)
    assert not scene.sent


def test_selected_render_uses_own_framing_snapshot(scene):
    from generation.pipelines.minimax_h3.continuity.versions import source_segment
    row = scene.data['segments'][0]
    original = copy.deepcopy(row)
    assert source_segment(row)['h3_scene_framing'] == {}
    row['renders'][0]['metadata'] = {'h3_scene_framing_snapshot': {'historical': True}}
    assert source_segment(row)['h3_scene_framing'] == {'historical': True}
    assert row['h3_scene_framing'] == original['h3_scene_framing']


def test_corrupted_saved_workflow_cannot_send_an_unreviewed_prompt(scene, monkeypatch, tmp_path):
    import json
    row = scene.data['segments'][0]
    slot = scene.pipe.adapter.profile.prompt
    graph = {str(slot.node_id): {'inputs': {slot.input_name: 'a different one-person prompt'}}}
    path = tmp_path / 'changed-workflow.json'
    path.write_text(json.dumps(graph), encoding='utf-8')
    row['workflow_path'] = str(path)
    report = SimpleNamespace(raise_for_errors=lambda: None)
    monkeypatch.setattr(scene.pipe, '_validate_source_assets', lambda *a: None)
    monkeypatch.setattr(scene.pipe.validator, 'validate_binding_contract', lambda *a: report)
    monkeypatch.setattr('generation.pipelines.minimax_h3.validator.H3Ref2VAValidator.validate_prompt',
                        lambda *a: report)
    provider = SimpleNamespace(name='runninghub', submit=lambda *a, **k: pytest.fail('must not submit changed graph'))
    # Invoke the real last boundary rather than the queue fixture's fake submit.
    with pytest.raises(ValueError, match='实际提示词.*不同'):
        MiniMaxH3Ref2VAPipeline._submit_segment(scene.pipe, provider, row, wait=False)


def web_queue(scene, monkeypatch, tmp_path):
    from services import web_video_bridge as bridge
    monkeypatch.setattr(bridge, '_DB', tmp_path / 'web.sqlite3')
    monkeypatch.setattr(bridge, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(bridge, 'get_current_project_name', lambda: 'test')
    monkeypatch.setattr(bridge, 'get_current_episode_name', lambda: '')
    monkeypatch.setattr(bridge, 'load_video_jobs', lambda: copy.deepcopy(scene.data))

    def save(value):
        saved = copy.deepcopy(value)
        scene.data.clear(); scene.data.update(saved); scene.writes.append(saved)

    monkeypatch.setattr(bridge, 'save_video_jobs', save)
    channel = SimpleNamespace(id='doubao_web', daily_limit=10,
        unavailable_reason=lambda *a: '', duration=lambda row: 4,
        prompt=lambda row: row['generation_config']['prompt_override'],
        assets=lambda row: [Path(a['path']) for a in row['generation_config']['asset_manifest']])
    return bridge, channel


def test_actual_provider_boundary_submits_without_state_or_framing(scene, monkeypatch, tmp_path):
    import json
    row = scene.data['segments'][0]
    row.update(h3_state_mode='unset', h3_continuity={'source_revision': 'stale'})
    row.pop('h3_scene_framing')
    row['job'].update(aligned_frame_count=107, part_id='part_001')
    slot = scene.pipe.adapter.profile.prompt
    graph = {str(slot.node_id): {'inputs': {slot.input_name: row['job']['final_prompt']}}}
    path = tmp_path / 'saved.json'
    path.write_text(json.dumps(graph), encoding='utf-8')
    row['workflow_path'] = str(path)
    report = SimpleNamespace(raise_for_errors=lambda: None)
    monkeypatch.setattr(scene.pipe, '_validate_source_assets', lambda *a: None)
    monkeypatch.setattr(scene.pipe.validator, 'validate_binding_contract', lambda *a: report)
    monkeypatch.setattr(scene.pipe, '_prepare_remote_workflow_assets', lambda *a: False)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context',
                        lambda *a, **k: pytest.fail('video dispatch cannot read scene readiness'))
    sent = []
    def submit(workflow, job, **kwargs):
        sent.append((workflow, job.prompt))
        return SimpleNamespace(backend='test', submitted=True, prompt_id='accepted', task_id='accepted',
                               video_path='', output_urls=[], submit_result={}, raw={})
    provider = SimpleNamespace(name='test', submit=submit)
    MiniMaxH3Ref2VAPipeline._submit_segment(scene.pipe, provider, row, wait=False)
    assert sent == [(graph, row['generation_config']['prompt_override'])]
    assert row['task_id'] == 'accepted'


def test_web_channel_preserves_scene_identity_and_coverage_snapshot(scene, monkeypatch, tmp_path):
    bridge, channel = web_queue(scene, monkeypatch, tmp_path)
    expected = copy.deepcopy(scene.data['segments'][0]['h3_scene_framing'])
    bridge.enqueue_segment(1, channel)
    row = scene.data['segments'][0]
    render = next(r for r in row['renders'] if r['render_id'] != 'previous')
    assert render['metadata']['h3_scene_framing_snapshot'] == expected
    assert [a['metadata']['role_id'] for a in render['asset_manifest']] == ['a', 'b']
    assert bridge.remaining_quota('doubao_web') == 9
    with pytest.raises(ValueError, match='未结束'):
        bridge.enqueue_segment(1, channel)
    assert bridge.remaining_quota('doubao_web') == 9


def test_web_channel_missing_framing_does_not_run_text_task(scene, monkeypatch, tmp_path):
    bridge, channel = web_queue(scene, monkeypatch, tmp_path)
    scene.data['segments'][0].pop('h3_scene_framing')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.framing.review_existing_framing',
                        lambda *a, **k: pytest.fail('web video retry cannot run LLM'))
    bridge.enqueue_segment(1, channel)
    assert scene.data['segments'][0]['status'] == 'queued'
    assert bridge.remaining_quota('doubao_web') == 9


def test_web_stale_state_and_framing_do_not_block_saved_prompt(scene, monkeypatch, tmp_path):
    bridge, channel = web_queue(scene, monkeypatch, tmp_path)
    row = scene.data['segments'][0]
    row['h3_continuity']['source_revision'] = 'stale'
    row['h3_scene_framing']['source_prompt'] = 'old prompt'
    original = copy.deepcopy(scene.data)
    bridge.enqueue_segment(1, channel)
    row = scene.data['segments'][0]
    assert row['video_path'] == original['segments'][0]['video_path']
    render = next(r for r in row['renders'] if r['render_id'] != 'previous')
    assert render['prompt_snapshot'] == row['generation_config']['prompt_override']
    assert render['metadata']['h3_scene_framing_snapshot'] == {}
    assert [a['metadata']['role_id'] for a in render['asset_manifest']] == ['a', 'b']
