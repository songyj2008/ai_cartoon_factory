from spatial_test_support import planner_reply, model_reply
"""Automatic metadata repair preserves the exact screenplay and selected video."""
import copy
from contextlib import contextmanager

import pytest

from generation.pipelines.minimax_h3.continuity import preparation as prep
from generation.pipelines.minimax_h3.continuity.checkpoints import digest
from generation.pipelines.minimax_h3.continuity.ledger import record_from_plan, state_before
from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.continuity.state import model_world


def sample():
    prompt = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；固定近景，室内安静；声音：无对白。'
    rows = [{'segment_uid': str(i), 'segment_index': i, 'part_id': f'part_{i:03d}',
             'scene_context_id': 'room', 'status': 'success', 'video_path': f'{i}.mp4',
             'generation_config': {'prompt_override': prompt, 'asset_manifest': [], 'duration_sec': 4},
             'h3_shot_editor': {'shot_uids': [f's{i}']}}
            for i in range(1, 4)]
    jobs = {'segments': rows, 'h3_scene_continuity': {'contexts': {'room': {
        'initial_state': {'actor': {'posture': {'value': '坐着'}, 'present': {'value': True}}}}}}}
    for row in rows:
        context = state_before(jobs, row['segment_uid'])
        row['h3_continuity'] = record_from_plan({'shots': [{'shot_uid': f"s{row['segment_uid']}",
            'world_events': []}]}, revision(row), context['input_revision'],
            opening_state=context['input_state'], segment_uid=row['segment_uid'],
            previous_segment_uid=context['previous_segment_uid'])
    return jobs


def fake_extractor(calls):
    def extract(row, context, *, existing=None):
        calls.append(copy.deepcopy((row, context, existing)))
        uid = row['segment_uid']
        ids = row.get('h3_shot_editor', {}).get('shot_uids') or [f's{uid}']
        result = record_from_plan({'shots': [{'shot_uid': s, 'world_events': []} for s in ids]},
            revision(row), context['input_revision'], opening_state=context['input_state'],
            segment_uid=uid, previous_segment_uid=context['previous_segment_uid'])
        result['state_trace_version'] = 1
        return result
    return extract


def test_missing_record_is_built_once_without_composition_or_video_edits(monkeypatch):
    jobs = sample()
    jobs['segments'][1].pop('h3_continuity')
    original = copy.deepcopy(jobs)
    calls = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fake_extractor(calls))
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
                        planner_reply(lambda *args: pytest.fail('metadata preparation cannot optimize composition')))
    prep.ensure_scene_state(jobs, '3')
    assert len(calls) == 1 and calls[0][0]['segment_uid'] == '2'
    assert state_before(jobs, '3')['previous_segment_uid'] == '2'
    first = copy.deepcopy(jobs)
    prep.ensure_scene_state(jobs, '3')
    assert jobs == first and len(calls) == 1
    for row, old in zip(jobs['segments'], original['segments']):
        for key in ('generation_config', 'status', 'video_path'):
            assert row[key] == old[key]


def test_equal_facts_allow_new_predecessor_without_model(monkeypatch):
    jobs = sample()
    jobs['segments'][1]['h3_continuity']['previous_segment_uid'] = 'old_predecessor'
    end = copy.deepcopy(jobs['segments'][1]['h3_continuity']['end_state'])
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **k: pytest.fail('same facts need no model'))
    prep.ensure_scene_state(jobs, '3')
    record = jobs['segments'][1]['h3_continuity']
    assert record['previous_segment_uid'] == '1' and record['end_state'] == end


def test_changed_facts_require_compatibility_analysis_not_blind_replay(monkeypatch):
    jobs = sample()
    first = jobs['segments'][0]['h3_continuity']
    first['shots'][0]['world_events'] = [{'role_id': 'actor', 'facts': {'posture': {'value': '站着'}}}]
    first['end_state']['actor']['posture']['value'] = '站着'
    first['end_revision'] = digest(model_world(first['end_state']))
    calls = []
    def conflict(row, context, *, existing=None):
        calls.append(row['segment_uid'])
        raise ValueError('Shot 1 人物 actor 字段 posture：开场冲突')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', conflict)
    before = copy.deepcopy(jobs['segments'][1])
    with pytest.raises(ValueError, match='part_002.*posture'):
        prep.ensure_scene_state(jobs, '3')
    assert calls == ['2'] and jobs['segments'][1] == before


def test_unanchored_historical_record_requires_analysis(monkeypatch):
    jobs=sample()
    record=jobs['segments'][0]['h3_continuity']
    record.pop('previous_end_revision')
    record.pop('input_state')
    calls=[]
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record',fake_extractor(calls))
    prep.ensure_scene_state(jobs,'2')
    assert len(calls)==1 and calls[0][0]['segment_uid']=='1'


