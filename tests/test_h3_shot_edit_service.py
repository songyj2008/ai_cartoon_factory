import copy
import json

import pytest

from generation.pipelines.minimax_h3 import shot_edit_service as service
from test_h3_shot_editor import PROMPT


def make_runtime(tmp_path):
    manifest = [{'asset_id': 'person_a', 'type': 'image', 'path': 'a.png', 'role': '测试角色',
                 'metadata': {'identity_id': 'test_person_a'}},
                {'asset_id': 'voice_a', 'type': 'audio', 'path': 'voice.mp3', 'role': '测试角色',
                 'metadata': {'identity_id': 'test_person_a', 'binding_source': 'character_voice'}}]
    segments, beats = [], []
    for i in range(1, 3):
        segments.append({'segment_uid': f'seg{i}', 'beat_uid': f'beat{i}', 'segment_index': i,
                         'model_id': service.MODEL, 'title': f'段{i}', 'video_ok': True,
                         'renders': [{'render_id': f'old{i}', 'prompt_snapshot': PROMPT, 'video_ok': True}],
                         'generation_config': {'asset_manifest': copy.deepcopy(manifest), 'prompt_override': PROMPT},
                         'job': {'duration_sec': 6, 'prompt': PROMPT}})
        beats.append({'beat_uid': f'beat{i}', 'plot': '旧剧情', 'estimated_duration_sec': 6,
                      'dialogue_units': [{'speaker': '旧人', 'line': '旧句'}]})
    (tmp_path / 'beats.json').write_text(json.dumps({'beats': beats, 'target_duration_sec': 90}), encoding='utf-8')
    (tmp_path / 'video_jobs.json').write_text(json.dumps({'segments': segments}), encoding='utf-8')
    return {'segment_index': 1}


def request(result):
    return {key: result[key] for key in ('runtime', 'fingerprint', 'documents')}


def seed_state_records(tmp_path):
    make_runtime(tmp_path)
    path = tmp_path / 'video_jobs.json'
    jobs = json.loads(path.read_text(encoding='utf-8'))
    for segment in jobs['segments']:
        parsed = service.parse_prompt(PROMPT, segment['segment_uid'],
                                      segment['generation_config']['asset_manifest'])
        uids = [f"{segment['segment_uid']}_stable_{i}" for i in range(len(parsed['shots']))]
        segment['h3_shot_editor'] = {
            'shot_uids': uids, 'prompt_hash': service._hash(PROMPT),
            'dialogue_uids': {uid: [f'{uid}_line'] for uid in uids},
        }
        segment['h3_continuity'] = {
            'schema_version': 3,
            'shots': [{'shot_uid': uid, 'world_events': []} for uid in uids],
        }
    path.write_text(json.dumps(jobs), encoding='utf-8')
    return jobs


def test_unsaved_composition_prose_keeps_stable_shot_and_dialogue_ids(tmp_path):
    jobs = seed_state_records(tmp_path)
    original = jobs['segments'][0]['h3_shot_editor']
    text = PROMPT.replace('；动作在', '；侧面近景；动作在', 1)
    initial = service.preview({'segment_index': 1, 'editor_prompt': text}, root=tmp_path)
    shots = initial['documents'][0]['shots']
    assert [shot['uid'] for shot in shots] == original['shot_uids']
    assert [shot['dialogues'][0]['uid'] for shot in shots] == [
        original['dialogue_uids'][uid][0] for uid in original['shot_uids']]
    service.commit(request(initial), root=tmp_path, check_files=False)
    saved = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    assert saved['h3_shot_editor']['shot_uids'] == original['shot_uids']
    assert not saved['h3_continuity'].get('state_pending')


