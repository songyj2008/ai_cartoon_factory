import copy
import pytest

from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan
from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline


MODEL = 'minimax_h3_local_ref2va'


def test_unchanged_facts_keep_their_original_source():
    world = {'actor': {'posture': {'value': '站着', 'origin': {'segment_uid': 'old'}}}}
    assert reduce_plan(world, {'shots': [{'shot_uid': 'new', 'world_events': [
        {'role_id': 'actor', 'facts': {'posture': {'value': '站着'}}}]}]}, 'new', ['actor']) == world


def test_draft_planner_returns_changes_in_its_existing_model_call(monkeypatch):
    import json
    from generation.pipelines.minimax_h3 import prompt_planner
    calls = []
    ctx = {'input_state': {'actor': {'posture': {'value': '坐着'}}},
           'tracked_role_ids': ['actor'], 'initializing': False}

    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        assert '动作执行前' in system
        return {'scene_relation': 'immediate', 'duration_sec': 4, 'duration_reason': '起身',
                'shot_mode': 'one_take', 'shots': [{'start_sec': 0, 'end_sec': 4,
                'scene_id': 'room', 'visible_roles': ['actor'], 'subject': '甲',
                'action': '甲从坐姿起身，站在桌旁。', 'audio': '本镜无对白',
                'world_events': [{'role_id': 'actor', 'facts': {'posture': {'value': '站着'}}}]}]}, 0

    monkeypatch.setattr(prompt_planner, '_call_model', model)
    monkeypatch.setattr('services.video_experience.retrieve', lambda *args, **kwargs: [])
    result = prompt_planner.plan_h3_prompt({'plot': '甲起身。'}, 1, duration_sec=4,
        draft_mode=True, continuity_context=ctx, force_replan=True)
    assert len(calls) == 1
    assert calls[0]['continuity_context']['input_state']['actor']['posture']['value'] == '坐着'
    assert not result['planning_issues']
    assert result['shots'][0]['world_events'][0]['facts']['posture']['value'] == '站着'


@pytest.mark.parametrize('separate', [False, True])
@pytest.mark.parametrize('grouping', ['unassigned', 'pending', 'ready'])
def test_segment_generation_ignores_scene_hints_and_saved_state(monkeypatch, separate, grouping):
    source = {'scene_contexts': [{'id': 'office', 'scene_id': 'room'}],
              'beats': [{'plot': action, 'estimated_duration_sec': 4,
                         'model_hints': {MODEL: {'shots': [{'scene_context_id': 'office',
                                                          'visible_roles': ['actor']}]}}}
                        for action in ('甲起身。', '甲站着说话。', '甲继续站着。')]}
    source_before = copy.deepcopy(source)
    store = {'segments': []}
    if grouping != 'unassigned':
        store['h3_scene_continuity'] = {'contexts': {'office': {
            'initial_pending': grouping == 'pending', 'initial_state': {}, 'config_revision': 'v1'}}}
        # Reproduce previously failed drafts, as well as manually assigned groups.
        store['segments'] = [{'segment_uid': f's{i}', 'segment_index': i,
                              'scene_context_id': 'office', 'h3_force_replan': True,
                              'planning_state': 'invalid', 'job': {'prompt': ''}}
                             for i in range(1, 4)]
    before = copy.deepcopy(store)
    calls = []
    pipeline = MiniMaxH3Ref2VAPipeline.__new__(MiniMaxH3Ref2VAPipeline)
    monkeypatch.setattr(pipeline, '_load_template', lambda: {})
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: copy.deepcopy(store))
    monkeypatch.setattr('workflow.video_merge.save_video_jobs', lambda result: store.update(copy.deepcopy(result)))
    monkeypatch.setattr('workflow.runninghub_queue.has_active_submission', lambda _: False)

    def prepare(template, beat, index, existing, **options):
        calls.append(options.get('previous_segment_context'))
        result = copy.deepcopy(existing)
        result.update(job={'prompt': f'prompt {index}', 'duration_sec': 4}, planning_state='ready')
        return result

    monkeypatch.setattr(pipeline, '_prepare_segment', prepare)
    for selection in ([{1}, {2}, {3}] if separate else [None]):
        result = pipeline.generate(source, submit=False, workflow_only=True, segment_indices=selection)
    assert calls == [None, None, None]
    assert [r['job']['prompt'] for r in result['segments']] == ['prompt 1', 'prompt 2', 'prompt 3']
    assert result.get('h3_scene_continuity') == before.get('h3_scene_continuity')
    assert all('h3_continuity' not in r for r in result['segments'])
    if grouping == 'unassigned':
        assert all('scene_context_id' not in r for r in result['segments'])
    else:
        assert all(r['scene_context_id'] == 'office' for r in result['segments'])
    assert source == source_before
