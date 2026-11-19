const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const P = require('../protocol.js');
const TextProviderPolicy=require('../text_provider_policy.js');
const TextTaskStore=require('../text_task_store.js');
const TextScheduler=require('../text_scheduler.js');
const FocusCoordinator=require('../focus_coordinator.js');

function fixture(extraRemotes = [], sendBarrier = null, options = {}) {
  let listener, sends = 0, reads = 0, claims = 0, offscreen = false, offscreenCreates = 0;
  let schedulerNow=Date.now(),nextTabId=21;
  const activeTabUpdates = [];
  const reports = [], debuggerCalls = [];
  const browserTabs = [{id:7,url:'https://chat.deepseek.com/',active:false,windowId:1}];
  const remote = { id: 'server-task', provider: 'deepseek', kind: 'revision', request: 'request', input: {}, created_at: Date.now()/1000, lease: 'lease', status: 'queued' };
  const remoteTasks = [remote, ...extraRemotes];
  const claimedIds = new Set();
  const store = { backend: {enabled: true, url: 'http://127.0.0.1:7860', token: 'paired', tabs: {deepseek: 7}}, clientId: 'client',textOwnedTabs:{deepseek:[7]} };
  const taskState = (id='server-task') => store[`bridgeTask:${id}`] || store.task;
  const writeTask = value => {store[value?.bridge ? `bridgeTask:${value.id}` : 'task']=value;};
  const event = { addListener() {} };
  const activationListeners=[];
  const testPolicies=options.disabledQianwen ? {...TextProviderPolicy,policy:id=>{
    const value=TextProviderPolicy.policy(id);return value ? {...value,available:id==='qianwen' ? false : value.available} : null;
  }} : TextProviderPolicy;
  const context = {
    PromptBridge: P,TextProviderPolicy:testPolicies,TextTaskStore,FocusCoordinator,
    TextScheduler:{createScheduler:options=>TextScheduler.createScheduler({...options,now:()=>schedulerNow})},
    importScripts() {}, videoPumpFromStorage:async()=>{}, URL, Date,
    navigator:{userAgent:'Chrome/130'},videoCatalog:async()=>[{id:'doubao_web'},{id:'runninghub_web'}],
    setTimeout:(fn,ms,...args)=>setTimeout(fn,ms===10000?5:ms===60000?50:ms,...args), clearTimeout, queueMicrotask,
    crypto: require('node:crypto').webcrypto, AbortSignal,
    fetch: async (url, opts) => {
      assert.equal(opts.headers.Authorization, 'Bearer paired');
      if(url.endsWith('/ping')) {store.lastPing=JSON.parse(opts.body);return {ok:true,json:async()=>({ok:true,...(store.poolLimits?{
        limits:store.poolLimits,queued:Object.fromEntries(Object.keys(store.poolLimits).filter(id=>id!=='total').map(id=>
          [id,remoteTasks.filter(task=>task.provider===id && task.status==='queued' && !claimedIds.has(task.id)).length]))
      }:{}),text_enabled:store.textEnabled !== false})};}
      if(url.endsWith('/claim')) {
        const attempt=++claims;
        const request=JSON.parse(opts.body);
        (store.claimRequests ||= []).push(request);
        const candidate=extraRemotes.length
          ? remoteTasks.find(task=>task.status==='queued' && request.providers.includes(task.provider) && !claimedIds.has(task.id))
          : attempt===1 ? remote : null;
        if(candidate)claimedIds.add(candidate.id);
        return {ok:true,json:async()=>({ok:true,task:candidate ? {...candidate} : null})};
      }
      const selected=remoteTasks.find(task=>url.endsWith('/'+task.id)) || remote;
      if(opts.method==='GET' && store.goneTask===selected.id)return {ok:false,status:410,json:async()=>({ok:false,code:'TASK_GONE',message:'任务已清理'})};
      if(opts.method === 'POST') {
        const report=JSON.parse(opts.body);reports.push(report);
        if(report.status==='complete' && store.failCompleteReport)return {ok:false,status:503,json:async()=>({ok:false,message:'backend unavailable'})};
        selected.status=report.status;
      }
      return {ok:true,json:async()=>({ok:true,task:{...selected}})};
    },
    chrome: {
      offscreen: {hasDocument:async()=>offscreen, createDocument:async options=>{
        assert.equal(options.url,'poller.html'); assert.deepEqual(Array.from(options.reasons),['WORKERS']);
        offscreen=true; offscreenCreates++;
      },closeDocument:async()=>{offscreen=false;}},
      runtime: {id:'ext',getManifest:()=>({version:'0.4.59'}),onInstalled:event,onStartup:event,onMessage:{addListener(fn){listener=fn;}}},
      debugger: {attach:async(target,version)=>debuggerCalls.push({action:'attach',target,version}),
        sendCommand:async(target,method,params)=>debuggerCalls.push({action:'command',target,method,params}),
        detach:async target=>debuggerCalls.push({action:'detach',target})},
      alarms: {create:async()=>{},onAlarm:event}, sidePanel:{setPanelBehavior:async()=>{}},
      windows:{getLastFocused:async()=>({id:1,focused:true}),onFocusChanged:event},
      storage: {onChanged:event,local:{get:async()=>structuredClone(store),set:async data=>{
        if(Object.keys(data).some(key=>key.startsWith('bridgeTask:')) && store.failTaskSave) throw new Error('storage quota exceeded');
        Object.assign(store,structuredClone(data));
      },remove:async keys=>{for(const key of Array.isArray(keys)?keys:[keys])delete store[key];}},session:{get:async key=>{
        const seeded=Object.fromEntries(Object.entries(store.textOwnedTabs || {}).flatMap(([providerId,ids])=>
          ids.map(id=>[`textOwnedTab:${id}`,{providerId,windowId:1}])));
        const values={...seeded,...(store.__session || {})};
        return key===null?structuredClone(values):{[key]:values[key]};
      },set:async data=>{store.__session={...(store.__session || {}),...structuredClone(data)};},
        remove:async keys=>{for(const key of Array.isArray(keys)?keys:[keys])delete store.__session?.[key];}}},
      tabs: {remove:async id=>{
        if(store.failTabClose){store.failTabClose--;throw new Error('close temporarily failed');}
        assert.ok(remoteTasks.some(task=>task.status==='complete'),'close only after backend acknowledgement');
        const index=browserTabs.findIndex(tab=>tab.id===id);
        if(index>=0)browserTabs.splice(index,1);
        (store.closedTabs ||= []).push(id);
      },onActivated:{addListener:fn=>activationListeners.push(fn)},create:async options=>{
        assert.equal(options.active,false);
        const tab={...options,id:nextTabId++,status:'complete',windowId:options.windowId || 1};browserTabs.push(tab);return {...tab};
      },query:async query=>query?.active?browserTabs.filter(tab=>tab.active&&(!Number.isInteger(query.windowId)||tab.windowId===query.windowId)):browserTabs.filter(tab=>PromptBridge.provider(tab.url)),get:async id=>{
        const tab=browserTabs.find(item=>item.id===id);if(!tab)throw new Error('No tab');return {...tab};
      },update:async(id,changes)=>{
        const tab=browserTabs.find(item=>item.id===id);if(!tab)throw new Error('No tab');
        if(changes.url){
          store.navigationAttempts=(store.navigationAttempts || 0)+1;
          if(store.failNavigation)throw new Error('navigation failed');
        }
        if(Object.hasOwn(changes,'active'))activeTabUpdates.push({id,active:changes.active});
        if(changes.active){for(const sibling of browserTabs)if(sibling.windowId===tab.windowId)sibling.active=false;tab.active=true;tab.frozen=false;
          for(const fn of activationListeners)fn({tabId:id,windowId:tab.windowId});}
        Object.assign(tab,changes);return {...tab};
      },sendMessage:async(id,message)=>{
        if(store.doubaoRespondOnlyWhenActive&&PromptBridge.provider(browserTabs.find(tab=>tab.id===id)?.url)==='豆包'&&!browserTabs.find(tab=>tab.id===id).active)return new Promise(()=>{});
        if(store.doubaoRespondOnlyWhenActive&&message.type==='AICF_RESUME'){
          reads++;sends++;writeTask({...taskState(),status:'waiting'});return {ok:true,task:structuredClone(taskState())};
        }
        if(store.assertActiveDuringSend&&message.type==='AICF_SEND')assert.equal(browserTabs.find(tab=>tab.id===id).active,true);
        if(store.operationTimeout) {store.operationTimeout=false; const error=new Error('网页操作超时'); error.code='PAGE_RESPONSE_TIMEOUT'; throw error;}
        if(message.type==='AICF_SLOT_STATUS')return {ok:true,task:{idle:!store.slotBusy}};
        if(message.type==='AICF_OPERATION_STATUS')return {ok:true,task:{...structuredClone(taskState(message.task.id)),busy:store.pageBusy===true}};
        if(message.type==='AICF_READ') {reads++; if(store.completeOnRead)writeTask({...taskState(message.task.id),status:'complete',result:{data:{story:'ready'}},raw:'ready'}); return {ok:true,task:structuredClone(taskState(message.task.id))};}
        if(message.type==='AICF_SEND'){
          if(sendBarrier)await sendBarrier(id);
          if(store.editorNotReady){store.editorNotReady=false;throw new Error('输入框尚未就绪');}
          sends++;writeTask({...message.task,status:'waiting',sent_at:new Date().toISOString()});
        }
        if(message.type==='AICF_RESUME') {
          reads++;
          if(store.completeOnRead) {
            writeTask({...taskState(message.task.id),status:'complete',result:{data:{story:'ready'}}});
            return {ok:true,task:structuredClone(taskState(message.task.id))};
          }
        }
        return {ok:true};
      }}, scripting:{executeScript:async({target,func})=>[{
        documentId:'document-'+target.tabId,...(func ? {result:store.inputFocusValid!==false} : {})}]}
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../worker.js'),'utf8'),context);
  const rawPoll=()=>new Promise(resolve=>listener({type:'AICF_BACKEND_POLL'},{id:'ext'},resolve));
  return {store,reports,remote,browserTabs,debuggerCalls,activeTabUpdates,saveTask:task=>context.saveBridgeTask(task),cleanup:id=>context.closeCompletedTextTab(store['bridgeTask:'+id]),get task(){return taskState();},get claims(){return claims;},get offscreen(){return offscreen;},get offscreenCreates(){return offscreenCreates;},get sends(){return sends;},get reads(){return reads;},rawPoll,
    poll:async()=>{schedulerNow+=3000;await rawPoll();await new Promise(resolve=>setTimeout(resolve,20));},
    qianwenInput:(message,sender)=>new Promise(resolve=>listener({type:'AICF_QIANWEN_INPUT',...message},sender,resolve)),
    trustedClick:(message,sender={id:'ext',tab:{id:7},url:'https://chat.deepseek.com/a/chat/test',frameId:0})=>new Promise(resolve=>listener({type:'AICF_TRUSTED_CONTINUE_CLICK',...message},sender,resolve))};
}

test('secondary browser registers capabilities without claiming text or opening task tabs',async()=>{
 const f=fixture();f.store.textEnabled=false;
 await f.poll();
 assert.equal(f.claims,0);
 assert.equal(f.sends,0);
 assert.equal(f.browserTabs.length,1);
 assert.equal(f.store.lastPing.browser.type,'Chrome');
 assert.deepEqual(Array.from(f.store.lastPing.browser.channels),['doubao_web','runninghub_web']);
 assert.match(f.store.bridgeStatus.message,/主浏览器/);
});

test('Qianwen trusted input inserts only stored text without clicking, Enter or switching tabs',async()=>{
 const f=fixture();
 f.browserTabs.push({id:88,url:'https://www.qianwen.com/',active:false,windowId:1});
 const task={id:'q-input',provider:'千问',tab_id:88,status:'prepared',request:'line one\nline two',
   binding_epoch:2,document_id:'document-88',bridge:{reported:false}};
 f.store['bridgeTask:q-input']=task;
 const sender={id:'ext',tab:{id:88},frameId:0,documentId:'document-88',url:'https://www.qianwen.com/'};
 const message={taskId:task.id,bindingEpoch:2,purpose:'request',text:'untrusted alternate text'};
 assert.equal((await f.qianwenInput(message,sender)).ok,true);
 const commands=f.debuggerCalls.filter(call=>call.action==='command');
 assert.deepEqual(commands.map(call=>call.method),['Emulation.setFocusEmulationEnabled',
   'Input.dispatchKeyEvent','Input.dispatchKeyEvent','Input.insertText','Emulation.setFocusEmulationEnabled']);
 assert.equal(commands.find(call=>call.method==='Input.insertText').params.text,task.request);
 assert.equal(f.debuggerCalls.at(-1).action,'detach');
 assert.deepEqual(f.activeTabUpdates,[]);
});

test('Qianwen trusted input rejects changed page, stale binding, ended task and wrong focus',async()=>{
 const f=fixture();
 f.browserTabs.push({id:88,url:'https://www.qianwen.com/',active:false,windowId:1});
 const task={id:'q-input',provider:'千问',tab_id:88,status:'prepared',request:'stored text',binding_epoch:2,
   document_id:'document-88',bridge:{reported:false}};
 f.store['bridgeTask:q-input']=task;
 const sender={id:'ext',tab:{id:88},frameId:0,documentId:'document-88',url:'https://www.qianwen.com/'};
 const message={taskId:task.id,bindingEpoch:2,purpose:'request'};
 for(const altered of [{documentId:'old'},{frameId:1},{id:'other'},{url:'https://evil.test/'}])
   assert.equal((await f.qianwenInput(message,{...sender,...altered})).ok,false);
 task.status='complete';assert.equal((await f.qianwenInput(message,sender)).ok,false);
 task.status='prepared';task.sent_at='already sent';assert.equal((await f.qianwenInput(message,sender)).ok,false);
 delete task.sent_at;f.store.inputFocusValid=false;
 assert.equal((await f.qianwenInput(message,sender)).ok,false);
 assert.equal(f.debuggerCalls.some(call=>call.method==='Input.insertText'),false);
 assert.equal(f.debuggerCalls.at(-1).action,'detach');
 f.store.inputFocusValid=true;task.url='https://www.qianwen.com/original';
 assert.equal((await f.qianwenInput(message,sender)).ok,false);
 assert.equal(f.debuggerCalls.some(call=>call.method==='Input.insertText'),false);
});

test('explicitly disabled Qianwen is neither advertised nor given a task page',async()=>{
 const f=fixture([],null,{disabledQianwen:true});await f.poll();
 assert.equal(f.store.lastPing.providers.includes('qianwen'),false);
 assert.equal(f.store.lastPing.provider_capabilities.qianwen,undefined);
 assert.equal(f.browserTabs.some(tab=>P.provider(tab.url)==='千问'),false);
});

test('oversized new and recovered unsent Qianwen jobs report error rather than waiting indefinitely',async()=>{
 for(const recovered of [false,true]) {
   const remote={id:'q-long',provider:'qianwen',kind:'text',request:'字'.repeat(13248),input:{},
     created_at:Date.now()/1000,lease:'q-lease',status:recovered?'waiting':'queued'};
   const f=fixture([remote]);
   f.store.poolLimits={total:3,deepseek:1,chatgpt:1,doubao:1,qianwen:1};
   if(recovered) {
     f.browserTabs.push({id:88,url:'https://www.qianwen.com/',active:false,windowId:1});
     f.store['bridgeTask:q-long']={...remote,provider:'千问',tab_id:88,status:'prepared',
       bridge:{url:'http://127.0.0.1:7860',lease:'q-lease',reported:false}};
   }
   await f.poll();
   assert.equal(remote.status,'error');
   assert.equal(f.store['bridgeTask:q-long'].bridge.reported,true);
   assert.match(f.store['bridgeTask:q-long'].message,/PROVIDER_INPUT_LIMIT:.*10000.*13248/);
   assert.equal(f.sends,1); // Only the unrelated DeepSeek job was sent.
   assert.equal(f.debuggerCalls.some(call=>call.method==='Input.insertText'),false);
 }
});

test('enabled Qianwen claims text without attachment capability and closes only its acknowledged page',async()=>{
 const remote={id:'q-task',provider:'qianwen',kind:'text',request:'q request',input:{},created_at:Date.now()/1000,lease:'q-lease',status:'queued'};
 const f=fixture([remote]);
 f.store.poolLimits={total:3,deepseek:1,chatgpt:1,doubao:1,qianwen:1};
 f.browserTabs.push({id:99,url:'https://www.qianwen.com/user-session',active:false,windowId:1});
 await f.poll();
 const task=f.store['bridgeTask:q-task'];
 assert.equal(task.provider,'千问');
 assert.notEqual(task.tab_id,99);
 assert.deepEqual(f.store.lastPing.provider_capabilities.qianwen,[]);
 assert.deepEqual(f.store.claimRequests.find(request=>request.providers.includes('qianwen')).capabilities,[]);
 task.status='complete';task.result={data:'finished'};task.raw='finished';
 await f.poll();
 assert.ok(f.store.closedTabs.includes(task.tab_id));
 assert.ok(f.browserTabs.some(tab=>tab.id===99));
 assert.equal(f.store['bridgeTask:server-task'].bridge.reported,false);
 assert.deepEqual(f.activeTabUpdates,[]);
});

test('trusted continuation input attaches briefly to the validated DeepSeek tab', async () => {
 const f=fixture(); await f.poll();
 const result=await f.trustedClick({taskId:f.task.id,x:140,y:115,viewportWidth:1440,viewportHeight:900});
 assert.equal(result.ok,true); assert.equal(result.method,'trusted-debugger-mouse');
 assert.deepEqual(f.debuggerCalls.map(call=>call.action),['attach','command','command','command','detach']);
 assert.deepEqual(f.debuggerCalls.filter(call=>call.action==='command').map(call=>call.params.type),['mouseMoved','mousePressed','mouseReleased']);
});

test('trusted continuation refuses stale tasks, wrong websites, and invalid coordinates', async () => {
 const f=fixture(); await f.poll();
 const sender={id:'ext',tab:{id:7},url:'https://chat.deepseek.com/a/chat/test',frameId:0};
 assert.equal((await f.trustedClick({taskId:'stale',x:140,y:115,viewportWidth:1440,viewportHeight:900},sender)).ok,false);
 assert.equal((await f.trustedClick({taskId:f.task.id,x:140,y:115,viewportWidth:1440,viewportHeight:900},{...sender,url:'https://chatgpt.com/'})).ok,false);
 assert.equal((await f.trustedClick({taskId:f.task.id,x:-1,y:115,viewportWidth:1440,viewportHeight:900},sender)).ok,false);
 assert.equal(f.debuggerCalls.length,0);
});

test('malformed cached tasks are backed up without blocking new jobs', async () => {
 const f=fixture(); f.store.task={status:'waiting',message:'incomplete'};
 await f.poll();
 assert.equal(f.store.invalidTaskBackup.message,'incomplete');
 assert.equal(f.task.id,'server-task');
 assert.equal(f.sends,1);
});

test('editor prompt tasks carry an isolated conversation policy to the page', async () => {
 const f=fixture(); f.remote.metadata={force_new_conversation:true};
 await f.poll();
 assert.equal(f.task.force_new_conversation,true);
});

test('ambiguous operation timeout stays waiting and only reads on recovery', async () => {
 const f=fixture(); f.store.operationTimeout=true;
 await f.poll();
 assert.equal(f.remote.status,'waiting');
 assert.equal(f.task.read_only_recovery,true);
 assert.equal(f.sends,0);
 await f.poll();
 assert.equal(f.reads,1);
 assert.equal(f.sends,0);
 assert.ok(!f.reports.some(r=>r.status==='error'));
});

test('automatically binds providers, keeps bindings stable and replaces closed tabs for future tasks',async()=>{
 const f=fixture(); delete f.store.backend.tabs;
 f.browserTabs.push({id:8,url:'https://chat.deepseek.com/',active:true},{id:9,url:'https://chatgpt.com/',active:true},{id:10,url:'https://www.doubao.com/chat',active:true});
 f.store.textOwnedTabs={deepseek:[7],chatgpt:[9],doubao:[10]};
 await f.poll(); assert.equal(f.store.autoBindings.deepseek,7); assert.equal(f.store.autoBindings.chatgpt,9); assert.equal(f.store.autoBindings.doubao,10);
 f.browserTabs[0].active=true; f.browserTabs[1].active=false;
 await f.poll(); assert.equal(f.store.autoBindings.deepseek,7);
 f.browserTabs.splice(0,1); await f.poll(); assert.notEqual(f.store.autoBindings.deepseek,8);
 assert.equal(f.task.tab_id,7);
});

test('queued text work opens an owned background tab and leaves user chats untouched',async()=>{
 const f=fixture();
 f.store.textOwnedTabs={};
 f.store.poolLimits={total:1,deepseek:1,chatgpt:1,doubao:1};
 f.browserTabs[0].active=true;
 await f.poll();
 assert.equal(f.sends,1);
 assert.equal(f.browserTabs[0].url,'https://chat.deepseek.com/');
 assert.equal(f.browserTabs[0].active,true);
 assert.notEqual(f.task.tab_id,7);
 assert.equal(f.store[`textOwnedTab:${f.task.tab_id}`].providerId,'deepseek');
 assert.equal(f.browserTabs.find(tab=>tab.id===f.task.tab_id).active,false);
});

test('a submitted text task restores its bound conversation without resending',async()=>{
 const f=fixture();await f.poll();
 f.task.conversation_url='https://chat.deepseek.com/a/chat/s/original';
 f.task.conversation_id='original';
 f.browserTabs[0].url='https://chat.deepseek.com/a/chat/s/other';
 await f.poll();
 assert.equal(f.browserTabs[0].url,f.task.conversation_url);
 assert.equal(f.sends,1);
 assert.ok(f.reads>=1);
 assert.equal(f.task.read_only_recovery,true);
});

test('conversation recovery never loops after a redirect or failed navigation',async()=>{
 for(const failNavigation of [false,true]) {
  const f=fixture();await f.poll();
  const target='https://chat.deepseek.com/a/chat/s/original';
  const redirected='https://chat.deepseek.com/';
  f.task.conversation_url=target;f.task.conversation_id='original';
  f.browserTabs[0].url=redirected;f.store.failNavigation=failNavigation;
  await f.poll();
  assert.equal(f.store.navigationAttempts,1);
  assert.ok(f.task.conversation_recovery_attempted_at);
  f.browserTabs[0].url=redirected;
  await f.poll();await f.poll();
  assert.equal(f.browserTabs[0].url,redirected);
  assert.equal(f.store.navigationAttempts,1);
  assert.equal(f.sends,1);
  // Read-only collection must still finish when the original reply is available.
  f.store.completeOnRead=true;await f.poll();
  assert.equal(f.remote.status,'complete');
 }
});

test('the completed ChatGPT tab closes after acknowledgement and the next job uses a new tab',async()=>{
 const next={id:'chat-next',provider:'chatgpt',kind:'text',request:'next request',input:{},created_at:Date.now()/1000,lease:'next-lease',status:'queued'};
 const f=fixture([next]);
 f.remote.provider='chatgpt';f.browserTabs[0].url='https://chatgpt.com/';
 f.store.textOwnedTabs={chatgpt:[7]};
 f.store.poolLimits={total:1,deepseek:1,chatgpt:1,doubao:1};
 await f.poll();
 assert.equal(f.task.tab_id,7);assert.equal(f.sends,1);
 const original='https://chatgpt.com/c/completed';
 f.task.conversation_url=original;f.task.conversation_id='completed';
 f.browserTabs[0].url=original;f.store.completeOnRead=true;
 await f.poll();await f.poll();
 const second=f.store['bridgeTask:chat-next'];
 assert.ok(second);assert.notEqual(second.tab_id,7);
 assert.equal(f.browserTabs.find(tab=>tab.id===7),undefined);
 assert.equal(f.store['bridgeTask:server-task'].tab_closed,true);
 assert.equal(f.sends,2);
 assert.equal(f.store.navigationAttempts || 0,0);
 assert.deepEqual(f.activeTabUpdates,[]);
});

test('frozen background page submission never activates a tab',async()=>{
 const f=fixture();f.browserTabs[0].frozen=true;f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 await f.poll();
 assert.equal(f.sends,1);assert.equal(f.browserTabs[0].active,false);assert.deepEqual(f.activeTabUpdates,[]);
 assert.equal(f.browserTabs[1].active,true);
});

test('stalled DeepSeek reply emulates renderer focus without switching the user tab',async()=>{
 const f=fixture();
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 await f.poll();
 f.task.sent_at=new Date(Date.now()-50000).toISOString();
 f.task.message='[读取] 回复区域 0 个；未找到本次回复标记';
 await f.poll();
 await new Promise(resolve=>setTimeout(resolve,430));
 assert.equal(f.reads,1);
   assert.deepEqual(f.activeTabUpdates,[]);
   assert.deepEqual(f.debuggerCalls.map(call=>call.action),['attach','command','command','detach']);
   const focusCommands=f.debuggerCalls.filter(call=>call.action==='command');
   assert.ok(focusCommands.every(call=>call.method==='Emulation.setFocusEmulationEnabled'));
   assert.deepEqual(focusCommands.map(call=>call.params.enabled),[true,false]);
 assert.equal(f.browserTabs[0].active,false);
 assert.equal(f.browserTabs[1].active,true);
 await f.poll();
 assert.equal(f.reads,2);
   assert.equal(f.activeTabUpdates.length,0);
 assert.equal(f.store['deepseekReadWake:7'].count,1);
});

test('active DeepSeek tab is read without a background wake',async()=>{
 const f=fixture();f.browserTabs[0].active=true;
 await f.poll();
 f.task.sent_at=new Date(Date.now()-50000).toISOString();
 f.task.message='[读取] 回复区域 0 个；未找到本次回复标记';
 await f.poll();
 assert.equal(f.reads,1);
 assert.deepEqual(f.activeTabUpdates,[]);
 assert.equal(f.store['deepseekReadWake:7'],undefined);
});

test('inactive Doubao page sends and reads without ever activating or switching tabs',async()=>{
 const f=fixture();
 f.browserTabs[0].url='https://www.doubao.com/chat';f.browserTabs[0].active=false;
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 f.remote.provider='doubao';
 f.store.textOwnedTabs={doubao:[7]};
 await f.poll();
 assert.equal(f.sends,1);assert.equal(f.browserTabs[0].active,false);assert.equal(f.browserTabs[1].active,true);
 assert.deepEqual(f.activeTabUpdates,[]);
 f.task.last_background_wake_at=Date.now()-13000;
 await f.poll();
 assert.equal(f.reads,1);assert.equal(f.browserTabs[0].active,false);assert.equal(f.browserTabs[1].active,true);
 assert.deepEqual(f.activeTabUpdates,[]);
});

test('a frozen Doubao tab sends and reads without switching tabs',async()=>{
 const f=fixture();
 f.browserTabs[0].url='https://www.doubao.com/chat';f.browserTabs[0].active=false;f.browserTabs[0].frozen=true;
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 f.remote.provider='doubao';
 f.store.textOwnedTabs={doubao:[7]};
 await f.poll();
 assert.equal(f.sends,1);assert.deepEqual(f.activeTabUpdates,[]);
 f.browserTabs[0].frozen=true;
 await f.poll();
 assert.equal(f.reads,1);assert.deepEqual(f.activeTabUpdates,[]);
});

test('unresponsive inactive Doubao retains uncertain send without activation or resending',async()=>{
 const f=fixture();
 f.browserTabs[0].url='https://www.doubao.com/chat';f.browserTabs[0].active=false;
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 f.remote.provider='doubao';f.store.doubaoRespondOnlyWhenActive=true;
 f.store.textOwnedTabs={doubao:[7]};
 await f.poll();
 await new Promise(resolve=>setTimeout(resolve,70));
 assert.equal(f.remote.status,'waiting');assert.equal(f.task.read_only_recovery,true);
 assert.equal(f.sends,0);
 assert.equal(f.browserTabs[0].active,false);assert.equal(f.browserTabs[1].active,true);
 assert.deepEqual(f.activeTabUpdates,[]);
});

test('enabled backend creates one sidebar-independent poller and disabling closes it',async()=>{
 const f=fixture(); await f.poll(); await f.poll();
 assert.equal(f.offscreen,true); assert.equal(f.offscreenCreates,1);
 f.store.backend.enabled=false; await f.poll(); assert.equal(f.offscreen,false);
});

test('background clock ticks every second',()=>{
 let tick, interval, messages=0;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../poller-clock.js'),'utf8'),{
   setInterval:(fn,ms)=>{tick=fn;interval=ms;},postMessage:()=>messages++
 });
 assert.equal(interval,1000); tick(); tick(); assert.equal(messages,2);
});

