from spatial_test_support import planner_reply, model_reply
"""Optimization uses incoming scene facts and publishes its own resulting facts."""
import copy

import pytest

from test_h3_scene_state_machine import jobs, ready_scene_fixture
from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint
from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
from generation.pipelines.minimax_h3.continuity.revisions import accept_candidate_state
from generation.pipelines.minimax_h3.continuity.inheritance import inherited_record, state_status


def sample(mode):
    data = jobs()
    row = data['segments'][2]
    row['generation_config'] = {'duration_sec': 4,
        'prompt_override': '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；人物调度：<Subject 1>坐下；镜头构图：全景；声音：无对白。',
        'asset_manifest': [{'asset_id': 'actor', 'type': 'image', 'role': '甲', 'order': 1,
                            'metadata': {'asset_kind': 'character', 'role_id': 'a'}}]}
    ready_scene_fixture(data, '3')
    context = read_ready_context(data, '3')
    row['h3_state_mode'] = mode
    if mode == 'unset':
        row.pop('h3_continuity')
    elif mode == 'inherit':
        row['h3_continuity'] = inherited_record(row, context)
    else:
        row['h3_continuity']['source_revision'] = 'outdated'
    return data, row


@pytest.mark.parametrize('mode', ['unset', 'inherit', 'extract'])
@pytest.mark.parametrize('change_text', [True, False])
def test_optimization_without_current_record_saves_real_end_state(monkeypatch, mode, change_text):
    data, row = sample(mode)
    before = copy.deepcopy(data)
    calls = []
    def plan(base, context):
        calls.append(context)
        assert context['input_state']['a']['posture']['value'] == '站着'
        return {'shots': [{'shot_uid': base['shot_uids'][0], 'visible_roles': ['a'],
                          'edits': [{'source_quote': '全景', 'replacement': '近景'}] if change_text else [],
                          'role_coverage': [{'role_id': role,
                                             'visibility': 'visible' if role == 'a' else 'out_of_frame',
                                             'reason': '镜头覆盖桌前主体' if role == 'a'
                                                       else '机位正对桌前主体，侧后方座位不在画框内'}
                                            for role in context['tracked_role_ids']],
                          'world_events': [{'role_id': 'a', 'facts': {'posture': {'value': '坐着'}},
                                            'source_quote': '<Subject 1>坐下'}]}],
                'conflicts': [], 'unresolved': []}
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch', planner_reply(plan))
    draft = snapshot(row)
    result = build_opening_candidate({'operation_contract': 'scene_state_v3', 'jobs': data,
        'segment_uid': '3', 'draft': draft, 'base_revision': fingerprint(draft)})
    assert len(calls) == 1
    assert data == before, 'Candidate generation must not save anything'
    assert result['scene_preview']['input_state']['a']['posture']['value'] == '坐着'
    candidate = result['draft']
    assert candidate['prompt_baseline']['prompt'] == draft['prompt']
    assert candidate['prompt_baseline']['shot_uids']
    # Shot/reference rendering can change the screenplay, but never adds a roster.
    assert not candidate.get('state_only', False)
    assert '【场景人物占位】' not in candidate['prompt']
    assert '<Subject 1>坐下' in candidate['prompt']
    assert '[Shot 1]（0–4 秒）' in candidate['prompt']
    row['generation_config']['prompt_override'] = candidate['prompt']
    row['generation_config']['asset_manifest'] = candidate['asset_manifest']
    accept_candidate_state(data, candidate['scene_patch'])
    assert row['h3_continuity']['record_kind'] == 'optimized'
    assert row['h3_state_mode'] == 'extract'
    assert state_status(data, '3')['ready'] is True
    assert '优化后' in state_status(data, '3')['message']
    assert read_ready_context(data, '5')['input_state']['a']['posture']['value'] == '坐着'


def test_missing_predecessor_and_uninitialized_scene_still_block(monkeypatch):
    data, row = sample('unset')
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.composition.plan_composition_patch',
                        planner_reply(lambda *a: pytest.fail('Invalid incoming state must not call the model')))
    draft = snapshot(row)
    payload = {'operation_contract': 'scene_state_v3', 'jobs': data,
               'segment_uid': '3', 'draft': draft, 'base_revision': fingerprint(draft)}
    data['segments'][0]['h3_state_mode'] = 'unset'
    with pytest.raises(ValueError, match='前序段'):
        build_opening_candidate(payload)
    data['h3_scene_continuity']['contexts']['room']['initial_pending'] = True
    with pytest.raises(ValueError, match='场景初态尚未建立'):
        build_opening_candidate(payload)


def test_http_optimization_accepts_unregistered_target(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from fastapi import FastAPI
    from starlette.requests import Request
    from ui import api_routes
    data, row = sample('unset')
    submitted = []
    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    monkeypatch.setattr(api_routes, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: data)
    monkeypatch.setattr('services.text_jobs.submit',
                        lambda kind, args, **kwargs: submitted.append((kind, args)) or {'id': 'optimization'})
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    endpoint = next(r.endpoint for r in app.routes if getattr(r, 'path', '') == '/api/segment/editor/{action}')
    payload = {'operation_contract': 'scene_state_v3', 'segment_uid': '3', 'draft': snapshot(row)}
    async def receive():
        return {'type': 'http.request', 'body': json.dumps(payload).encode()}
    response = asyncio.run(endpoint('continuity', Request({'type': 'http', 'headers': []}, receive)))
    assert response.status_code == 200, response.body.decode()
    assert json.loads(response.body)['operation_id'] == 'optimization'
    assert submitted[0][0] == 'opening_continuity'
    assert row['h3_state_mode'] == 'unset', 'Submitting must not silently declare no state changes'
