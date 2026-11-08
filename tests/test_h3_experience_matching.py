import hashlib
import json
import sqlite3

import pytest

from services import video_experience as learning
from services.h3_experience_matching import speech_layout


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = tmp_path / 'ok.sqlite3'
    monkeypatch.setattr(learning, 'library_path', lambda: path)
    return path


def assets(tmp_path, voices=('a-new', 'b')):
    manifest = []
    for index, (name, voice) in enumerate(zip(('A', 'B'), voices), 1):
        manifest.append({'type': 'image', 'role': name, 'order': index,
                         'metadata': {'identity_id': f'person-{name}', 'role_id': f'role-{name}'}})
        path = tmp_path / f'{voice}.wav'
        path.write_bytes(voice.encode())
        manifest.append({'type': 'audio', 'path': str(path), 'order': index + 2,
                         'metadata': {'identity_id': f'person-{name}', 'role_id': f'role-{name}'}})
    return manifest


def prompt(speaker=1, silent=False):
    first = '[Shot 1] (0-1 秒)：画面人物：<Subject 1>、<Subject 2>；声音：无对白，仅有室内底噪。\n' if silent else ''
    number = 2 if silent else 1
    return ('【主体定义】\n<Subject 1>：A；可见内容来自 <Picture 1>。\n'
            '<Subject 2>：B；可见内容来自 <Picture 2>。\n【详细镜头描述】\n' + first
            + f'[Shot {number}] (1-8 秒)：画面人物：<Subject {speaker}>；'
              f'<Subject {speaker}>说：<d>[Chinese]这段历史台词不能复制。</d>\n'
              '【整体声音环境】\n室内底噪。')


def put(identifier, manifest, text, *, updated=1, status='complete', active=1, malformed=False):
    voices = {item['metadata']['identity_id']: learning.file_hash(item['path'])
              for item in manifest if item['type'] == 'audio'}
    sample = {'sample_id': identifier, 'model_id': learning.MODEL_ID, 'project': '__pytest__',
              'source': 'historical', 'episode': '001', 'segment_index': 1, 'render_id': f'render-{identifier}',
              'generated_prompt': text, 'asset_manifest': manifest, 'actual_duration_sec': 8.125,
              'speaker_voice_fingerprints': voices, 'voice_fingerprints': list(voices.values()),
              'human_review': {'source': 'video_ok', 'reviewed_by': 'human'}}
    analysis = {'analysis_basis': 'user_approved_prompt', 'summary': '历史软经验。',
                'timing_analysis': ['计划时间，不是实测发声。'], 'transition_analysis': [],
                'speech_control_analysis': [], 'reusable_lessons': [], 'limitations': ['没有逐镜核验。']}
    with learning.database() as db:
        db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (identifier, '__pytest__', 'historical', identifier, active, '', '', status,
                    '{bad' if malformed else json.dumps(sample),
                    json.dumps(analysis if status == 'complete' else {'error': 'analysis failed'}), updated))


def test_current_speaking_voice_wins_over_shared_listener_voice(library, tmp_path):
    current = assets(tmp_path)
    changed = assets(tmp_path, ('a-old', 'b'))
    put('listener', changed, prompt(speaker=2), updated=20)
    put('speaker', current, prompt(speaker=1), updated=1)
    result = learning.retrieve(current, prompt=prompt(speaker=1))
    assert result[0]['sample_id'] == 'speaker'
    assert result[0]['relevance']['speaking_voice_match']
    assert 'listener' not in {item['sample_id'] for item in result}


def test_replaced_voice_is_not_reported_as_same_speaking_voice(library, tmp_path):
    current = assets(tmp_path)
    put('old', assets(tmp_path, ('a-old', 'b')), prompt())
    item = learning.retrieve(current, prompt=prompt().replace('<Subject 1>', '< Subject 1 >'))[0]
    assert item['relevance']['speaker_match']
    assert not item['relevance']['speaking_voice_match']
    assert item['voice_match']


