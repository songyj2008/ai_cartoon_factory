import shutil
import subprocess

import pytest

from ui.scripts import APP_JS
from ui.web_llm_ui import WEB_LLM_JS
from ui.text_jobs_ui import PANEL_JS


HELPERS = 'window.AICF.savePromptBeforeTask = async function(ov) {' + APP_JS.split(
    'window.AICF.savePromptBeforeTask = async function(ov) {', 1)[1].split(
    'window.AICF.savePromptSourceExcerpt =', 1)[0]

FIXTURE = r'''
const assert=require('node:assert/strict');
const trace=[];
const editor={value:'current',readOnly:false,disabled:false,isConnected:true};
const button={disabled:false,isConnected:true,textContent:''};
const excerpt={value:'context',dataset:{savedValue:'context'}};
const shot={dirty:true,stale:false,reading:false,saving:false,lastPrompt:'current',manifestBase:'[]',sourceUid:'s',
  documents:[{uid:'s',shots:[{}]}],refreshControls(){},
  async read(){trace.push('read');this.stale=false;this.dirty=true;this.lastPrompt=editor.value;return true;},
  async saveForTask(){trace.push('save');this.dirty=false;editor.value='canonical';this.lastPrompt='canonical';return true;}};
const ov={dataset:{modelId:'minimax_h3_local_ref2va',h3Session:'1',segmentIndex:'1'},_shotEditor:shot,
  classList:{contains:()=>true},
  querySelector:s=>s==='.aicf-prompt-modal-body'?editor:s==='#aicf-prompt-source-excerpt'?excerpt:button,
  querySelectorAll:s=>s==='.h3-asset-input'?[]:[editor,button]};
global.document={getElementById:()=>ov};
global.window={AICF:{readH3Manifest:()=>[],promptTextMatches:(a,b)=>a===b,
  setPromptModalFeedback:(m)=>trace.push('feedback:'+m)}};
const A=window.AICF;
A.readPageLLMProviders=()=>['chatgpt'];
'''