test('task storage failures are reported to backend instead of leaving claimed jobs stuck',async()=>{
 const f=fixture(); f.store.failTaskSave=true; await f.poll();
 assert.equal(f.reports.at(-1).status,'error');
 assert.match(f.reports.at(-1).message,/storage quota exceeded/);
 assert.equal(f.sends,0);
});

test('project task sent once, resumed by read, and result returned with lease',async()=>{
  const f=fixture();await f.poll();assert.equal(f.sends,1);assert.equal(f.task.bridge.lease,'lease');
  await f.poll();assert.equal(f.sends,1);assert.equal(f.reads,1);
  f.task.status='complete';f.task.result={revised_prompt:'fixed',changes:['motion']};
  await f.poll();assert.equal(f.sends,1);assert.equal(f.task.bridge.reported,true);
  assert.equal(f.reports.at(-1).lease,'lease');assert.equal(f.reports.at(-1).result.revised_prompt,'fixed');
  assert.equal(f.task.request,undefined);assert.equal(f.task.result,undefined);
});

test('different provider tabs send concurrently while a second same-provider task stays queued',async()=>{
  const chat={id:'chat-task',provider:'chatgpt',kind:'text',request:'chat request',input:{},created_at:Date.now()/1000+1,lease:'chat-lease',status:'queued'};
  const nextDeepSeek={id:'next-deepseek',provider:'deepseek',kind:'text',request:'next request',input:{},created_at:Date.now()/1000+2,lease:'next-lease',status:'queued'};
  const arrived=[];
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const barrier=async id=>{arrived.push(id);if(arrived.length===2)release();await gate;};
  const f=fixture([chat,nextDeepSeek],barrier);
  f.browserTabs.push({id:9,url:'https://chatgpt.com/',active:false,windowId:1});
  f.store.textOwnedTabs.chatgpt=[9];
  await Promise.race([f.poll(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('provider sends did not overlap')),2000))]);
  assert.equal(f.sends,2);
  assert.deepEqual(new Set(arrived),new Set([7,9]));
  assert.equal(f.store['bridgeTask:server-task'].tab_id,7);
  assert.equal(f.store['bridgeTask:chat-task'].tab_id,9);
  assert.equal(f.store['bridgeTask:next-deepseek'],undefined);
  assert.equal(nextDeepSeek.status,'queued');
  assert.deepEqual(f.activeTabUpdates,[]);
});

