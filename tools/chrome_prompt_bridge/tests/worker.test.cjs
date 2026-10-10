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

function fixture(extraRemotes = [], sendBarrier = null) {
  let listener, sends = 0, reads = 0, claims = 0, offscreen = false, offscreenCreates = 0;
  let schedulerNow=Date.now();
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
  const context = {
    PromptBridge: P,TextProviderPolicy,TextTaskStore,FocusCoordinator,
    TextScheduler:{createScheduler:options=>TextScheduler.createScheduler({...options,now:()=>schedulerNow})},
    importScripts() {}, videoPumpFromStorage:async()=>{}, URL, Date,
    setTimeout:(fn,ms,...args)=>setTimeout(fn,ms===10000?5:ms,...args), clearTimeout, queueMicrotask,
    crypto: require('node:crypto').webcrypto, AbortSignal,
    fetch: async (url, opts) => {
      assert.equal(opts.headers.Authorization, 'Bearer paired');
      if(url.endsWith('/ping')) return {ok:true,json:async()=>({ok:true,...(store.poolLimits?{
        limits:store.poolLimits,queued:Object.fromEntries(Object.keys(store.poolLimits).filter(id=>id!=='total').map(id=>
          [id,remoteTasks.filter(task=>task.provider===id && task.status==='queued' && !claimedIds.has(task.id)).length]))
      }:{})})};
      if(url.endsWith('/claim')) {
        const attempt=++claims;
        const request=JSON.parse(opts.body);
        const candidate=extraRemotes.length
          ? remoteTasks.find(task=>task.status==='queued' && request.providers.includes(task.provider) && !claimedIds.has(task.id))
          : attempt===1 ? remote : null;
        if(candidate)claimedIds.add(candidate.id);
        return {ok:true,json:async()=>({ok:true,task:candidate ? {...candidate} : null})};
      }
      const selected=remoteTasks.find(task=>url.endsWith('/'+task.id)) || remote;
      if(opts.method === 'POST') { const report=JSON.parse(opts.body);reports.push(report);selected.status=report.status; }
      return {ok:true,json:async()=>({ok:true,task:{...selected}})};
    },
    chrome: {
      offscreen: {hasDocument:async()=>offscreen, createDocument:async options=>{
        assert.equal(options.url,'poller.html'); assert.deepEqual(Array.from(options.reasons),['WORKERS']);
        offscreen=true; offscreenCreates++;
      },closeDocument:async()=>{offscreen=false;}},
      runtime: {id:'ext',onInstalled:event,onStartup:event,onMessage:{addListener(fn){listener=fn;}}},
      debugger: {attach:async(target,version)=>debuggerCalls.push({action:'attach',target,version}),
        sendCommand:async(target,method,params)=>debuggerCalls.push({action:'command',target,method,params}),
        detach:async target=>debuggerCalls.push({action:'detach',target})},
      alarms: {create:async()=>{},onAlarm:event}, sidePanel:{setPanelBehavior:async()=>{}},
      windows:{getLastFocused:async()=>({id:1,focused:true}),onFocusChanged:event},
      storage: {onChanged:event,local:{get:async()=>structuredClone(store),set:async data=>{
        if(Object.keys(data).some(key=>key.startsWith('bridgeTask:')) && store.failTaskSave) throw new Error('storage quota exceeded');
        Object.assign(store,structuredClone(data));
      }},session:{get:async key=>{
        const seeded=Object.fromEntries(Object.entries(store.textOwnedTabs || {}).flatMap(([providerId,ids])=>
          ids.map(id=>[`textOwnedTab:${id}`,{providerId,windowId:1}])));
        const values={...seeded,...(store.__session || {})};
        return key===null?structuredClone(values):{[key]:values[key]};
      },set:async data=>{store.__session={...(store.__session || {}),...structuredClone(data)};},
        remove:async key=>{delete store.__session?.[key];}}},
      tabs: {onActivated:{addListener:fn=>activationListeners.push(fn)},create:async options=>{
        assert.equal(options.active,false);
        const tab={...options,id:20+browserTabs.length,status:'complete',windowId:options.windowId || 1};browserTabs.push(tab);return {...tab};
      },query:async query=>query?.active?browserTabs.filter(tab=>tab.active&&(!Number.isInteger(query.windowId)||tab.windowId===query.windowId)):browserTabs.filter(tab=>PromptBridge.provider(tab.url)),get:async id=>{
        const tab=browserTabs.find(item=>item.id===id);if(!tab)throw new Error('No tab');return {...tab};
      },update:async(id,changes)=>{
        const tab=browserTabs.find(item=>item.id===id);if(!tab)throw new Error('No tab');
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
        if(message.type==='AICF_SLOT_STATUS')return {ok:true,task:{idle:true}};
        if(message.type==='AICF_OPERATION_STATUS')return {ok:true,task:{...structuredClone(taskState(message.task.id)),busy:store.pageBusy===true}};
        if(message.type==='AICF_READ') {reads++; return {ok:true,task:structuredClone(taskState(message.task.id))};}
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
      }}, scripting:{executeScript:async({target})=>[{documentId:'document-'+target.tabId}]}
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../worker.js'),'utf8'),context);
  const rawPoll=()=>new Promise(resolve=>listener({type:'AICF_BACKEND_POLL'},{id:'ext'},resolve));
  return {store,reports,remote,browserTabs,debuggerCalls,activeTabUpdates,get task(){return taskState();},get claims(){return claims;},get offscreen(){return offscreen;},get offscreenCreates(){return offscreenCreates;},get sends(){return sends;},get reads(){return reads;},rawPoll,
    poll:async()=>{schedulerNow+=3000;await rawPoll();await new Promise(resolve=>setTimeout(resolve,20));},
    trustedClick:(message,sender={id:'ext',tab:{id:7},url:'https://chat.deepseek.com/a/chat/test',frameId:0})=>new Promise(resolve=>listener({type:'AICF_TRUSTED_CONTINUE_CLICK',...message},sender,resolve))};
}

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
 assert.equal(f.store.__session[`textOwnedTab:${f.task.tab_id}`].providerId,'deepseek');
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

test('frozen background page is woken automatically for submission then restores the user tab',async()=>{
 const f=fixture();f.browserTabs[0].frozen=true;f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});f.store.assertActiveDuringSend=true;
 await f.poll();
 assert.equal(f.sends,1);assert.equal(f.browserTabs[0].active,false);assert.equal(f.browserTabs[0].frozen,false);
 assert.equal(f.browserTabs[1].active,true);
});

