"""Exercise correction and replay through the real local web-task bridge."""
import copy
import json

import pytest

from services import context, text_jobs, web_llm_bridge as bridge


@pytest.fixture
def local_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'BRIDGE_DIR', tmp_path / 'bridge')
    monkeypatch.setattr(text_jobs, 'root', lambda: tmp_path / 'operations')
    monkeypatch.setattr(text_jobs, 'ensure_worker', lambda: None)
    monkeypatch.setattr(context, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(context, 'ASSET_INDEX_PATH', tmp_path / 'assets.json')
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    bridge.save_settings({'overrides': {'other': ['doubao'], 'prompt': ['chatgpt']}})
    (tmp_path / 'video_jobs.json').write_text('{"segments":[]}', encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('kind', ['scene_state', 'opening_continuity'])
def test_correction_has_distinct_web_task_and_recovery_replays_both_without_resending(local_bridge, monkeypatch, kind):
    if kind == 'scene_state':
        from test_h3_state_trace import inputs
        from generation.pipelines.minimax_h3.continuity.state_trace import extract_state_record
        segment, ctx, valid = inputs()
        invalid = copy.deepcopy(valid)
        # Reproduce the actual rejected reply: material numbering in a fact.
        invalid['shots'][0]['world_events'][0]['facts']['posture']['value'] = '<Subject 1>'
        invoke = lambda: extract_state_record(segment, ctx)
    else:
        from test_h3_composition_patch import example
        from generation.pipelines.minimax_h3.continuity.composition import plan_composition_patch
        base, valid = example()
        invalid = dict(copy.deepcopy(valid), initial_state={})
        invoke = lambda: plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']})

    real_create = bridge.create_task
    sent = []

    def complete_locally(provider, task_kind, data, **kwargs):
        task = real_create(provider, task_kind, data, **kwargs)
        assert task['entry'] == 'other' and provider == 'doubao'
        if task['status'] == 'queued':
            sent.append(task['id'])
            claim = bridge.claim('test-extension', ['doubao'])
            assert claim['id'] == task['id']
            payload = json.loads(data['user'])
            reply = valid if 'validation_feedback' in payload else invalid
            bridge.report(task['id'], claim['lease'], 'complete', result={'data': copy.deepcopy(reply)})
        return bridge.task_status(task['id'])

    monkeypatch.setattr(bridge, 'create_task', complete_locally)
    task = text_jobs.submit(kind, [{'segment_uid': 'synthetic'}], start=False, hold=True)
    token = text_jobs.ACTIVE.set(task)
    try:
        first = invoke()
        assert len(sent) == 2 and len(set(sent)) == 2
        keys = text_jobs.get(task['id'])['web_calls']
        assert len(keys) == 2 and any(key.endswith('_repair1') for key in keys)
        original = bridge.task_status(sent[0])
        correction = bridge.task_status(sent[1])
        assert original['request_fingerprint'] != correction['request_fingerprint']
        assert json.loads(correction['input']['user'])['validation_feedback']['previous_response'] == invalid
        assert 'validation_feedback' not in json.loads(original['input']['user'])
        # Restart/recovery repeats the calls but reads their existing results.
        assert invoke() == first
        assert len(sent) == 2
        assert bridge.task_status(sent[0])['input'] == original['input']
        assert bridge.task_status(sent[1])['input'] == correction['input']
        # A changed input under the original identity must remain forbidden.
        changed = {**original['input'], 'user': original['input']['user'] + ' '}
        with pytest.raises(RuntimeError, match='输入与原请求不一致'):
            real_create('doubao', 'json', changed, entry='other', metadata=original['metadata'])
    finally:
        text_jobs.ACTIVE.reset(token)