test('same-provider pool grows in background, isolates results and never claims an occupied tab twice',async()=>{
  const job=id=>({id,provider:'deepseek',kind:'text',request:id,input:{},created_at:Date.now()/1000,lease:id+'-lease',status:'queued'});
  const arrived=[];let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const f=fixture([job('second'),job('third')],async id=>{arrived.push(id);if(arrived.length===2)release();await gate;});
  f.store.poolLimits={total:2,deepseek:2,chatgpt:1,doubao:1};
  await Promise.race([f.poll(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('same-provider sends did not overlap')),2000))]);
  assert.equal(f.sends,2);assert.equal(new Set(arrived).size,2);
  assert.equal(f.store.autoPools.deepseek.length,2);
  assert.equal(f.store['bridgeTask:third'],undefined);
  await f.poll();assert.equal(f.sends,2);
  f.task.status='complete';f.task.result={revised_prompt:'first only',changes:[]};
  await f.poll();assert.equal(f.store['bridgeTask:second'].status,'waiting');
  await f.poll();assert.equal(f.sends,3);
  assert.notEqual(f.store['bridgeTask:third'].tab_id,7);
  assert.equal(f.browserTabs.length,2);assert.deepEqual(f.activeTabUpdates,[]);
});

test('late webpage result cannot revive cancelled backend task',async()=>{
  const f=fixture();await f.poll();f.remote.status='cancelled';f.task.status='complete';
  f.task.result={revised_prompt:'late',changes:[]};await f.poll();
  assert.equal(f.task.status,'cancelled');assert.ok(!f.reports.some(r=>r.status==='complete'));
});

