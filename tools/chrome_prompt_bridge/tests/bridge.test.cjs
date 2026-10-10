const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const P = require('../protocol.js');
const TextProviderPolicy=require('../text_provider_policy.js');

test('only exact supported HTTPS hosts are accepted', () => {
  assert.equal(P.provider('https://chatgpt.com/c/example'), 'ChatGPT');
  assert.equal(P.provider('https://chat.deepseek.com/'), 'DeepSeek');
  assert.equal(P.provider('https://www.doubao.com/chat'), '豆包');
  assert.equal(P.provider('https://doubao.com/chat'), '豆包');
  for (const url of ['http://chatgpt.com/', 'https://chatgpt.com.evil.org', 'https://evil.org/?chatgpt.com', 'invalid']) assert.equal(P.provider(url), null);
});

test('request preserves user text and demands a correlated complete answer', () => {
  const prompt = '中文提示词\n"引号" <Subject 1> 🎬';
  const request = P.buildRequest({ prompt, issue: '6 秒人物消失' }, 'test-1');
  assert.ok(request.includes(JSON.stringify(prompt)));
  assert.ok(request.includes('AICF_RESULT_test-1_END'));
  assert.throws(() => P.buildRequest({ prompt }, 'test-1'), /问题/);
  assert.throws(() => P.buildRequest({ issue: '问题' }, 'test-1'), /原提示词/);
});

function answer(id, overrides = {}) {
  const m = P.markers(id);
  return `${m.start}\n${JSON.stringify({ request_id: id, changes: ['明确动作'], revised_prompt: '新提示词', ...overrides })}\n${m.end}`;
}

test('never accepts old, partial, malformed or mismatched replies', () => {
  assert.equal(P.parseResult(answer('old'), 'new'), null);
  assert.equal(P.parseResult(answer('new').replace('AICF_RESULT_new_END', ''), 'new'), null);
  assert.throws(() => P.parseResult(answer('new', { request_id: 'old' }), 'new'), /不匹配/);
  assert.throws(() => P.parseResult(answer('new', { revised_prompt: ' ' }), 'new'), /缺少/);
  assert.throws(() => P.parseResult(answer('new', { changes: 'bad' }), 'new'), /格式/);
  assert.equal(P.parseResult(answer('new'), 'new').revised_prompt, '新提示词');
  assert.equal(P.parseResult(answer('new', { revised_prompt: '<script>alert(1)</script>' }), 'new').revised_prompt, '<script>alert(1)</script>');
});