def run_node(tmp_path, code):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    path = tmp_path / 'save_before_action.cjs'
    path.write_text(FIXTURE + HELPERS + code, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_save_submit_order_failures_noop_and_duplicate_click(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
  await A.runSavedPromptAction(ov,async target=>{
    trace.push('submit');assert.equal(target.segment_uid,'s');assert.equal(target.prompt,'canonical');
    assert.equal(shot.dirty,false);assert.equal(editor.readOnly,true);
  });
  assert.deepEqual(trace,['save','submit']);assert.equal(button.disabled,false);assert.equal(editor.readOnly,false);
  trace.length=0;
  await A.runSavedPromptAction(ov,async()=>trace.push('submit'));
  assert.deepEqual(trace,['submit']); // no changes: no extra saved version
  shot.dirty=true;shot.saveForTask=async()=>{trace.push('save-failed');throw new Error('save failed');};
  await assert.rejects(A.runSavedPromptAction(ov,async()=>assert.fail('must not submit')),
    e=>e.message==='save failed'&&e.saved===false);
  assert.equal(shot.dirty,true);assert.equal(button.disabled,false);
  shot.saveForTask=async()=>{shot.dirty=false;return true;};
  await assert.rejects(A.runSavedPromptAction(ov,async()=>{throw new Error('network failed');}),
    e=>e.message==='network failed'&&e.saved===true);
  assert.equal(shot.dirty,false); // keep successful save when submission fails
  let finish;const pending=A.runSavedPromptAction(ov,()=>new Promise(r=>finish=r));
  await new Promise(r=>setImmediate(r));
  await assert.rejects(A.runSavedPromptAction(ov,async()=>assert.fail('duplicate')),/重复/);
  finish();await pending;assert.equal(ov._promptTaskBusy,false);
  shot.stale=true;shot.dirty=true;shot.documents[0].structure_changed=true;
  await assert.rejects(A.runSavedPromptAction(ov,async()=>assert.fail('conflicting drafts')),/不一致/);
  delete shot.documents[0].structure_changed;shot.stale=false;shot.dirty=false;excerpt.value='';
  await A.runSavedPromptAction(ov,async()=>assert.equal(shot.dirty,false));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_page_provider_reader_and_composite_snapshot_share_one_selection(tmp_path):
    reader = 'window.AICF.readPageLLMProviders = function(ov) {' + APP_JS.split(
        'window.AICF.readPageLLMProviders = function(ov) {', 1)[1].split(
        'window.AICF.readPromptReplanOptions =', 1)[0]
    run_node(tmp_path, reader + r'''
let selected=[{value:'chatgpt',disabled:false},{value:'doubao',disabled:true}];
ov.querySelectorAll=()=>selected;
assert.deepEqual(A.readPageLLMProviders(ov),['chatgpt']);
selected=[{value:'deepseek',disabled:false}];
assert.deepEqual(A.readPageLLMProviders(ov),['deepseek']);
ov._h3CompositeRun={providers:['chatgpt']};
const snapshot=A.readPageLLMProviders(ov);snapshot.push('doubao');
assert.deepEqual(A.readPageLLMProviders(ov),['chatgpt']);
delete ov._h3CompositeRun;selected=[];
assert.throws(()=>A.readPageLLMProviders(ov),/本页面使用的 LLM/);
''')


def test_task_waits_for_autosave_and_cancels_on_session_change(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let finish;
 A.h3AutosavePending=new Promise(resolve=>finish=resolve);
 const saving=A.runSavedPromptAction(ov,async()=>trace.push('submit'));
 await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(trace,[]);
 ov.dataset.h3Session='new-session';finish();
 await assert.rejects(saving,/分段已切换/);
 assert.ok(!trace.includes('save'));assert.ok(!trace.includes('submit'));
 assert.equal(ov._promptTaskBusy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_revision_uses_post_save_snapshot_and_preserves_feedback(tmp_path):
    submit = WEB_LLM_JS.split("        if(action==='submit') {", 1)[1].split(
        "        if(action==='cancel'", 1)[0]
    run_node(tmp_path, r'''
const index=1,valid=()=>true;
let snapshot={runtime:'r',fingerprint:'before'};
const fields={'.review-use-editor':{checked:true},'.review-issue':{value:'口型不对'},
 '.review-expected':{value:'自然说话'},'.review-provider-options':{},'.review-original-text':{value:'historical'}};
const q=s=>fields[s],selectedRoutes=()=>['chatgpt'],referenceState=()=>'refs',showTask=()=>{},message=()=>{};
const api=async(path,payload)=>{
  if(path==='/segments/1')return {snapshot:{runtime:'r',fingerprint:'after',segment_uid:'s',current_prompt:'canonical'}};
  trace.push('submit');assert.equal(shot.dirty,false);assert.equal(payload.fingerprint,'after');
  assert.equal(payload.edited_prompt,'canonical');assert.equal(payload.issue,'口型不对');
  assert.equal(payload.expected,'自然说话');assert.deepEqual(payload.providers,['chatgpt']);
  return {task:{},operation_id:'operation'};
};
async function submitRevision(){if(true){
''' + submit + r'''
}
(async()=>{await submitRevision();assert.deepEqual(trace,['save','submit']);})()
 .catch(e=>{console.error(e);process.exitCode=1});
''')


def test_retime_saves_first_and_keeps_click_time_options(tmp_path):
    replan = 'window.AICF.replanPromptModal = async function() {' + APP_JS.split(
        'window.AICF.replanPromptModal = async function() {', 1)[1].split(
        'window.AICF.closePromptModal =', 1)[0]
    run_node(tmp_path, replan + r'''
A.readPromptReplanOptions=()=>({duration_sec:11,providers:['deepseek']});
A.savePromptSourceExcerpt=async(replan,options)=>{
 assert.equal(replan,true);assert.equal(shot.dirty,false);trace.push('retime');
 assert.equal(options.duration_sec,11);assert.deepEqual(options.providers,['deepseek']);
};
(async()=>{
 await A.replanPromptModal();assert.equal(trace[0],'save');assert.ok(trace.includes('retime'));
 assert.equal(button.textContent,'保存并重新规划时长');assert.equal(button.disabled,false);
 ov._sourceReplanPending={id:'running'};trace.length=0;await A.replanPromptModal();
 assert.equal(trace.filter(x=>x==='save'||x==='retime').length,0);
 ov._syncSourceReplan=async()=>{trace.push('check');delete ov._sourceReplanPending;};
 trace.length=0;await A.replanPromptModal();
 assert.equal(trace[0],'check');assert.ok(trace.includes('retime'));
 ov._sourceReplanPending={id:'network'};
 ov._syncSourceReplan=async()=>{throw new Error('network failed');};
 trace.length=0;await A.replanPromptModal();
 assert.equal(ov._sourceReplanPending.id,'network');assert.ok(!trace.includes('retime'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_stale_compound_draft_reports_cause_and_force_save_recovers(tmp_path):
    save = 'window.AICF.savePromptModal = async function(forceSave, contentOnly) {' + APP_JS.split(
        'window.AICF.savePromptModal = async function(forceSave, contentOnly) {', 1)[1].split(
        'window.AICF.readPromptReplanOptions =', 1)[0]
    run_node(tmp_path, save + r'''
const draft={draft:{prompt:'current'},operation_base:{prompt:'old'}};
ov._editorDraft=draft;shot.dirty=false;
A.saveH3EditorDraft=async(root,propagate)=>{
 assert.equal(propagate,true);throw Error('优化所读取的人物状态或稿件已变化，请重新优化；当前稿已保留');
};
A.savePromptSourceExcerpt=async()=>{};
A.segmentEditorFromButton=()=>null;A.segmentCardFromButton=()=>null;
let fail=true,saves=0,clears=0;
A.saveH3GenerationConfig=async(index,button,root,force)=>{
 assert.equal(force,true);assert.equal(root._editorDraft,draft);saves++;
 if(fail)throw Error('network failed');return {canonical_prompt:'current'};
};
A.clearH3EditorDraft=root=>{clears++;delete root._editorDraft;};
A.closePromptModal=()=>{};
(async()=>{
 // Saving before retiming must show the real cause and never submit the task.
 await assert.rejects(A.runSavedPromptAction(ov,async()=>assert.fail('must not retime')),/人物状态或稿件已变化/);
 await A.savePromptModal();assert.equal(saves,0);
 assert.ok(trace.at(-1).includes('人物状态或稿件已变化'));
 await A.savePromptModal(true);assert.equal(saves,1);assert.equal(clears,0);
 assert.equal(ov._editorDraft,draft);assert.equal(editor.value,'current');
 fail=false;await A.savePromptModal(true);
 assert.equal(saves,2);assert.equal(clears,1);assert.equal(ov._editorDraft,undefined);
 // Saving while another task owns the editor must not start a competing request.
 ov._promptTaskBusy=true;await A.savePromptModal(true);assert.equal(saves,2);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_content_only_recovery_keeps_conflict_guard_and_cleans_only_after_success(tmp_path):
    save = 'window.AICF.savePromptModal = async function(forceSave, contentOnly) {' + APP_JS.split(
        'window.AICF.savePromptModal = async function(forceSave, contentOnly) {', 1)[1].split(
        'window.AICF.readPromptReplanOptions =', 1)[0]
    run_node(tmp_path, save + r'''
const draft={draft:{prompt:'current'},operation_base:{prompt:'old'}};
ov._editorDraft=draft;ov._staleOptimizationDraft=draft;shot.dirty=false;
A.saveH3EditorDraft=async()=>assert.fail('must not resubmit stale scene state');
A.savePromptSourceExcerpt=async()=>{};
A.segmentEditorFromButton=()=>null;A.segmentCardFromButton=()=>null;
let fail=true,saves=0,clears=0;
A.saveH3GenerationConfig=async(index,button,root,force)=>{
 assert.equal(force,false,'Ordinary content recovery must retain the backend conflict guard');
 assert.equal(root._editorDraft,draft);saves++;
 if(fail)throw Error('后台内容已变化');return {canonical_prompt:'current'};
};
A.clearH3EditorDraft=root=>{clears++;delete root._editorDraft;delete root._staleOptimizationDraft;};
A.closePromptModal=()=>{};
(async()=>{
 await A.savePromptModal(false,true);assert.equal(saves,1);assert.equal(clears,0);
 assert.equal(ov._editorDraft,draft);assert.equal(editor.value,'current');
 fail=false;await A.savePromptModal(false,true);
 assert.equal(saves,2);assert.equal(clears,1);assert.equal(ov._editorDraft,undefined);
 assert.ok(trace.some(text=>text.includes('未确认过期')));
 // A stale recovery button cannot target a different candidate.
 ov._editorDraft=draft;await A.savePromptModal(false,true);assert.equal(saves,2);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_clear_all_releases_only_the_cleared_modal_wait(tmp_path):
    clear = 'async function clearAllTasks(button,propagate=false){' + PANEL_JS.split(
        '  async function clearAllTasks(button,propagate=false){', 1)[1].split(
        '  function bindClearButton()', 1)[0]
    run_node(tmp_path, clear + r'''
let videoTasks=[],signature='',seenStates={},taskCleanupPending=false;
const root=()=>({querySelector:()=>({})});
const message=()=>{},toast=()=>{},refreshVideos=async()=>{},refresh=async()=>{};
const updateCleanupButtons=()=>{button.disabled=taskCleanupPending;};
const localStorage={removeItem(){}};
let api=async()=>({deleted:1,video_tasks:0});
(async()=>{
 ov._sourceReplanPending={id:'cleared'};await clearAllTasks(button);
 assert.equal(ov._sourceReplanPending,undefined);assert.equal(button.disabled,false);
 ov._sourceReplanPending={id:'keep-on-failure'};
 api=async()=>{throw new Error('network failed');};await clearAllTasks(button);
 assert.equal(ov._sourceReplanPending.id,'keep-on-failure');
 let finish;api=()=>new Promise(resolve=>finish=resolve);
 const clearing=clearAllTasks(button);ov._sourceReplanPending={id:'new'};
 finish({deleted:1,video_tasks:0});await clearing;
 assert.equal(ov._sourceReplanPending.id,'new');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_reopening_keeps_same_segment_tracking_without_blocking_other_segments(tmp_path):
    reopen = APP_JS.split('    ov._h3Session = Number(ov._h3Session || 0) + 1;', 1)[1].split(
        '    ov._previousFocus =', 1)[0]
    run_node(tmp_path, 'function reopen(segmentIndex){ov._h3Session=2;' + reopen + r'''
}
ov._sourceReplanPending={id:'active',session:'1',runtime:'0'};
reopen('1');assert.equal(ov._sourceReplanPending.session,'2');
reopen('2');assert.equal(ov._sourceReplanPending,undefined);
ov._sourceReplanPending={id:'other-runtime',session:'1',runtime:'old'};
reopen('1');assert.equal(ov._sourceReplanPending,undefined);
''')