test('unpaired/disabled extension never polls or transmits',async()=>{
  const f=fixture();f.store.backend.enabled=false;await f.poll();assert.equal(f.sends,0);assert.equal(f.task,undefined);
});

test('result completed during a read is reported in the same poll',async()=>{
 const f=fixture(); await f.poll(); f.store.completeOnRead=true;
 await f.poll(); assert.equal(f.reports.at(-1).status,'complete');
 assert.equal(f.task.bridge.reported,true); assert.equal(f.sends,1);
});

test('a quarantined busy page cannot occupy the next queued task pool slot',async()=>{
 const next={id:'next-task',provider:'deepseek',kind:'text',request:'next',input:{},created_at:Date.now()/1000,lease:'next-lease',status:'queued'};
 const f=fixture([next]);
 f.store.poolLimits={total:1,deepseek:1,chatgpt:1,doubao:1};
 await f.poll(); const oldTab=f.task.tab_id;
 f.remote.status='timeout'; f.store.slotBusy=true;
 for(let i=0;i<6;i++)await f.poll();
 assert.equal(next.status,'waiting');
 assert.notEqual(f.store['bridgeTask:next-task'].tab_id,oldTab);
 assert.equal(f.sends,2);
 assert.equal(f.store['bridgeTask:server-task'].bridge.reported,true);
 assert.equal(f.store['bridgeTask:server-task'].retired,true);
 assert.ok(f.store['textRetiredTab:'+oldTab]);
});