function page(initial = '', options = {}) {
  const notifications = [];
  let pageReads = 0;
  let listener, state = {}, now = Date.now(), clicks = 0, newChats = 0, sentText = '';
  const location = {href: options.url || (options.doubao ? 'https://www.doubao.com/chat' : options.deepseek ? 'https://chat.deepseek.com/' : 'https://chatgpt.com/')};
  const timers = new Map(); let timerId = 0;
  let mutation = null;
  class Input {
    constructor() { this._value = initial; }
    get value() { return this._value; }
    set value(v) { this._value = v; }
    getClientRects() { return [1]; }
    focus() {}
    dispatchEvent(event) { if ((options.deepseek || options.doubao || options.noButton) && event.type === 'keydown' && options.accept !== false) { clicks++; sentText = this.value; this.value = ''; if(options.conversationAfterSend) location.href=options.conversationAfterSend; } }
  }
  const editor = new Input();
  let renamedTo = '', renameMenuOpen = false, renameInputOpen = false;
  const renameInput = new Input();
  renameInput.closest=()=>null;
  renameInput.getBoundingClientRect=()=>({left:700,top:20,right:900,bottom:55,width:200,height:35});
  const renameSave = {innerText:'保存',getClientRects:()=>[1],closest:()=>null,
    getBoundingClientRect:()=>({left:910,top:20,right:970,bottom:55,width:60,height:35}),
    click:()=>{renamedTo=renameInput.value; renameInputOpen=false;}};
  const renameControl = {getClientRects:()=>[1],getAttribute:key=>key==='aria-haspopup'?'menu':null,
    click:()=>{renameMenuOpen=true;}};
  const renameAction = {innerText:'重命名',getClientRects:()=>[1],contains:()=>false,
    closest:()=>null,click:()=>{renameMenuOpen=false;renameInputOpen=true;}};
  const headerMenu = {innerText:'',getClientRects:()=>[1],getAttribute:()=>null,
    getBoundingClientRect:()=>({left:1320,top:15,right:1360,bottom:55,width:40,height:40}),
    click:()=>{renameMenuOpen=true;}};
  const headerTitle = {get innerText(){return renamedTo || '新对话';},getClientRects:()=>[1],
    getBoundingClientRect:()=>({left:650,top:10,right:900,bottom:45,width:250,height:35})};
  const renameLink = {getClientRects:()=>[1],getAttribute:key=>key==='href'?'/chat/rename-target':null,
    querySelector:()=>null,querySelectorAll:()=>[renameLink]};
  const renameRow = {get innerText(){return renamedTo || '新对话';},parentElement:null,
    contains:el=>el===renameInput || el===renameSave,
    querySelector:()=>renameControl,
    querySelectorAll:selector=>selector==='a[href], [data-conversation-id], [data-chat-id]'?[renameLink]:[renameControl]};
  renameLink.parentElement=renameRow;
  const button = { getClientRects: () => [1], getAttribute: k => k === 'data-testid' ? (options.doubaoIconSend ? null : 'send-button') : k === 'class' && options.doubaoIconSend ? 'send-control' : null, click: () => { clicks++; if (options.accept !== false) {sentText = editor.value; editor.value = ''; if(options.conversationAfterSend) location.href=options.conversationAfterSend;} }, innerText: '', getBoundingClientRect:()=>({left:900,top:840,right:940,bottom:880,width:40,height:40}) };
  let replies = [], hitTargets = [], continueClicks = 0;
  const context = {
    PromptBridge: P,TextProviderPolicy, URL, location, innerWidth: 1440, innerHeight: 900, window:{},
    MouseEvent: class {constructor(type, options) {this.type=type; Object.assign(this,options);}},
    PointerEvent: class {constructor(type, options) {this.type=type; Object.assign(this,options);}},
    KeyboardEvent: class {constructor(type) {this.type = type;}},
    setTimeout: (fn, delay) => {if (delay <= 200 && delay !== 150 && !(options.throttleAfterSend && clicks)) {fn(); return 0;} const id=++timerId; timers.set(id,{fn,due:now+(options.throttleAfterSend && clicks && delay <= 200 ? 30000 : delay)}); return id;},
    clearTimeout:id=>timers.delete(id),
    MutationObserver: class {constructor(fn){this.fn=fn;} observe(){mutation=this.fn;} disconnect(){mutation=null;}},
    HTMLTextAreaElement: Input, HTMLInputElement: class {}, Event: class {constructor(type){this.type=type;}},
    getComputedStyle: () => ({ visibility: 'visible' }),
    Date: class extends Date { static now() { return now; } },
    setInterval: () => {throw new Error('Reply polling must not be used');}, clearInterval: id => timers.delete(id),
    document: { querySelector:()=>editor, elementFromPoint: (x,y) => hitTargets.find(el => {
      const r = el.getBoundingClientRect();
      return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    }) || null, body: {get innerText() { return editor.value + sentText + replies.map(r=>r.innerText).join(''); }, cloneNode() { return {querySelectorAll:()=>[], get textContent() { return sentText + replies.map(r=>r.innerText).join(''); }}; }}, querySelectorAll: selector => {
      pageReads++;
      if(options.renameChat && selector==='a[href], [data-conversation-id], [data-chat-id]') return [renameLink];
      if(options.headerRename && selector==='a[href], [data-conversation-id], [data-chat-id]') return [];
      if((options.renameChat || options.headerRename) && selector==='[role="menuitem"],button,span,div') return renameMenuOpen?[renameAction]:[];
      if((options.renameChat || options.headerRename) && selector==='input,textarea,[contenteditable="true"]') return renameInputOpen?[renameInput]:[];
      if((options.renameChat || options.headerRename) && selector==='button,[role="button"]' && renameInputOpen) return options.headerRename?[button,headerMenu,renameSave]:[button,renameSave];
      if(options.headerRename && selector==='button,[role="button"]') return [button,headerMenu];
      if(options.headerRename && selector==='h1,h2,[role="heading"],span,div') return [headerTitle];
      if (selector === 'a,button,[role="button"],div,span' && options.chatgptNewChat) {
        const nav = {};
        const control = {
          innerText: '新聊天', getClientRects: () => [1],
          getBoundingClientRect: () => ({left:8,top:100,right:200,bottom:145}), contains: () => false,
          getAttribute: key => key === 'href' ? '/' : null,
          closest: selector => selector === 'a[href],button,[role="button"]' ? control : selector === 'nav,aside,[role="navigation"]' ? nav : null,
          click: () => {newChats++; location.href = 'https://chatgpt.com/'; replies = [];}
        };
        const child = () => ({...control, closest: () => control});
        const plainText = {...control, closest: () => null};
        const messageButton = {...control, closest: selector => selector === 'a[href],button,[role="button"]' ? messageButton : {}};
        const nodes = [plainText, messageButton];
        if (!options.onlyFalseMatches) nodes.push(control, child(), child());
        const duplicate = {...control,
          getBoundingClientRect: () => options.offscreenNavigation ? ({left:-300,top:100,right:-100,bottom:145}) : ({left:400,top:100,right:600,bottom:145}),
          getAttribute: key => key === 'href' ? (options.differentDestination ? '/?temporary-chat=true' : '/') : null,
          click: () => {throw new Error('Should click upper-left navigation');}, closest: function(selector) {
          return selector === 'a[href],button,[role="button"]' ? this : selector === 'nav,aside,[role="navigation"]' ? nav : null;
        }};
        hitTargets = [control];
        if (options.duplicateNavigation) {
          nodes.unshift(duplicate);
          if (!options.coveredNavigation) hitTargets.push(duplicate);
        }
        return nodes;
      }
      if (selector === 'a,button,[role="button"],div,span' && options.newChat) {
        const leaf = {innerText:options.doubao?'新对话':'开启新对话',getClientRects:()=>[1],getAttribute:()=>null,contains:()=>false,closest:()=>null,click:()=>{newChats++;location.href=options.doubao?'https://www.doubao.com/chat':'https://chat.deepseek.com/';replies=[];}};
        return options.nested ? [{...leaf,contains:node=>node===leaf,click:()=>{throw new Error('Should click leaf, not duplicate parent');}},leaf] : [leaf];
      }
      if (selector === 'a,button,[role="button"],div,span' && options.doubaoDuplicateNewChat) {
        const nav = {};
        const sidebar = {
          innerText:'新对话 Ctrl Shift K', getClientRects:()=>[1], getAttribute:()=>null,
          contains:()=>false, closest:selector=>selector==='a[href],button,[role="button"],[tabindex="0"]' ? sidebar : selector==='nav,aside,[role="navigation"]' ? nav : null,
          click:()=>{newChats++;location.href='https://www.doubao.com/chat';replies=[];}
        };
        const duplicate = label => ({innerText:'',getClientRects:()=>[1],getAttribute:key=>key==='aria-label'||key==='title'?label:null,contains:()=>false,closest:()=>null,click:()=>{throw new Error('Should choose the labelled sidebar action');}});
        return [sidebar,duplicate('新对话'),duplicate('新对话')];
      }
      if (selector === 'a,button,[role="button"],div,span' && options.doubaoTextOnlyNewChat) {
        const sidebarText = {innerText:'新对话',getClientRects:()=>[1],getBoundingClientRect:()=>({left:20,top:150,right:120,bottom:190}),
          getAttribute:()=>null,contains:()=>false,closest:()=>null,click:()=>{newChats++;location.href='https://www.doubao.com/chat';replies=[];}};
        const duplicate = () => ({innerText:'',getClientRects:()=>[1],getBoundingClientRect:()=>({left:380,top:70,right:420,bottom:110}),
          getAttribute:key=>key==='aria-label'?'新对话':null,contains:()=>false,closest:()=>null,click:()=>{throw new Error('Should click the left sidebar text');}});
        return [sidebarText,duplicate(),duplicate()];
      }
      if (selector === 'a,button,[role="button"],div,span' && options.doubaoRecentTitleDuplicate) {
        const textAction = (top, recent=false) => ({innerText:'新对话',getClientRects:()=>[1],getBoundingClientRect:()=>({left:20,top,right:130,bottom:top+38}),
          getAttribute:()=>null,contains:()=>false,closest:()=>null,
          click:()=>{if(recent)throw new Error('Recent conversation title is not the new-chat action');newChats++;location.href='https://www.doubao.com/chat';replies=[];}});
        return [textAction(120),textAction(470,true),
          {innerText:'',getClientRects:()=>[1],getBoundingClientRect:()=>({left:370,top:70,right:420,bottom:110}),getAttribute:key=>key==='aria-label'?'新对话':null,contains:()=>false,closest:()=>null,click:()=>{throw new Error('Toolbar icon is not the sidebar action');}}];
      }
      if (selector === '#prompt-textarea') return options.modernEditor ? [] : [editor];
      if (selector === '[contenteditable="true"][role="textbox"],[contenteditable="true"][data-lexical-editor="true"],[contenteditable="true"].ProseMirror') return options.modernEditor ? (options.ambiguous ? [editor, new Input()] : [editor]) : [];
      if (selector === 'textarea[placeholder]') return [editor];
      if (selector === 'textarea') return options.doubao ? [editor] : [];
      if (options.doubaoIconSend && selector.includes('[data-testid*="send"')) return [button];
      if (selector === 'button,[role="button"]') return options.deepseek || options.noButton ? [] : [button];
      if (selector === '[data-message-author-role="assistant"]') return options.markdownOnly || options.noReplyClasses ? [] : replies;
      if (selector === '.ds-markdown' || (options.doubao && /data-testid.*message|message-content/.test(selector))) return replies;
      if (selector === '.markdown,.prose,[data-message-author-role="assistant"],[data-testid="conversation-turn"]') return options.noReplyClasses ? [] : replies;
      return [];
    } },
    chrome: { runtime: { id: 'test-extension', sendMessage:async message=>{
      if(message.type==='AICF_TASK_PATCH'){
        const key=`bridgeTask:${message.taskId}`;
        state[key]={...state[key],...message.patch,record_revision:(state[key]?.record_revision || 0)+1};
        return {ok:true,task:state[key]};
      }
      notifications.push({message,storedStatus:(state[`bridgeTask:${message.taskId}`] || state.task)?.status});
      if(message.type==='AICF_TRUSTED_CONTINUE_CLICK') {
        if(options.clickError) return {ok:false,error:'click rejected'};
        continueClicks++;
        return {ok:true,method:'trusted-debugger-mouse'};
      }
    }, onMessage: { addListener: fn => { listener = fn; } } }, storage: { local: {
      get: async () => state, set: async value => { state = { ...state, ...value }; }
    } } }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8'), context);
  return {
    editor, notifications, get continueClicks(){return continueClicks;}, get pageReads(){return pageReads;}, get now() {return now;},
    saveTask: value => {state[value.bridge ? `bridgeTask:${value.id}` : 'task']=value;},
    get newChats() {return newChats;}, get clicks() { return clicks; },
    get task() { return Object.entries(state).find(([key])=>key.startsWith('bridgeTask:'))?.[1] || state.task; }, get renamedTo(){return renamedTo;},
    continuation: (text, label='继续生成', disabled=false) => {
      const bounds = {left:100,top:100,right:180,bottom:130,width:80,height:30};
      const owner = {tagName:'BUTTON',innerText:label,className:'ds-button',disabled,getClientRects:()=>[1],getAttribute:()=>null,
        getBoundingClientRect:()=>bounds,scrollIntoView:()=>{},focus:()=>{},closest:selector=>selector==='button,[role="button"]'?owner:null,contains:el=>el===control,
        dispatchEvent:event=>{if(event.type==='click') {if(options.clickError) throw new Error('click rejected'); assert.equal(event.clientX,140); assert.equal(event.clientY,115); continueClicks++;}},
        click:()=>{if(options.clickError) throw new Error('click rejected'); continueClicks++;}};
      const control = {tagName:'SPAN',innerText:label,className:'ds-button__content',disabled,getClientRects:()=>[1],getAttribute:()=>null,
        getBoundingClientRect:()=>({left:100,top:100,right:180,bottom:130,width:80,height:30}),
        closest:selector=>selector==='button,[role="button"]'?owner:null, dispatchEvent:()=>{}, click:()=>{if(options.pointerTransparentLabel) throw new Error('Text label is not the hit target');}, contains:()=>false};
      if (options.nativeOnly) owner.dispatchEvent=event=>{if(event.type==='click') throw new Error('Synthetic click rejected');};
      hitTargets = options.coveredContinue ? [] : [control];
      const reply = {innerText:text, getClientRects:()=>[1], closest:()=>null, contains:()=>false};
      const parts = Array.isArray(text) ? text : [text];
      replies = parts.map(innerText=>({...reply,innerText}));
      const parent = {contains:el=>replies.includes(el), querySelectorAll:()=>[control], parentElement:null};
      replies.forEach(el=>{el.parentElement=parent;}); mutation?.();
    },
    reply: text => { replies = [{ innerText: text, getClientRects: () => [1], closest: () => null }]; mutation?.(); },
    replySilently: text => { replies = [{ innerText: text, getClientRects: () => [1], closest: () => null }]; },
    message: (type, task) => {
      if(task?.bridge && !state[`bridgeTask:${task.id}`])state[`bridgeTask:${task.id}`]={...task,binding_epoch:1};
      return new Promise(resolve => listener({ type, task:{...task,binding_epoch:task?.bridge ? 1 : task?.binding_epoch} }, { id: 'test-extension' }, resolve));
    },
    tick: async ms => { const target=now+ms; await Promise.resolve();
      while(true){
        const next=[...timers.entries()].filter(([,item])=>item.due<=target).sort((a,b)=>a[1].due-b[1].due)[0];
        if(!next)break;
        const [id,item]=next; now=item.due; timers.delete(id); await item.fn();
      }
      now=target;
    }, navigate: url => { location.href=url; sentText=''; replies=[]; },
    settleRoute: url => { location.href=url; }
  };
}
const task = id => ({ id, request: P.buildRequest({ prompt: '原文', issue: '问题' }, id), settings: {}, created_at: new Date().toISOString() });

test('malformed Doubao JSON requests one isolated repair and collects only its new envelope', async () => {
  const p=page('',{doubao:true,newChat:true}); const t={...task('json-repair'),kind:'json'};
  await p.message('AICF_SEND',t);
  const bad=answer(t.id,{data:{beats:[]}}).replace('"request_id":','"request_id" ');
  p.reply(bad); await p.tick(5000);
  assert.equal(p.clicks,2); assert.equal(p.newChats,1);
  assert.equal(p.task.json_repair.original_raw,bad);
  assert.equal(p.task.status,'waiting');
  await p.message('AICF_RESUME',p.task); await p.tick(5000);
  assert.equal(p.clicks,2); assert.equal(p.task.status,'waiting');
  const m=P.markers(p.task.json_repair.envelopeId);
  p.reply(`${m.start}\n${JSON.stringify({request_id:t.id,data:{beats:[{id:'beat-1'}]}})}\n${m.end}`);
  await p.tick(5000);
  assert.equal(p.task.status,'complete'); assert.equal(p.task.result.data.beats[0].id,'beat-1');
  assert.equal(p.clicks,2);
});

test('Doubao arrow-only submit control is clicked automatically', async () => {
  const p=page('',{doubao:true,newChat:true,doubaoIconSend:true});
  const response=await p.message('AICF_SEND',task('doubao-icon-send'));
  assert.equal(response.ok,true);
  assert.equal(p.clicks,1);
  assert.equal(p.task.status,'waiting');
  assert.equal(p.editor.value,'');
});

test('a second syntax failure stops without a repair loop', async () => {
  const p=page(); const t=task('repair-limit'); await p.message('AICF_SEND',t);
  p.reply(answer(t.id).replace('"request_id":','"request_id" ')); await p.tick(5000);
  const m=P.markers(p.task.json_repair.envelopeId);
  p.reply(`${m.start}\n{broken}\n${m.end}`); await p.tick(5000);
  assert.equal(p.task.status,'error'); assert.equal(p.clicks,2);
  assert.match(p.task.message,/自动修复一次/);
});

test('JSON repair never overwrites an existing draft or repairs a wrong task ID', async () => {
  for (const draft of [true,false]) {
    const p=page(); const t=task('repair-guard'); await p.message('AICF_SEND',t);
    if(draft) p.editor.value='user draft';
    p.reply(draft ? answer(t.id).replace('"request_id":','"request_id" ') : answer(t.id,{request_id:'another-task'}));
    await p.tick(5000);
    assert.equal(p.task.status,'error'); assert.equal(p.clicks,1);
    assert.equal(p.task.json_repair,undefined);
    if(draft) assert.equal(p.editor.value,'user draft');
  }
});

test('unresponsive continuation retries at most three times and reports uncertainty', async () => {
  const p=page('',{deepseek:true}); const t=task('retry-continue');
  await p.message('AICF_SEND',t);
  p.continuation(P.markers(t.id).start + '\npartial');
  await p.tick(40000);
  assert.equal(p.continueClicks,3);
  assert.equal(p.task.continuation_diagnostics.filter(e=>e.event==='click_dispatched').length,3);
  assert.deepEqual(Array.from(p.task.continuation_diagnostics.filter(e=>e.event==='click_dispatched'), e=>e.method),
    ['trusted-debugger-mouse','trusted-debugger-mouse','trusted-debugger-mouse']);
  assert.ok(p.task.continuation_diagnostics.some(e=>e.event==='no_reply_growth'));
  assert.ok(!p.task.continuation_diagnostics.some(e=>e.event==='reply_grew'));
  assert.ok(p.task.message.includes('请手动点击'));
  await p.tick(40000);
  assert.equal(p.continueClicks,3);
  p.reply(answer(t.id)); await p.tick(5000);
  assert.equal(p.task.status,'complete');
});

test('continuation uses native activation and the actual hit target of transparent labels', async () => {
  for (const options of [{nativeOnly:true}, {pointerTransparentLabel:true}]) {
    const p=page('',{deepseek:true,...options}); const t=task('native-continue');
    await p.message('AICF_SEND',t);
    p.continuation(P.markers(t.id).start+' partial'); await p.tick(5000);
    assert.equal(p.continueClicks,1);
    const dispatched = p.task.continuation_diagnostics.find(e=>e.event==='click_dispatched');
    assert.equal(dispatched.method,'trusted-debugger-mouse');
    assert.equal(dispatched.target.tag,'BUTTON');
    assert.equal(dispatched.hit.tag,'SPAN');
    p.reply(answer(t.id)); await p.tick(5000);
    assert.equal(p.task.status,'complete');
  }
});

test('continuation refuses a covered button and records why no click was sent', async () => {
  const p=page('',{deepseek:true,coveredContinue:true}); const t=task('covered-continue');
  await p.message('AICF_SEND',t);
  p.continuation(P.markers(t.id).start+' partial'); await p.tick(5000);
  assert.equal(p.continueClicks,0);
  assert.ok(p.task.continuation_diagnostics.some(e=>e.event==='click_failed' && e.error.includes('被遮挡')));
  assert.ok(!p.task.continuation_diagnostics.some(e=>e.event==='click_dispatched'));
});

test('DeepSeek continues incomplete current answers once per content and finishes normally', async () => {
  const p = page('', {deepseek:true}); const t = task('continue-current');
  await p.message('AICF_SEND', t);
  const partial = P.markers(t.id).start + '\n{"request_id":"continue-current"';
  p.continuation(partial); await p.tick(5000);
  assert.equal(p.continueClicks, 1);
  p.continuation(partial); await p.tick(5000);
  assert.equal(p.continueClicks, 1);
  p.continuation(partial + ',"changes":['); await p.tick(5000);
  assert.equal(p.continueClicks, 2);
  p.continuation(answer(t.id)); await p.tick(5000);
  assert.equal(p.continueClicks, 2);
  assert.equal(p.task.status, 'complete');
  assert.ok(p.task.continuation_diagnostics.some(e=>e.event==='reply_grew'));
});

test('continuation click exceptions are logged without claiming dispatch success', async () => {
  const p=page('',{deepseek:true,clickError:true}); const t=task('click-error');
  await p.message('AICF_SEND',t);
  p.continuation(P.markers(t.id).start+' partial'); await p.tick(5000);
  assert.ok(p.task.continuation_diagnostics.some(e=>e.event==='click_failed' && e.error==='click rejected'));
  assert.ok(!p.task.continuation_diagnostics.some(e=>e.event==='click_dispatched'));
  assert.equal(p.task.status,'waiting');
});

test('DeepSeek split reply blocks can continue and are collected as one result', async () => {
  const p=page('',{deepseek:true}); const t=task('split-continue');
  await p.message('AICF_SEND',t);
  const full=answer(t.id), cut=full.indexOf('新提示词');
  p.continuation([full.slice(0,cut), '新']); await p.tick(5000);
  assert.equal(p.continueClicks,1);
  p.continuation([full.slice(0,cut),full.slice(cut)]); await p.tick(5000);
  assert.equal(p.task.status,'complete');
  assert.equal(p.task.result.revised_prompt,'新提示词');
  assert.ok(p.task.continuation_diagnostics.some(e=>e.event==='reply_grew'));
});

test('continuation ignores regeneration, old answers, disabled buttons and other providers', async () => {
  for (const scenario of [
    {deepseek:true,label:'重新生成'}, {deepseek:true,old:true},
    {deepseek:true,disabled:true}, {deepseek:false},
  ]) {
    const p = page('', scenario); const t = task('safe-continue');
    await p.message('AICF_SEND', t);
    p.continuation(P.markers(scenario.old ? 'old' : t.id).start + '\npartial', scenario.label, scenario.disabled);
    await p.tick(5000);
    assert.equal(p.continueClicks, 0);
  }
});

test('cancelled and expired tasks never click continuation', async () => {
  for (const cancel of [true,false]) {
    const p=page('', {deepseek:true}); const t=task('stop-continue');
    await p.message('AICF_SEND',t);
    if(cancel) await p.message('AICF_CANCEL',t); else await p.tick(901000);
    p.continuation(P.markers(t.id).start + '\npartial'); await p.tick(5000);
    assert.equal(p.continueClicks,0);
  }
});

test('both providers recheck the DOM on backend polls as well as mutation events', async () => {
 for(const deepseek of [false,true]){
   const p=page('',{deepseek}); const t={...task('event-only'),bridge:{reported:false}};
   await p.message('AICF_SEND',t); const reads=p.pageReads;
   await p.tick(5000); await p.message('AICF_RESUME',p.task);
   assert.ok(p.pageReads>reads);
   p.reply(answer(t.id)); await p.tick(5000);
   assert.equal(p.task.status,'complete'); assert.equal(p.notifications.length,1);
 }
});

test('completed bridge reply proactively notifies worker after result is stored', async () => {
 const p=page(); const t={...task('push-result'),bridge:{reported:false}};
 await p.message('AICF_SEND',t);
 p.reply(answer(t.id)); await p.tick(200);
 assert.equal(p.notifications.length,0);
 await p.tick(400);
 assert.equal(p.notifications.length,1);
 assert.equal(p.notifications[0].message.type,'AICF_RESULT_READY');
 assert.equal(p.notifications[0].message.taskId,t.id);
 assert.equal(p.notifications[0].storedStatus,'complete');
});

test('markdown-only replies are read without accepting the request example', async () => {
 const p=page('',{markdownOnly:true}); const t=task('markdown-result');
 await p.message('AICF_SEND',t);
 const start=t.request.indexOf(P.markers(t.id).start);
 p.reply(t.request.slice(start)); await p.tick(2000); await p.tick(5000);
 assert.equal(p.task.status,'waiting');
 p.reply(answer(t.id)); await p.tick(2000); await p.tick(5000);
 assert.equal(p.task.status,'complete'); assert.equal(p.clicks,1);
});

test('reply without known CSS classes is recovered by its exact protocol envelope', async () => {
 const p=page('',{noReplyClasses:true}); const t=task('classless');
 await p.message('AICF_SEND',t);
 await p.tick(2000); await p.tick(5000);
 assert.equal(p.task.status,'waiting');
 p.reply(answer('other')); await p.tick(5000); assert.equal(p.task.status,'waiting');
 p.reply(answer(t.id)); await p.tick(2000); await p.tick(5000);
 assert.equal(p.task.status,'complete'); assert.equal(p.task.result.revised_prompt,'新提示词');
 assert.equal(p.clicks,1);
});

test('existing webpage draft is never overwritten or sent', async () => {
  const p = page('用户尚未发送的草稿');
  const response = await p.message('AICF_SEND', task('draft'));
  assert.equal(response.ok, false); assert.match(response.error, /已有草稿/);
  assert.equal(p.editor.value, '用户尚未发送的草稿'); assert.equal(p.clicks, 0);
});

test('fill does not send; send is at most once; old replies ignored; complete reply collected', async () => {
  const p = page(); const t = task('roundtrip');
  assert.equal((await p.message('AICF_FILL', t)).ok, true);
  assert.equal(p.clicks, 0); assert.equal(p.editor.value, t.request);
  assert.equal((await p.message('AICF_SEND', t)).ok, true);
  assert.equal(p.clicks, 1);
  assert.equal((await p.message('AICF_SEND', t)).ok, false);
  assert.equal(p.clicks, 1);
  p.reply(t.request); await p.tick(2000); await p.tick(5000); assert.equal(p.task.status, 'waiting');
  p.reply(answer('old')); await p.tick(5000); assert.equal(p.task.status, 'waiting');
  p.reply(answer('roundtrip')); await p.tick(200); assert.equal(p.task.status, 'waiting');
  await p.tick(400); assert.equal(p.task.status, 'complete'); assert.equal(p.task.result.revised_prompt, '新提示词');
});

test('worker resume checks a completed reply even when the page emitted no DOM mutation', async () => {
  const p=page(); const t=task('silent-render');
  await p.message('AICF_SEND',t);
  p.replySilently(answer(t.id));
  const observed=await p.message('AICF_RESUME',t);
  assert.match(observed.task.message,/已找到完整任务标记/);
  await p.tick(5000);
  assert.equal(p.task.status,'complete');
  assert.equal(p.task.result.revised_prompt,'新提示词');
});

test('cancel stops collection; timeout never resends', async () => {
  const p = page(); const t = task('timeout');
  t.created_at = new Date(p.now).toISOString();
  await p.message('AICF_SEND', t); await p.tick(899000);
  assert.equal(p.task.status, 'waiting');
  await p.tick(1000);
  assert.equal(p.task.status, 'timeout'); assert.equal(p.clicks, 1);
  await p.message('AICF_CANCEL', t); p.reply(answer('timeout')); await p.tick(5000);
  assert.equal(p.task.status, 'timeout'); assert.equal(p.clicks, 1);
});

test('manifest is restricted to two providers and local extension assets exist', () => {
  const base = path.join(__dirname, '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(base, 'manifest.json')));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, ['https://chatgpt.com/*', 'https://chat.deepseek.com/*', 'https://*.doubao.com/*', 'http://127.0.0.1/*', 'http://localhost/*']);
  assert.ok(!manifest.permissions.includes('cookies'));
  for (const file of [manifest.side_panel.default_path, manifest.background.service_worker, 'panel.css', 'panel.js', 'protocol.js', 'content.js']) assert.ok(fs.existsSync(path.join(base, file)));
});

test('DeepSeek icon-only composer submits with Enter and confirms clearing', async () => {
  const p = page('', {deepseek: true});
  const result = await p.message('AICF_SEND', task('deepseek'));
  assert.equal(result.ok, true);
  assert.equal(p.clicks, 1);
  assert.equal(p.task.status, 'waiting');
  assert.equal(p.editor.value, '');
});

test('DeepSeek send returns without waiting on throttled background timers', async () => {
 const p=page('',{deepseek:true,throttleAfterSend:true,
   conversationAfterSend:'https://chat.deepseek.com/a/chat/s/new-background-route'});
 const t={...task('deepseek-fast-send'),force_new_conversation:true};
 const response=await Promise.race([
   p.message('AICF_SEND',t),
   new Promise((_,reject)=>setTimeout(()=>reject(new Error('send waited for throttled timers')),100))
 ]);
 assert.equal(response.ok,true);
 assert.equal(p.task.status,'waiting');
 assert.equal(p.task.conversation_id,'new-background-route');
 assert.equal(p.clicks,1);
});

test('ineffective submit and reading an unsent draft never claim it was sent', async () => {
  const p = page('', {accept: false}); const t = task('unsent');
  await p.message('AICF_SEND', t);
  assert.equal(p.task.status, 'prepared');
  assert.match(p.task.message, /尚未接受提交/);
  await p.message('AICF_READ', t);
  assert.equal(p.task.status, 'prepared');
  assert.match(p.task.message, /尚未发送/);
  assert.equal((await p.message('AICF_SEND', t)).ok, false);
  assert.equal(p.clicks, 1);
});

test('unsent task resumes after manual send and collects generic JSON without resending', async () => {
 const p=page('',{accept:false}); const t={...task('manual-json'),kind:'json'};
 await p.message('AICF_SEND',t); assert.equal(p.task.status,'prepared');
 p.editor.value='';
 const m=P.markers(t.id); p.reply(`${m.start}\n${JSON.stringify({request_id:t.id,data:{story:'story result'}})}\n${m.end}`);
 await p.tick(2000); await p.message('AICF_READ',t); await p.tick(5000);
 assert.equal(p.task.status,'complete'); assert.equal(p.task.result.data.story,'story result');
 assert.equal(p.clicks,1);
});

test('conversation policy isolates independent jobs and caps consecutive revisions', () => {
 const t={id:'next',entry:'revision',conversation_key:'project:segment1',request:'request'};
 const old={taskId:'previous',key:t.conversation_key,rounds:2,chars:200,url:'url'};
 assert.equal(P.conversationPlan({},t,old,'url').fresh,true);
 assert.equal(P.conversationPlan({mode:'smart'},t,old,'url').fresh,false);
 assert.equal(P.conversationPlan({mode:'reuse'},{...t,force_new_conversation:true},old,'url').fresh,true);
 assert.equal(P.conversationPlan({mode:'smart',maxRounds:2},t,old,'url').fresh,true);
 assert.equal(P.conversationPlan({mode:'smart',maxChars:205},t,old,'url').fresh,true);
 assert.equal(P.conversationPlan({mode:'smart'},t,old,'different chat').fresh,true);
 assert.equal(P.conversationPlan({mode:'smart'},{...t,conversation_key:'other'},old,'url').fresh,true);
 assert.equal(P.conversationPlan({mode:'new',overrides:{revision:'reuse'}},t,old,'url').fresh,false);
 assert.equal(P.conversationPlan({mode:'new'},{...t,id:old.taskId},old,'url').fresh,false);
});

test('starts fresh inside the bound tab once before filling and never during reads', async () => {
 const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/old',newChat:true});
 const t=task('fresh');
 assert.equal((await p.message('AICF_FILL',t)).ok,true); assert.equal(p.newChats,1);
 assert.equal((await p.message('AICF_SEND',t)).ok,true); assert.equal(p.newChats,1);
 await p.message('AICF_READ',t); assert.equal(p.newChats,1);
});

test('Doubao starts a new conversation before text submission', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/old',newChat:true,nested:true});
 const t=task('doubao-fresh');
 const result=await p.message('AICF_SEND',t);
 assert.equal(result.ok,true,result.error);
 assert.equal(p.newChats,1); assert.equal(p.clicks,1);
 assert.equal(p.task.status,'waiting');
});

test('Doubao chooses the unique sidebar new-chat text action when duplicate controls exist', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/old',doubaoDuplicateNewChat:true});
 const result=await p.message('AICF_SEND',task('doubao-duplicate-new-chat'));
 assert.equal(result.ok,true,result.error);
 assert.equal(p.newChats,1); assert.equal(p.clicks,1);
 assert.equal(p.task.status,'waiting');
});

