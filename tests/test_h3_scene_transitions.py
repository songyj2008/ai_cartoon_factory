from spatial_test_support import planner_reply, model_reply
"""Scene facts and reference identity survive visibility and presence changes."""
import copy

import pytest

from test_h3_state_trace import inputs
from generation.pipelines.minimax_h3.continuity.state_trace import validate_state_result


@pytest.mark.parametrize('movement,events,expected_position', [
    ('镜头切到门边，甲留在桌前画外', [], '桌前'),
    ('甲走到窗边，随后走出画框，仍留在室内', [
        {'role_id': 'a', 'source_quote': '甲走到窗边', 'facts': {'position': {'value': '窗边'}}}], '窗边'),
    ('甲穿过门离开房间', [
        {'role_id': 'a', 'source_quote': '甲穿过门离开房间',
         'facts': {'present': {'value': False}, 'position': {'value': None}}}], None),
])
def test_camera_exit_room_movement_and_scene_exit_have_distinct_saved_facts(movement, events, expected_position):
    segment, context, _ = inputs()
    segment['generation_config']['prompt_override'] = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；' + movement + '；本镜无对白。'
    context['input_state']['a']['position'] = {'value': '桌前'}
    response = {'shots': [{'shot_uid': 'stable-shot', 'world_events': events}],
                'opening_requirements': [], 'conflicts': [], 'unresolved': []}
    before = copy.deepcopy((segment, context, response))
    record = validate_state_result(segment, context, response)
    assert record['end_state']['a']['position']['value'] == expected_position
    assert record['end_state']['a']['present']['value'] is (expected_position is not None)
    assert record['record_status'] == 'ready'
    assert (segment, context, response) == before


def test_late_entry_leave_and_reentry_do_not_restore_old_seat_or_create_opening_conflict():
    segment, context, _ = inputs()
    context['input_state']['a'] = {'present': {'value': False},
        'position': {'value': '历史桌前座位'}, 'posture': {'value': '坐着'}}
    bodies = ['甲尚在房间外，随后进门，站在入口内侧', '甲走到窗边',
              '甲穿过门离开房间', '甲再次进入房间，落点与基本姿势未交代']
    uids = ['s1', 's2', 's3', 's4']
    segment['h3_shot_editor']['shot_uids'] = uids
    segment['generation_config']['prompt_override'] = '【详细镜头描述】\n' + '\n'.join(
        f'[Shot {i+1}]（{i*2}–{i*2+2} 秒）：画面人物：无；{body}；本镜无对白。'
        for i, body in enumerate(bodies))
    facts = [
        {'present': {'value': True}, 'position': {'value': '入口内侧'}, 'posture': {'value': '站着'}},
        {'position': {'value': '窗边'}},
        {'present': {'value': False}, 'position': {'value': None}},
        {'present': {'value': True}, 'position': {'value': None}, 'posture': {'value': None}},
    ]
    response = {'shots': [{'shot_uid': uid, 'world_events': [
        {'role_id': 'a', 'source_quote': body, 'facts': change}]} for uid, body, change in zip(uids, bodies, facts)],
        'opening_requirements': [{'shot_uid': 's1', 'role_id': 'a', 'field': 'present',
                                  'value': False, 'source_quote': '甲尚在房间外'}],
        'conflicts': [], 'unresolved': []}
    record = validate_state_result(segment, context, response)
    assert record['record_status'] == 'ready'
    assert record['end_state']['a']['present']['value'] is True
    assert record['end_state']['a']['position']['value'] is None
    assert record['end_state']['a']['posture']['value'] is None
    assert record['input_state']['a']['present']['value'] is False


def test_visible_during_entry_and_exit_is_not_required_present_at_end():
    from test_h3_composition_patch import example
    from generation.pipelines.minimax_h3.continuity.composition import finalize_composition, bind_final_state_quotes
    from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan
    base, patch = example()
    base['prompt'] = base['prompt'].replace('面无表情，纸张落地', '<Subject 1>进门，站在入口，随后穿过门离开房间')
    patch['shots'][0]['world_events'] = [
        {'role_id': 'a', 'source_quote': '<Subject 1>进门，站在入口',
         'facts': {'present': {'value': True}, 'position': {'value': '入口'}, 'posture': {'value': '站着'}}},
        {'role_id': 'a', 'source_quote': '随后穿过门离开房间',
         'facts': {'present': {'value': False}, 'position': {'value': None}}}]
    text, manifest = finalize_composition(base, patch)
    rows = bind_final_state_quotes(base, patch, text, asset_manifest=manifest)
    end = reduce_plan({'a': {'present': {'value': False}}}, {'shots': rows}, 'seg', ['a'])
    assert '画面人物：<Subject 1>' in text
    assert '<Subject 1>进门，站在入口，随后穿过门离开房间' in text
    assert any(a.get('metadata', {}).get('role_id') == 'a' for a in manifest)
    assert end['a']['present']['value'] is False
    assert end['a']['position']['value'] is None