test('backend-gone tasks retire the original page without replay and allow queued work',async()=>{
 const next={id:'next-task',provider:'deepseek',kind:'text',request:'next',input:{},created_at:Date.now()/1000,lease:'next-lease',status:'queued'};
 const f=fixture([next]); f.store.poolLimits={total:1,deepseek:1,chatgpt:1,doubao:1};
 await f.poll(); f.store.goneTask=f.remote.id;
 for(let i=0;i<4;i++)await f.poll();
 assert.equal(f.store['bridgeTask:server-task'].retired,true);
 assert.equal(next.status,'waiting');assert.equal(f.sends,2);
});

test('acknowledged history expires while unreported results stay recoverable',async()=>{
 const f=fixture();
 const expired=Date.now()-8*24*60*60*1000;
 f.store['bridgeTask:old']={id:'old',status:'complete',reported_at:expired,bridge:{url:'http://127.0.0.1:7860',reported:true}};
 f.store['bridgeTask:pending']={id:'pending',tab_id:99,status:'complete',reported_at:expired,bridge:{url:'http://127.0.0.1:7860',reported:false}};
 await f.poll();
 assert.equal(f.store['bridgeTask:old'],undefined);
 assert.ok(f.store['bridgeTask:pending']);
});

test('an orphaned completed reply is retained before its page is retired',async()=>{
 const f=fixture();await f.poll();
 f.task.status='complete';f.task.result={data:'not yet delivered'};f.task.raw='reply';
 f.store.goneTask=f.remote.id;await f.poll();
 assert.equal(f.store['orphanedTextResult:server-task'].result.data,'not yet delivered');
 assert.equal(f.task.retired,true);assert.equal(f.sends,1);
});