@pytest.mark.parametrize('change', ['add', 'delete', 'reorder'])
def test_explicit_structure_changes_mark_scene_record_pending(tmp_path, change):
    seed_state_records(tmp_path)
    initial = service.preview({'segment_index': 1}, root=tmp_path)
    doc = initial['documents'][0]
    if change == 'add':
        extra = copy.deepcopy(doc['shots'][-1])
        extra.update(uid='new_shot', duration_cs=100)
        doc['shots'].append(extra)
    elif change == 'delete':
        doc['shots'].pop(1)
    else:
        doc['shots'][0], doc['shots'][1] = doc['shots'][1], doc['shots'][0]
    doc.update(touched=True, structure_changed=True)
    service.commit(request(initial), root=tmp_path, check_files=False)
    saved = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))['segments'][0]
    assert saved['h3_continuity']['state_pending'] is True
    assert [shot['shot_uid'] for shot in saved['h3_continuity']['shots']] != saved['h3_shot_editor']['shot_uids']


def test_report_dialogue_preserves_explicit_subject_owner(tmp_path):
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    make_runtime(tmp_path)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    config = H3GenerationConfig.from_mapping(jobs['segments'][0]['generation_config'])
    text = PROMPT.replace('(S1)说：', '(S1)以急促、清晰的声音报告：', 1)
    dialogues = service._dialogues(text, config)
    assert len(dialogues) == 3
    assert dialogues[0]['speaker'] == '测试角色'
    assert dialogues[0]['line'] == '金额从3亿做到30亿。'
    assert dialogues[0]['scope'] == 'onscreen'


