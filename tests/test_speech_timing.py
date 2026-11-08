import copy
import json

import pytest

from services import context, speech_timing as timing, video_experience as learning


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, 'library_path', lambda: tmp_path / 'learning.sqlite3')
    monkeypatch.setattr(context, 'ASSETS_DIR', tmp_path / 'assets')
    return tmp_path


def manifest(identity='actor', role='当前角色', voice=None):
    result = [{'id': identity, 'type': 'image', 'path': identity + '.png', 'role': role,
               'metadata': {'identity_id': identity, 'asset_kind': 'character'}}]
    if voice:
        result.append({'id': 'voice-' + identity, 'type': 'audio', 'path': str(voice),
                       'metadata': {'identity_id': identity}})
    return result


def sample(identifier, rows, *, project='old-project', role='旧名字', identity='actor', assets=None):
    prompt = f'【主体定义】\n<Subject 1>：{role}；可见内容来自 <Picture 1>。\n【详细镜头描述】\n'
    cursor = 0
    for index, (line, duration) in enumerate(rows, 1):
        end = cursor + duration + .2
        prompt += (f'[Shot {index}]（{cursor:.3f}–{end:.3f} 秒）\n'
                   f'At {cursor + .1:.3f}–{cursor + .1 + duration:.3f} 秒：人物保持原位。\n'
                   f'<Subject 1> (S1)说：<d>[Chinese]{line}</d>\n声音：坚定，动作与说话并行。\n')
        cursor = end
    return {'sample_id': identifier, 'project': project, 'model_id': learning.MODEL_ID,
            'render_id': 'render-' + identifier, 'generated_prompt': prompt,
            'asset_manifest': assets or manifest(identity, role), 'voice_fingerprints': [],
            'actual_duration_sec': cursor}


def store(value, *, active=1, status='queued', analysis=None):
    with learning.database() as db:
        db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
            value['sample_id'], value['project'], 'runtime-' + value['project'], 'segment', active,
            'token', '', status, json.dumps(value, ensure_ascii=False),
            json.dumps(analysis) if analysis else None, 1))


def test_full_population_median_preserves_fast_approved_sample(library):
    first = sample('one', [('好，职责权限和未解决风险正式交接，邮件随后补。', 2.55),
                            ('别以为没你不行！', 1.1),
                            ('你拿九十五万奖金，以后系统出问题别找我！', 3.35), ('干活去，散会！', 1.1)])
    second = sample('two', [('研发？随时可以换一批。', 1.5),
                             ('你把我们当垃圾啊？我们熬夜扛系统的时候你在哪？', 1.4),
                             ('没有我们，你拿什么做三个亿？', 1.4), ('刘洋你再说一遍试试！', 1.5)])
    store(first)
    store(second, status='error')  # Numeric data does not wait for qualitative analysis.
    result = timing.build_context(manifest())
    stats = result['population']
    assert stats['line_count'] == 8
    assert stats['median_chars_per_sec'] == pytest.approx((6 + 7 / 1.1) / 2)
    assert stats['weighted_mean_chars_per_sec'] == pytest.approx(101 / 13.9)
    assert stats['max_chars_per_sec'] == pytest.approx(15)
    assert stats['render_count'] == 2
    newcomer = timing.build_context(manifest('new-person'))
    assert newcomer['speakers'][0]['personal']['line_count'] == 0
    assert len(newcomer['examples']) == 8
    assert newcomer['examples'][-1]['chars_per_sec'] == pytest.approx(15)


def test_same_identity_cross_project_names_merge_but_same_names_do_not(library):
    store(sample('one', [('你好。', 1)], project='a', role='甲'))
    store(sample('two', [('请你回答。', 1)], project='b', role='乙'))
    store(sample('other', [('请你马上回答。', 1)], project='c', role='甲', identity='other'))
    result = timing.build_context(manifest('actor', '新项目名字'))
    own = result['speakers'][0]
    assert own['personal']['line_count'] == 2
    assert own['personal']['median_chars_per_sec'] == 3
    assert own['current_role_name'] == '新项目名字'
    assert result['population']['line_count'] == 3
    timing.merge_identity('other', 'actor')
    assert timing.build_context(manifest())['speakers'][0]['personal']['line_count'] == 3
    with pytest.raises(ValueError, match='循环'):
        timing.merge_identity('actor', 'other')