test('generic text and JSON envelope parsing preserves original result types',()=>{
  for(const [kind,data] of [['text','完整提示词'],['json',{beats:[{id:1}]}]]){
    const id='generic';const m=P.markers(id);
    assert.deepEqual(P.parseResult(`${m.start}\n${JSON.stringify({request_id:id,data})}\n${m.end}`,id,kind),{data});
  }
});

test('ChatGPT retries when the editor was not ready before any dispatch', async () => {
 const f=fixture();
 f.remote.provider='chatgpt'; f.browserTabs[0].url='https://chatgpt.com/';
 f.store.textOwnedTabs={chatgpt:[7]}; f.store.editorNotReady=true;
 await f.poll(); assert.equal(f.sends,0); assert.equal(f.task.status,'prepared');
 f.store.pageBusy=true;
 await f.poll(); assert.equal(f.sends,0);
 f.store.pageBusy=false;
 await f.poll(); assert.equal(f.sends,1); assert.equal(f.task.status,'waiting');
 await f.poll(); assert.equal(f.sends,1);
});

test('prepared backend tasks continue observing manual sends without resending', async () => {
 const f=fixture(); await f.poll(); f.task.status='prepared';
 await f.poll(); assert.equal(f.reads,1); assert.equal(f.sends,1);
 f.task.status='complete'; f.task.result={data:{story:'returned story'}};
 await f.poll(); assert.equal(f.reports.at(-1).result.data.story,'returned story');
 assert.equal(f.task.bridge.reported,true);
});

