"""Equivalent source serialization must not invalidate confirmed scene facts."""
import copy

import pytest

from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.continuity.ledger import record_from_plan
from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
from generation.pipelines.minimax_h3.continuity.revisions import revision
from generation.pipelines.minimax_h3.continuity.versions import (
    _equivalent_draft_record, source_record, source_segment,
)


PROMPT = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；固定近景；声音：无对白。'


def sample():
    assets = [
        {'id': 'actor-image', 'type': 'image', 'path': 'actor.png', 'order': 1,
         'role': 'actor', 'label': 'Actor',
         'metadata': {'asset_kind': 'character', 'role_id': 'actor', 'identity_id': 'actor'}},
        {'id': 'room-image', 'type': 'image', 'path': 'room.png', 'order': 2,
         'role': 'background', 'label': 'Room',
         'metadata': {'asset_kind': 'background', 'scene_id': 'room'}},
    ]
    row = {'segment_uid': 'first', 'part_id': 'part_001', 'scene_context_id': 'room',
           'generation_config': {'prompt_override': PROMPT, 'asset_manifest': assets, 'duration_sec': 4},
           'h3_shot_editor': {'shot_uids': ['first-shot']}, 'active_render_id': 'selected'}
    data = {'segments': [row, {'segment_uid': 'next', 'scene_context_id': 'room'}],
            'h3_scene_continuity': {'contexts': {'room': {'initial_state': {
                'actor': {'position': {'value': 'desk'}, 'present': {'value': True}}
            }}}}}
    context = read_ready_context(data, 'first')
    record = record_from_plan({'shots': [{'shot_uid': 'first-shot', 'world_events': []}]},
                              revision(row), context['input_revision'],
                              opening_state=context['input_state'], segment_uid='first')
    record.update(context_id='room', source_prompt=PROMPT, state_trace_source='reviewed_recovery')
    row['h3_continuity'] = copy.deepcopy(record)
    row['renders'] = [{'render_id': 'selected', 'status': 'success', 'prompt_snapshot': PROMPT,
                       'asset_manifest': H3GenerationConfig.from_mapping(row['generation_config']).to_dict()['asset_manifest'],
                       'requested_media_spec': {'duration_sec': 4},
                       'metadata': {'h3_continuity_snapshot': copy.deepcopy(record)}}]
    return data


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('reading equivalent source facts must not call a model')
    monkeypatch.setattr('services.llm.call_llm', fail)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.state_trace.extract_state_record', fail)


def test_id_alias_rebinds_only_the_returned_copy_and_unblocks_downstream():
    data = sample()
    row = data['segments'][0]
    before = copy.deepcopy(data)
    view = source_segment(row)
    assert revision(row) != revision(view)  # Reproduces the existing stored fingerprints.
    result = source_record(row, view)
    expected = copy.deepcopy(row['h3_continuity'])
    expected['source_revision'] = revision(view)
    assert result == expected
    context = read_ready_context(data, 'next')
    assert context['previous_segment_uid'] == 'first'
    assert context['input_state'] == row['h3_continuity']['end_state']
    assert context['previous_source']['kind'] == 'render'
    assert context['previous_source']['render_id'] == 'selected'
    result['shots'].clear()
    assert data == before


def test_frozen_id_alias_record_survives_later_draft_edits():
    data = sample()
    row = data['segments'][0]
    original = copy.deepcopy(row['renders'][0]['metadata']['h3_continuity_snapshot'])
    row['generation_config']['prompt_override'] = PROMPT.replace('固定近景', '固定全景')
    row['generation_config']['asset_manifest'][0]['path'] = 'new-actor.png'
    row['h3_continuity'] = {}
    before = copy.deepcopy(data)
    view = source_segment(row)
    record = source_record(row, view)
    assert record['source_revision'] == revision(view)
    assert record['source_prompt'] == PROMPT
    assert record['end_state'] == original['end_state']
    assert read_ready_context(data, 'next')['input_state'] == original['end_state']
    assert data == before


@pytest.mark.parametrize('change', ['prompt', 'image', 'identity', 'fingerprint', 'evidence', 'state'])
def test_frozen_alias_proof_rejects_changed_source_or_state(change):
    data = sample()
    row = data['segments'][0]
    row['generation_config']['prompt_override'] = PROMPT + '\n新版当前稿'
    row['h3_continuity'] = {}
    render = row['renders'][0]
    record = render['metadata']['h3_continuity_snapshot']
    if change == 'prompt':
        render['prompt_snapshot'] = PROMPT.replace('固定近景', '固定全景')
    elif change == 'image':
        render['asset_manifest'][0]['path'] = 'other.png'
    elif change == 'identity':
        render['asset_manifest'][0]['metadata']['role_id'] = 'other'
    elif change == 'fingerprint':
        record['source_revision'] = 'unknown'
    elif change == 'evidence':
        record.pop('source_prompt')
    else:
        record['previous_end_revision'] = 'stale-input'
    before = copy.deepcopy(data)
    with pytest.raises(ValueError, match='part_001'):
        read_ready_context(data, 'next')
    assert data == before