test('Doubao chooses left sidebar text even without semantic navigation landmarks', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/old',doubaoTextOnlyNewChat:true});
 const result=await p.message('AICF_SEND',task('doubao-text-only-new-chat'));
 assert.equal(result.ok,true,result.error);
 assert.equal(p.newChats,1); assert.equal(p.clicks,1);
});

test('Doubao ignores a recent conversation also titled 新对话', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/old',doubaoRecentTitleDuplicate:true});
 const result=await p.message('AICF_SEND',task('doubao-recent-title-duplicate'));
 assert.equal(result.ok,true,result.error);
 assert.equal(p.newChats,1); assert.equal(p.clicks,1);
});

test('reply collection stays bound to the conversation that accepted the task', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat',newChat:true,conversationAfterSend:'https://www.doubao.com/chat/task-conversation'});
 const t=task('bound-conversation');
 await p.message('AICF_SEND',t);
 assert.equal(p.task.conversation_id,'task-conversation');
 p.navigate('https://www.doubao.com/chat/other-conversation');
 p.reply(answer(t.id));
 await p.message('AICF_READ',p.task);
 assert.equal(p.task.status,'waiting');
 assert.match(p.task.message,/任务绑定的对话/);
 assert.equal(p.task.result,undefined);
});

test('Doubao binds a late-created conversation URL by this task marker and reads its completed reply', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat',newChat:true});
 const t=task('doubao-late-conversation-id');
 await p.message('AICF_SEND',t);
 assert.equal(p.task.conversation_id,'');
 p.navigate('https://www.doubao.com/chat/conversation-created-late');
 p.reply(answer(t.id));
 await p.message('AICF_READ',p.task);
 await p.tick(5000);
 assert.equal(p.task.conversation_id,'conversation-created-late');
 assert.equal(p.task.status,'complete');
 assert.equal(p.task.result.revised_prompt,'新提示词');
});

