import json
import shutil
import subprocess

import pytest

from ui.h3_shot_editor_ui import SHOT_EDITOR_JS
from ui.scripts import APP_JS


def test_read_cannot_install_old_documents_after_references_change():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    body = SHOT_EDITOR_JS.split('    async function read(restore=true){', 1)[1].split('    state.stop=', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const A={},ov={dataset:{segmentIndex:'1'},querySelector:()=>null},editor={value:'unchanged text'};
const state={sequence:0,documents:[{uid:'existing'}],manifestBase:'previous baseline'};
const button={disabled:false},host={querySelector:()=>button};
const valid=()=>true,updateTimes=()=>{},info=()=>{};
let previewTimer,current=[{id:'old',type:'image'}],resolveRead,request;
const manifest=()=>current;
const api=async(action,payload)=>{request=payload;return await new Promise(resolve=>resolveRead=resolve);};
async function read(restore=true){
''' + body + r'''
(async()=>{
 const pending=read(false);
 current=[{id:'new',type:'image'}];
 resolveRead({documents:[{uid:'stale',index:1}],fingerprint:'old'});
 assert.equal(await pending,false);
 assert.deepEqual(request.asset_manifest,[{id:'old',type:'image'}]);
 assert.deepEqual(state.documents,[{uid:'existing'}]);
 assert.equal(state.manifestBase,'previous baseline');
 assert.equal(button.disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_install_candidate_ignores_older_inflight_preview(tmp_path):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    preview=SHOT_EDITOR_JS.split('    async function preview() {',1)[1].split('    function schedule()',1)[0]
    install=SHOT_EDITOR_JS.split('    state.installContentDraft=function(draft,document){',1)[1].split('    read();',1)[0]
    program=r'''
const assert=require('node:assert/strict');
const clone=v=>JSON.parse(JSON.stringify(v));
const A={};
const state={sequence:0,sourceUid:'s',documents:[{uid:'s',shots:[],config:{},prompt:'original'}]};
const editor={value:'original'},valid=()=>true,payload=()=>({});
const info=()=>{},render=()=>{},updateTimes=()=>{},persist=()=>{},updateAvailability=()=>{};
const syncPrompt=doc=>editor.value=doc.prompt;
let resolvePreview;
const api=()=>new Promise(resolve=>resolvePreview=resolve);
let timerRan=false,previewTimer=setTimeout(()=>timerRan=true,0);
async function preview(){
''' + preview + r'''
state.installContentDraft=function(draft,document){
''' + install + r'''
(async()=>{
  const pending=preview();
  state.installContentDraft({prompt:'candidate',asset_manifest:[]},{shots:[]});
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(timerRan,false);
  resolvePreview({documents:[{uid:'s',prompt:'older preview',errors:[],warnings:[],config:{}}],errors:[],total_duration_sec:4});
  await pending;
  assert.equal(editor.value,'candidate');
  assert.equal(state.documents[0].prompt,'candidate');
  assert.equal(state.dirty,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path=tmp_path/'candidate_preview_race.cjs'
    path.write_text(program,encoding='utf-8')
    result=subprocess.run([node,str(path)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr


def test_real_shot_javascript_ripple_align_and_transfer(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    fixture = r'''
const assert=require('node:assert/strict');
global.window={AICF:{}};
global.crypto=require('node:crypto').webcrypto;
'''+SHOT_EDITOR_JS+r'''
const M=window.AICF.shotEditorMath;
// Snap the Part total, rather than rounding the edited Shot itself.
const snapShot={duration_cs:363},snapDoc={shots:[{duration_cs:937},snapShot]};
assert.equal(M.snapTotalDuration(snapDoc,snapShot,370,1500),363);
assert.equal(M.snapTotalDuration(snapDoc,snapShot,352,1500),363);
assert.equal(M.snapTotalDuration(snapDoc,snapShot,350,1500),350);
assert.equal(M.snapTotalDuration(snapDoc,snapShot,460,1500),463);
assert.equal(M.snapTotalDuration(snapDoc,snapShot,370,360),370);
assert.equal(snapShot.duration_cs,363);
assert.equal(M.snapTotalDuration({shots:[snapShot]},snapShot,5,1500),5);
const baseline=[{uid:'base',title:'saved',config:{channel_id:'runninghub'},shots:[{uid:'shot',duration_cs:200,body:'saved text',dialogues:[]}]}];
const cached=JSON.parse(JSON.stringify(baseline));
cached[0].touched=true;cached[0].prompt='preview text';cached[0].warnings=['old warning'];cached[0].config.channel_id='comfyui';
assert.equal(M.hasDraftChanges(cached,baseline),false);
cached[0].shots[0].duration_cs=201;assert.equal(M.hasDraftChanges(cached,baseline),true);
cached[0].shots[0].duration_cs=200;cached[0].shots[0].dialogues=[{uid:'line',speaker_key:'person',line:'new'}];
assert.equal(M.hasDraftChanges(cached,baseline),true);
const source={uid:'a',title:'source',model_id:'minimax_h3_local_ref2va',config:{asset_manifest:[{type:'audio',path:'selected.mp3'}]},
 shots:[{uid:'one',duration_cs:210},{uid:'two',duration_cs:140},{uid:'three',duration_cs:250}]};
const originalNext={uid:'b',title:'old next',shots:[{uid:'last',duration_cs:400}],model_id:'minimax_h3_local_ref2va'};
source.shots[0].duration_cs=280;
assert.deepEqual(M.ranges(source).map(s=>[s.start_cs,s.end_cs]),[[0,280],[280,420],[420,670]]);
M.align(source);
assert.equal(source.shots[2].duration_cs,280);
assert.equal(M.ranges(source).at(-1).end_cs,700);
let docs=[source,originalNext];
let newSegment=M.move(docs,'a','two',true,'new');
assert.equal(docs[1],newSegment);
assert.equal(docs[2],originalNext);
assert.deepEqual(newSegment.shots.map(s=>s.uid),['two','three']);
assert.deepEqual(source.shots.map(s=>s.uid),['one']);
assert.deepEqual(newSegment.config,source.config);
newSegment.config.asset_manifest[0].path='new.mp3';
assert.equal(source.config.asset_manifest[0].path,'selected.mp3');
M.move(docs,newSegment.uid,'two',false,'next');
assert.deepEqual(originalNext.shots.map(s=>s.uid),['two','last']);
assert.deepEqual(M.ranges(originalNext).map(s=>[s.start_cs,s.end_cs]),[[0,140],[140,540]]);
// Transfer into an existing Part by default, preserving all Shot durations.
const existingCount=docs.length;
M.move(docs,'a','one',true);
assert.equal(docs.length,existingCount);
assert.deepEqual(newSegment.shots.map(s=>s.uid),['one','three']);
assert.equal(source.shots.length,0);
// Last Part transfer creates exactly one new Part with copied configuration.
originalNext.config={asset_manifest:[]};originalNext.sections=[];
originalNext.history=['old'];originalNext.prompt='old prompt';
const appended=M.move(docs,'b','last',false);
assert.equal(docs.at(-1),appended);
assert.equal(docs.length,existingCount+1);
assert.deepEqual(appended.shots.map(s=>s.uid),['last']);
assert.deepEqual(M.ranges(appended).map(s=>[s.start_cs,s.end_cs]),[[0,400]]);
assert.equal(appended.history,undefined);
assert.equal(appended.prompt,undefined);
assert.deepEqual(docs.flatMap(d=>d.shots.map(s=>s.uid)).sort(),['last','one','three','two']);
// Incompatible next Parts are not skipped or silently replaced.
const invalid=[{uid:'x',shots:[{uid:'s',duration_cs:400}]},{uid:'y',model_id:'other',shots:[]}];
assert.throws(()=>M.move(invalid,'x','s',false),/H3/);
assert.equal(invalid[0].shots.length,1);
assert.equal(invalid.length,2);
assert.equal(M.fmt(280),'2.80');
assert.equal(window.AICF.h3PromptDuration('【详细镜头描述】\n[Shot 1]（0.00–1.550 秒）：描述\n[Shot 2] At 00:01.550（1.550–5.00 秒）：描述\n【整体声音环境】安静'),5);
assert.equal(window.AICF.h3PromptDuration('【详细镜头描述】\n[Shot 1]（0–2 秒）：描述\n[Shot 2]（3–5 秒）：描述'),null);
'''
    path=tmp_path/'shot_editor.cjs'
    path.write_text(fixture, encoding='utf-8')
    result=subprocess.run([node,str(path)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr


def test_assembled_application_javascript_parses(tmp_path):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    path=tmp_path/'app.cjs'
    path.write_text(APP_JS,encoding='utf-8')
    result=subprocess.run([node,'--check',str(path)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr


def test_manual_text_save_bypasses_stale_shot_draft(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    save_function = APP_JS.split('window.AICF.savePromptModal = async function(forceSave, contentOnly) {', 1)[1].split(
        'window.AICF.readPromptReplanOptions =', 1)[0]
    fixture = r'''
const assert = require('node:assert/strict');
let shotSaves = 0, textSaves = 0;
const ov = {dataset:{}, querySelector:()=>({value:'edited text'}),
    _shotEditor:{dirty:true,stale:true,documents:[],save:async()=>{shotSaves++;}}};
global.document = {getElementById:()=>ov};
global.window = {AICF:{
    savePromptSourceExcerpt:async()=>{textSaves++;throw new Error('stop after routing');},
    setPromptModalFeedback:()=>{}
}};
window.AICF.savePromptModal = async function(forceSave, contentOnly) {
''' + save_function + r'''
(async()=>{
    await window.AICF.savePromptModal();
    assert.equal(textSaves,1);
    assert.equal(shotSaves,0);
    assert.equal(ov._shotEditor.dirty,true);
    ov._shotEditor.documents=[{new:true,shots:[{uid:'transferred'}]}];
    await window.AICF.savePromptModal();
    assert.equal(textSaves,1);
    assert.equal(shotSaves,0);
    assert.equal(ov._shotEditor.documents[0].shots[0].uid,'transferred');
    ov._shotEditor.stale=false;
    await window.AICF.savePromptModal();
    assert.equal(shotSaves,1);
    assert.equal(textSaves,1);
    // A manual whole-prompt edit supersedes an old dialogue-only projection.
    let reads=0;
    ov._shotEditor.documents=[{dialogue_changed:true}];ov._shotEditor.stale=true;
    ov._shotEditor.read=async restore=>{assert.equal(restore,false);reads++;ov._shotEditor.stale=false;return true;};
    await window.AICF.savePromptModal();
    assert.equal(reads,1);assert.equal(shotSaves,2);assert.equal(textSaves,1);
    // A failed parse preserves both drafts and never reports a successful save.
    ov._shotEditor.stale=true;ov._shotEditor.read=async()=>false;
    await window.AICF.savePromptModal();
    assert.equal(shotSaves,2);assert.equal(ov._shotEditor.dirty,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'save_routing.cjs'
    path.write_text(fixture, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_duration_save_allows_stale_shot_draft(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    duration_function = APP_JS.split('ov._savePromptDuration = async function() {', 1)[1].split(
        '\n    };', 1)[0]
    program = r'''
const assert = require('node:assert/strict');
const ov = {_shotEditor:{dirty:true,stale:true}};
const valid = ()=>true;
const snapshot = {duration_sec:15};
const A = {};
const editor = {value:''};
const q = ()=>({value:'15'});
ov._savePromptDuration = async function() {
''' + duration_function + r'''
};
(async()=>{
    await ov._savePromptDuration();
    ov._shotEditor.stale=false;
    await assert.rejects(ov._savePromptDuration(),/未保存/);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = tmp_path / 'duration_save.cjs'
    path.write_text(program, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_shot_save_restores_read_button_and_keeps_draft(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    body = SHOT_EDITOR_JS.split('state.save=async()=>{', 1)[1].split('\n    };', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const A={};
const button={disabled:false,isConnected:true};
const host={querySelectorAll:()=>[button]};
const editor={readOnly:false};
const state={dirty:true,stale:false,saving:false,documents:[],manifestBase:'[]',sequence:0};
const valid=()=>true,manifest=()=>[],payload=()=>({}),render=()=>{},persist=()=>{};
const ov={querySelectorAll:()=>[],querySelector:()=>null};
const api=async()=>{throw new Error('后台内容已变化');};
let previewTimer;
state.save=async()=>{
''' + body + r'''
};
(async()=>{
  await assert.rejects(state.save(),/已变化/);
  assert.equal(button.disabled,false);
  assert.equal(editor.readOnly,false);
  assert.equal(state.saving,false);
  assert.equal(state.dirty,true);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    path = tmp_path / 'failed_save.cjs'
    path.write_text(program, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_text_events_do_not_lock_slider_and_real_text_edits_do(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    on_editor = SHOT_EDITOR_JS.split('    function onEditor(){', 1)[1].split("    editor.addEventListener('input',onEditor);", 1)[0]
    availability = SHOT_EDITOR_JS.split('    function updateAvailability(){', 1)[1].split('    function updateTimes(){', 1)[0]
    program = r'''
const assert=require('node:assert/strict');
const A={},ov={};
const slider={disabled:false,dataset:{}},text={disabled:false,dataset:{}},unavailable={disabled:true,dataset:{unavailable:'true'}};
const undo={disabled:false},redo={disabled:true};
const host={querySelectorAll:()=>[slider,text,unavailable],querySelector:s=>s.includes('undo')?undo:redo};
const state={lastPrompt:'current text',stale:false,dirty:true,sequence:0,history:[[]],future:[],own:false};
const editor={value:'current text'};
let previewTimer,scheduled=0;
const schedule=()=>scheduled++,info=()=>{};
function updateAvailability(){
''' + availability + r'''
const updateTimes=()=>updateAvailability();
function onEditor(){
''' + on_editor + r'''
updateAvailability();assert.equal(slider.disabled,false);assert.equal(unavailable.disabled,true);
onEditor();onEditor();assert.equal(state.stale,false);assert.equal(state.sequence,0);
editor.value='manual edit';onEditor();assert.equal(state.stale,true);assert.equal(slider.disabled,true);
assert.equal(undo.disabled,true);
editor.value='current text';onEditor();assert.equal(state.stale,false);assert.equal(slider.disabled,false);
assert.equal(scheduled,1);assert.equal(unavailable.disabled,true);
state.reading=true;updateAvailability();assert.equal(slider.disabled,true);
state.reading=false;updateAvailability();assert.equal(slider.disabled,false);
'''
    path = tmp_path / 'slider_state.cjs'
    path.write_text(program, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