def test_transition_complements_same_voice_immediate_speech_examples(library, tmp_path):
    current = assets(tmp_path)
    for index in range(4):
        put(f'direct-{index}', current, prompt(), updated=10 + index)
    put('transition', assets(tmp_path, ('a-old', 'b-old')), prompt(silent=True))
    put('without-reference', [], prompt(silent=True), updated=30)
    result = learning.retrieve(current, prompt=prompt(silent=True))
    assert len(result) == 3
    transition = next(item for item in result if item['sample_id'] == 'transition')
    assert transition['relevance']['opening_layout_match']
    assert transition['relevance']['audio_reference_presence_match']
    assert not transition['relevance']['speaking_voice_match']
    assert 'without-reference' not in {item['sample_id'] for item in result}


def test_structural_examples_do_not_crowd_out_current_speaker(library, tmp_path):
    current = assets(tmp_path)
    for index in range(4):
        put(f'other-speaker-{index}', current, prompt(speaker=2, silent=True), updated=10 + index)
    put('same-speaker', assets(tmp_path, ('a-old', 'b')), prompt())
    result = learning.retrieve(current, prompt=prompt(silent=True))
    assert len(result) == 3
    assert any(item['relevance']['opening_layout_match'] for item in result)
    same = next(item for item in result if item['sample_id'] == 'same-speaker')
    assert same['relevance']['speaker_match']
    assert not same['relevance']['speaking_voice_match']


def test_approved_raw_snapshot_survives_analysis_failure_and_bad_rows(library, tmp_path):
    current = assets(tmp_path)
    put('raw', current, prompt(silent=True), status='error')
    put('cancelled', current, prompt(), active=0)
    put('bad-json', current, prompt(), malformed=True)
    result = learning.retrieve(current, prompt=prompt(silent=True))
    assert [item['sample_id'] for item in result] == ['raw']
    assert result[0]['analysis_basis'] == 'human_approved_prompt_only'
    assert '[Shot 1]' in result[0]['original_prompt_evidence']['shot_descriptions']
    assert result[0]['source']['render_id'] == 'render-raw'


def test_retrieval_does_not_migrate_or_write_a_legacy_database(library, tmp_path):
    current = assets(tmp_path)
    put('old-schema', current, prompt())
    before = hashlib.sha256(library.read_bytes()).hexdigest()
    result = learning.retrieve(current, dialogue=[{'speaker': 'role-A', 'line': '当前原文。'}])
    assert result[0]['relevance']['speaker_match']
    assert hashlib.sha256(library.read_bytes()).hexdigest() == before
    with sqlite3.connect(library) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='speech_identity_aliases'").fetchone() is None


def test_layout_records_text_only_and_does_not_mislabel_explicit_continuation():
    text = ('【详细镜头描述】\n[Shot 1] 听者特写，镜外继续说上一句。\n'
            '[Shot 2] <Subject 1>说：<d>[Chinese]下一句。</d>')
    layout = speech_layout(text)
    assert layout['shot_states'] == ['continuing_dialogue', 'dialogue']
    assert not layout['starts_without_dialogue_text']
    assert layout['basis'] == 'prompt_declarations_not_media_measurement'


def test_unrelated_context_and_zero_limit_produce_no_examples(library, tmp_path):
    current = assets(tmp_path)
    put('other', current, prompt())
    with learning.database() as db:
        row = db.execute("SELECT sample FROM samples WHERE id='other'").fetchone()
        sample = json.loads(row['sample'])
        sample['project'] = 'another project'
        db.execute("UPDATE samples SET sample=? WHERE id='other'", (json.dumps(sample),))
    assert learning.retrieve([], prompt='【详细镜头描述】\n[Shot 1] <d>不相关当前稿。</d>') == []
    assert learning.retrieve(current, limit=0, prompt=prompt()) == []


@pytest.mark.parametrize('field,value', [
    ('human_review', 'broken'), ('asset_manifest', ['broken']),
    ('speaker_voice_fingerprints', ['broken']), ('voice_fingerprints', 'broken'),
    ('generated_prompt', ''), ('generated_prompt', '没有历史镜头原文'),
])
def test_one_malformed_snapshot_does_not_hide_valid_examples(library, tmp_path, field, value):
    current = assets(tmp_path)
    put('good', current, prompt())
    put('broken', current, prompt(), updated=10)
    with learning.database() as db:
        sample = json.loads(db.execute("SELECT sample FROM samples WHERE id='broken'").fetchone()['sample'])
        sample[field] = value
        db.execute("UPDATE samples SET sample=? WHERE id='broken'", (json.dumps(sample),))
    assert [item['sample_id'] for item in learning.retrieve(current, prompt=prompt())] == ['good']