test('Doubao corrects a provisional conversation ID when a new-chat send settles on its real ID', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/real-conversation'});
 const t={...task('route-settled'),status:'waiting',dispatch_url:'https://www.doubao.com/chat',
   conversation_url:'https://www.doubao.com/chat/provisional',conversation_id:'provisional'};
 p.saveTask(t); p.reply(t.request.replace(/\s+/g,' ').trim().slice(0,120)+'\n'+answer(t.id));
 await p.message('AICF_READ',t); await p.tick(5000);
 assert.equal(p.task.conversation_id,'real-conversation');
 assert.equal(p.task.status,'complete');
});

test('Doubao replaces its local_ conversation ID from the matching reply without needing expanded prompt text', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/38444851520295170'});
 const t={...task('local-route-settled'),status:'waiting',sent_at:new Date(p.now).toISOString(),
   dispatch_url:'https://www.doubao.com/chat',
   conversation_url:'https://www.doubao.com/chat/local_8156253421219812',
   conversation_id:'local_8156253421219812'};
 p.saveTask(t); p.reply(answer(t.id));
 await p.message('AICF_READ',t); await p.tick(5000);
 assert.equal(p.task.conversation_id,'38444851520295170');
 assert.equal(p.task.status,'complete');
});

test('a local_ conversation does not adopt an unrelated reply', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/38444851520295170'});
 const t={...task('local-route-guard'),status:'waiting',sent_at:new Date(p.now).toISOString(),
   dispatch_url:'https://www.doubao.com/chat',
   conversation_url:'https://www.doubao.com/chat/local_8156253421219812',
   conversation_id:'local_8156253421219812'};
 p.saveTask(t); p.reply(answer('another-task'));
 await p.message('AICF_READ',t);
 assert.equal(p.task.conversation_id,'local_8156253421219812');
 assert.equal(p.task.status,'waiting');
});