def test_old_selected_render_uses_its_own_prompt_and_derived_record(monkeypatch):
    jobs = sample()
    row = jobs['segments'][1]
    saved_prompt = row['generation_config']['prompt_override']
    old_record = copy.deepcopy(row['h3_continuity'])
    row.update(active_render_id='old', renders=[{'render_id': 'old', 'status': 'success',
        'prompt_snapshot': saved_prompt, 'asset_manifest': [], 'requested_media_spec': {'duration_sec': 4},
        'metadata': {'h3_continuity_snapshot': old_record}}])
    row['generation_config']['prompt_override'] = saved_prompt.replace('固定近景', '新的全景')
    row.pop('h3_continuity')
    history = copy.deepcopy(row['renders'])
    calls = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fake_extractor(calls))
    prep.ensure_scene_state(jobs, '3')
    assert row['renders'] == history and row['active_render_id'] == 'old'
    assert '新的全景' in row['generation_config']['prompt_override']
    assert 'h3_continuity' not in row
    assert row['h3_continuity_versions']['old']['source_revision'] == old_record['source_revision']
    assert calls == []


def test_include_target_prepares_draft_without_switching_existing_video(monkeypatch):
    jobs = sample()
    target = jobs['segments'][2]
    target.pop('h3_continuity')
    target.update(active_render_id='old', renders=[{'render_id': 'old', 'status': 'success',
        'prompt_snapshot': target['generation_config']['prompt_override'].replace('固定近景', '旧全景'),
        'asset_manifest': [], 'metadata': {}}])
    calls = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fake_extractor(calls))
    prep.ensure_scene_state(jobs, '3', include_target=True)
    assert len(calls) == 1 and '固定近景' in calls[0][0]['generation_config']['prompt_override']
    assert target['active_render_id'] == 'old' and 'h3_continuity_versions' not in target


def test_preparation_patch_merges_provider_progress_but_rejects_source_edits(monkeypatch):
    before = sample()
    after = copy.deepcopy(before)
    prep.ensure_scene_state(after, '3')
    patch = prep.preparation_updates(before, after, '3')
    assert patch
    latest = copy.deepcopy(before)
    latest['segments'][0].update(status='running', task_id='accepted')
    prep.apply_preparation_updates(latest, patch)
    assert latest['segments'][0]['task_id'] == 'accepted'
    edited = copy.deepcopy(before)
    edited['segments'][0]['generation_config']['prompt_override'] += '\n新稿'
    with pytest.raises(ValueError, match='已变化'):
        prep.apply_preparation_updates(edited, patch)


def test_noop_candidate_still_guards_upstream_changes(monkeypatch):
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot,fingerprint
    jobs=sample()
    prep.ensure_scene_state(jobs,'3',include_target=True)
    draft=snapshot(jobs['segments'][2])
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
        planner_reply(lambda base,context: _coverage_patch(context)))
    result=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':jobs,
        'draft':draft,'segment_uid':'3','base_revision':fingerprint(draft)})
    patch=result['draft']['state_guard']
    assert patch and 'rows' not in patch
    assert 'state_preparation' not in result['draft']
    changed=copy.deepcopy(jobs)
    changed['segments'][0]['generation_config']['prompt_override']+='\n人物已经站起'
    with pytest.raises(ValueError,match='已变化'):
        from generation.pipelines.minimax_h3.continuity.readiness import validate_scene_guard
        validate_scene_guard(changed,patch)


def test_scene_guard_conflict_identifies_changed_dependency(monkeypatch):
    from generation.pipelines.minimax_h3.continuity import readiness
    from generation.pipelines.minimax_h3.editor_draft import EditorConflictError
    original = {'uid': 'target', 'source': 'source-v1', 'input_revision': 'state-v1'}
    for field in ('source', 'input_revision'):
        current = {**original, field: 'changed'}
        monkeypatch.setattr(readiness, 'scene_state_guard', lambda *args: current)
        with pytest.raises(EditorConflictError) as conflict:
            readiness.validate_scene_guard({}, original)
        assert conflict.value.code == 'STALE_OPTIMIZATION'
        assert conflict.value.details['changed_fields'] == [field]
        assert original == {'uid': 'target', 'source': 'source-v1', 'input_revision': 'state-v1'}


def _complete_equivalent_render(row):
    row['active_render_id'] = 'completed'
    row['renders'] = [{'render_id': 'completed', 'status': 'success',
        'prompt_snapshot': row['generation_config']['prompt_override'],
        'asset_manifest': copy.deepcopy(row['generation_config']['asset_manifest']),
        'requested_media_spec': {'duration_sec': row['generation_config']['duration_sec']},
        'output': {'video_path': 'completed.mp4'}}]


def test_optimization_accepts_equivalent_upstream_render_completion():
    from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard, validate_scene_guard, source_guard
    jobs = sample()
    prep.ensure_scene_state(jobs, '3', include_target=True)
    guard = scene_state_guard(jobs, '3')
    extraction_guard = source_guard(jobs, '3', target_source='draft')
    _complete_equivalent_render(jobs['segments'][1])
    before_validation = copy.deepcopy(jobs)
    validate_scene_guard(jobs, guard)
    assert jobs == before_validation
    # Extraction still owns a specific source version for its stored evidence.
    assert source_guard(jobs, '3', target_source='draft') != extraction_guard
    jobs['segments'][1]['renders'][0]['render_id'] = 'another-equivalent-render'
    jobs['segments'][1]['active_render_id'] = 'another-equivalent-render'
    validate_scene_guard(jobs, guard)


