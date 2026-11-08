import copy
import hashlib
import json
import shutil
import uuid

import pytest

from services import context, text_jobs


def _project_documents():
    return {
        'beats.json': {
            'beats': [
                {'beat_uid':'beat-1','plot':'first','model_hints':{'minimax':{'shots':[{'action':'old 1'}]}}},
                {'beat_uid':'beat-2','plot':'second','model_hints':{'minimax':{'shots':[{'action':'old 2'}]}}},
            ]
        },
        'video_jobs.json': {
            'segments': [
                {'segment_uid':'segment-1','segment_index':1,'job':{'prompt':'old 1'},'renders':[]},
                {'segment_uid':'segment-2','segment_index':2,'job':{'prompt':'old 2'},'renders':[]},
            ]
        },
    }


def _seed_apply_task(tmp_path, monkeypatch, *, current_segment=1, candidate_segment=2, kind='segment'):
    source = tmp_path / 'project'
    storage = tmp_path / 'operations'
    work = storage / str(uuid.uuid4()) / 'work'
    baseline_dir = work / text_jobs.MERGE_BASELINE_DIR
    source.mkdir()
    baseline_dir.mkdir(parents=True)
    base = _project_documents()
    candidate = copy.deepcopy(base)
    current = copy.deepcopy(base)

    def update_segment(documents, segment, prompt):
        if kind == 'revision_wait':
            row = documents['video_jobs.json']['segments'][segment - 1]
            row.setdefault('generation_config', {})['prompt_override'] = prompt
        else:
            documents['beats.json']['beats'][segment - 1]['model_hints']['minimax']['shots'][0]['action'] = prompt
            documents['video_jobs.json']['segments'][segment - 1]['job']['prompt'] = prompt

    update_segment(candidate, candidate_segment, 'candidate prompt')
    update_segment(current, current_segment, 'already applied prompt')
    for name, document in base.items():
        (source / name).write_text(json.dumps(document), encoding='utf-8')
        (baseline_dir / name).write_text(json.dumps(document), encoding='utf-8')
    for name, document in candidate.items():
        (work / name).write_text(json.dumps(document), encoding='utf-8')
    for name, document in current.items():
        (source / name).write_text(json.dumps(document), encoding='utf-8')
    unrelated_path = source / 'unrelated.json'
    unrelated_path.write_text('{"note":"at submission"}', encoding='utf-8')

    monkeypatch.setattr(text_jobs, 'root', lambda: storage)
    monkeypatch.setattr(context, 'get_project_dir', lambda: source)
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *_: None)
    identifier = work.parent.name
    item = {
        'id':identifier,
        'kind':kind,
        'state':'ready',
        'segment':{'segment_index':candidate_segment,
                   'segment_uid':f'segment-{candidate_segment}',
                   'beat_uid':f'beat-{candidate_segment}'},
        'source':str(source.resolve()),
        'work':str(work),
        'editors':{},
        'baseline':{
            **{name:hashlib.sha256((baseline_dir / name).read_bytes()).hexdigest() for name in base},
            'unrelated.json':text_jobs.digest(unrelated_path),
        },
        'merge_baseline_files':{name:f'{text_jobs.MERGE_BASELINE_DIR}/{name}' for name in base},
        'meta_baseline':{},
        'changes':{'runtime':{name:'write' for name in base},'meta':{}},
    }
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (identifier,'ready',1,1,json.dumps(item)))
    return identifier, source