test('a local_ binding from an existing conversation cannot move on a reply marker alone', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/other-conversation'});
 const t={...task('local-route-existing'),status:'waiting',sent_at:new Date(p.now).toISOString(),
   dispatch_url:'https://www.doubao.com/chat/old-conversation',
   conversation_url:'https://www.doubao.com/chat/local_8156253421219812',
   conversation_id:'local_8156253421219812'};
 p.saveTask(t); p.reply(answer(t.id));
 await p.message('AICF_READ',t);
 assert.equal(p.task.conversation_id,'local_8156253421219812');
 assert.equal(p.task.status,'waiting');
});

test('Doubao renames only the conversation bound to the submitted task ID', async () => {
 const p=page('',{doubao:true,newChat:true,renameChat:true,
   conversationAfterSend:'https://www.doubao.com/chat/rename-target'});
 const t=task('submitted-task-id');
 assert.equal((await p.message('AICF_SEND',t)).ok,true);
 await p.message('AICF_RESUME',p.task);
 assert.equal(p.renamedTo,t.id);
 assert.equal(p.task.conversation_renamed_id,'rename-target');
 assert.equal(p.task.status,'waiting');
 p.reply(answer(t.id)); await p.tick(5000);
 assert.equal(p.task.status,'complete');
});

test('Doubao can rename the open conversation through its header menu when history rows have no links', async () => {
 const p=page('',{doubao:true,newChat:true,headerRename:true,
   conversationAfterSend:'https://www.doubao.com/chat/header-target'});
 const t=task('header-task-id');
 assert.equal((await p.message('AICF_SEND',t)).ok,true);
 await p.message('AICF_RESUME',p.task);
 assert.equal(p.renamedTo,t.id);
 assert.equal(p.task.conversation_renamed_id,'header-target');
});

