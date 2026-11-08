import copy
import json
import pytest
from spatial_test_support import envelope
from test_h3_scene_framing import sample, segment
from generation.pipelines.minimax_h3.continuity import spatial_plan as sp
from generation.pipelines.minimax_h3.continuity.composition import plan_composition_patch
from generation.pipelines.minimax_h3.continuity.framing import build_framing_record, validate_framing_record, FramingConflict


def setup_plan():
    base, ctx, patch = sample()
    wrapper = envelope(patch, ctx['tracked_role_ids'], sp.sources(base, ctx))
    return base, ctx, patch, wrapper


@pytest.mark.parametrize('fault', ['duplicate', 'unknown_source', 'missing_role', 'visible_union'])
def test_rejects_invalid_spatial_contract(fault):
    base, ctx, patch, result = setup_plan()
    plan = result['spatial_plan']; people = plan['shots'][0]['views'][0]['characters']
    if fault == 'duplicate': people.append(copy.deepcopy(people[0]))
    if fault == 'missing_role': people.pop()
    if fault == 'visible_union': people[1]['visibility'] = 'visible'
    if fault == 'unknown_source': plan['basis'] = [{'basis_id': 'b', 'source_id': 'invented', 'quote': 'chair'}]
    with pytest.raises(ValueError): sp.validate(plan, patch, ctx['tracked_role_ids'], result['spatial_sources'])


def test_movement_allows_role_to_change_layer_and_preserves_union():
    base, ctx, patch, result = setup_plan()
    plan = result['spatial_plan']; view = plan['shots'][0]['views'][0]
    later = copy.deepcopy(view); later['phase'] = '运动终点'; later['characters'][0]['layer'] = '背景'
    plan['shots'][0]['views'].append(later)
    sp.validate(plan, patch, ctx['tracked_role_ids'], result['spatial_sources'])


@pytest.mark.parametrize('failure', ['none', 'design', 'input_conflict', 'persistent'])
def test_real_planner_reviews_final_candidate_and_bounds_repair(monkeypatch, failure):
    base, ctx, patch, wrapper = setup_plan(); calls = []
    def llm(system, user, **kwargs):
        calls.append(kwargs['label']); payload = json.loads(user)
        if 'review' in kwargs['label']:
            assert 'candidate_prompt' in payload and 'spatial_plan' in payload
            assert '【场景人物占位】' not in payload['candidate_prompt']
            fail = failure != 'none' and ('repair1' not in kwargs['label'] or failure == 'persistent')
            return {'issues': [{'shot_uid': 's1', 'kind': 'input_conflict' if failure == 'input_conflict' else 'design',
                                'reason': '缺少确定的拍摄方向', 'quote': '甲正面近景'}] if fail else []}, 0
        assert 'spatial_sources' in payload
        return {k: copy.deepcopy(wrapper[k]) for k in ('patch', 'spatial_plan')}, 0
    monkeypatch.setattr('services.llm.call_llm', llm)
    if failure in ('input_conflict', 'persistent'):
        with pytest.raises((FramingConflict, ValueError)): plan_composition_patch(base, ctx)
    else:
        result = plan_composition_patch(base, ctx)
        assert result['spatial_review'] == {'issues': []}
        assert base['prompt'].endswith('声音：本镜无对白。')
    assert len(calls) == (4 if failure in ('design', 'persistent') else 2)


def test_saved_record_keeps_plan_and_rejects_stale_content():
    base, ctx, patch, wrapper = setup_plan()
    record = build_framing_record(base, ctx, patch, base['prompt'], base['asset_manifest'])
    record.update({k: wrapper[k] for k in ('spatial_plan', 'spatial_sources', 'spatial_review')})
    saved = segment(base, ctx, record)
    assert validate_framing_record(saved, ctx)['spatial_plan'] == wrapper['spatial_plan']
    saved['generation_config']['prompt_override'] += '改构图'
    with pytest.raises(ValueError): validate_framing_record(saved, ctx)


def test_failed_review_cannot_be_saved():
    base, ctx, patch, wrapper = setup_plan()
    record = build_framing_record(base, ctx, patch, base['prompt'], base['asset_manifest'])
    record.update({k: wrapper[k] for k in ('spatial_plan', 'spatial_sources', 'spatial_review')})
    record['spatial_review'] = {'issues': ['failed']}
    with pytest.raises(ValueError): validate_framing_record(segment(base, ctx, record), ctx)


def test_reference_rebind_keeps_internal_plan_without_subject_numbers():
    from test_h3_scene_framing import reference_roundtrip
    from generation.pipelines.minimax_h3.continuity.framing import advance_framing_reference_record
    saved, ctx, record = reference_roundtrip()
    wrapper = envelope({'shots': record['shots']}, ctx['tracked_role_ids'], {})
    saved['h3_scene_framing'].update({k: wrapper[k] for k in ('spatial_plan', 'spatial_sources', 'spatial_review')})
    assert advance_framing_reference_record(saved, ctx)
    assert validate_framing_record(saved, ctx)['spatial_plan'] == wrapper['spatial_plan']


def test_predecessor_camera_is_optional_and_never_prepares_upstream(monkeypatch):
    base, ctx, patch, wrapper = setup_plan()
    from generation.pipelines.minimax_h3.continuity.state import model_world
    row = {'segment_uid': 'prior', 'active_render_id': 'chosen'}
    selected = {'selected': True}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.versions.source_segment', lambda r: selected)
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context', lambda *a: {})
    def validate_selected(view, context):
        assert view is selected
        return {'scene_layout': {}, 'shots': [{'state_after': model_world(ctx['input_state'])}],
                'spatial_plan': wrapper['spatial_plan']}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.framing.validate_framing_record', validate_selected)
    context = dict(ctx, previous_segment_uid='prior', scene_layout={})
    assert sp.predecessor_plan({'segments': [row]}, context) == wrapper['spatial_plan']
    context['scene_layout'] = {'description': 'replacement background'}
    assert sp.predecessor_plan({'segments': [row]}, context) is None