def test_unbound_speaker_contributes_to_population_but_not_personal_pool(library):
    value = sample('one', [('请你回答。', 1)])
    value['asset_manifest'] = []
    store(value)
    data = timing.build_context(manifest())
    assert data['population']['median_chars_per_sec'] == 4
    assert data['speakers'][0]['personal']['line_count'] == 0
    assert data['unassigned_identity_records'] == 1


def test_undo_reactivate_and_archive_copy_do_not_double_count(library):
    original = sample('original', [('请你回答。', 1)])
    store(original)
    duplicate = {**copy.deepcopy(original), 'sample_id': 'archive-copy', 'project': 'archive'}
    store(duplicate)
    before = timing.build_context(manifest())
    assert before['population']['line_count'] == 1
    with learning.database() as db:
        db.execute('UPDATE samples SET active=0')
    empty = timing.build_context(manifest())
    assert empty['population']['median_chars_per_sec'] is None
    assert empty['statistics_version'] != before['statistics_version']
    with learning.database() as db:
        db.execute("UPDATE samples SET active=1 WHERE id='original'")
    assert timing.build_context(manifest())['population']['line_count'] == 1


def test_shot_budget_and_mixed_text_are_not_speech_rates(library):
    value = sample('one', [('请等三秒。', 1)])
    value['generated_prompt'] = value['generated_prompt'].replace(
        'At 0.100–1.100 秒：人物保持原位。\n', '')
    result = timing.extract_sample(value)
    assert result['lines'][0]['eligibility_reason'] == 'missing_explicit_speech_window'
    value = sample('mixed', [('请等3秒。', 1)])
    assert timing.extract_sample(value)['lines'][0]['eligibility_reason'] == 'unsupported_counting_basis'
    forged = {'speech_timing': [{'line_id': 1, 'line': '请等3秒。', 'speech_intervals': [[0, .01]]}]}
    assert timing.extract_sample(value, forged)['lines'][0]['chars_per_sec'] is None


def test_cross_shot_continuation_counts_original_line_once(library):
    value = sample('one', [('请你回答。', 1)])
    value['generated_prompt'] += ('[Shot 2]（1.200–2.400 秒）\n'
        'At 1.300–2.300 秒：人物保持原位。\n'
        '接着上个 Shot 继续说，<Subject 1>，画外说：<d>[Chinese]请你回答。</d>')
    rows = timing.extract_sample(value)['lines']
    assert len(rows) == 1 and rows[0]['line_ids'] == [1, 2]
    assert rows[0]['hanzi_count'] == 4
    assert rows[0]['speech_window_sec'] == pytest.approx(2)
    assert rows[0]['chars_per_sec'] == pytest.approx(2)


def test_speech_window_before_closing_silence_is_selected(library):
    value = sample('one', [('请你回答。', 1)])
    value['generated_prompt'] = ('【主体定义】\n<Subject 1>：旧名字；可见内容来自 <Picture 1>。\n'
        '【详细镜头描述】\n[Shot 1]（0–3 秒）\n'
        'At 0.0–0.2秒：<Subject 1>抬眼。\n'
        'At 0.2–2.8秒：<Subject 1>完整说出台词。\n'
        'At 2.8–3.0秒：<Subject 1>说完闭口，保持目光。\n'
        '<Subject 1> (S1)说：<d>[Chinese]请你回答。</d>')
    row = timing.extract_sample(value)['lines'][0]
    assert row['speech_intervals'] == [[.2, 2.8]]
    assert row['chars_per_sec'] == pytest.approx(4 / 2.6)
    forged = {'speech_timing': [{'line_id': 1, 'line': '请你回答。', 'speech_intervals': [[2.8, 3]]}]}
    assert timing.extract_sample(value, forged)['lines'][0]['speech_intervals'] == [[.2, 2.8]]


def test_ambiguous_multiple_action_ranges_do_not_become_speech_window(library):
    value = sample('one', [('请你回答。', 1)])
    value['generated_prompt'] = value['generated_prompt'].replace(
        'At 0.100–1.100 秒：人物保持原位。',
        'At 0.100–0.500 秒：人物抬眼。\nAt 0.500–1.100 秒：人物移动物件。')
    row = timing.extract_sample(value)['lines'][0]
    assert row['chars_per_sec'] is None
    assert row['eligibility_reason'] == 'missing_explicit_speech_window'