test('missing new-chat control stops before filling or sending in the old conversation', async () => {
 const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/old'});
 const response=await p.message('AICF_SEND',task('missing-control'));
 assert.equal(response.ok,false); assert.match(response.error,/新对话/);
 assert.equal(p.editor.value,''); assert.equal(p.clicks,0);
});

test('ChatGPT selects the clickable navigation entry and deduplicates its children', async () => {
 const p=page('',{url:'https://chatgpt.com/c/old',chatgptNewChat:true});
 const result=await p.message('AICF_SEND',task('chatgpt-nav'));
 assert.equal(result.ok,true,result.error); assert.equal(p.newChats,1); assert.equal(p.clicks,1);
});

test('ChatGPT ignores plain text and message buttons and rejects ambiguous navigation', async () => {
 for (const extra of [{onlyFalseMatches:true},{duplicateNavigation:true,differentDestination:true}]) {
   const p=page('',{url:'https://chatgpt.com/c/old',chatgptNewChat:true,...extra});
   const result=await p.message('AICF_SEND',task('chatgpt-invalid-nav'));
   assert.equal(result.ok,false); assert.match(result.error,/新对话/);
   assert.equal(p.newChats,0); assert.equal(p.clicks,0); assert.equal(p.editor.value,'');
 }
});

