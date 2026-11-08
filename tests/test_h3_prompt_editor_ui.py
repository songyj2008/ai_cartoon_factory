"""The visible editor cannot invite changes that the compiler will overwrite."""
import subprocess

from ui.h3_prompt_editor_ui import PROMPT_EDITOR_JS
from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS


def test_generated_sections_are_readonly_and_authored_edits_roundtrip(tmp_path):
    script = tmp_path / 'prompt_view.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');
function element(){return {children:[],hidden:false,textContent:'',className:'',listeners:{},
 append(...items){this.children.push(...items);},before(item){this.previous=item;},setAttribute(){},
 addEventListener(event,listener){(this.listeners[event]||=[]).push(listener);},
 dispatchEvent(event){for(const listener of this.listeners[event.type]||[])listener(event);}};}
class Textarea{constructor(){Object.assign(this,element());this.nativeValue='';}}
Object.defineProperty(Textarea.prototype,'value',{get(){return this.nativeValue;},set(text){this.nativeValue=String(text).replace(/\r\n?/g,'\n');}});
global.HTMLTextAreaElement=Textarea;
global.document={createElement:()=>element()};global.window={AICF:{}};
''' + PROMPT_EDITOR_JS + r'''
const A=window.AICF,editor=new Textarea(),ov={querySelector:()=>editor};
const note='本段未安排入画；在场状态与站位以镜头描述为准。';
const full='【主体定义】\n<Subject 1>：甲。'+note+'\n\n【任务概述】\n原任务。\n\n'
 +'【参考保留关系】\n原参考。\n\n【人物音色绑定】\n原绑定。\n\n'
 +'【详细镜头描述】\n[Shot 1]（0–4 秒）：<Subject 1>说：<d>[Chinese]原台词</d>\n\n【非叙事性音乐】\n原音乐。';
const native=Object.getOwnPropertyDescriptor(Textarea.prototype,'value');
A.installH3PromptEditor(ov,true);editor.value=full;
assert.equal(editor.value,full,'Opening a prompt must not create a false unsaved edit');
assert.ok(!native.get.call(editor).includes('【主体定义】'));
assert.ok(!native.get.call(editor).includes(note));
assert.ok(native.get.call(editor).includes('【详细镜头描述】'));
assert.ok(editor._h3PromptView.preview.textContent.includes(note));
assert.equal(editor._h3PromptView.panel.hidden,false);
assert.equal(editor._h3PromptView.panel.children[0].textContent,'自动参考信息（只读）');
// Actual user typing changes the native textarea; save APIs still read a full prompt.
native.set.call(editor,native.get.call(editor).replace('原台词','人工修改的台词'));
editor.dispatchEvent({type:'input'});
assert.equal(editor.value,full.replace('原台词','人工修改的台词'));
const submitted=editor.value;
editor.value=submitted; // The saved canonical response and a subsequent reopen.
A.installH3PromptEditor(ov,true);
assert.ok(native.get.call(editor).includes('人工修改的台词'));
assert.ok(!native.get.call(editor).includes(note));assert.equal(editor.value,submitted);
// New compiler output refreshes the readonly view without moving it into the editor.
editor.value=submitted.replace(note,'该人物出现在 Shot 1。');
assert.ok(editor._h3PromptView.preview.textContent.includes('该人物出现在 Shot 1。'));
assert.ok(!native.get.call(editor).includes('该人物出现在 Shot 1。'));
// Pasting an entire old prompt must neither duplicate references nor restore stale notes.
native.set.call(editor,full.replace('原台词','粘贴后的台词'));
editor.dispatchEvent({type:'input'});
assert.equal((editor.value.match(/【主体定义】/g)||[]).length,1);
assert.ok(editor.value.includes('粘贴后的台词'));
assert.ok(editor.value.includes('该人物出现在 Shot 1。'));
assert.ok(!editor.value.includes(note));
assert.ok(!native.get.call(editor).includes('【主体定义】'));
// Section-like words spoken in dialogue remain editable authored text.
const quoted=full.replace('原台词','台词\n【主体定义】\n说的是标题');editor.value=quoted;
assert.ok(native.get.call(editor).includes('台词\n【主体定义】\n说的是标题'));
assert.equal(editor.value,quoted);
// Plain drafts have no generated panel. Other models retain their ordinary textarea.
editor.value='【详细镜头描述】\n正文';assert.equal(editor._h3PromptView.panel.hidden,true);
A.installH3PromptEditor(ov,false);editor.value=full;
assert.equal(native.get.call(editor),full);assert.equal(editor._h3PromptView.panel.hidden,true);
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_candidate_capture_keeps_complete_prompt_with_author_only_view(tmp_path):
    # Exercise the real capture helper, not a separate imitation of a save payload.
    capture = 'function capture(ov){' + EDITOR_DRAFT_JS.split(' function capture(ov){', 1)[1].split(' function signature', 1)[0]
    script = tmp_path / 'capture_view.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict'),clone=value=>JSON.parse(JSON.stringify(value));
class Textarea{constructor(){this.nativeValue='';}before(){}addEventListener(){}}
Object.defineProperty(Textarea.prototype,'value',{get(){return this.nativeValue;},set(text){this.nativeValue=text;}});
global.HTMLTextAreaElement=Textarea;global.window={AICF:{}};
global.document={createElement:()=>({append(){},setAttribute(){}})};
''' + PROMPT_EDITOR_JS + r'''
const A=window.AICF,editor=new Textarea();
const full='【主体定义】\n自动备注\n\n【详细镜头描述】\n原正文';
const ov={_editorSaved:{draft:{prompt:full,duration_sec:4,asset_manifest:[]}},
 querySelector:s=>s==='.aicf-prompt-modal-body'?editor:s==='.h3-state-select'?{value:'room'}:{value:'剧情'}};
A.readH3Manifest=()=>[];A.h3PromptDuration=()=>4;
A.installH3PromptEditor(ov,true);editor.value=full;
''' + capture + r'''
assert.equal(capture(ov).prompt,full);
editor.nativeValue=editor.nativeValue.replace('原正文','人工正文');
const payload=capture(ov);
assert.equal(payload.prompt,full.replace('原正文','人工正文'));
assert.ok(!editor.nativeValue.includes('自动备注'));
assert.ok(payload.prompt.includes('自动备注'));
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
