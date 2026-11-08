"""Scene coverage survives content/state persistence without blessing new prose."""
import copy
import json
from contextlib import nullcontext

import pytest

from generation.pipelines.minimax_h3.continuity.framing import (
    build_framing_record, validate_framing_record,
)
from generation.pipelines.minimax_h3.continuity.ledger import record_from_plan
from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.editor_draft import commit, fingerprint, saved_baseline, snapshot
from generation.pipelines.minimax_h3.segment_content_sync import sync_segment_content
from test_h3_state_preparation import sample
from test_scene_state_jobs import runtime, save, load, execute


def framed_jobs(*, changes=False):
    jobs = sample()
    row = jobs['segments'][0]
    row['beat_uid'] = 'beat1'
    row['generation_config']['prompt_override'] = (
        '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；'
        '镜头构图：摄影机朝向桌对面的墙面，不覆盖actor所在桌边；'
        + ('actor在画外起身；' if changes else '') + '声音：本镜无对白。')
    incoming = read_ready_context(jobs, '1', target_source='draft')
    events = ([{'role_id': 'actor', 'source_quote': 'actor在画外起身',
                'facts': {'posture': {'value': '站着'}}}] if changes else [])
    patch = {'shots': [{'shot_uid': 's1', 'visible_roles': [], 'edits': [],
                       'world_events': events, 'role_coverage': [
                           {'role_id': 'actor', 'visibility': 'out_of_frame',
                            'reason': '机位朝向桌对面墙面，actor所在桌边不在画框范围内'}]}],
             'conflicts': [], 'unresolved': []}
    row['h3_continuity'] = record_from_plan(patch, revision(row), incoming['input_revision'],
        opening_state=incoming['input_state'], segment_uid='1', previous_segment_uid=None)
    row['h3_continuity'].update(context_id='room', record_kind='optimized',
                               source_prompt=row['generation_config']['prompt_override'])
    row['h3_state_mode'] = 'extract'
    base = snapshot(row)
    row['h3_scene_framing'] = build_framing_record(
        base, incoming, patch, base['prompt'], base['asset_manifest'])
    validate_framing_record(row, incoming)
    return jobs


def editing_storage(tmp_path, monkeypatch, jobs):
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': [{'beat_uid': 'beat1'}]}), encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: jobs)
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock', nullcontext)
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *a: None)
    monkeypatch.setattr('generation.pipelines.minimax_h3.shot_edit_service._editing_fingerprint', lambda *a: 'saved')
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **kw: pytest.fail('content save cannot call a model'))
    writes = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',
                        lambda *a, **kw: writes.append(copy.deepcopy(kw)))
    return writes


def test_save_installs_verified_framing_and_reopens_as_independent_ready_contract(tmp_path, monkeypatch):
    jobs = framed_jobs()
    row = jobs['segments'][0]
    candidate_record = row.pop('h3_scene_framing')
    writes = editing_storage(tmp_path, monkeypatch, jobs)
    draft = snapshot(row)
    candidate = dict(draft, scene_framing=copy.deepcopy(candidate_record))
    result = commit({'segment_uid': '1', 'base_revision': fingerprint(draft),
                     'draft': candidate, 'operation_base': draft})
    assert row['h3_scene_framing'] == candidate_record
    assert result['draft']['scene_framing'] == candidate_record
    assert writes[0]['jobs']['segments'][0]['h3_scene_framing'] == candidate_record
    reopened = saved_baseline(jobs, row)
    assert reopened['framing_status']['ready']
    assert reopened['state_status']['ready']


def test_tampered_candidate_framing_is_not_written(tmp_path, monkeypatch):
    jobs = framed_jobs()
    row = jobs['segments'][0]
    candidate_record = row.pop('h3_scene_framing')
    writes = editing_storage(tmp_path, monkeypatch, jobs)
    draft = snapshot(row)
    candidate_record['source_prompt'] += '未核对的新构图'
    with pytest.raises(ValueError, match='变化|核对'):
        commit({'segment_uid': '1', 'base_revision': fingerprint(draft),
                'draft': dict(draft, scene_framing=candidate_record), 'operation_base': draft})
    assert writes == []
    assert 'h3_scene_framing' not in row


def test_human_camera_edit_keeps_old_record_as_stale_without_reusing_candidate_permissions(tmp_path, monkeypatch):
    jobs = framed_jobs()
    row = jobs['segments'][0]
    record = copy.deepcopy(row['h3_scene_framing'])
    writes = editing_storage(tmp_path, monkeypatch, jobs)
    draft = snapshot(row)
    changed = copy.deepcopy(draft)
    changed['prompt'] = changed['prompt'].replace('朝向桌对面的墙面', '朝向门口')
    commit({'segment_uid': '1', 'base_revision': fingerprint(draft), 'draft': changed, 'manual_edit': True})
    assert row['h3_scene_framing'] == record and writes
    assert not saved_baseline(jobs, row)['framing_status']['ready']
    assert row['h3_state_mode'] == 'unset'