test('ChatGPT clicks upper-left equivalent link and excludes offscreen or covered entries', async () => {
 for (const extra of [{},{offscreenNavigation:true},{coveredNavigation:true}]) {
   const p=page('',{url:'https://chatgpt.com/c/old',chatgptNewChat:true,duplicateNavigation:true,...extra});
   const result=await p.message('AICF_SEND',task('chatgpt-duplicate-nav'));
   assert.equal(result.ok,true,result.error); assert.equal(p.newChats,1); assert.equal(p.clicks,1);
 }
});

test('empty editor in a different conversation never proves the current request was sent', async () => {
 const p=page(); const t=task('not-in-this-chat');
 const response=await p.message('AICF_READ',t);
 assert.equal(response.ok,true);assert.equal(p.task.status,'prepared');
 assert.match(p.task.message,/未找到本次任务编号/);assert.equal(p.clicks,0);
 p.reply(answer(t.id)); await p.message('AICF_READ',t); await p.tick(5000);
 assert.equal(p.task.status,'complete');assert.equal(p.clicks,0);
});

test('automatic recovery fills and submits an unsent job after a new-chat reload', async () => {
 const p=page('',{deepseek:true}); const t={...task('recover-empty'),status:'prepared'};
 p.saveTask(t); await p.message('AICF_RESUME',t);
 assert.equal(p.clicks,1); assert.equal(p.task.status,'waiting');
 await p.message('AICF_RESUME',p.task); assert.equal(p.clicks,1);
});

test('legacy READ timestamp on a blank new chat does not prevent automatic delivery', async () => {
 const p=page(); const t={...task('legacy-read'),status:'waiting',sent_at:new Date().toISOString()};
 p.saveTask(t); await p.message('AICF_RESUME',t);
 assert.equal(p.clicks,1);assert.equal(p.task.dispatch_count,1);
});

test('automatic recovery reads an existing answer without resending', async () => {
 const p=page(); const t={...task('already-answered'),status:'prepared'};
 p.saveTask(t);p.reply(answer(t.id)); await p.message('AICF_RESUME',t); await p.tick(5000);
 assert.equal(p.clicks,0);assert.equal(p.task.status,'complete');
});

test('recovery retries unchanged drafts with backoff and stops after three attempts', async () => {
 const p=page('',{accept:false}); const t={...task('retry-draft'),status:'prepared'};
 p.saveTask(t); await p.message('AICF_RESUME',t);assert.equal(p.clicks,1);
 await p.message('AICF_RESUME',p.task);assert.equal(p.clicks,1);
 await p.tick(11000);await p.message('AICF_RESUME',p.task);assert.equal(p.clicks,2);
 await p.tick(11000);await p.message('AICF_RESUME',p.task);assert.equal(p.clicks,3);
 await p.tick(11000);await p.message('AICF_RESUME',p.task);assert.equal(p.task.status,'error');
 assert.equal(p.clicks,3);
});

test('uncertain dispatch on an empty page does not automatically duplicate a send', async () => {
 const p=page(); const t={...task('uncertain'),status:'sending',dispatch_attempted_at:Date.now()-40000,dispatch_count:1,dispatch_url:'https://chatgpt.com/'};
 p.saveTask(t);await p.message('AICF_RESUME',t);
 assert.equal(p.clicks,0);assert.equal(p.task.status,'waiting');
});

test('DeepSeek hidden request waits past 30 seconds and accepts later reply without resend', async () => {
 const p=page('',{deepseek:true,noReplyClasses:true});
 const t={...task('deepseek-late'),created_at:new Date(p.now).toISOString(),
   status:'waiting',sent_at:new Date(p.now).toISOString(),
   dispatch_attempted_at:p.now,dispatch_url:'https://chat.deepseek.com/',dispatch_count:1};
 p.saveTask(t);
 await p.tick(32000); await p.message('AICF_RESUME',t);
 assert.equal(p.task.status,'waiting'); assert.equal(p.clicks,0);
 p.reply(answer(t.id)); await p.tick(2000); await p.tick(5000);
 assert.equal(p.task.status,'complete'); assert.equal(p.clicks,0);
});

test('DeepSeek reads an unbound new-chat route after send response is lost', async () => {
 for (const mode of ['AICF_READ','AICF_RESUME']) {
   const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/s/new-route',noReplyClasses:true});
   const t={...task('deepseek-unbound-route'),status:'waiting',force_new_conversation:true,
     created_at:new Date(p.now).toISOString(),sent_at:new Date(p.now).toISOString(),
     dispatch_attempted_at:p.now,dispatch_count:1,dispatch_url:'https://chat.deepseek.com/'};
   p.saveTask(t);
   await p.message(mode,t);
   assert.equal(p.task.status,'waiting');
   assert.equal(p.clicks,0);
   p.reply(answer(t.id)); await p.tick(5000);
   assert.equal(p.task.status,'complete');
   assert.equal(p.task.result.revised_prompt,'新提示词');
   assert.equal(p.clicks,0);
 }
});

