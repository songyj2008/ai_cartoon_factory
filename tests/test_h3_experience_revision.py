"""Feedback revisions use selected-draft examples without altering revision ownership."""
import copy

import pytest

from services import video_experience, web_llm_bridge as bridge


MODEL = 'minimax_h3_local_ref2va'
EDITOR = ('【主体定义】\n<Subject 1>：当前人物；可见内容来自 <Picture 1>。\n'
          '【详细镜头描述】\n[Shot 1]（0–8 秒）：画面人物：<Subject 1>；'
          '<Subject 1> (S1)说：<d>[Chinese]当前编辑稿台词。</d>')
ORIGINAL = EDITOR.replace('当前编辑稿台词。', '生成视频时台词。')


@pytest.fixture(autouse=True)
def isolated_bridge(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setattr(bridge, 'BRIDGE_DIR', tmp_path / 'bridge')
    monkeypatch.setattr(bridge, 'get_project_dir', lambda: project)
    monkeypatch.setattr(video_experience, 'library_path', lambda: tmp_path / 'missing.sqlite3')
    from services import text_jobs
    monkeypatch.setattr(text_jobs, 'current', lambda: None)


@pytest.mark.parametrize('use_editor', [False, True])
def test_revision_uses_selected_prompt_and_its_manifest(monkeypatch, use_editor):
    person = {'id': 'person', 'type': 'image', 'path': 'person.png', 'role': '当前人物',
              'metadata': {'identity_id': 'person'}}
    original_assets = [person, {'id': 'old-voice', 'type': 'audio', 'path': 'old.mp3',
                                'metadata': {'identity_id': 'person'}}]
    editor_assets = [person, {'id': 'new-voice', 'type': 'audio', 'path': 'new.mp3',
                              'metadata': {'identity_id': 'person'}}]
    snapshot = {'runtime': bridge.runtime_key(), 'fingerprint': 'selected',
                'original_prompt': ORIGINAL, 'original_asset_manifest': original_assets,
                'model_id': MODEL, 'duration_sec': 8,
                'context': {'asset_manifest': editor_assets, 'old_dialogue': '不能恢复的旧台词',
                            'requested_media_spec': {'duration_sec': 8, 'fps': 24}}}
    payload = {'runtime': snapshot['runtime'], 'fingerprint': 'selected',
               'issue': '开头有额外人声', 'edited_prompt': EDITOR, 'use_editor_prompt': use_editor}
    originals = copy.deepcopy((snapshot, payload))
    received = []
    example = {'sample_id': 'ok', 'summary': '同结构的人工认可例',
               'evidence': {'shot_descriptions': '只作文字证据'}}
    def retrieve(manifest, **kwargs):
        received.append((copy.deepcopy(manifest), kwargs))
        return [example]
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    task = bridge.revision_task(2, payload, snapshot_override=snapshot, provider_override='chatgpt')
    selected = EDITOR if use_editor else ORIGINAL
    assert received == [(editor_assets if use_editor else original_assets, {'prompt': selected, 'purpose': 'revision'})]
    assert task['input']['base_prompt'] == selected
    assert task['input']['approved_prompt_experiences'] == [example]
    assert video_experience.PLANNING_RULES in task['request']
    assert '本次仅修正反馈指定范围及直接冲突' in task['request']
    assert (snapshot, payload) == originals
    assert not task['metadata'].get('attachments')
    assert 'attachments' not in task['input']
    assert 'AICF_RESULT_' + task['id'] + '_START' in task['request']
    assert '"revised_prompt"' in task['request'] and '"changes"' in task['request']
    assert task['input']['shot_speech'][0]['dialogues'][0]['line'] == (
        '当前编辑稿台词。' if use_editor else '生成视频时台词。')
    if use_editor:
        assert '不能恢复的旧台词' not in task['request']
        assert '生成视频时台词。' not in task['request']


@pytest.mark.parametrize('unavailable', [False, True])
def test_revision_without_relevant_examples_preserves_original_protocol(monkeypatch, unavailable):
    def retrieve(*args, **kwargs):
        if unavailable:
            raise OSError('test unavailable')
        return []
    monkeypatch.setattr(video_experience, 'retrieve', retrieve)
    data = {'model_id': MODEL, 'base_prompt': EDITOR, 'video_issues': '修正人声'}
    original = copy.deepcopy(data)
    task = bridge.create_task('chatgpt', 'revision', data)
    assert data == original
    assert 'approved_prompt_experiences' not in task['input']
    assert video_experience.PLANNING_RULES not in task['request']
    assert '输出仍采用本次 revised_prompt 协议' in task['request']


@pytest.mark.parametrize('kind, model', [('revision', 'other-model'), ('json', MODEL)])
def test_other_models_and_non_revision_tasks_do_not_retrieve(monkeypatch, kind, model):
    def unexpected(*args, **kwargs):
        pytest.fail('only H3 feedback revisions retrieve examples')
    monkeypatch.setattr(video_experience, 'retrieve', unexpected)
    data = {'model_id': model, 'base_prompt': EDITOR}
    original = copy.deepcopy(data)
    task = bridge.create_task('chatgpt', kind, data)
    assert data == original
    assert 'approved_prompt_experiences' not in task['input']


def test_enriched_revision_does_not_mutate_caller_data_or_metadata(monkeypatch):
    example = {'sample_id': 'ok', 'summary': '文字经验'}
    monkeypatch.setattr(video_experience, 'retrieve', lambda *args, **kwargs: [example])
    data = {'model_id': MODEL, 'base_prompt': EDITOR}
    metadata = {'speech_manifest': [{'id': 'voice', 'type': 'audio'}], 'segment_index': 3}
    original = copy.deepcopy((data, metadata))
    task = bridge.create_task('chatgpt', 'revision', data, metadata=metadata)
    assert (data, metadata) == original
    assert task['input']['approved_prompt_experiences'] == [example]


def test_revision_recovery_keeps_fingerprint_check_and_never_overwrites_task(monkeypatch):
    examples = [{'sample_id': 'ok', 'summary': '原经验'}]
    monkeypatch.setattr(video_experience, 'retrieve', lambda *args, **kwargs: copy.deepcopy(examples))
    data = {'model_id': MODEL, 'base_prompt': EDITOR}
    metadata = {'operation_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'call_key': 'revision'}
    first = bridge.create_task('chatgpt', 'revision', data, metadata=metadata)
    same = bridge.create_task('chatgpt', 'revision', data, metadata=metadata)
    assert same['id'] == first['id']
    assert same['request_fingerprint'] == first['request_fingerprint']
    examples[0]['summary'] = '后来变化的经验'
    with pytest.raises(RuntimeError, match='输入与原请求不一致'):
        bridge.create_task('chatgpt', 'revision', data, metadata=metadata)
    saved = bridge.task_status(first['id'])
    assert saved['input']['approved_prompt_experiences'] == first['input']['approved_prompt_experiences']
    assert saved['request_fingerprint'] == first['request_fingerprint']
