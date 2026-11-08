"""Human edits consume content without accepting an earlier model's facts."""
import copy
import json
from contextlib import nullcontext

import pytest

from generation.pipelines.minimax_h3.editor_draft import commit, snapshot, fingerprint, EditorConflictError
from test_h3_scene_state_machine import jobs, ready_scene_fixture


@pytest.fixture
def editing(tmp_path, monkeypatch):
    data = jobs()
    row = data['segments'][0]
    row['beat_uid'] = 'beat1'
    row['generation_config']['asset_manifest'] = [{
        'asset_id': 'actor', 'type': 'image', 'path': 'actor.png', 'role': '甲',
        'metadata': {'asset_kind': 'character', 'role_id': 'a'}}]
    row['generation_config']['prompt_override'] = (
        '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；'
        '镜头构图：全景；<Subject 1> (S1)说：<d>[Chinese]原台词</d>；声音：室内环境声。')
    ready_scene_fixture(data, '1')
    before = copy.deepcopy(row)
    draft = snapshot(row)
    # These stale model objects must never be applied by a human-content save.
    draft.update(scene_patch={'obsolete': True}, state_guard={'obsolete': True},
                 composition_patch={'shots': []}, state_only=True,
                 plan={'obsolete': True}, optimization_attempt_id='old-attempt')
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': [{'beat_uid': 'beat1'}]}), encoding='utf-8')
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: data)
    monkeypatch.setattr('workflow.video_merge.video_jobs_lock', nullcontext)
    monkeypatch.setattr('services.context.get_project_dir', lambda: tmp_path)
    monkeypatch.setattr('services.context.assert_current_runtime_writable', lambda *a: None)
    monkeypatch.setattr('generation.pipelines.minimax_h3.shot_edit_service._editing_fingerprint', lambda *a: 'saved')
    monkeypatch.setattr('services.llm.call_llm', lambda *a, **k: pytest.fail('saving content cannot call LLM'))
    writes = []
    monkeypatch.setattr('generation.pipelines.minimax_h3.segment_content_sync.write_content_pair',
                        lambda *a, **kw: writes.append(copy.deepcopy(kw)))
    payload = {'segment_uid': '1', 'base_revision': fingerprint(snapshot(row)),
               'draft': draft, 'manual_edit': True}
    return row, before, payload, writes


@pytest.mark.parametrize('kind', ['dialogue', 'staging'])
def test_human_edit_saves_content_and_invalidates_old_candidate_state(editing, kind):
    row, before, payload, writes = editing
    source, replacement = ('原台词', '人工修改的新台词') if kind == 'dialogue' else ('全景', '桌前单人中景')
    payload['draft']['prompt'] = payload['draft']['prompt'].replace(source, replacement)
    original_payload = copy.deepcopy(payload)
    result = commit(payload)
    assert result['manual_edit'] is True
    assert replacement in row['generation_config']['prompt_override']
    assert replacement in writes[0]['beats']['beats'][0]['plot']
    assert row['h3_state_mode'] == 'unset'
    assert row['h3_continuity']['state_pending'] is True
    assert row['h3_continuity']['end_state'] == before['h3_continuity']['end_state']
    assert not (row.get('h3_state_optimization') or {}).get('last_saved')
    assert row['generation_config']['prompt_version_info']['source'] == 'manual_edit'
    assert payload == original_payload
    assert len(writes) == 1


def test_human_edit_still_rejects_conflicting_saved_revision(editing):
    row, before, payload, writes = editing
    payload['base_revision'] = 'outdated'
    with pytest.raises(EditorConflictError) as error:
        commit(payload)
    assert error.value.code == 'CONTENT_CONFLICT'
    assert not writes and row == before


def test_human_edit_still_rejects_unbound_subjects(editing):
    row, before, payload, writes = editing
    payload['draft']['prompt'] = payload['draft']['prompt'].replace('原台词</d>', '原台词</d>；望向<Subject 99>')
    with pytest.raises(ValueError, match='参考绑定不存在'):
        commit(payload)
    assert not writes and row == before