def test_reference_audio_transcript_is_bound_to_exact_selected_file(library):
    directory = library / 'assets' / 'characters' / 'identities' / 'actor'
    directory.mkdir(parents=True)
    voice = directory / 'voice.mp3'
    voice.write_bytes(b'voice')
    (directory / 'asset_meta.json').write_text(json.dumps({'identity': {'voice_reference': {
        'file': 'voice.mp3', 'transcript': '请你回答。', 'duration_sec': 2}}}), encoding='utf-8')
    assets = manifest(voice=voice)
    result = timing.build_context(assets)
    ref = result['speakers'][0]['reference_audio']
    assert ref['chars_per_sec'] == 2 and ref['duration_basis'] == 'file_duration'
    assert result['population']['median_chars_per_sec'] is None
    other = directory / 'other.mp3'
    other.write_bytes(b'other')
    assets[-1]['path'] = str(other)
    assert timing.build_context(assets)['speakers'][0]['reference_audio']['chars_per_sec'] is None


def test_voice_replacement_keeps_personal_pool_and_separates_same_voice(library):
    a, b = library / 'a.mp3', library / 'b.mp3'
    a.write_bytes(b'a'); b.write_bytes(b'b')
    store(sample('one', [('请你回答。', 1)], assets=manifest(voice=a)))
    store(sample('two', [('请你回答。', 2)], assets=manifest(voice=b)))
    who = timing.build_context(manifest(voice=b))['speakers'][0]
    assert who['personal']['line_count'] == 2
    assert who['same_voice']['line_count'] == 1
    assert who['same_voice']['median_chars_per_sec'] == 2


def test_experience_retrieval_finds_person_across_projects_beyond_old_limit(library):
    analysis = {'analysis_basis': 'user_approved_prompt', 'timing_analysis': [], 'transition_analysis': [],
                'speech_control_analysis': [], 'reusable_lessons': [], 'limitations': [], 'summary': '已人工认可'}
    own = sample('old', [('请你回答。', 1)], project='different-project', role='历史名字')
    store(own, status='complete', analysis=analysis)
    for index in range(51):
        store(sample(f'other-{index}', [('请你回答。', 1)], identity='another'),
              status='complete', analysis=analysis)
    with learning.database() as db:
        db.execute("UPDATE samples SET updated=0 WHERE id='old'")
    selected = learning.retrieve(manifest(role='本项目名字'))
    assert selected[0]['sample_id'] == 'old'
    assert selected[0]['person_match'] is True
    assert 'shot_descriptions' not in selected[0]


def test_planners_receive_same_statistics_and_cache_changes_when_ok_changes(library, monkeypatch):
    from generation.pipelines.minimax_h3 import prompt_planner, prompt_retimer
    record = sample('one', [('请你回答。', 1)])
    store(record)
    assets = manifest()
    captured = []
    def plan(system, user, **kwargs):
        captured.append(json.loads(user)['speech_timing_context'])
        return {'core_idea': '无言语动作', 'shot_mode': 'one_take',
                'shots': [{'start_sec': 0, 'end_sec': 4, 'action': '人物停下。', 'audio': '\u672c\u955c\u65e0\u5bf9\u767d\u3002'}],
                'duration_sec': 4, 'duration_reason': '按必要动作完成估时'}, 0
    monkeypatch.setattr(prompt_planner, '_call_model', plan)
    beat = {'estimated_duration_sec': 4, 'plot': '人物停下。'}
    first = prompt_planner.plan_h3_prompt(beat, 1, asset_manifest=assets, auto_duration=True)
    prompt_planner.plan_h3_prompt(beat, 1, asset_manifest=assets, auto_duration=True, cached_plan=first)
    assert len(captured) == 1
    def retime(system, user, **kwargs):
        payload = json.loads(user)
        captured.append(payload['speech_timing_context'])
        return {'duration_sec': 4, 'duration_reason': '按台词完成',
                'timings': {s['id']: s['seconds'] for s in payload['time_slots']}}, 0
    monkeypatch.setattr(prompt_retimer, '_call_model', retime)
    prompt_retimer.retime_h3_prompt('【任务概述】\n4 秒\n【详细镜头描述】\n[Shot 1] (0-4 秒)：人物停下。',
                                   1, reference_duration_sec=4, asset_manifest=assets)
    assert captured[0] == captured[1]
    store(sample('two', [('请你回答。', 2)]))
    second = prompt_planner.plan_h3_prompt(beat, 1, asset_manifest=assets, auto_duration=True, cached_plan=first)
    assert second['planning_fingerprint'] != first['planning_fingerprint']
    assert len(captured) == 3