@pytest.mark.parametrize('origin', ['cached', 'row', 'original'])
def test_each_proven_draft_record_can_be_rebound(origin):
    data = sample()
    row = data['segments'][0]
    record = row.pop('h3_continuity')
    row['renders'][0]['metadata'] = {}
    if origin == 'cached':
        row['h3_continuity_versions'] = {'selected': record}
    elif origin == 'row':
        row['h3_continuity'] = record
    else:
        row['renders'][0]['metadata']['h3_continuity_snapshot'] = record
    view = source_segment(row)
    assert source_record(row, view)['source_revision'] == revision(view)


def test_generated_asset_identity_cannot_prove_source_equivalence():
    row = sample()['segments'][0]
    for asset in row['generation_config']['asset_manifest']:
        asset.pop('id')
    record = row['h3_continuity']
    record['source_revision'] = revision(row)
    row['renders'][0]['asset_manifest'] = H3GenerationConfig.from_mapping(row['generation_config']).to_dict()['asset_manifest']
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = copy.deepcopy(record)
    view = source_segment(row)
    assert source_record(row, view)['source_revision'] != revision(view)


@pytest.mark.parametrize('invalid', [
    'manifest_object', 'asset_scalar', 'missing_id', 'conflicting_ids', 'path',
    'metadata', 'order', 'label', 'audio_flag', 'type',
])
def test_invalid_manifest_cannot_be_normalized_into_source_proof(invalid):
    row = sample()['segments'][0]
    view = source_segment(row)
    manifest = view['generation_config']['asset_manifest']
    if invalid == 'manifest_object':
        view['generation_config']['asset_manifest'] = {}
    elif invalid == 'asset_scalar':
        manifest[0] = 'actor'
    elif invalid == 'missing_id':
        manifest[0].pop('asset_id')
    elif invalid == 'conflicting_ids':
        manifest[0]['id'] = 'other-identity'
    elif invalid == 'audio_flag':
        manifest[0]['use_video_audio'] = 'false'
    else:
        manifest[0][invalid] = {'path': None, 'metadata': [], 'order': 'bad',
                                'label': [], 'type': 'unsupported'}[invalid]
    before = copy.deepcopy((row, view))
    assert _equivalent_draft_record(row['h3_continuity'], row, view) is None
    assert (row, view) == before


@pytest.mark.parametrize('difference', [
    'unproven_revision', 'record_scene', 'record_prompt', 'draft_prompt', 'render_prompt',
    'asset_id', 'path', 'order', 'role', 'label', 'role_id', 'identity_id', 'scene_id',
    'asset_kind', 'metadata', 'manifest_order', 'missing_asset',
])
def test_source_differences_are_not_rebound(difference):
    data = sample()
    row = data['segments'][0]
    record = row['h3_continuity']
    render = row['renders'][0]
    image = render['asset_manifest'][0]
    if difference == 'unproven_revision':
        record['source_revision'] = 'unknown-source'
    elif difference == 'record_scene':
        record['context_id'] = 'other-room'
    elif difference == 'record_prompt':
        record['source_prompt'] = PROMPT + 'changed'
    elif difference == 'draft_prompt':
        row['generation_config']['prompt_override'] = PROMPT + 'changed'
        record['source_revision'] = revision(row)
        record['source_prompt'] = row['generation_config']['prompt_override']
    elif difference == 'render_prompt':
        render['prompt_snapshot'] = PROMPT + 'changed'
    elif difference == 'manifest_order':
        render['asset_manifest'].reverse()
    elif difference == 'missing_asset':
        render['asset_manifest'].pop()
    elif difference == 'scene_id':
        render['asset_manifest'][1]['metadata']['scene_id'] = 'other-room'
    elif difference == 'metadata':
        image['metadata']['source_identity'] = 'other'
    elif difference in ('role_id', 'identity_id', 'asset_kind'):
        image['metadata'][difference] = 'other'
    elif difference == 'order':
        image['order'] = 9
    else:
        image[difference] = 'other'
    render['metadata']['h3_continuity_snapshot'] = copy.deepcopy(record)
    before = copy.deepcopy(data)
    view = source_segment(row)
    result = source_record(row, view)
    assert result['source_revision'] != revision(view)
    with pytest.raises(ValueError, match='part_001'):
        read_ready_context(data, 'next')
    assert data == before


@pytest.mark.parametrize('exact_origin', ['cached', 'row', 'original'])
def test_exact_render_record_wins_over_equivalent_draft_record(exact_origin):
    row = sample()['segments'][0]
    view = source_segment(row)
    row['h3_continuity_versions'] = {'selected': {**copy.deepcopy(row['h3_continuity']), 'marker': 'equivalent-cache'}}
    exact = {**copy.deepcopy(row['h3_continuity']), 'source_revision': revision(view), 'marker': 'exact'}
    if exact_origin == 'cached':
        row['h3_continuity_versions']['selected'] = exact
    elif exact_origin == 'row':
        row['h3_continuity'] = exact
    else:
        row['renders'][0]['metadata']['h3_continuity_snapshot'] = exact
    assert source_record(row, view)['marker'] == 'exact'