@pytest.mark.parametrize('source_mode', ['selected', 'unselected', 'different_scene', 'draft', 'current', 'nearest'])
def test_reappearing_role_uses_only_selected_same_scene_history_and_keeps_current_reference(monkeypatch, source_mode):
    from test_h3_composition_patch import example
    from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
    from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint
    base, _ = example()
    actor = base['asset_manifest'][0]
    actor['path'] = 'a.png'
    history_actor = {'asset_id': 'b-selected', 'type': 'image', 'path': 'b-selected.png', 'role': '乙',
                     'metadata': {'asset_kind': 'character', 'role_id': 'b'}}
    historical_prompt = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；乙站在门边；声音：本镜无对白。'
    prior = {'segment_uid': 'prior', 'scene_context_id': 'room', 'active_render_id': 'selected',
             'generation_config': {'duration_sec': 4, 'asset_manifest': [], 'prompt_override': historical_prompt},
             'renders': [{'render_id': 'selected', 'status': 'success', 'prompt_snapshot': historical_prompt,
                          'asset_manifest': [history_actor]}]}
    target = {'segment_uid': 'target', 'scene_context_id': 'room', 'h3_shot_editor': {'shot_uids': ['s1']},
              'generation_config': {'duration_sec': 4, 'prompt_override': base['prompt'], 'asset_manifest': [actor]}}
    if source_mode == 'unselected':
        prior['active_render_id'] = None
    elif source_mode == 'different_scene':
        prior['scene_context_id'] = 'another-room'
    elif source_mode == 'draft':
        prior['h3_continuity_source'] = 'draft'
    elif source_mode == 'current':
        target['generation_config']['asset_manifest'].append(dict(history_actor, asset_id='b-current', path='b-current.png'))
    data = {'segments': [prior, target]}
    if source_mode == 'nearest':
        nearer = copy.deepcopy(prior)
        nearer['segment_uid'] = 'nearer'
        nearer['renders'][0]['asset_manifest'][0]['path'] = 'b-nearer.png'
        data['segments'].insert(1, nearer)
    world = {r: {'present': {'value': True}, 'position': {'value': pos}, 'posture': {'value': '站着'}}
             for r, pos in [('a', '桌前'), ('b', '门边')]}
    context = {'context_id': 'room', 'input_state': world, 'tracked_role_ids': ['a', 'b'],
               'input_revision': 'input', 'previous_segment_uid': 'prior', 'initializing': False}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context', lambda *a, **k: copy.deepcopy(context))
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.scene_state_guard', lambda *a, **k: {})
    def plan(prepared, incoming):
        assert incoming['input_state'] == world
        return {'shots': [{'shot_uid': 's1', 'visible_roles': ['a', 'b'], 'edits': [], 'world_events': [],
                           'role_coverage': [{'role_id': 'a', 'visibility': 'visible',
                                              'reason': '镜头覆盖桌前主体甲'},
                                             {'role_id': 'b', 'visibility': 'partial',
                                              'reason': '门边人物乙处于同镜头后景范围内'}]}],
                'conflicts': [], 'unresolved': []}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch', planner_reply(plan))
    draft = snapshot(target)
    original = copy.deepcopy(data)
    request = {'operation_contract': 'scene_state_v3', 'jobs': data, 'segment_uid': 'target',
               'draft': draft, 'base_revision': fingerprint(draft)}
    if source_mode in ('unselected', 'different_scene', 'draft'):
        with pytest.raises(ValueError, match='入画人物没有对应图片参考'):
            build_opening_candidate(request)
    else:
        result = build_opening_candidate(request)
        bound = next(a for a in result['draft']['asset_manifest'] if a['metadata'].get('role_id') == 'b')
        expected = {'current': 'b-current.png', 'nearest': 'b-nearer.png'}.get(source_mode, 'b-selected.png')
        assert bound['path'] == expected
        assert result['draft']['scene_patch']['record']['end_state']['b']['position']['value'] == '门边'
    assert data == original
