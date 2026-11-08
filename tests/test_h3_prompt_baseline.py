import copy

import pytest

from generation.pipelines.minimax_h3 import prompt_baseline
from generation.pipelines.minimax_h3.shot_editor import parse_prompt, dialogue_units


def draft(line='原台词', camera='全景', speaker=1):
    return {'segment_uid': 's', 'shot_uids': ['shot-1'], 'duration_sec': 4,
            'asset_manifest': [
                {'asset_id': name, 'path': name + '.png', 'type': 'image', 'role': name,
                 'metadata': {'asset_kind': 'character', 'role_id': name}}
                for name in ('甲', '乙')],
            'prompt': '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>、<Subject 2>；'
                      f'镜头构图：{camera}；人物调度：桌前站着；<Subject {speaker}>说：<d>[Chinese]{line}</d>'}


def setup_restore(monkeypatch, baseline, current):
    segment = {'segment_uid': 's', 'h3_prompt_baseline': baseline}
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: {'segments': [segment]})
    return {'segment_uid': 's', 'base_revision': 'revision', 'draft': current}


def test_restore_visuals_keeps_latest_speaker_line_and_timing_without_writing(monkeypatch):
    baseline, current = draft(), draft('最新台词', '近景，坐在墙边', 2)
    current['prompt'] = current['prompt'].replace('0–4', '0–5')
    current['duration_sec'] = 5
    current.update(scene_patch={'old': True}, composition_patch={'old': True})
    payload = setup_restore(monkeypatch, baseline, current)
    before = copy.deepcopy(payload)
    result = prompt_baseline.restore(payload)
    text = result['draft']['prompt']
    assert '全景' in text and '近景' not in text and '桌前站着' in text
    assert '最新台词' in text and '原台词' not in text
    doc = parse_prompt(text, 's', result['draft']['asset_manifest'])
    assert dialogue_units(doc['shots'][0])[0]['speaker'] == '乙'
    assert doc['shots'][0]['duration_cs'] == 500
    assert 'scene_patch' not in result['draft'] and 'composition_patch' not in result['draft']
    assert payload == before


def test_deleted_dialogue_is_not_restored(monkeypatch):
    baseline, current = draft(), draft()
    current['prompt'] = current['prompt'].split('<Subject 1>说：')[0] + '本镜无台词'
    result = prompt_baseline.restore(setup_restore(monkeypatch, baseline, current))
    assert '<d>' not in result['draft']['prompt']


def test_added_dialogue_and_latest_offscreen_delivery_survive(monkeypatch):
    baseline, current = draft(), draft('新台词')
    current['prompt'] = current['prompt'].replace('<Subject 1>说', '<Subject 1>在画外说')
    current['prompt'] += '；<Subject 2>说：<d>[Chinese]新增回答</d>'
    result = prompt_baseline.restore(setup_restore(monkeypatch, baseline, current))
    doc = parse_prompt(result['draft']['prompt'], 's', result['draft']['asset_manifest'])
    units = dialogue_units(doc['shots'][0])
    assert [u['line'] for u in units] == ['新台词', '新增回答']
    assert units[0]['scope'] == 'offscreen'


def test_reordered_references_keep_visual_and_dialogue_identity(monkeypatch):
    baseline, current = draft(), draft('新台词', '近景', 2)
    current['asset_manifest'].reverse()
    # Subject 2 now refers to 甲. Restoring the baseline must remap its tags.
    result = prompt_baseline.restore(setup_restore(monkeypatch, baseline, current))
    assert '画面人物：<Subject 2>、<Subject 1>' in result['draft']['prompt']
    doc = parse_prompt(result['draft']['prompt'], 's', result['draft']['asset_manifest'])
    assert dialogue_units(doc['shots'][0])[0]['speaker'] == '甲'


@pytest.mark.parametrize('problem', ['missing', 'reordered', 'count'])
def test_missing_baseline_and_changed_structure_leave_current_untouched(monkeypatch, problem):
    baseline, current = draft(), draft('最新台词')
    if problem == 'missing': baseline = None
    if problem == 'reordered': current['shot_uids'] = ['different-shot']
    if problem == 'count':
        current['prompt'] += '\n[Shot 2]（4–5 秒）：本镜无台词'
    payload = setup_restore(monkeypatch, baseline, current)
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match='暂无底稿|Shot 结构已变化'):
        prompt_baseline.restore(payload)
    assert payload == before