def test_inherit_preserves_coverage_even_though_ending_record_has_no_shots(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project, calls = runtime
    jobs = framed_jobs()
    record = copy.deepcopy(jobs['segments'][0]['h3_scene_framing'])
    save(project, jobs)
    inherit_segment_state('1')
    saved = load(project)
    row = saved['segments'][0]
    assert row['h3_continuity']['shots'] == []
    assert row['h3_scene_framing'] == record
    validate_framing_record(row, read_ready_context(saved, '1', target_source='draft'))
    assert not calls


@pytest.mark.parametrize('with_framing', [False, True])
def test_manual_inherit_cannot_overwrite_current_evidenced_state_changes(runtime, with_framing):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project, calls = runtime
    jobs = framed_jobs(changes=True)
    if not with_framing:
        jobs['segments'][0].pop('h3_scene_framing')
    save(project, jobs)
    original = (project / 'video_jobs.json').read_bytes()
    with pytest.raises(ValueError, match='状态变化|发生变化'):
        inherit_segment_state('1')
    assert (project / 'video_jobs.json').read_bytes() == original
    assert not calls


def test_explicit_extraction_preserves_independent_framing(runtime):
    from services.scene_state_jobs import request_scene_state_refresh
    project, calls = runtime
    jobs = framed_jobs()
    record = copy.deepcopy(jobs['segments'][0]['h3_scene_framing'])
    save(project, jobs)
    execute(request_scene_state_refresh('1'))
    saved = load(project)
    row = saved['segments'][0]
    assert row['h3_scene_framing'] == record
    assert row['h3_continuity']['record_kind'] == 'extracted'
    assert len(calls) == 1
    validate_framing_record(row, read_ready_context(saved, '1', target_source='draft'))


def test_extraction_cannot_silently_replace_current_framing_state_with_different_end(runtime):
    from services import text_jobs
    from services.scene_state_jobs import request_scene_state_refresh
    project, calls = runtime
    jobs = framed_jobs(changes=True)
    save(project, jobs)
    original = (project / 'video_jobs.json').read_bytes()
    task = request_scene_state_refresh('1')
    text_jobs._execute(text_jobs.get(task['id']))
    failed = text_jobs.get(task['id'])
    assert failed['state'] == 'error'
    assert '状态变化' in failed['message']
    assert (project / 'video_jobs.json').read_bytes() == original
    assert len(calls) == 1


def test_clock_only_save_rebinds_framing_after_inherit_without_model(runtime):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
    project, calls = runtime
    save(project, framed_jobs())
    inherit_segment_state('1')
    jobs = load(project)
    row = jobs['segments'][0]
    original = copy.deepcopy(row['h3_scene_framing'])
    cfg = copy.deepcopy(row['generation_config'])
    cfg.update(duration_sec=6, prompt_override=cfg['prompt_override'].replace('0–4 秒', '0–6 秒'))
    _, updated, result = sync_segment_content({'beat_uid': 'beat1'}, row,
        prompt=cfg['prompt_override'], generation_config=cfg)
    assert result['verified']
    jobs['segments'][0] = updated
    record = validate_framing_record(updated, read_ready_context(jobs, '1', target_source='draft'))
    assert record['source_prompt'] == cfg['prompt_override']
    assert record['shots'] == original['shots']
    assert updated['h3_continuity']['shots'] == []
    assert not calls


def test_non_time_edit_does_not_bless_old_framing_as_current():
    jobs = framed_jobs()
    row = jobs['segments'][0]
    record = copy.deepcopy(row['h3_scene_framing'])
    cfg = copy.deepcopy(row['generation_config'])
    cfg['prompt_override'] = cfg['prompt_override'].replace('朝向桌对面的墙面', '朝向门口')
    _, updated, _ = sync_segment_content({'beat_uid': 'beat1'}, row,
        prompt=cfg['prompt_override'], generation_config=cfg)
    assert updated['h3_scene_framing'] == record
    jobs['segments'][0] = updated
    with pytest.raises(ValueError, match='变化|核对'):
        validate_framing_record(updated, read_ready_context(jobs, '1', target_source='draft'))


def test_reopen_with_unready_scene_keeps_old_record_but_reports_framing_not_ready():
    jobs = framed_jobs()
    row = jobs['segments'][0]
    original = copy.deepcopy(row['h3_scene_framing'])
    jobs['h3_scene_continuity']['contexts']['room']['initial_pending'] = True
    result = saved_baseline(jobs, row)
    assert not result['framing_status']['ready']
    assert '初态' in result['framing_status']['message']
    assert row['h3_scene_framing'] == original