def test_exact_cache_priority_and_returned_record_are_unchanged():
    row = sample()['segments'][0]
    view = source_segment(row)
    exact = copy.deepcopy(row['h3_continuity'])
    exact['source_revision'] = revision(view)
    row['h3_continuity_versions'] = {'selected': {**copy.deepcopy(exact), 'marker': 'cached'}}
    row['h3_continuity'] = {**copy.deepcopy(exact), 'marker': 'row'}
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = {**copy.deepcopy(exact), 'marker': 'original'}
    before = copy.deepcopy(row)
    assert source_record(row, view) == row['h3_continuity_versions']['selected']
    assert row == before


@pytest.mark.parametrize('invalid', ['empty_shots', 'missing_events'])
def test_incomplete_exact_record_is_rejected_without_borrowing_older_facts(invalid):
    data = sample()
    row = data['segments'][0]
    view = source_segment(row)
    exact = copy.deepcopy(row['h3_continuity'])
    exact.update(source_revision=revision(view), marker='original')
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = exact
    bad = copy.deepcopy(exact)
    if invalid == 'empty_shots':
        bad['shots'] = []
    elif invalid == 'missing_events':
        bad['shots'][0].pop('world_events')
    row['h3_continuity_versions'] = {'selected': copy.deepcopy(bad)}
    row['h3_continuity'] = bad
    assert source_record(row, view) == bad
    with pytest.raises(ValueError, match='part_001'):
        read_ready_context(data, 'next')


def test_stale_source_records_do_not_hide_exact_original():
    row = sample()['segments'][0]
    view = source_segment(row)
    exact = copy.deepcopy(row['h3_continuity'])
    exact.update(source_revision=revision(view), marker='original')
    row['renders'][0]['metadata']['h3_continuity_snapshot'] = exact
    stale = {**copy.deepcopy(exact), 'source_revision': 'unknown-source'}
    row['h3_continuity_versions'] = {'selected': copy.deepcopy(stale)}
    row['h3_continuity'] = stale
    assert source_record(row, view) == exact


@pytest.mark.parametrize('authority', ['cache', 'row', 'both'])
def test_invalid_exact_inheritance_cannot_fall_back_to_old_extracted_facts(authority):
    from generation.pipelines.minimax_h3.continuity.inheritance import inherited_record, state_status

    data = sample()
    second = {'segment_uid': 'second', 'part_id': 'part_002', 'scene_context_id': 'room',
              'h3_state_mode': 'inherit', 'active_render_id': 'selected',
              'generation_config': copy.deepcopy(data['segments'][0]['generation_config']),
              'h3_shot_editor': {'shot_uids': ['second-shot']},
              'renders': [copy.deepcopy(data['segments'][0]['renders'][0])]}
    second['renders'][0]['metadata'] = {}
    data['segments'][1] = second
    data['segments'].append({'segment_uid': 'third', 'scene_context_id': 'room'})
    context = read_ready_context(data, 'second')
    view = source_segment(second)
    extracted = record_from_plan({'shots': [{'shot_uid': 'second-shot', 'world_events': []}]},
                                 revision(view), context['input_revision'],
                                 opening_state=context['input_state'], segment_uid='second',
                                 previous_segment_uid=context['previous_segment_uid'])
    extracted['context_id'] = 'room'
    second['renders'][0]['metadata']['h3_continuity_snapshot'] = extracted
    invalid = inherited_record(view, context)
    invalid['user_declared_unchanged'] = False
    if authority in ('cache', 'both'):
        second['h3_continuity_versions'] = {'selected': copy.deepcopy(invalid)}
    second['h3_continuity'] = copy.deepcopy(invalid if authority in ('row', 'both') else extracted)
    before = copy.deepcopy(data)
    assert source_record(second)['user_declared_unchanged'] is False
    with pytest.raises(ValueError, match='沿用状态声明无效'):
        read_ready_context(data, 'second', include_target=True)
    with pytest.raises(ValueError, match='沿用状态声明无效'):
        read_ready_context(data, 'third')
    assert state_status(data, 'second')['ready'] is False
    assert data == before


@pytest.mark.parametrize('invalid', ['input', 'end'])
def test_rebinding_does_not_bypass_state_revision_checks(invalid):
    data = sample()
    row = data['segments'][0]
    key = 'previous_end_revision' if invalid == 'input' else 'end_revision'
    row['h3_continuity'][key] = 'changed-state'
    row['renders'][0]['metadata']['h3_continuity_snapshot'][key] = 'changed-state'
    with pytest.raises(ValueError, match='part_001'):
        read_ready_context(data, 'next')
