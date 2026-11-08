import copy

import pytest

from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.continuity.versions import source_record, source_segment, store_source_record
from generation.pipelines.minimax_h3.editor_draft import snapshot
from generation.pipelines.minimax_h3.shot_editor import parse_prompt


OLD_PROMPT = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；甲站在桌前；本镜无对白。'
NEW_PROMPT = '【详细镜头描述】\n[Shot 1]（0–6 秒）：画面人物：无；甲坐在门旁；本镜无对白。'


def example():
    return {'segment_uid': 'part3', 'scene_context_id': 'room', 'active_render_id': 'old',
            'compiled_prompt_snapshot': NEW_PROMPT,
            'generation_config': {'prompt_override': NEW_PROMPT, 'duration_sec': 6, 'asset_manifest': []},
            'h3_shot_editor': {'shot_uids': ['new-draft-shot']},
            'renders': [{'render_id': 'old', 'status': 'success', 'prompt_snapshot': OLD_PROMPT,
                         'asset_manifest': [], 'requested_media_spec': {'duration_sec': 4},
                         'output': {'video_path': 'old.mp4'}, 'metadata': {}},
                        {'render_id': 'new', 'status': 'success', 'prompt_snapshot': NEW_PROMPT,
                         'asset_manifest': [], 'requested_media_spec': {'duration_sec': 6},
                         'output': {'video_path': 'new.mp4'}}]}


def record_for(view, shot_uid='old-shot', **extra):
    return {'schema_version': 3, 'source_revision': revision(view), 'context_id': 'room',
            'shots': [{'shot_uid': shot_uid, 'world_events': []}], **extra}


def test_selected_old_render_is_source_without_mutating_new_draft_or_history(monkeypatch):
    row = example()
    monkeypatch.setattr('generation.render_history.active_render', lambda *_: pytest.fail('must not change selection'))
    before = copy.deepcopy(row)
    view = source_segment(row)
    assert row == before
    assert snapshot(view)['prompt'] == view['compiled_prompt_snapshot'] == OLD_PROMPT
    assert snapshot(view)['duration_sec'] == 4
    assert view['segment_uid'] == row['segment_uid'] and view['scene_context_id'] == 'room'
    assert view['_continuity_source'] == {'kind': 'render', 'render_id': 'old', 'revision': revision(view)}
    assert '_continuity_source' not in view['generation_config']
    assert snapshot(row)['prompt'] == NEW_PROMPT and row['active_render_id'] == 'old'
    view['renders'][0]['prompt_snapshot'] = 'modified copy'
    view['generation_config']['asset_manifest'].append({'asset_id': 'copy'})
    assert row == before


def test_render_uses_its_own_reference_manifest():
    row = example()
    old_asset = {'asset_id': 'old-person', 'type': 'image', 'path': 'old.png',
                 'metadata': {'asset_kind': 'character', 'role_id': 'a'}}
    row['renders'][0]['asset_manifest'] = [old_asset]
    row['generation_config']['asset_manifest'] = [{**old_asset, 'asset_id': 'new-person', 'path': 'new.png'}]
    view = source_segment(row)
    assert snapshot(view)['asset_manifest'] == [old_asset]
    assert revision(view) != revision(row)


def test_missing_render_duration_comes_from_frozen_timeline_not_current_draft():
    row = example()
    row['renders'][0].pop('requested_media_spec')
    assert snapshot(source_segment(row))['duration_sec'] == 4
    assert snapshot(row)['duration_sec'] == 6


@pytest.mark.parametrize('reason', ['no_selection', 'missing', 'failed', 'running', 'deleted', 'output_deleted', 'no_prompt'])
def test_unusable_selection_falls_back_to_draft_without_choosing_another_render(reason):
    row = example()
    if reason == 'no_selection':
        row.pop('active_render_id')
    elif reason == 'missing':
        row['active_render_id'] = 'missing'
    elif reason in ('failed', 'running'):
        row['renders'][0]['status'] = reason
    elif reason == 'deleted':
        row['renders'][0]['deleted'] = True
    elif reason == 'output_deleted':
        row['renders'][0]['output']['video_deleted'] = True
    else:
        row['renders'][0]['prompt_snapshot'] = ''
    before = copy.deepcopy(row)
    view = source_segment(row)
    assert view['_continuity_source'] == {'kind': 'draft', 'render_id': None, 'revision': revision(row)}
    assert snapshot(view)['prompt'] == NEW_PROMPT
    assert row == before


@pytest.mark.parametrize('explicit', [True, False])
def test_target_draft_can_be_forced_without_persisting_source_markers(explicit):
    row = example()
    if explicit:
        view = source_segment(row, prefer_render=False)
    else:
        row['_continuity_use_draft'] = True
        view = source_segment(row)
    assert view['_continuity_source']['kind'] == 'draft'
    assert snapshot(view)['prompt'] == NEW_PROMPT
    assert '_continuity_use_draft' not in view
    assert not any(key.startswith('_continuity') for key in view['generation_config'])