test('cached completed video learning replies retain the video upload receipt',async()=>{
 const f=fixture();
 f.remote.entry='video_learning';
 f.remote.metadata={attachments:[{id:'approved_video',name:'approved.mp4'}]};
 await f.poll();
 assert.deepEqual(f.task.attachments,f.remote.metadata.attachments);
 f.task.status='complete';f.task.result={data:{sample_id:'sample'}};f.task.raw='reply';
 f.task.attachment_receipt={kind:'media_attachment',sha256:'video-hash'};
 await f.poll();
 assert.ok(f.reports.at(-1).diagnostics.some(d=>d.kind==='media_attachment'&&d.sha256==='video-hash'));
});

test('collects completed reply while the bound tab is loading',async()=>{
 const f=fixture(); await f.poll();
 f.browserTabs.find(tab=>tab.id===f.task.tab_id).status='loading';
 f.store.completeOnRead=true;
 await f.poll();
 assert.equal(f.reads,1);
 assert.equal(f.sends,1);
 assert.ok(f.reports.some(report=>report.status==='complete'));
 assert.deepEqual(f.activeTabUpdates,[]);
});


test('acknowledged completed text tabs close without clearing other tasks or backend history',async()=>{
 const f=fixture();await f.poll();
 f.browserTabs.push({id:90,url:'https://chatgpt.com/c/personal',active:true});
 f.store['bridgeTask:other']={id:'other',tab_id:91,status:'waiting',bridge:{reported:false}};
 f.store.completeOnRead=true;await f.poll();await f.poll();
 assert.equal(f.remote.status,'complete');assert.equal(f.task.tab_closed,true);
 assert.ok(f.browserTabs.find(tab=>tab.id===90));
 assert.equal(f.store['bridgeTask:other'].status,'waiting');
 assert.equal(f.store.taskResetEpoch,undefined);
 assert.deepEqual(f.store.closedTabs,[7]);
});

