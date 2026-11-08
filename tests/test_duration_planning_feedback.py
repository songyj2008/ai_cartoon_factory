import json
import shutil
import subprocess

import pytest

from ui.scripts import APP_JS
from ui.web_llm_ui import WEB_LLM_JS


def run_js(tmp_path, source):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    path = tmp_path / 'feedback.cjs'
    path.write_text(source, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshots_do_not_restore_old_planning_feedback(tmp_path, monkeypatch):
    from services import web_llm_bridge as bridge
    roots = [tmp_path / 'project-a', tmp_path / 'project-b']
    for root in roots:
        root.mkdir()
        segments = [{'segment_index': i, 'segment_uid': 's'+str(i), 'job': {'duration_sec': 9},
                     'h3_duration_adjustment': {'reference_sec': 11, 'duration_sec': 9,
                                                'reason': root.name + '-part-' + str(i)}} for i in (1, 2)]
        (root / 'video_jobs.json').write_text(json.dumps({'segments': segments}), encoding='utf-8')
    monkeypatch.setattr(bridge, 'get_project_dir', lambda: roots[0])
    first, second = bridge.segment_snapshot(1), bridge.segment_snapshot(2)
    assert 'duration_planning' not in first
    assert 'duration_planning' not in second
    monkeypatch.setattr(bridge, 'get_project_dir', lambda: roots[1])
    other = bridge.segment_snapshot(1)
    assert 'duration_planning' not in other
    assert other['runtime'] != first['runtime']
    # A display-only reason update must not invalidate an unrelated content edit.
    path = roots[1] / 'video_jobs.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['segments'][0]['h3_duration_adjustment']['reason'] = '补充说明'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert bridge.segment_snapshot(1)['fingerprint'] == other['fingerprint']


def test_planning_feedback_is_temporary_and_never_restored_from_snapshot(tmp_path):
    render = WEB_LLM_JS.split('  A.renderH3DurationPlanning=function(ov,snapshot,record){', 1)[1].split('  let config', 1)[0]
    run_js(tmp_path, r'''
const assert=require('node:assert/strict'),A={};let panel;
const anchor={after:value=>panel=value},ov={querySelector:s=>s==='.h3-duration-planning'?panel:anchor};
const document={createElement:()=>({textContent:'',hidden:false,setAttribute(){}})};
let expire;global.setTimeout=(callback,ms)=>{assert.equal(ms,15000);expire=callback;return 1;};global.clearTimeout=()=>{};
A.renderH3DurationPlanning=function(ov,snapshot,record){
''' + render + r'''
const snapshot=(project,part,reason)=>({project_name:project,episode_name:'第001集',segment_index:part,duration_sec:9,
 duration_planning:{reference_sec:11,duration_sec:9,reason}});
const first=snapshot('甲项目',1,'第一段理由');
A.renderH3DurationPlanning(ov,first,first.duration_planning);
assert.ok(panel.textContent.includes('甲项目 / 第001集 / part_001'));
assert.ok(panel.textContent.includes('11 → 9 秒'));
A.renderH3DurationPlanning(ov,null);assert.equal(panel.hidden,true);assert.equal(panel.textContent,'');
const second=snapshot('乙项目',2,'第二段理由');
A.renderH3DurationPlanning(ov,second,second.duration_planning);
assert.ok(panel.textContent.includes('乙项目 / 第001集 / part_002'));
assert.ok(!panel.textContent.includes('第一段理由'));
A.renderH3DurationPlanning(ov,{segment_index:3});assert.equal(panel.hidden,true);
A.renderH3DurationPlanning(ov,first);assert.equal(panel.hidden,true);assert.equal(panel.textContent,'');
A.renderH3DurationPlanning(ov,first,first.duration_planning);
assert.ok(panel.textContent.includes('第一段理由'));expire();
assert.equal(panel.hidden,true);assert.equal(panel.textContent,'');
''')


def test_late_submission_does_not_attach_feedback_or_pending_task_to_another_part(tmp_path):
    save = APP_JS.split('window.AICF.savePromptSourceExcerpt = async function(replan, submittedOptions)', 1)[1].split('window.AICF.replanPromptModal', 1)[0]
    run_js(tmp_path, r'''
const assert=require('node:assert/strict');
let resolveRequest,request,feedback=0;
const field={value:'当前稿',dataset:{}},ov={dataset:{segmentIndex:'1',h3Session:'one'},_editorRuntime:'F:/项目A/第001集',querySelector:()=>field};
const window={AICF:{promptRuntimeRevision:1,setPromptModalFeedback:()=>feedback++}};
const document={getElementById:()=>ov,querySelector:()=>null},_h3Manifest=()=>[];
const fetch=async(url,options)=>{request=options;await new Promise(resolve=>resolveRequest=resolve);
return {ok:true,json:async()=>({ok:true,operation_id:'old-project-part-1'})};};
window.AICF.savePromptSourceExcerpt=async function(replan, submittedOptions)
''' + save + r'''
(async()=>{
const pending=window.AICF.savePromptSourceExcerpt(true,{duration_sec:11,providers:['chatgpt']});
assert.equal(decodeURIComponent(request.headers['X-AICF-Runtime']),'F:/项目A/第001集');
ov.dataset.segmentIndex='2';ov.dataset.h3Session='two';ov._editorRuntime='F:/项目B';window.AICF.promptRuntimeRevision++;
resolveRequest();await pending;
assert.equal(ov._sourceReplanPending,undefined);assert.equal(feedback,0);
})().catch(e=>{console.error(e);process.exitCode=1});
''')