def test_source_record_prefers_matching_version_then_matching_top_level():
    row = example()
    view = source_segment(row)
    cached = record_for(view, marker='version')
    row['h3_continuity_versions'] = {'old': cached}
    row['h3_continuity'] = record_for(view, marker='top')
    assert source_record(row, view)['marker'] == 'version'
    row['h3_continuity_versions']['old']['source_revision'] = 'other'
    assert source_record(row, view)['marker'] == 'top'
    row['h3_continuity_versions']['old'] = record_for(view, marker='legacy')
    row['h3_continuity_versions']['old'].pop('context_id')
    assert source_record(row, view)['marker'] == 'legacy'
    row['h3_continuity_versions']['old']['context_id'] = 'other-room'
    assert source_record(row, view)['marker'] == 'top'
    row['h3_continuity']['context_id'] = 'other-room'
    assert source_record(row, view) == {}


def test_new_draft_record_cannot_be_borrowed_for_selected_old_render():
    row = example()
    row['h3_continuity'] = record_for(row, 'new-draft-shot')
    assert source_record(row) == {}
    view = source_segment(row)
    expected = [shot['uid'] for shot in parse_prompt(OLD_PROMPT, 'part3', [])['shots']]
    assert view['h3_shot_editor']['shot_uids'] == expected
    assert view['h3_shot_editor']['shot_uids'] != row['h3_shot_editor']['shot_uids']


def test_raw_render_snapshot_is_historical_evidence_even_with_old_fingerprint():
    row = example()
    original = {'schema_version': 3, 'source_revision': 'before-retiming', 'text_pending': True,
                'shots': [{'shot_uid': 'historic-stable', 'world_events': []}]}
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = original
    before = copy.deepcopy(row)
    view = source_segment(row)
    assert view['h3_shot_editor']['shot_uids'] == ['historic-stable']
    assert source_record(row, view) == original
    detached = source_record(row, view)
    detached['shots'].clear()
    assert row == before


@pytest.mark.parametrize('original', [{}, {'schema_version': 2, 'shots': [{'shot_uid': 'old'}]},
                                     {'schema_version': 3, 'shots': []}])
def test_incomplete_render_snapshot_is_not_treated_as_record(original):
    row = example()
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = original
    assert source_record(row) == {}


def test_matching_version_keeps_stable_uids_and_count_mismatch_uses_parsed_uids():
    row = example()
    view = source_segment(row)
    row['h3_continuity_versions'] = {'old': record_for(view, 'record-stable')}
    assert source_segment(row)['h3_shot_editor']['shot_uids'] == ['record-stable']
    row['h3_continuity_versions']['old']['shots'].append({'shot_uid': 'extra', 'world_events': []})
    expected = [shot['uid'] for shot in parse_prompt(OLD_PROMPT, 'part3', [])['shots']]
    assert source_segment(row)['h3_shot_editor']['shot_uids'] == expected
    assert source_segment(row)['h3_shot_editor']['shot_uids'] == expected


def test_derived_old_render_record_is_separate_from_current_draft_and_history():
    row = example()
    row['h3_continuity'] = record_for(row, 'new-draft-shot')
    current = copy.deepcopy(row['h3_continuity'])
    history = copy.deepcopy(row['renders'])
    view = source_segment(row)
    record = record_for(view)
    store_source_record(row, view, record)
    assert row['h3_continuity_versions']['old'] == record
    assert row['h3_continuity'] == current
    assert row['renders'] == history
    record['shots'].clear()
    assert row['h3_continuity_versions']['old']['shots']
    assert '_continuity_source' not in row and '_continuity_use_draft' not in row


def test_render_record_syncs_top_level_only_when_draft_has_same_revision():
    row = example()
    row['generation_config']['prompt_override'] = OLD_PROMPT
    view = source_segment(row)
    record = record_for(view)
    store_source_record(row, view, record)
    assert row['h3_continuity'] == row['h3_continuity_versions']['old'] == record
    row['h3_continuity']['shots'].clear()
    assert row['h3_continuity_versions']['old']['shots']


def test_draft_record_is_read_and_stored_at_top_level():
    row = example()
    view = source_segment(row, prefer_render=False)
    row['h3_continuity'] = record_for(view, 'new-draft-shot')
    assert source_record(row, view) == row['h3_continuity']
    replacement = record_for(view, 'replacement')
    store_source_record(row, view, replacement)
    assert row['h3_continuity'] == replacement
    assert 'h3_continuity_versions' not in row
    replacement['shots'].clear()
    assert row['h3_continuity']['shots']
