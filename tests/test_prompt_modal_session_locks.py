import shutil
import subprocess

import pytest

from ui.scripts import APP_JS


RESET = 'window.AICF.resetPromptModalOperationState = function(ov)' + APP_JS.split(
    'window.AICF.resetPromptModalOperationState = function(ov)', 1)[1].split(
    'window.AICF.openPromptModalFromButton = function', 1)[0]


def run_js(tmp_path, source):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    path = tmp_path / 'session_locks.cjs'
    path.write_text(source, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_reopened_editor_can_save_latest_dialogue_and_keeps_backend_write_promise(tmp_path):
    save = 'window.AICF.savePromptModal = async function(forceSave, contentOnly)' + APP_JS.split(
        'window.AICF.savePromptModal = async function(forceSave, contentOnly)', 1)[1].split(
        'window.AICF.readPageLLMProviders = function', 1)[0]
    run_js(tmp_path, r'''
const assert=require('node:assert/strict');let saves=0,feedback='';
const window={AICF:{setPromptModalFeedback:text=>feedback=text}};
const pendingWrite=Promise.resolve(),latestLine={value:'专家说，最好还是陈默来处理。'};
const ov={dataset:{h3Session:'new'},_editorBusy:true,_promptTaskBusy:true,_operationPending:true,
 _sceneStateBusy:true,_h3SceneConfigBusy:true,_promptTaskOwner:{},_h3SavePending:pendingWrite,
 _shotEditor:{dirty:true,stale:false,documents:[],save:async()=>{saves++;assert.equal(latestLine.value,'专家说，最好还是陈默来处理。');return true;}}};
const document={getElementById:()=>ov};
''' + RESET + save + r'''
(async()=>{
 await window.AICF.savePromptModal();assert.equal(saves,0);assert.ok(feedback.includes('正在保存或提交'));
 window.AICF.resetPromptModalOperationState(ov);
 assert.equal(ov._h3SavePending,pendingWrite);
 assert.equal(ov._operationPending,false);assert.equal(ov._sceneStateBusy,false);
 await window.AICF.savePromptModal();assert.equal(saves,1);
 assert.equal(ov._shotEditor.dirty,true,'Reset must not discard the unsaved Shot draft');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_old_request_finally_cannot_unlock_or_overwrite_new_session(tmp_path):
    run_action = 'window.AICF.runSavedPromptAction = async function(ov,submit)' + APP_JS.split(
        'window.AICF.runSavedPromptAction = async function(ov,submit)', 1)[1].split(
        'window.AICF.savePromptSourceExcerpt = async function', 1)[0]
    run_js(tmp_path, r'''
const assert=require('node:assert/strict');let rejectSave;
const editor={readOnly:false,value:'新台词'},control={disabled:false,isConnected:true};
const ov={dataset:{h3Session:'old'},querySelector:()=>editor,querySelectorAll:()=>[control]};
const window={AICF:{savePromptBeforeTask:()=>new Promise((resolve,reject)=>rejectSave=reject)}};
''' + RESET + run_action + r'''
(async()=>{
 const previous=window.AICF.runSavedPromptAction(ov,()=>{throw Error('Old session must not submit');});
 assert.equal(ov._promptTaskBusy,true);
 ov.dataset.h3Session='new';window.AICF.resetPromptModalOperationState(ov);
 const newOwner={};ov._promptTaskBusy=true;ov._promptTaskOwner=newOwner;
 editor.readOnly=true;control.disabled=true;
 rejectSave(Error('编辑会话已切换'));await assert.rejects(previous,/编辑会话已切换/);
 assert.equal(ov._promptTaskBusy,true);assert.equal(ov._promptTaskOwner,newOwner);
 assert.equal(editor.readOnly,true);assert.equal(control.disabled,true);assert.equal(editor.value,'新台词');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


@pytest.mark.parametrize('switch_session', [False, True])
def test_optimized_draft_save_waits_for_latest_shot_preview(tmp_path, switch_session):
    from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
    save = 'A.saveH3EditorDraft=async (ov,throwOnFailure=false)=>{' + EDITOR_DRAFT_JS.split(
        ' A.saveH3EditorDraft=async (ov,throwOnFailure=false)=>{', 1)[1].split(' const mount=A.mountShotEditor;', 1)[0]
    run_js(tmp_path, 'const SWITCH=' + str(switch_session).lower() + ';\n' + r'''
const assert=require('node:assert/strict'),A={};let releasePreview,request,text='同步前旧台词';
const snapshot=()=>({prompt:text,source_excerpt:'',duration_sec:9,asset_manifest:[]});
const ov={dataset:{h3Session:'one'},_editorSaved:{base_revision:'r'},_editorDraft:{draft:snapshot()},
 _shotEditor:{flushPreview:()=>new Promise(resolve=>releasePreview=()=>{text='右侧最新台词';resolve();})}};
const setBusy=(ov,value)=>ov._editorBusy=value,note=()=>{},capture=()=>snapshot(),signature=JSON.stringify;
const api=async(ov,action,payload)=>{assert.equal(action,'commit');request=payload;throw Error('Captured request');};
''' + save + r'''
(async()=>{
 const pending=A.saveH3EditorDraft(ov,true);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(request,undefined);
 if(SWITCH){ov.dataset.h3Session='two';ov._editorBusy=true;}
 releasePreview();
 if(SWITCH){await pending;assert.equal(request,undefined);assert.equal(ov._editorBusy,true);}
 else{
  await assert.rejects(pending,/Captured request/);
  assert.equal(request.draft.prompt,'右侧最新台词');assert.equal(request.manual_edit,true);
  assert.equal(ov._editorBusy,false);
 }
})().catch(e=>{console.error(e);process.exitCode=1});
''')
