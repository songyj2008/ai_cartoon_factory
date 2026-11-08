import copy
import pytest
from generation.pipelines.minimax_h3.continuity.constraints import DEFAULT, save_constraints, scene_constraints, prompt_with_constraints
from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard
from generation.pipelines.minimax_h3.continuity.inheritance import state_status
from generation.pipelines.minimax_h3.editor_draft import EditorConflictError
from test_scene_state_jobs import ready_sample


def test_constraint_change_preserves_states_and_expires_optimization_guard():
    jobs = ready_sample()
    scene = jobs['h3_scene_continuity']['contexts']['room']
    records = copy.deepcopy([s['h3_continuity'] for s in jobs['segments']])
    before = scene_state_guard(jobs, '1')
    save_constraints(scene, {'spatial_constraints': '固定设施相对位置保持不变', 'constraints_revision': None})
    assert scene_state_guard(jobs, '1') != before
    assert state_status(jobs, '1')['ready'] is True
    assert [s['h3_continuity'] for s in jobs['segments']] == records
    original = copy.deepcopy(scene)
    with pytest.raises(EditorConflictError):
        save_constraints(scene, {'spatial_constraints': '旧页面', 'constraints_revision': None})
    assert scene == original
    save_constraints(scene, {'spatial_constraints': '', 'constraints_revision': scene['constraints_revision']})
    assert scene_constraints(scene) == ''


def test_default_and_prompt_section_are_shared_and_not_duplicated():
    assert scene_constraints({}) == DEFAULT
    text = '【详细镜头描述】\n[Shot 1] 原稿\n\n【整体声音环境】\n自然声'
    once = prompt_with_constraints(text, DEFAULT)
    assert prompt_with_constraints(once, DEFAULT) == once
    updated = prompt_with_constraints(once, '新的空间约束')
    assert DEFAULT not in updated
    assert updated.count('【场景空间约束】') == 1
    assert prompt_with_constraints(updated, '') == text


def test_layout_is_versioned_against_background_without_changing_world_facts():
    from generation.pipelines.minimax_h3.continuity.constraints import save_scene_layout, scene_layout
    scene = {'initial_state': {'a': {'present': {'value': True}}}}
    original = copy.deepcopy(scene['initial_state'])
    background = {'type': 'image', 'path': 'room-v1.png',
                  'metadata': {'asset_kind': 'background', 'background_id': 'room'}}
    manifest = [background]
    assert save_scene_layout(scene, {'scene_layout': '门在桌子左侧', 'layout_revision': None}, manifest)
    revision = scene['layout_revision']
    assert scene_layout(scene, manifest)['current'] is True
    assert scene['initial_state'] == original
    assert not save_scene_layout(scene, {'scene_layout': '门在桌子左侧', 'layout_revision': revision}, manifest)
    assert scene['layout_revision'] == revision
    replacement = [dict(background, path='room-v2.png')]
    assert scene_layout(scene, replacement)['current'] is False
    before = copy.deepcopy(scene)
    with pytest.raises(EditorConflictError):
        save_scene_layout(scene, {'scene_layout': '旧窗口覆盖', 'layout_revision': None}, replacement)
    assert scene == before
    assert save_scene_layout(scene, {'scene_layout': '门在桌子左侧', 'layout_revision': revision}, replacement)
    assert scene_layout(scene, replacement)['current'] is True
    assert scene['layout_revision'] != revision
    assert scene['initial_state'] == original


def test_missing_layout_is_optional_and_not_invented_from_background():
    from generation.pipelines.minimax_h3.continuity.constraints import scene_layout
    result = scene_layout({}, [{'type': 'image', 'path': 'room.png',
                               'metadata': {'asset_kind': 'background'}}])
    assert result['description'] == '' and result['current'] is True