test('stalled DeepSeek reply briefly wakes the bound tab and restores the user tab',async()=>{
 const f=fixture();
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 await f.poll();
 f.task.sent_at=new Date(Date.now()-50000).toISOString();
 f.task.message='[读取] 回复区域 0 个；未找到本次回复标记';
 await f.poll();
 await new Promise(resolve=>setTimeout(resolve,430));
 assert.equal(f.reads,1);
 assert.deepEqual(f.activeTabUpdates,[{id:7,active:true},{id:11,active:true}]);
 assert.equal(f.browserTabs[0].active,false);
 assert.equal(f.browserTabs[1].active,true);
 await f.poll();
 assert.equal(f.reads,2);
 assert.equal(f.activeTabUpdates.length,2);
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

test('a frozen Doubao tab is woken only for initial submission, never repeatedly for reads',async()=>{
 const f=fixture();
 f.browserTabs[0].url='https://www.doubao.com/chat';f.browserTabs[0].active=false;f.browserTabs[0].frozen=true;
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 f.remote.provider='doubao';
 f.store.textOwnedTabs={doubao:[7]};
 await f.poll();
 assert.equal(f.sends,1);assert.deepEqual(f.activeTabUpdates,[{id:7,active:true},{id:11,active:true}]);
 f.browserTabs[0].frozen=true;
 await f.poll();
 assert.equal(f.reads,1);assert.deepEqual(f.activeTabUpdates,[{id:7,active:true},{id:11,active:true}]);
});

test('unresponsive inactive Doubao is automatically woken once and resumed without manual tab selection',async()=>{
 const f=fixture();
 f.browserTabs[0].url='https://www.doubao.com/chat';f.browserTabs[0].active=false;
 f.browserTabs.push({id:11,url:'http://127.0.0.1:7860/',active:true,windowId:1});
 f.remote.provider='doubao';f.store.doubaoRespondOnlyWhenActive=true;
 f.store.textOwnedTabs={doubao:[7]};
 await f.poll();
 assert.equal(f.sends,1);assert.equal(f.reads,1);
 assert.equal(f.browserTabs[0].active,false);assert.equal(f.browserTabs[1].active,true);
 assert.deepEqual(f.activeTabUpdates,[{id:7,active:true},{id:11,active:true}]);
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
  assert.equal(f.store['bridgeTask:third'].tab_id,7);
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