def test_time_only_commit_keeps_history_and_updates_beat(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    doc = initial['documents'][0]
    doc['shots'][0]['duration_cs'] += 100
    out = service.commit(request(initial), root=tmp_path, check_files=False)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    beats = json.loads(out['beats_text'])
    seg = jobs['segments'][0]
    assert seg['job']['duration_sec'] == 7 and beats['total_duration_sec'] == 13
    assert seg['renders'][0]['prompt_snapshot'] == PROMPT and seg['renders'][0]['video_ok']
    assert 'video_ok' not in seg and seg['status'] == 'needs_regenerate'
    assert seg['generation_config']['asset_manifest'][1]['path'] == 'voice.mp3'
    assert beats['target_duration_sec'] == 90
    assert '旧句' not in json.dumps(beats['beats'][0], ensure_ascii=False)


def test_new_next_segment_copies_manifest_and_shifts_identity(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    source = initial['documents'][0]
    new = copy.deepcopy(source)
    new.update(uid='seg_new', new=True, touched=True, structure_changed=True, title='续段')
    new['shots'] = source['shots'][1:]
    source['shots'] = source['shots'][:1]
    source['shots'][0]['duration_cs'] = 400
    source['structure_changed'] = True
    # Remaining 1.40 + 2.50 explicitly padded to 4.00.
    new['shots'][-1]['duration_cs'] += 10
    initial['documents'].insert(1, new)
    service.commit(request(initial), root=tmp_path, check_files=False)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    a, b, old = jobs['segments']
    assert b['generation_config']['asset_manifest'] == a['generation_config']['asset_manifest']
    assert b['segment_uid'] == 'seg_new' and old['segment_uid'] == 'seg2'
    assert old['video_ok'] and old['renders'][0]['prompt_snapshot'] == PROMPT
    assert not b.get('renders') and not b.get('video_ok')
    assert '第二句话' not in a['generation_config']['prompt_override']
    assert '第二句话' in b['generation_config']['prompt_override']


def test_transfer_to_existing_part_retimes_and_saves_both(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    source, target = initial['documents']
    moved = source['shots'].pop()
    moved['duration_cs'] = 200
    source['shots'][0]['duration_cs'] = 260
    target['shots'].insert(0, moved)
    for doc in (source, target):
        doc.update(touched=True, structure_changed=True)
    service.commit(request(initial), root=tmp_path, check_files=False)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    assert len(jobs['segments']) == 2
    a, b = jobs['segments']
    assert (a['job']['duration_sec'], b['job']['duration_sec']) == (4, 8)
    assert a['segment_uid'] == 'seg1' and b['segment_uid'] == 'seg2'
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt
    shots = parse_prompt(b['generation_config']['prompt_override'], 'target')['shots']
    assert len(shots) == 4
    assert shots[0]['original_start_cs'] == 0
    assert shots[1]['original_start_cs'] == 200
    assert '最后一句' in shots[0]['body'] and '金额从' in shots[1]['body']
    assert len(parse_prompt(a['generation_config']['prompt_override'], 'source')['shots']) == 2


def test_overlong_transfer_saves_editable_draft_with_submission_warning(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    source, target = initial['documents']
    target['shots'] = source['shots'][1:] + target['shots']
    source['shots'] = source['shots'][:1]
    source['shots'][0]['duration_cs'] = 400
    target['shots'][0]['duration_cs'] = 1500
    for doc in (source, target):
        doc.update(touched=True, structure_changed=True)
    before = {name: (tmp_path / name).read_bytes() for name in ('beats.json', 'video_jobs.json')}
    service.commit(request(initial), root=tmp_path, check_files=False)
    assert len(source['shots']) + len(target['shots']) == 6
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    assert jobs['segments'][1]['job']['duration_sec'] > 15
    assert jobs['segments'][1]['planning_state'] == 'warning'
    assert any(e['code'] == 'duration_out_of_range' for e in jobs['segments'][1]['planning_issues'])


def test_fractional_and_stale_commit_do_not_write(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    path = tmp_path / 'beats.json'
    before = path.read_bytes()
    initial['documents'][0]['shots'][0]['duration_cs'] += 1
    service.commit(request(initial), root=tmp_path, check_files=False)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    assert jobs['segments'][0]['job']['duration_sec'] == 6.01
    assert any(e['code'] == 'duration_not_integer' for e in jobs['segments'][0]['planning_issues'])
    path.write_text(json.dumps({'beats': [], 'changed': True}))
    with pytest.raises(ValueError, match='已变化|数量不一致'):
        service.commit(request(initial), root=tmp_path, check_files=False)


def test_channel_autosave_and_render_poll_do_not_conflict_or_get_overwritten(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    initial['documents'][0]['shots'][0]['duration_cs'] += 100
    path = tmp_path / 'video_jobs.json'
    jobs = json.loads(path.read_text(encoding='utf-8'))
    jobs['segments'][0]['generation_config']['channel_id'] = 'comfyui'
    jobs['segments'][1]['renders'].append({'render_id': 'finished', 'status': 'success'})
    jobs['runninghub_usage'] = {'generation_count': 2}
    path.write_text(json.dumps(jobs), encoding='utf-8')
    service.commit(request(initial), root=tmp_path, check_files=False)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['segments'][0]['generation_config']['channel_id'] == 'comfyui'
    assert saved['segments'][0]['job']['duration_sec'] == 7
    assert saved['segments'][1]['renders'][-1]['render_id'] == 'finished'
    assert saved['runninghub_usage']['generation_count'] == 2


@pytest.mark.parametrize('change', ['prompt', 'manifest', 'order', 'beat'])
def test_real_content_changes_still_reject_old_draft(tmp_path, change):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    path = tmp_path / 'video_jobs.json'
    jobs = json.loads(path.read_text(encoding='utf-8'))
    if change == 'prompt':
        jobs['segments'][0]['generation_config']['prompt_override'] += '\n新内容'
    elif change == 'manifest':
        jobs['segments'][0]['generation_config']['asset_manifest'][0]['path'] = 'new.png'
    elif change == 'order':
        jobs['segments'].reverse()
    else:
        beats_path = tmp_path / 'beats.json'
        beats = json.loads(beats_path.read_text(encoding='utf-8'))
        beats['beats'][0]['plot'] = '新的剧情'
        beats_path.write_text(json.dumps(beats), encoding='utf-8')
    path.write_text(json.dumps(jobs), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='已变化'):
        service.commit(request(initial), root=tmp_path, check_files=False)
    assert path.read_bytes() == before


def test_read_current_text_keeps_unsaved_dialogue_as_dirty_candidate(tmp_path):
    make_runtime(tmp_path)
    result = service.preview({'segment_index': 1,
                              'editor_prompt': PROMPT.replace('第二句话。', '新的第二句。')}, root=tmp_path)
    assert result['documents'][0]['editor_changed']
    service.commit(request(result), root=tmp_path, check_files=False)
    beats = json.loads((tmp_path / 'beats.json').read_text(encoding='utf-8'))
    assert beats['beats'][0]['dialogue_units'][1]['line'] == '新的第二句。'


def test_transaction_rolls_back_second_file_failure(tmp_path, monkeypatch):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    before = [(tmp_path / f).read_bytes() for f in ('beats.json', 'video_jobs.json')]
    old = service._replace_json
    failed = False
    def write(path, value):
        nonlocal failed
        if path.name == 'video_jobs.json' and not failed:
            failed = True
            raise OSError('simulated second file failure')
        old(path, value)
    monkeypatch.setattr(service, '_replace_json', write)
    with pytest.raises(OSError):
        service.commit(request(initial), root=tmp_path, check_files=False)
    for name, original in zip(('beats.json', 'video_jobs.json'), before):
        assert json.loads((tmp_path / name).read_text(encoding='utf-8')) == json.loads(original)


def test_conflicting_voice_versions_are_not_silently_replaced():
    a = {'asset_id': 'a', 'type': 'audio', 'path': 'old.mp3', 'metadata': {'identity_id': 'same'}}
    with pytest.raises(ValueError, match='版本冲突'):
        service.merge_manifests([a], [{**a, 'path': 'new.mp3'}])


def test_move_into_next_maps_subject_and_audio_and_preserves_source_versions(tmp_path):
    initial = service.preview(make_runtime(tmp_path), root=tmp_path)
    source, target = initial['documents']
    # Existing next segment uses a different person and soundtrack before source audio.
    target_manifest = [
        {'asset_id': 'person_b', 'type': 'image', 'path': 'b.png', 'role': '目标人物'},
        {'asset_id': 'vid', 'type': 'video', 'path': 'v.mp4', 'use_video_audio': True},
        {'asset_id': 'voice_b', 'type': 'audio', 'path': 'b.mp3', 'role': '目标人物'}]
    target['config']['asset_manifest'] = target_manifest
    for shot in target['shots']:
        shot['reference_manifest'] = copy.deepcopy(target_manifest)
    moved = source['shots'].pop()
    moved['body'] += ' <Audio 1> 为音色参考。'
    source['shots'][0]['duration_cs'] += 50
    moved['duration_cs'] = 200
    source['structure_changed'] = target['structure_changed'] = True
    target['shots'].insert(0, moved)
    target['touched'] = True
    service.commit(request(initial), root=tmp_path, check_files=False)
    jobs = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    text = jobs['segments'][1]['generation_config']['prompt_override']
    assert '<Subject 2> (S1)说：<d>[Chinese]最后一句。</d>' in text
    assert '<Audio 3> 为音色参考' in text
    assert jobs['segments'][0]['job']['duration_sec'] == 4
    assert jobs['segments'][1]['job']['duration_sec'] == 8


def test_active_target_blocks_whole_batch(tmp_path):
    make_runtime(tmp_path)
    path=tmp_path/'video_jobs.json'
    jobs=json.loads(path.read_text(encoding='utf-8'))
    jobs['segments'][0]['status']='running'
    path.write_text(json.dumps(jobs),encoding='utf-8')
    initial=service.preview({'segment_index':1},root=tmp_path)
    before=(tmp_path/'beats.json').read_bytes()
    with pytest.raises(ValueError,match='正在生成'):
        service.commit(request(initial),root=tmp_path,check_files=False)
    assert (tmp_path/'beats.json').read_bytes()==before