@pytest.mark.parametrize('field', ['prompt_snapshot', 'asset_manifest', 'duration'])
def test_optimization_rejects_changed_upstream_render_content(field):
    from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard, validate_scene_guard
    jobs = sample()
    prep.ensure_scene_state(jobs, '3', include_target=True)
    guard = scene_state_guard(jobs, '3')
    _complete_equivalent_render(jobs['segments'][1])
    render = jobs['segments'][1]['renders'][0]
    if field == 'prompt_snapshot':
        render[field] += '\n新的镜头内容'
    elif field == 'asset_manifest':
        render[field] = [{'asset_id': 'voice', 'type': 'audio', 'path': 'changed.mp3'}]
    else:
        render['requested_media_spec']['duration_sec'] = 5
    # Keep a valid state record for the new source: unchanged end facts alone
    # must not authorize a changed screenplay, binding or duration.
    from generation.pipelines.minimax_h3.continuity.versions import source_segment
    row = jobs['segments'][1]
    row['h3_continuity_versions'] = {'completed': copy.deepcopy(row['h3_continuity'])}
    row['h3_continuity_versions']['completed']['source_revision'] = revision(source_segment(row))
    with pytest.raises(ValueError):
        validate_scene_guard(jobs, guard)


def test_optimization_rejects_changed_incoming_state_after_equivalent_render():
    from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard, validate_scene_guard
    jobs = sample()
    prep.ensure_scene_state(jobs, '3', include_target=True)
    guard = scene_state_guard(jobs, '3')
    row = jobs['segments'][1]
    _complete_equivalent_render(row)
    record = copy.deepcopy(row['h3_continuity'])
    record['end_state']['actor']['posture']['value'] = '站着'
    record['end_revision'] = digest(model_world(record['end_state']))
    row['h3_continuity_versions'] = {'completed': record}
    with pytest.raises(ValueError):
        validate_scene_guard(jobs, guard)


def test_candidate_commits_after_upstream_video_completes(tmp_path, monkeypatch):
    import json
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint, commit
    jobs = sample()
    prep.ensure_scene_state(jobs, '3', include_target=True)
    target = jobs['segments'][2]
    target['beat_uid'] = 'beat3'
    draft = snapshot(target)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
        planner_reply(lambda base, context: _coverage_patch(context)))
    candidate = build_opening_candidate({'operation_contract': 'scene_state_v3', 'jobs': jobs,
        'segment_uid': '3', 'draft': draft, 'base_revision': fingerprint(draft)})
    _complete_equivalent_render(jobs['segments'][1])
    upstream = copy.deepcopy(jobs['segments'][1])
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': [{'beat_uid': 'beat3'}]}), encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: jobs)
    writes = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',
                        lambda *args, **kwargs: writes.append(copy.deepcopy(kwargs)))
    monkeypatch.setattr('services.llm.call_llm', lambda *args, **kwargs: pytest.fail('saving cannot reoptimize'))
    saved = commit({'segment_uid': '3', 'base_revision': fingerprint(draft),
                    'draft': candidate['draft'], 'operation_base': candidate['operation_base']})
    assert len(writes) == 1
    assert saved['draft']['prompt'] == candidate['draft']['prompt']
    assert target['h3_continuity']['record_kind'] == 'optimized'
    assert jobs['segments'][1] == upstream


def test_runtime_failure_does_not_save_partial_repairs(monkeypatch):
    jobs = sample()
    jobs['segments'][1].pop('h3_continuity')
    original = copy.deepcopy(jobs)
    writes, locked = [], {'depth': 0}
    @contextmanager
    def lock():
        locked['depth'] += 1
        try:
            yield
        finally:
            locked['depth'] -= 1
    def fail(*a, **k):
        assert locked['depth'] == 0
        raise ValueError('关键开场冲突')
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock', lock)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: copy.deepcopy(jobs))
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda value: writes.append(value))
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fail)
    with pytest.raises(ValueError, match='关键开场冲突'):
        prep.prepare_runtime_state('3')
    assert writes == [] and jobs == original


def _coverage_patch(context):
    return {'shots': [{'shot_uid': 's3', 'visible_roles': [], 'edits': [], 'world_events': [],
            'role_coverage': [{'role_id': role, 'visibility': 'out_of_frame',
                               'reason': '本镜拍摄环境局部，人物座位在摄影机后方'} for role in context['tracked_role_ids']]}],
            'conflicts': [], 'unresolved': []}


def test_layout_edit_invalidates_inflight_optimization_without_changing_world():
    from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard, validate_scene_guard
    jobs = sample()
    prep.ensure_scene_state(jobs, '3', include_target=True)
    guard = scene_state_guard(jobs, '3')
    context_id = jobs['segments'][2]['scene_context_id']
    scene = jobs['h3_scene_continuity']['contexts'][context_id]
    scene['scene_layout'] = {'description': '新校准的固定设施关系', 'background_signature': 'background'}
    scene['layout_revision'] = 'new-layout'
    with pytest.raises(ValueError, match='已变化'):
        validate_scene_guard(jobs, guard)