def test_baseline_retained_once_only_when_optimized_candidate_is_saved():
    segment = {}
    prompt_baseline.retain_baseline(segment, {'prompt_baseline': draft()})
    assert not segment
    prompt_baseline.retain_baseline(segment, {'scene_patch': {'record': {}}, 'prompt_baseline': draft()})
    prompt_baseline.retain_baseline(segment, {'scene_patch': {'record': {}}, 'prompt_baseline': draft('修改后')})
    assert segment['h3_prompt_baseline']['prompt'] == draft()['prompt']


def test_restored_draft_commits_latest_speech_to_beat_and_preserves_baseline(tmp_path, monkeypatch):
    import json
    from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint, commit
    from generation.job_schema import normalize_video_jobs_payload
    from services import context
    from workflow import video_merge
    baseline, current = draft(), draft('最新台词', '近景')
    segment = {'segment_uid': 's', 'segment_index': 1, 'beat_uid': 'b', 'title': 'test',
               'generation_config': {'duration_sec': 4, 'prompt_override': current['prompt'],
                                     'asset_manifest': current['asset_manifest']},
               'job': {'duration_sec': 4}, 'h3_prompt_baseline': baseline,
               'h3_shot_editor': {'shot_uids': ['shot-1']}}
    jobs = {'segments': [segment]}
    for name, data in [('video_jobs.json', jobs), ('beats.json', {'beats': [{'beat_uid': 'b'}]})]:
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(context, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(context, 'assert_current_runtime_writable', lambda *a: None)
    monkeypatch.setattr(video_merge, 'load_video_jobs', lambda: copy.deepcopy(jobs))
    original = snapshot(segment)
    restored = prompt_baseline.restore({'segment_uid': 's', 'base_revision': fingerprint(original), 'draft': original})
    commit({'segment_uid': 's', **restored})
    saved = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
    beats = json.loads((tmp_path / 'beats.json').read_text(encoding='utf-8'))
    row = normalize_video_jobs_payload(saved)['segments'][0]
    assert row['h3_prompt_baseline'] == baseline
    assert '全景' in row['generation_config']['prompt_override']
    assert beats['beats'][0]['dialogue_units'][0]['line'] == '最新台词'
    assert row['job']['final_prompt'] == beats['beats'][0]['plot']


@pytest.mark.parametrize('edit_during_request', [False, True])
def test_restore_button_uses_latest_editor_and_preserves_edits_during_request(tmp_path, edit_during_request):
    import shutil
    import subprocess
    from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    action = EDITOR_DRAFT_JS.split(' A.restoreH3PromptBaseline=', 1)[1].split(' A.editH3Reference=', 1)[0]
    apply = EDITOR_DRAFT_JS.split(' function apply(ov,throwOnFailure=false){', 1)[1].split(' A.restoreH3PromptBaseline=', 1)[0]
    # Exercise the real install guard and undo capture with an asynchronous response.
    program = r'''
const assert=require('node:assert/strict');
const A={},clone=v=>JSON.parse(JSON.stringify(v));
let text='最新台词',installed=0,resolveRequest,payload,flushed=0;
const ov={dataset:{h3Session:'session'},_editorUid:'s',_editorSaved:{base_revision:'r'},
 _shotEditor:{sourceUid:'s',documents:[{uid:'s'}],flushPreview:async()=>{flushed++;}},querySelector:()=>({}),_editorRuntime:'runtime'};
const prepare=async()=>{},setBusy=()=>{},capture=()=>({prompt:text}),signature=JSON.stringify;
const note=()=>{},install=(ov,draft)=>{text=draft.prompt;installed++;},recordApplication=()=>{};
const operationMessage=()=>'',localStorage={setItem:()=>{}};
const api=async(ov,action,body)=>{assert.equal(action,'restore-baseline');payload=body;
 return await new Promise(resolve=>resolveRequest=resolve);};
function apply(ov,throwOnFailure=false){
''' + apply + '\nA.restoreH3PromptBaseline=' + action + r'''
(async()=>{
 const pending=A.restoreH3PromptBaseline(ov);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(payload.draft.prompt,'最新台词');assert.equal(flushed,1);
 if(EDIT)text='请求期间的新修改';
 resolveRequest({draft:{prompt:'恢复镜头＋最新台词'},document:{},changes:[]});await pending;
 assert.equal(installed,EDIT?0:1);
 assert.equal(text,EDIT?'请求期间的新修改':'恢复镜头＋最新台词');
 if(!EDIT)assert.equal(ov._editorUndo.draft.prompt,'最新台词');
 assert.equal(ov._operationPending,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'restore.cjs'
    path.write_text('const EDIT=' + str(edit_during_request).lower() + ';\n' + program, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