test('a task does not read a matching marker from a different conversation', async () => {
 const p=page('',{doubao:true,url:'https://www.doubao.com/chat/other'});
 const t={...task('wrong-conversation'),status:'waiting',dispatch_url:'https://www.doubao.com/chat/task-original',
   conversation_url:'https://www.doubao.com/chat/task-original',conversation_id:'task-original'};
 p.saveTask(t);p.reply(answer(t.id));
 await p.message('AICF_READ',t);
 assert.equal(p.task.status,'waiting');
 assert.match(p.task.message,/任务绑定的对话/);
 assert.equal(p.task.result,undefined);
});

test('DeepSeek follows a post-submit route change only with the submitted request and task marker', async () => {
 const p=page('',{deepseek:true,conversationAfterSend:'https://chat.deepseek.com/a/chat/s/provisional'});
 const t={...task('deepseek-route-settled'),force_new_conversation:true};
 await p.message('AICF_SEND',t);
 assert.equal(p.task.conversation_id,'provisional');
 p.settleRoute('https://chat.deepseek.com/a/chat/s/final');
 p.reply(answer(t.id)); await p.tick(5000);
 assert.equal(p.task.status,'complete');
 assert.equal(p.task.conversation_id,'final');
 assert.equal(p.clicks,1);
});

test('ChatGPT follows its settled conversation route while retaining the task request', async () => {
 const p=page('',{conversationAfterSend:'https://chatgpt.com/c/provisional'});
 const t={...task('chatgpt-route-settled'),force_new_conversation:true};
 await p.message('AICF_SEND',t);
 p.settleRoute('https://chatgpt.com/g/example/c/final');
 p.reply(answer(t.id));await p.tick(5000);
 assert.equal(p.task.status,'complete');
 assert.equal(p.task.conversation_id,'final');
 assert.equal(p.clicks,1);
});

test('DeepSeek does not follow a foreign route with only a copied task marker', async () => {
 const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/s/foreign'});
 const t={...task('deepseek-foreign'),status:'waiting',force_new_conversation:true,
   sent_at:new Date(p.now).toISOString(),dispatch_url:'https://chat.deepseek.com/',
   conversation_url:'https://chat.deepseek.com/a/chat/s/original',conversation_id:'original'};
 p.saveTask(t);p.reply(answer(t.id));
 await p.message('AICF_READ',t);
 assert.equal(p.task.status,'waiting');
 assert.equal(p.task.conversation_id,'original');
 assert.equal(p.task.result,undefined);
});

test('DeepSeek does not treat a copied request template as its own reply', async () => {
 const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/s/foreign'});
 const t={...task('deepseek-request-only'),status:'waiting',force_new_conversation:true,
   sent_at:new Date(p.now).toISOString(),dispatch_url:'https://chat.deepseek.com/',
   conversation_url:'https://chat.deepseek.com/a/chat/s/original',conversation_id:'original'};
 p.saveTask(t);p.reply(t.request);
 await p.message('AICF_READ',t);
 assert.equal(p.task.status,'waiting');
 assert.equal(p.task.conversation_id,'original');
});

test('uncertain submission still stops at the overall 900 second deadline', async () => {
 const p=page('',{deepseek:true});
 const t={...task('deepseek-deadline'),created_at:new Date(p.now).toISOString(),status:'waiting',
   dispatch_attempted_at:p.now,dispatch_url:'https://chat.deepseek.com/old'};
 p.saveTask(t); await p.tick(32000); await p.message('AICF_RESUME',t);
 await p.tick(868000); assert.equal(p.task.status,'timeout'); assert.equal(p.clicks,0);
});

test('DeepSeek nested div/span new-chat text is clicked once without requiring button role', async () => {
 const p=page('',{deepseek:true,url:'https://chat.deepseek.com/a/chat/old',newChat:true,nested:true});
 const t={...task('nested-control'),status:'prepared'};p.saveTask(t);
 const result=await p.message('AICF_RESUME',t);
 assert.equal(result.ok,true);assert.equal(p.newChats,1);assert.equal(p.clicks,1);
 assert.equal(p.task.status,'waiting');
});

test('ChatGPT icon-only submit falls back to Enter and confirms submission', async () => {
 const p=page('',{noButton:true}); const t={...task('chatgpt-enter'),status:'prepared'};
 p.saveTask(t);await p.message('AICF_RESUME',t);
 assert.equal(p.clicks,1);assert.equal(p.editor.value,'');assert.equal(p.task.status,'waiting');
 await p.message('AICF_RESUME',p.task);assert.equal(p.clicks,1);
});

test('ChatGPT editor without legacy prompt-textarea ID can be found and sent', async () => {
 const p=page('',{modernEditor:true});const t={...task('modern-editor'),status:'prepared'};
 p.saveTask(t);const response=await p.message('AICF_RESUME',t);
 assert.equal(response.ok,true);assert.equal(p.clicks,1);assert.equal(p.task.status,'waiting');
});

test('multiple modern editors prevent filling or submitting to the wrong composer', async () => {
 const p=page('',{modernEditor:true,ambiguous:true});
 const response=await p.message('AICF_SEND',task('ambiguous-editor'));
 assert.equal(response.ok,false);assert.match(response.error,/找到 2 个/);
 assert.equal(p.editor.value,'');assert.equal(p.clicks,0);
});

test('UUID in a rich editor with different paragraph spacing is not a sent message', async () => {
 const t={...task('draft-body-uuid'),status:'waiting',sent_at:new Date().toISOString()};
 const p=page(t.request.replace(/\n/g,'\n\n'));p.saveTask(t);
 const response=await p.message('AICF_RESUME',t);
 assert.equal(response.ok,true);assert.equal(p.clicks,1);assert.equal(p.task.dispatch_count,1);
 await p.message('AICF_RESUME',p.task);assert.equal(p.clicks,1);
});

for (const options of [{deepseek:true},{doubao:true,newChat:true,doubaoIconSend:true}]) test('complete envelope finishes promptly despite changing surrounding text '+JSON.stringify(options),async()=>{
 const p=page('',options), t=task('fast-envelope');
 await p.message('AICF_SEND',t);
 p.reply(answer(t.id)+' tail one');await p.tick(200);
 assert.equal(p.task.status,'waiting');
 p.reply(answer(t.id)+' tail two');await p.tick(400);
 assert.equal(p.task.status,'complete');
});
test('changed complete envelope requires a new short verification',async()=>{
 const p=page(),t=task('changed-envelope');await p.message('AICF_SEND',t);
 p.reply(answer(t.id));await p.tick(200);
 p.reply(answer(t.id,{revised_prompt:'updated'}));await p.tick(200);
 assert.equal(p.task.status,'waiting');await p.tick(400);
 assert.equal(p.task.result.revised_prompt,'updated');
});