@pytest.mark.parametrize('kind', ['segment', 'revision_wait'])
def test_apply_merges_independent_segment_changes(tmp_path, monkeypatch, kind):
    identifier, source = _seed_apply_task(tmp_path, monkeypatch, kind=kind)
    (source / 'unrelated.json').write_text('{"note":"changed after submit"}', encoding='utf-8')

    result = text_jobs.apply(identifier, {})

    assert result['state'] == 'applied'
    beats = json.loads((source / 'beats.json').read_text(encoding='utf-8'))['beats']
    segments = json.loads((source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']
    if kind == 'revision_wait':
        assert beats == _project_documents()['beats.json']['beats']
        assert segments[0]['generation_config']['prompt_override'] == 'already applied prompt'
        assert segments[1]['generation_config']['prompt_override'] == 'candidate prompt'
    else:
        assert beats[0]['model_hints']['minimax']['shots'][0]['action'] == 'already applied prompt'
        assert beats[1]['model_hints']['minimax']['shots'][0]['action'] == 'candidate prompt'
        assert segments[0]['job']['prompt'] == 'already applied prompt'
        assert segments[1]['job']['prompt'] == 'candidate prompt'


def test_invalid_revision_cannot_bypass_review_via_apply_api(tmp_path, monkeypatch):
    identifier, source = _seed_apply_task(tmp_path, monkeypatch, kind='revision_wait')
    before = {name: (source / name).read_bytes() for name in ('beats.json', 'video_jobs.json')}
    text_jobs.update(identifier, 'ready', result={
        'revision_validation': {'blocking_issues': ['Shot 1 台词状态未确认']}})
    with pytest.raises(ValueError, match='结构检查未通过'):
        text_jobs.apply(identifier, {})
    assert all((source / name).read_bytes() == value for name, value in before.items())


@pytest.mark.parametrize('kind', ['segment', 'revision_wait'])
def test_apply_keeps_conflict_on_same_segment_field(tmp_path, monkeypatch, kind):
    identifier, source = _seed_apply_task(
        tmp_path, monkeypatch, current_segment=2, candidate_segment=2, kind=kind)

    with pytest.raises(ValueError, match='对应内容已变化'):
        text_jobs.apply(identifier, {})

    segments = json.loads((source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']
    if kind == 'revision_wait':
        assert segments[1]['generation_config']['prompt_override'] == 'already applied prompt'
    else:
        assert segments[1]['job']['prompt'] == 'already applied prompt'


def test_old_ready_task_recovers_baseline_from_matching_apply_backup(tmp_path, monkeypatch):
    identifier, source = _seed_apply_task(tmp_path, monkeypatch)
    storage = text_jobs.root()
    work = storage / identifier / 'work'
    old_apply_backup = storage / 'previous-apply' / 'before_apply'
    old_apply_backup.mkdir(parents=True)
    for name in ('beats.json','video_jobs.json'):
        (old_apply_backup / f'000_{name}').write_bytes(
            (work / text_jobs.MERGE_BASELINE_DIR / name).read_bytes())
    with text_jobs.database() as db:
        row = db.execute('SELECT payload FROM operations WHERE id=?',(identifier,)).fetchone()
        item = json.loads(row['payload'])
        item.pop('merge_baseline_files')
        db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(item),identifier))

    result = text_jobs.apply(identifier,{})

    assert result['state'] == 'applied'
    segments = json.loads((source / 'video_jobs.json').read_text(encoding='utf-8'))['segments']
    assert segments[0]['job']['prompt'] == 'already applied prompt'
    assert segments[1]['job']['prompt'] == 'candidate prompt'


def test_expiring_applied_task_preserves_baseline_for_old_ready_task(tmp_path, monkeypatch):
    pending_id, _source = _seed_apply_task(tmp_path, monkeypatch)
    storage = text_jobs.root()
    pending_work = storage / pending_id / 'work'
    shutil.rmtree(pending_work / text_jobs.MERGE_BASELINE_DIR)
    with text_jobs.database() as db:
        row = db.execute('SELECT payload FROM operations WHERE id=?',(pending_id,)).fetchone()
        pending = json.loads(row['payload'])
        pending.pop('merge_baseline_files')
        db.execute('UPDATE operations SET payload=? WHERE id=?',(json.dumps(pending),pending_id))

    applied_id = str(uuid.uuid4())
    applied_work = storage / applied_id / 'work'
    backup = applied_work.parent / 'before_apply'
    backup.mkdir(parents=True)
    original = _project_documents()
    for name in ('beats.json','video_jobs.json'):
        (backup / f'000_{name}').write_text(json.dumps(original[name]),encoding='utf-8')
    applied = {'id':applied_id,'work':str(applied_work),'source':pending['source']}
    with text_jobs.database() as db:
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (applied_id,'applied',1,1,json.dumps(applied)))

    assert text_jobs._delete_expired_applied(now=text_jobs.APPLIED_RETENTION_SECONDS + 101) == 1

    pending_after = text_jobs.get(pending_id)
    for name in ('beats.json','video_jobs.json'):
        saved_path = pending_work / pending_after['merge_baseline_files'][name]
        assert saved_path.is_file()
        assert text_jobs.digest(saved_path) == pending_after['baseline'][name]
    assert not (storage / applied_id).exists()


def test_json_three_way_merge_reports_overlapping_leaf():
    base = {'beats':[{'beat_uid':'b1','plot':'old'}]}
    candidate = {'beats':[{'beat_uid':'b1','plot':'candidate'}]}
    current = {'beats':[{'beat_uid':'b1','plot':'newer'}]}
    conflicts = []

    merged = text_jobs._merge_json_values(base, candidate, current, ('beats.json',), conflicts)

    assert merged == current
    assert conflicts == ['beats.json.beats[uid:b1].plot']


def _seed_duration_task(tmp_path, monkeypatch, *, candidate_segment=2, same_target=False, durations=(8, 12)):
    identifier, source = _seed_apply_task(
        tmp_path, monkeypatch, current_segment=candidate_segment if same_target else 3 - candidate_segment,
        candidate_segment=candidate_segment, kind='source_replan')
    item = text_jobs.get(identifier)
    work = source.parent / 'operations' / identifier / 'work'
    for name, collection in [('beats.json', 'beats'), ('video_jobs.json', 'segments')]:
        paths = [work / text_jobs.MERGE_BASELINE_DIR / name, source / name, work / name]
        for path, changed, duration in zip(paths, [None, candidate_segment if same_target else 3 - candidate_segment,
                                                  candidate_segment], [None, durations[0], durations[1]]):
            doc = json.loads(path.read_text(encoding='utf-8'))
            for index, row in enumerate(doc[collection], 1):
                value = duration if index == changed else [4, 6][index - 1]
                if collection == 'beats':
                    row['estimated_duration_sec'] = value
                else:
                    row['job']['duration_sec'] = value
            doc['total_duration_sec'] = sum(
                row['estimated_duration_sec'] if collection == 'beats' else row['job']['duration_sec']
                for row in doc[collection])
            path.write_text(json.dumps(doc), encoding='utf-8')
        item['baseline'][name] = text_jobs.digest(paths[0])
    text_jobs.update(identifier, baseline=item['baseline'])
    return identifier, source


@pytest.mark.parametrize('candidate_segment', [1, 2])
@pytest.mark.parametrize('durations', [(8, 12), (8, 10)])
def test_concurrent_duration_apply_recomputes_totals_and_preserves_render_history(
        tmp_path, monkeypatch, candidate_segment, durations):
    identifier, source = _seed_duration_task(
        tmp_path, monkeypatch, candidate_segment=candidate_segment, durations=durations)
    jobs_path = source / 'video_jobs.json'
    jobs = json.loads(jobs_path.read_text(encoding='utf-8'))
    jobs['segments'][0]['renders'].append({'render_id': 'completed-in-parallel', 'status': 'success'})
    jobs_path.write_text(json.dumps(jobs), encoding='utf-8')

    assert text_jobs.apply(identifier, {})['state'] == 'applied'

    beats = json.loads((source / 'beats.json').read_text(encoding='utf-8'))
    jobs = json.loads(jobs_path.read_text(encoding='utf-8'))
    expected = list(durations) if candidate_segment == 2 else list(reversed(durations))
    assert [row['estimated_duration_sec'] for row in beats['beats']] == expected
    assert [row['job']['duration_sec'] for row in jobs['segments']] == expected
    assert beats['total_duration_sec'] == jobs['total_duration_sec'] == sum(expected)
    assert jobs['segments'][0]['renders'] == [{'render_id': 'completed-in-parallel', 'status': 'success'}]


def test_concurrent_duration_apply_still_rejects_same_segment_changes(tmp_path, monkeypatch):
    identifier, source = _seed_duration_task(tmp_path, monkeypatch, same_target=True)
    before = {name: (source / name).read_bytes() for name in text_jobs.SEGMENT_MERGE_FILES}
    with pytest.raises(ValueError, match='estimated_duration_sec'):
        text_jobs.apply(identifier, {})
    assert all((source / name).read_bytes() == value for name, value in before.items())


def test_segment_apply_ignores_only_other_segment_prompt_updates_in_editor():
    before = {'beats':[
        {'beat_uid':'b1','plot':'first','model_hints':{'minimax':{'shots':['old']}}},
        {'beat_uid':'b2','plot':'second','model_hints':{'minimax':{'shots':['old']}}},
    ]}
    after = copy.deepcopy(before)
    after['beats'][0]['model_hints']['minimax']['shots'] = ['regenerated']

    assert text_jobs._beats_editor_only_prompt_state_changed(
        json.dumps(before), json.dumps(after))

    after['beats'][0]['plot'] = 'edited plot'
    assert not text_jobs._beats_editor_only_prompt_state_changed(
        json.dumps(before), json.dumps(after))