test('a completed reply is kept until the backend confirms its result',async()=>{
 const f=fixture();await f.poll();f.store.completeOnRead=true;f.store.failCompleteReport=true;
 await f.poll();
 assert.equal(f.task.bridge.reported,false);assert.ok(f.task.result);
 assert.ok(f.browserTabs.find(tab=>tab.id===7));
 assert.equal(f.store.closedTabs,undefined);
 f.store.failCompleteReport=false;await f.poll();await f.poll();
 assert.equal(f.task.tab_closed,true);assert.deepEqual(f.store.closedTabs,[7]);
});

test('failed tab cleanup is retried after acknowledged results survive in storage',async()=>{
 const f=fixture();await f.poll();f.store.failTabClose=1;f.store.completeOnRead=true;
 await f.poll();
 assert.equal(f.task.bridge.reported,true);assert.equal(f.task.tab_closed,false);
 assert.match(f.task.tab_cleanup_error,/temporarily failed/);
 await f.poll();
 assert.equal(f.task.tab_closed,true);assert.equal(f.task.tab_cleanup_error,'');
 assert.deepEqual(f.store.closedTabs,[7]);
});

test('historical completed result tabs are cleaned without repeating model requests',async()=>{
 const f=fixture();f.remote.status='complete';
 f.store['textOwnedTab:7']={providerId:'deepseek',windowId:1};
 f.store['bridgeTask:old']={id:'old',provider:'DeepSeek',tab_id:7,status:'complete',bridge:{url:f.store.backend.url,reported:true}};
 await f.poll();await f.poll();
 assert.equal(f.store['bridgeTask:old'].tab_closed,true);
 assert.equal(f.sends,0);assert.equal(f.reads,0);assert.deepEqual(f.store.closedTabs,[7]);
});

test('completion cleanup preserves a user tab or a task tab navigated to another conversation',async()=>{
 for(const changed of ['unowned','different-conversation','manual-task','other-live-task']) {
  const f=fixture();await f.poll();
  const terminal={...f.task,status:'complete',conversation_url:'https://chat.deepseek.com/a/chat/s/completed',bridge:{...f.task.bridge,reported:true}};
  f.remote.status='complete';f.browserTabs[0].url=terminal.conversation_url;
  if(changed==='unowned') {delete f.store['textOwnedTab:7'];f.store.textOwnedTabs={};}
  if(changed==='different-conversation')f.browserTabs[0].url='https://chat.deepseek.com/a/chat/s/personal';
  if(changed==='manual-task')f.store.task={id:'manual',tab_id:7,status:'waiting'};
  if(changed==='other-live-task')f.store['bridgeTask:active']={id:'active',tab_id:7,status:'waiting',bridge:{reported:false}};
  await f.saveTask(terminal);
  assert.ok(f.browserTabs.find(tab=>tab.id===7),changed);
  assert.equal(f.store.closedTabs,undefined,changed);
 }
});
