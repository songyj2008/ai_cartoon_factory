"use strict";
importScripts("protocol.js");
importScripts("text_provider_policy.js");
importScripts("text_task_store.js");
importScripts("text_scheduler.js");
importScripts("focus_coordinator.js");
importScripts("video_worker.js");
importScripts("runow_worker.js");
importScripts("runow_submission_patch.js");
const ENABLED_PROVIDERS = TextProviderPolicy.ids.filter(id=>TextProviderPolicy.policy(id).available);
const PROVIDER_NAMES = Object.fromEntries(ENABLED_PROVIDERS.map(id=>[id,TextProviderPolicy.policy(id).displayName]));
const PROVIDER_URLS = Object.fromEntries(ENABLED_PROVIDERS.map(id=>[id,TextProviderPolicy.policy(id).homeUrl]));
const textStore=TextTaskStore.createStore(chrome,TextProviderPolicy);
const focus=FocusCoordinator.createCoordinator(chrome);
const pageFlights = new Map();
const DOUBAO_BACKGROUND_SEND_TIMEOUT = 10000;
const TEXT_HISTORY_MS = 24 * 60 * 60 * 1000;
const TEXT_FAILURE_HISTORY_MS = 7 * TEXT_HISTORY_MS;
let lastHistoryCleanup = 0;
let pumping = false;
let scanningExisting = false;
let pollerSetup = null;
const scheduler=TextScheduler.createScheduler({onTick:()=>{
  scanExistingTasks().catch(error=>chrome.storage.local.set({bridgeStatus:{message:error.message,error:true,at:Date.now()}}));
  scheduler.scheduleDiscovery(pump);
  scheduler.scheduleVideo(videoPumpFromStorage);
},onError:(error,scope)=>{
  chrome.storage.local.set({bridgeStatus:{message:`${scope}：${error.message || String(error)}`,error:true,at:Date.now()}}).catch(()=>{});
}});
const bridgeTaskKey = id => `bridgeTask:${id}`;
async function taskAttachment(sender,message) {
  const task=await textStore.get(message.taskId);
  const provider=TextProviderPolicy.fromName(task?.provider);
  if(sender.id!==chrome.runtime.id || sender.frameId!==0 || !sender.documentId || !task ||
     task.tab_id!==sender.tab?.id || task.document_id!==sender.documentId ||
     task.binding_epoch!==message.bindingEpoch || !provider?.matchesUrl(sender.url || '') ||
     !['prepared','sending','waiting'].includes(task.status))throw new Error('STALE_BINDING: 视频附件页面绑定失效');
  const attachment=(task.attachments || []).find(item=>item.id===message.attachmentId);
  if(task.entry!=='video_learning' || !attachment)throw new Error('此任务未授权读取视频附件');
  const {backend}=await chrome.storage.local.get('backend');
  if(!backend?.enabled || baseUrl(backend.url)!==baseUrl(task.bridge?.url || ''))throw new Error('附件后台连接已变化');
  const response=await fetch(baseUrl(backend.url)+'/api/web-llm/bridge/tasks/'+encodeURIComponent(task.id)+
    '/attachments/'+encodeURIComponent(attachment.id), {headers:{Authorization:'Bearer '+backend.token,'X-Task-Lease':task.bridge.lease}});
  if(!response.ok)throw new Error('读取已确认视频失败：HTTP '+response.status);
  const mime=(response.headers.get('content-type') || '').split(';')[0];
  if(!['video/mp4','video/webm','video/quicktime'].includes(mime))throw new Error('附件不是视频');
  const bytes=new Uint8Array(await response.arrayBuffer());
  if(bytes.length>32*1024*1024)throw new Error('视频附件超过 32 MB');
  let binary='';
  for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return {ok:true,base64:btoa(binary),mime,sha256:response.headers.get('x-content-sha256')};
}
async function savedTaskForId(id) {
  const current=await textStore.get(id);
  if(current)return current;
  const manual=(await chrome.storage.local.get("task")).task;
  return manual?.id===id ? manual : null;
}
async function saveBridgeTask(task) {
  const resetState=await chrome.storage.local.get('clearedTaskIds');
  if(resetState.clearedTaskIds?.[task.id])return;
  // The backend keeps the result. Release large prompts and replies from
  // extension storage once its terminal state has been acknowledged.
  const stored=task.bridge?.reported ? {
    id:task.id,provider:task.provider,kind:task.kind,entry:task.entry,
    tab_id:task.tab_id,status:task.status,message:task.message,
    conversation_url:task.conversation_url,
    tab_closed:task.tab_closed === true,tab_cleanup_skipped:task.tab_cleanup_skipped === true,
    tab_cleanup_error:task.tab_cleanup_error || '',
    quarantined:task.quarantined === true,
    quarantine_checks:task.quarantine_checks || 0,
    retired:task.retired === true,
    bridge:{url:task.bridge.url,reported:true},
    reported_at:task.reported_at || Date.now()
  } : task;
  const existing=await textStore.get(task.id);
  if(existing)await textStore.patchFromWorker(task.id,stored);
  else await textStore.create(stored);
  if(needsTextTabCleanup(stored))await closeCompletedTextTab(stored);
}
function needsTextTabCleanup(task) {
  return task?.status==='complete' && task.bridge?.reported && Number.isInteger(task.tab_id) && !task.quarantined &&
    !task.tab_closed && !task.tab_cleanup_skipped;
}
async function closeCompletedTextTab(task) {
  if(!needsTextTabCleanup(task))return;
  try {
    const latest=await textStore.get(task.id);
    if(!latest || !needsTextTabCleanup(latest) || latest.tab_id!==task.tab_id)return;
    const provider=TextProviderPolicy.fromName(latest.provider);
    const skip=async()=>textStore.patchFromWorker(task.id,{tab_cleanup_skipped:true});
    if(!provider || !Number.isInteger(task.tab_id) || !await textStore.ownsTab(task.tab_id)) {
      await skip();return;
    }
    const saved=await chrome.storage.local.get(null);
    const busy=Object.entries(saved).some(([key,value])=>
      key.startsWith('bridgeTask:') && value?.id!==task.id && value?.tab_id===task.tab_id &&
      (!value.bridge?.reported || value.quarantined));
    const manual=saved.task?.tab_id===task.tab_id && ['prepared','sending','waiting'].includes(saved.task.status);
    const video=Object.entries(saved).some(([key,value])=>
      (key.startsWith('videoTask:') && value?.tabId===task.tab_id) ||
      (key.startsWith('videoDedicatedTabId:') && value===task.tab_id));
    if(busy || manual || video)return;
    const tab=await chrome.tabs.get(task.tab_id).catch(()=>null);
    if(tab && (!provider.matchesUrl(tab.url || '') ||
        latest.conversation_url && tab.url!==latest.conversation_url)) {
      await skip();return;
    }
    if(tab)await chrome.tabs.remove(task.tab_id);
    // This is task-scoped cleanup, not a queue reset. Keep its receipt/history.
    await textStore.patchFromWorker(task.id,{tab_closed:true,tab_cleanup_error:''});
    const keys=['textOwnedTab:'+task.tab_id,'textRetiredTab:'+task.tab_id];
    await chrome.storage.local.remove(keys);
    await chrome.storage.session.remove(keys);
  } catch(error) {
    await textStore.patchFromWorker(task.id,{tab_cleanup_error:error.message || String(error)}).catch(()=>{});
  }
}
async function syncQueueReset(pool,saved) {
  const epoch=Number(pool.reset_epoch || 0);
  if(!epoch || epoch<=Number(saved.taskResetEpoch || 0))return false;
  const ids={...(saved.clearedTaskIds || {})}, keys=[], tabs=new Set();
  for(const [key,value] of Object.entries(saved)) {
    if(key.startsWith('bridgeTask:') || key.startsWith('videoTask:')) {if(value?.id)ids[value.id]=epoch;keys.push(key);}
    if(key.startsWith('textOwnedTab:'))tabs.add(Number(key.split(':')[1]));
    if(key.startsWith('videoTask:') && value?.ownedTab && Number.isInteger(value.tabId))tabs.add(value.tabId);
    if(key.startsWith('textOwnedTab:') || key.startsWith('textRetiredTab:') || key.startsWith('videoDedicatedTabId:') || key.startsWith('orphanedTextResult:'))keys.push(key);
  }
  if(saved.task?.id)ids[saved.task.id]=epoch;
  await chrome.storage.local.set({clearedTaskIds:ids,taskResetEpoch:epoch,task:null,autoBindings:{},autoPools:{}});
  for(const tabId of tabs)if(Number.isInteger(tabId))await chrome.tabs.remove(tabId).catch(()=>{});
  if(keys.length){await chrome.storage.local.remove(keys);await chrome.storage.session.remove(keys);}
  return true;
}
async function cleanupTextHistory(saved) {
  if(Date.now()-lastHistoryCleanup<60000)return;
  const keys=[];
  for(const [key,task] of Object.entries(saved)) {
    if(key.startsWith('bridgeTask:') && task?.bridge?.reported && !task.quarantined && !needsTextTabCleanup(task) &&
        Date.now()-(task.reported_at || Date.now()) >=
        (task.status==='complete'?TEXT_HISTORY_MS:TEXT_FAILURE_HISTORY_MS))keys.push(key);
    if(key.startsWith('textRetiredTab:') && !await chrome.tabs.get(Number(key.split(':')[1])).catch(()=>null))keys.push(key);
    if(key.startsWith('orphanedTextResult:') && Date.now()-task.at>=TEXT_FAILURE_HISTORY_MS)keys.push(key);
  }
  if(keys.length)await chrome.storage.local.remove(keys);
  lastHistoryCleanup=Date.now();
}
async function retireTextTab(task) {
  // Keep the original conversation intact, but never let a broken page take
  // a place in the runnable pool. A fresh owned page can serve later work.
  await chrome.storage.local.set({['textRetiredTab:'+task.tab_id]:{taskId:task.id,at:Date.now()}});
  await saveBridgeTask({...task,quarantined:false,retired:true});
}
async function syncPoller() {
  if (pollerSetup) return pollerSetup;
  pollerSetup = (async () => {
    const {backend} = await chrome.storage.local.get('backend');
    const exists = await chrome.offscreen.hasDocument();
    if (backend?.enabled && backend.token) {
      if (!exists) await chrome.offscreen.createDocument({url:'poller.html', reasons:['WORKERS'],
        justification:'Run a worker that checks the local task queue every second while the sidebar is closed.'});
    } else if (exists) await chrome.offscreen.closeDocument();
  })();
  try { await pollerSetup; } finally { pollerSetup = null; }
}
async function setup() {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  await chrome.alarms.create("bridge-poll", { periodInMinutes: 0.5 });
  await focus.recoverInterruptedLease();
  scheduler.requestTick('setup');
}
chrome.runtime.onInstalled.addListener(setup);
chrome.runtime.onStartup.addListener(setup);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "bridge-poll") scheduler.requestTick('alarm'); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.task || changes.backend)) scheduler.requestTick('storage');
});
function baseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("后台地址必须是本机 HTTP 地址，例如 http://127.0.0.1:7860");
  return url.origin;
}
async function api(config, path, data) {
  let response;
  try { response = await fetch(baseUrl(config.url) + "/api/web-llm/bridge" + path, {
    method: data === undefined ? "GET" : "POST",
    headers: { "Authorization": "Bearer " + config.token, "Content-Type": "application/json" },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }), signal: AbortSignal.timeout(10000)
  }); } catch(error) {
    throw new Error("无法连接本机后台。请确认项目已启动、地址端口正确，并在浏览器扩展管理页重新加载扩展以启用本机访问权限。" + (error.name === "TimeoutError" ? "（请求超时）" : ""));
  }
  if(response.status === 404) throw new Error("项目后台缺少扩展接口，请重启项目以加载新版代码。");
  let body;
  try {body = await response.json();} catch {throw new Error("后台没有返回有效接口数据，请确认项目地址和端口正确。");}
  if (!response.ok || !body.ok) {
    const error=new Error(body.message || "后台请求失败");
    error.code=body.code;
    throw error;
  }
  return body;
}
async function invoke(task, type) {
  if(pageFlights.has(task.tab_id)) {
    const error=new Error('原标签页操作仍在进行，等待原操作结束；不会重复发送。');
    error.code='PAGE_RESPONSE_TIMEOUT';throw error;
  }
  let timeout;
  let stage = '获取绑定标签页';
  const flight=invokePage(task,type,value=>{stage=value;});
  pageFlights.set(task.tab_id,flight);
  const clearFlight=()=>{if(pageFlights.get(task.tab_id)===flight)pageFlights.delete(task.tab_id);};
  flight.then(clearFlight,clearFlight);
  try {
    return await Promise.race([flight, new Promise((_, reject) => {
      timeout = setTimeout(() => {
        const error = new Error(`网页操作 60 秒未响应（${stage}）；发送状态未知，后续仅检查已有回复，不会自动重发。`);
        error.code = 'PAGE_RESPONSE_TIMEOUT'; reject(error);
      }, 60000);
    })]);
  } finally { clearTimeout(timeout); }
}
async function invokePage(task, type, progress = () => {}) {
  const initialDoubaoSend = task.provider === '豆包' && type === 'AICF_SEND';
  const readExisting = ['AICF_READ','AICF_CANCEL','AICF_OPERATION_STATUS'].includes(type);
  let tab=await ensureTabReady(task.tab_id, {allowLoading:readExisting});
  const sendToPage=async (actionType=type)=>{
    tab=await ensureTabReady(task.tab_id, {allowLoading:readExisting});
    if (PromptBridge.provider(tab.url) !== task.provider) throw new Error("绑定的 LLM 标签页已关闭或切换网站。");
    progress('注入网页脚本');
    const injection=await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["protocol.js", "text_provider_policy.js", "text_attachments.js", "qianwen.js", "content.js"] });
    let boundDocumentId=injection?.[0]?.documentId;
    if(task.bridge){task=await textStore.bindDocument(task.id,tab.id,boundDocumentId);boundDocumentId=task.document_id;}
    progress(`等待网页处理 ${actionType}`);
    const pageTask=task.bridge ? {...task,document_id:boundDocumentId} : task;
    const result=await chrome.tabs.sendMessage(tab.id,{type:actionType,task:pageTask});
    if (!result?.ok) throw new Error(result?.error || "网页未响应");
    return result;
  };
  // Text tasks must never activate a tab or take focus from the project.
  // Injection and trusted debugger input operate on the background document.
  const wake=async(reason,action)=>{
    if(reason!=='stalled-reply')return action();
    // Emulate page focus for the bound renderer without changing the user's tab.
    const target={tabId:tab.id};
    let attached=false, emulated=false;
    try {
      try {await chrome.debugger.attach(target,'1.3'); attached=true;}
      catch {return await action();}
      try {await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:true});}
      catch {return await action();}
      emulated=true;
      await new Promise(resolve=>setTimeout(resolve,300));
      return await action();
    } finally {
      if(emulated)try {await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:false});}catch{}
      if(attached)try {await chrome.debugger.detach(target);}catch{}
    }
  };
  if(task.background_read_wake && type!=='AICF_SEND')return wake('stalled-reply',()=>sendToPage(type));
  if(tab.frozen && type==='AICF_SEND')return wake('frozen-send',()=>sendToPage(type));
  if(initialDoubaoSend && !tab.active) {
    let timeout;
    try {
      return await Promise.race([sendToPage(type),new Promise((_,reject)=>{
        timeout=setTimeout(()=>{const error=new Error('豆包后台页面暂未响应');
          error.code='DOUBAO_BACKGROUND_UNRESPONSIVE';reject(error);},DOUBAO_BACKGROUND_SEND_TIMEOUT);
      })]);
    } catch(error) {
      if(error.code!=='DOUBAO_BACKGROUND_UNRESPONSIVE')throw error;
      // The original sendMessage may still be running. Resume the persisted
      // operation after a brief wake; never issue a fresh AICF_SEND.
      return wake('doubao-unresponsive',()=>sendToPage('AICF_RESUME'));
    } finally {clearTimeout(timeout);}
  }
  return sendToPage(type);
}
const qianwenInputFlights=new Set();
async function trustedQianwenInput(sender,message) {
  const tabId=sender.tab?.id,target={tabId};
  if(sender.id!==chrome.runtime.id || sender.frameId!==0 || !sender.documentId || !Number.isInteger(tabId) ||
     PromptBridge.provider(sender.url || '')!=='千问')throw new Error('千问输入来源无法验证');
  if(qianwenInputFlights.has(tabId))throw new Error('千问输入正在处理，请等待原操作完成');
  qianwenInputFlights.add(tabId);
  let attached=false,focusEmulated=false;
  try {
    const validate=async()=>{
      const task=await savedTaskForId(message.taskId),tab=await chrome.tabs.get(tabId);
      const initial=message.purpose==='request' && task?.status==='prepared' && !task.sent_at && !task.dispatch_attempted_at;
      const repair=message.purpose==='repair' && task?.status==='waiting' && Boolean(task.json_repair?.request);
      if(!task || task.provider!=='千问' || task.tab_id!==tabId || (!initial && !repair) ||
         (task.bridge && (task.document_id!==sender.documentId || task.binding_epoch!==message.bindingEpoch)) ||
         PromptBridge.provider(tab.url || '')!=='千问' ||
         ((task.conversation_url || task.url) && tab.url!==(task.conversation_url || task.url)))
        throw new Error('千问输入任务或页面绑定已变化，未输入');
      return {task,text:repair ? task.json_repair.request : task.request};
    };
    const expected=await validate();
    TextProviderPolicy.fromName(expected.task.provider)?.assertInputLength(expected.text);
    if(typeof expected.text!=='string' || !expected.text.trim())throw new Error('千问任务文本为空');
    await chrome.debugger.attach(target,'1.3');attached=true;
    await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:true});focusEmulated=true;
    const checkFocus=async(requireSelection)=>{
      const result=(await chrome.scripting.executeScript({target:{tabId},func:(text,selectionRequired)=>{
        const el=document.activeElement;
        if(!el?.matches('div[data-slate-editor="true"][role="textbox"][contenteditable="true"]') ||
           !globalThis.QianwenPage)return false;
        const current=globalThis.QianwenPage.readEditor(el);
        if(current.trim() && current!==text)return false;
        const selection=window.getSelection();
        return !selectionRequired || Boolean(selection?.rangeCount &&
          el.contains(selection.anchorNode) && el.contains(selection.focusNode));
      },args:[expected.text,requireSelection]}))[0];
      if(result?.documentId!==sender.documentId || result.result!==true)
        throw new Error('千问输入框焦点、草稿或选区已变化，未覆盖内容');
    };
    await checkFocus(false);
    await validate();
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{
      type:'keyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{
      type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});
    await checkFocus(true);
    const current=await validate();
    if(current.text!==expected.text)throw new Error('千问任务内容已变化，未输入');
    // Trusted browser insertion updates Slate via its editing event pipeline.
    // No Enter, click, DOM mutation or private editor state is involved.
    await chrome.debugger.sendCommand(target,'Input.insertText',{text:expected.text});
    return {ok:true};
  } catch(error) {
    throw new Error('千问浏览器输入失败：'+error.message+'。若打开了此页面的开发者工具，请关闭后刷新页面再试；未改用 DOM 写入。');
  } finally {
    if(focusEmulated)try{await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:false});}catch{}
    if(attached)try{await chrome.debugger.detach(target);}catch{}
    qianwenInputFlights.delete(tabId);
  }
}
async function trustedContinueClick(sender, message) {
  const tabId = sender.tab?.id;
  if (!Number.isInteger(tabId) || sender.frameId !== 0) throw new Error('续写点击只允许来自顶层网页');
  if (PromptBridge.provider(sender.url || '') !== 'DeepSeek') throw new Error('真实鼠标输入仅允许用于 DeepSeek 续写');
  const saved = await savedTaskForId(message.taskId);
  if (saved?.id !== message.taskId || saved.provider !== 'DeepSeek' || saved.tab_id !== tabId || saved.status !== 'waiting') {
    throw new Error('续写任务已变化或结束，已取消点击');
  }
  const tab = await chrome.tabs.get(tabId);
  if (PromptBridge.provider(tab.url || '') !== 'DeepSeek') throw new Error('绑定标签页已离开 DeepSeek，已取消点击');
  const {x,y,viewportWidth,viewportHeight} = message;
  if (![x,y,viewportWidth,viewportHeight].every(Number.isFinite) || x < 0 || y < 0 || x >= viewportWidth || y >= viewportHeight) {
    throw new Error('续写按钮坐标无效，已取消点击');
  }
  if (!chrome.debugger?.attach || !chrome.debugger?.sendCommand) {
    throw new Error('扩展缺少 Chrome debugger 权限，请重新加载并接受权限提示');
  }
  const target = {tabId};
  await chrome.debugger.attach(target, '1.3');
  try {
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {type:'mouseMoved', x, y, button:'none'});
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {type:'mousePressed', x, y, button:'left', clickCount:1});
    await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {type:'mouseReleased', x, y, button:'left', clickCount:1});
  } finally {
    try { await chrome.debugger.detach(target); } catch { /* The tab may close during the click. */ }
  }
  return {ok:true, method:'trusted-debugger-mouse'};
}
async function trustedDoubaoSendClick(sender, message) {
  const tabId=sender.tab?.id;
  let attached=false, inputAttempted=false, focusEmulated=false;
  const target={tabId};
  try {
    if (!Number.isInteger(tabId) || sender.frameId!==0) throw new Error('豆包发送只允许来自绑定标签页的顶层页面');
    if (sender.id!==chrome.runtime.id) throw new Error('豆包发送只允许由已注入扩展的页面请求');
    if (PromptBridge.provider(sender.url || '')!=='豆包' || message.buttonTestId!=='chat_input_send_button')
      throw new Error('豆包发送按钮或网页来源校验失败');
    const saved=await savedTaskForId(message.taskId);
    const tab=await chrome.tabs.get(tabId);
    const validInitial=saved?.status==='sending' && Number.isFinite(saved.dispatch_attempted_at);
    const validRepair=saved?.status==='waiting' && Boolean(saved.json_repair);
    const isBound=saved?.id===message.taskId && saved.provider==='豆包' && saved.tab_id===tabId;
    if (!isBound || (!validInitial && !validRepair)) {
      const detail=`任务状态不匹配（${saved?.status||'missing'}，${saved?.provider||'no provider'}，标签页 ${saved?.tab_id??'none'} / ${tabId}，发送时间 ${Boolean(saved?.dispatch_attempted_at)}）`;
      throw new Error('豆包发送校验失败：'+detail);
    }
    if (PromptBridge.provider(tab.url || '')!=='豆包') throw new Error('绑定标签页已离开豆包，已取消发送');
    const {x,y,viewportWidth,viewportHeight}=message;
    if (![x,y,viewportWidth,viewportHeight].every(Number.isFinite) || viewportWidth<=0 || viewportHeight<=0 ||
        x<0 || y<0 || x>=viewportWidth || y>=viewportHeight) throw new Error('豆包发送按钮坐标无效');
    if (tab.active) return {ok:false,useDomClick:true};
    const dispatch=async()=>{
    if (!chrome.debugger?.attach || !chrome.debugger?.sendCommand)
      throw new Error('扩展没有可用的真实鼠标输入权限；后台标签页未发送，请在扩展管理页检查 debugger 权限');
    try { await chrome.debugger.attach(target,'1.3'); attached=true; }
    catch(error) { throw new Error('无法向豆包后台标签页发送真实鼠标输入；未改用合成点击：' + (error.message || 'debugger 附加失败')); }
    // Give the background renderer page focus without activating a browser tab.
    // A dispatched mouse event alone does not prove the site accepted submit.
    await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:true});
    focusEmulated=true;
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseMoved',x,y,button:'none'});
    inputAttempted=true;
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
    return {ok:true,dispatched:true,method:'trusted-debugger-mouse'};
    };
    return await dispatch();
  } catch(error) {
    return {ok:false,dispatched:inputAttempted,error:error.message || String(error)};
  } finally {
    if (focusEmulated) {
      try { await chrome.debugger.sendCommand(target,'Emulation.setFocusEmulationEnabled',{enabled:false}); } catch {}
    }
    if (attached) { try { await chrome.debugger.detach(target); } catch {} }
  }
}
async function ensureTabReady(tabId, {allowLoading=false} = {}) {
  let tab = await chrome.tabs.get(tabId);
  // Keep the dedicated provider page from being discarded by Chrome's memory
  // saver. This does not activate or focus the tab.
  if (tab.autoDiscardable !== false && chrome.tabs.update) {
    tab = await chrome.tabs.update(tabId, {autoDiscardable:false});
  }
  // Existing replies remain readable while unrelated page resources load.
  if (!tab.discarded && allowLoading) return tab;
  if (tab.discarded || tab.status === "loading") {
    let rejectReady;
    const ready = new Promise((resolve, reject) => {
      rejectReady = reject;
      let settled = false;
      const finish = error => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(onUpdated);
        if (error) reject(error); else resolve();
      };
      const onUpdated = (updatedId, changeInfo, updatedTab) => {
        if (updatedId === tabId && changeInfo.status === "complete" && !updatedTab.discarded) finish();
      };
      const timeout = setTimeout(() => finish(Object.assign(new Error("后台 LLM 标签页加载超时，等待页面恢复后继续检查。"), {code:'PAGE_LOAD_TIMEOUT'})), 30000);
      chrome.tabs.onUpdated.addListener(onUpdated);
      chrome.tabs.get(tabId).then(updated => {
        if (!updated.discarded && updated.status === "complete") finish();
      }).catch(finish);
    });
    if (tab.discarded) {
      try { await chrome.tabs.reload(tabId); }
      catch (error) { rejectReady(error); throw error; }
    }
    await ready;
  }
  tab = await chrome.tabs.get(tabId);
  return tab;
}
async function preserveUncertainTask(task, config, error) {
  const latest = await savedTaskForId(task.id);
  if (latest?.id === task.id) {
    await saveBridgeTask({...latest, read_only_recovery:true, message:error.message});
  }
  await api(config, '/tasks/' + task.id, {lease:task.bridge.lease,status:'waiting',message:error.message});
}
async function recoverConversation(task) {
  // A closed page cannot finish an in-flight action. Only restart a prepared
  // task with no durable dispatch evidence; submitted tasks remain read-only.
  const bound=await chrome.tabs.get(task.tab_id).catch(()=>null);
  if (!bound && task.status==='prepared' && !task.sent_at && !task.dispatch_attempted_at &&
      !task.read_only_recovery && !pageFlights.has(task.tab_id)) {
    const providerId=TextProviderPolicy.fromName(task.provider)?.id;
    if (!providerId) return task;
    const replacement=await chrome.tabs.create({url:PROVIDER_URLS[providerId],active:false});
    await textStore.registerOwnedTab(providerId,replacement);
    await ensureTabReady(replacement.id);
    await textStore.rebind(task.id,{tabId:replacement.id});
    return textStore.patchFromWorker(task.id,{url:replacement.url,read_only_recovery:false,
      page_operation:null,message:'原标签页已关闭，已重建未发送任务的专用页面。'});
  }
  if (!task.sent_at || !task.conversation_url) return task;
  const target=task.conversation_url;
  if (PromptBridge.provider(target)!==task.provider) return task;
  // New chats change from the home URL after send. Wait for a stable ID.
  if (!/\/(?:c|chat|s)\/[^/?#]+/.test(new URL(target).pathname)) return task;
  const tab=await chrome.tabs.get(task.tab_id).catch(()=>null);
  if (tab?.url===target) return task;
  // A route change can be ChatGPT/DeepSeek settling after send. Let the
  // content script adopt it when the submitted request is visible.
  if (tab && PromptBridge.provider(tab.url)===task.provider) {
    const observed=await invoke(task,'AICF_READ').catch(()=>null);
    if (observed?.task?.conversation_url===tab.url || observed?.task?.status==='complete') return observed.task;
  }
  // Recovery may redirect or fail to load. Repeated polls must not navigate
  // again, even if the first navigation never returned an acknowledgement.
  if (task.conversation_recovery_attempted_at) return task;
  const isOwned=await textStore.ownsTab(task.tab_id);
  task=await textStore.patchFromWorker(task.id,{read_only_recovery:true,
    conversation_recovery_attempted_at:Date.now(),
    message:'已尝试找回任务对话；后续仅检查回复，不再自动刷新或跳转。'});
  let recovered;
  if (tab && isOwned) {
    recovered=await chrome.tabs.update(tab.id,{url:target,autoDiscardable:false});
  } else {
    recovered=await chrome.tabs.create({url:target,active:false,
      ...(tab?.windowId===undefined ? {} : {windowId:tab.windowId})});
    const id=TextProviderPolicy.fromName(task.provider)?.id;
    if (id) await textStore.registerOwnedTab(id,recovered);
  }
  await ensureTabReady(recovered.id);
  if(recovered.id!==task.tab_id)await textStore.rebind(task.id,{tabId:recovered.id});
  return textStore.patchFromWorker(task.id,{message:'后台已找回任务对话，继续只读检查回复。'});
}
async function processExisting(task, config) {
  if (task.bridge.url !== baseUrl(config.url)) throw new Error('任务 '+task.id+' 属于旧后台地址，请恢复原地址处理');
  let remote;
  try {remote=(await api(config, '/tasks/' + task.id)).task;}
  catch(error) {
    if(error.code!=='TASK_GONE')throw error;
    if(task.result)await chrome.storage.local.set({['orphanedTextResult:'+task.id]:{
      id:task.id,result:task.result,raw:task.raw,at:Date.now(),message:'后端任务已清理；结果保留供恢复，未自动应用。'}});
    await retireTextTab({...task,status:'cancelled',message:error.message,bridge:{...task.bridge,reported:true}});
    return;
  }
  if (['cancelled','error','complete'].includes(remote.status)) {
    try { await invoke(task, 'AICF_CANCEL'); } catch {}
    await saveBridgeTask({...task,status:remote.status,message:remote.message,
      quarantined:remote.status !== 'complete',bridge:{...task.bridge,reported:true}});
    return;
  }
  // A content script may have completed while the service worker was asleep.
  // Report that durable result before accepting an independently timed-out
  // backend snapshot.
  if (['complete','error','timeout','cancelled'].includes(task.status)) {
    await api(config, '/tasks/' + task.id, {lease:task.bridge.lease,
      status:task.status === 'complete' ? 'complete' : 'error',result:task.result,
      raw:task.raw,diagnostics:[...(task.continuation_diagnostics || []),...(task.attachment_receipt?[task.attachment_receipt]:[])],
      message:task.status === 'complete' ? '网页 LLM 任务已完成' : task.message || '扩展任务已结束'});
    await saveBridgeTask({...task,quarantined:task.status!=='complete',bridge:{...task.bridge,reported:true}});
    return;
  }
  if (remote.status==='timeout') {
    try { await invoke(task, 'AICF_CANCEL'); } catch {}
    await saveBridgeTask({...task,status:remote.status,message:remote.message,
      quarantined:remote.status !== 'complete',bridge:{...task.bridge,reported:true}});
    return;
  }
  if(!task.sent_at && !task.dispatch_attempted_at) {
    try {TextProviderPolicy.fromName(task.provider)?.assertInputLength(task.request);}
    catch(error) {
      await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'error',message:error.message});
      await retireTextTab({...task,status:'error',message:error.message,bridge:{...task.bridge,reported:true}});
      return;
    }
  }
  task=await recoverConversation(task);
  let retryUnsent = false;
  if(task.status==='prepared' && !task.sent_at && !task.dispatch_attempted_at && !task.read_only_recovery) {
    const operation=await invoke(task,'AICF_OPERATION_STATUS').catch(()=>null);
    if(operation?.task?.dispatch_attempted_at || operation?.task?.sent_at)
      task=await textStore.patchFromWorker(task.id,{read_only_recovery:true});
    else if(operation?.task?.id===task.id && operation.task.busy===false)
      retryUnsent = true;
  }
  let observed;
  let backgroundReadWake = false;
  if (task.provider === 'DeepSeek' && ['sending','waiting'].includes(task.status) &&
      task.sent_at && Date.now() - Date.parse(task.sent_at) >= 15000) {
    const boundTab = await chrome.tabs.get(task.tab_id).catch(() => null);
    if (boundTab && !boundTab.active) {
      const key = `deepseekReadWake:${task.tab_id}`;
      const stored = (await chrome.storage.local.get(key))[key];
      const previous = stored?.taskId === task.id ? stored : {count:0,at:0};
      if (Date.now() - previous.at >= 30000) {
        backgroundReadWake = true;
        await chrome.storage.local.set({[key]:{taskId:task.id,count:previous.count+1,at:Date.now()}});
      }
    }
  }
  const readOnly=task.read_only_recovery || task.sent_at || task.dispatch_attempted_at ||
    task.page_operation?.kind==='send';
  try { observed = await invoke({...task,background_read_wake:backgroundReadWake},retryUnsent ? 'AICF_SEND' : readOnly ? 'AICF_READ' : 'AICF_RESUME'); }
  catch (error) {
    if(/^MEDIA_ATTACHMENT_/.test(error.message)) {
      await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'error',message:error.message});
      await retireTextTab({...task,status:'error',message:error.message,bridge:{...task.bridge,reported:true}});
      return;
    }
    if (error.code === 'PAGE_RESPONSE_TIMEOUT' || error.code === 'PAGE_LOAD_TIMEOUT' ||
        /CONVERSATION_BINDING_PENDING|会话地址缺少本任务发送证据/.test(error.message) ||
        (['sending','waiting'].includes(task.status) && /No tab|找不到标签页/i.test(error.message))) {
      await preserveUncertainTask(task,config,error); return;
    }
    const latest = await savedTaskForId(task.id);
    if (retryUnsent && latest?.status==='prepared' && !latest.sent_at && !latest.dispatch_attempted_at) {
      await textStore.patchFromWorker(task.id,{message:error.message});
      await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'waiting',
        message:error.message+'；尚未发送，等待网页就绪后继续提交。'});
      return;
    }
    await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'error',message:error.message});
    await saveBridgeTask({...task,status:'error',message:error.message,bridge:{...task.bridge,reported:true}});
    return;
  }
  if (observed.task?.status === 'complete') {
    const completed = observed.task;
    await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'complete',
      result:completed.result,raw:completed.raw,diagnostics:[...(completed.continuation_diagnostics || []),...(completed.attachment_receipt?[completed.attachment_receipt]:[])],
      message:'网页 LLM 任务已完成'});
    await saveBridgeTask({...completed,bridge:{...task.bridge,reported:true}});
    return;
  }
  const continuation=(observed.task?.continue_events || []).map(e=>`${e.at} 第${e.attempt}次请求点击，回复${e.chars}字符`).join('；');
  await api(config,'/tasks/'+task.id,{lease:task.bridge.lease,status:'waiting',
    diagnostics:observed.task?.continuation_diagnostics,
    message:(observed.task?.message || task.message || '网页处理中') +
      (continuation ? '\n续写检查记录：'+continuation : '')});
}
async function processNew(claimed, tab, settings, config) {
  await api(config,'/tasks/'+claimed.id,{lease:claimed.lease,status:'waiting',message:'扩展已领取，正在保存任务并连接绑定网页'});
  const task={id:claimed.id,provider:PROVIDER_NAMES[claimed.provider],kind:claimed.kind,
    entry:claimed.entry,conversation_key:claimed.conversation_key,
    attachments:claimed.metadata?.attachments || [],
    conversation_title:claimed.conversation_title || claimed.id,
    tab_id:tab.id,url:tab.url,request:claimed.request,
    draft:{prompt:claimed.input.base_prompt || claimed.input.edited_prompt || claimed.input.original_prompt || '',
      issue:claimed.input.video_issues || ''},
    force_new_conversation:true,
    settings:settings?.[PROVIDER_NAMES[claimed.provider]] || {},
    status:'prepared',created_at:new Date((claimed.execution_started_at || claimed.created_at)*1000).toISOString(),
    deadline_at:claimed.execution_deadline_at,
    message:'已领取项目任务，准备提交',
    page_operation:{id:crypto.randomUUID(),kind:'send',phase:'started',started_at:Date.now()},
    bridge:{url:baseUrl(config.url),lease:claimed.lease,reported:false}};
  try {
    // The task-specific record is durable before any page action.
    await saveBridgeTask(task);
    try {
      TextProviderPolicy.fromName(task.provider)?.assertInputLength(task.request);
      const sent=await invoke(task,'AICF_SEND');
      if(sent.task?.status==='waiting' || sent.task?.status==='complete')
        await textStore.patchFromWorker(task.id,{page_operation:{...task.page_operation,phase:'finished'}});
      if (sent.task?.status === 'prepared') {
        await api(config,'/tasks/'+task.id,{lease:claimed.lease,status:'waiting',message:sent.task.message});
        return;
      }
    } catch (error) {
      if (error.code === 'PAGE_RESPONSE_TIMEOUT' || /CONVERSATION_BINDING_PENDING|会话地址缺少本任务发送证据/.test(error.message)) { await preserveUncertainTask(task,config,error); return; }
      const latest=await savedTaskForId(task.id);
      if(/^(MEDIA_ATTACHMENT_|PROVIDER_INPUT_LIMIT:)/.test(error.message)) {
        await api(config,'/tasks/'+task.id,{lease:claimed.lease,status:'error',message:error.message});
        await retireTextTab({...task,status:'error',message:error.message,bridge:{...task.bridge,reported:true}});
      } else if (latest?.status === 'prepared' && !error.message.includes('发送状态不明确')) {
        await saveBridgeTask({...latest,message:error.message});
        await api(config,'/tasks/'+task.id,{lease:claimed.lease,status:'waiting',
          message:error.message+'；自动流程会继续检查，不会重复提交。'});
      } else {
        await api(config,'/tasks/'+task.id,{lease:claimed.lease,status:'error',message:error.message});
      }
      return;
    }
    await api(config,'/tasks/'+task.id,{lease:claimed.lease,status:'waiting',
      message:'已尝试提交到 '+task.provider+'，正在确认发送并读取回复；不会重复提交'});
  } catch (error) {
    await api(config,'/tasks/'+claimed.id,{lease:claimed.lease,status:'error',
      message:'扩展领取后处理失败：'+error.message});
    throw error;
  }
}
async function scanExistingTasks() {
  if(scanningExisting)return;
  scanningExisting=true;
  try {
  const saved=await chrome.storage.local.get(null);
  const config=saved.backend;
  if(!config?.enabled || !config.token)return;
  const tasks=Object.entries(saved).filter(([key,value])=>key.startsWith('bridgeTask:') && value?.id &&
    value.bridge && (!value.bridge.reported || value.quarantined || needsTextTabCleanup(value))).map(([,value])=>value);
  for(const task of tasks) {
    scheduler.scheduleTask(task.id,task.tab_id,async()=>{
      const latest=await textStore.get(task.id);
      if(!latest || !latest.bridge)return;
      if(needsTextTabCleanup(latest)) {await closeCompletedTextTab(latest);return;}
      if(latest.bridge.reported && !latest.quarantined)return;
      if(latest.bridge.reported) {
        const checks=(latest.quarantine_checks || 0)+1;
        const state=await invoke(latest,'AICF_SLOT_STATUS').catch(()=>null);
        if(state?.task?.idle)await saveBridgeTask({...latest,quarantined:false});
        else if(checks>=3)await retireTextTab(latest);
        else await saveBridgeTask({...latest,quarantine_checks:checks});
      } else await processExisting(latest,config);
    });
  }
  } finally {scanningExisting=false;}
}
async function pump() {
  if (pumping) return;
  pumping=true;
  try {
    await syncPoller();
    const saved=await chrome.storage.local.get(null);
    await cleanupTextHistory(saved);
    const config=saved.backend;
    if (!config?.enabled || !config.token) return;
    if (saved.task && (typeof saved.task.id !== 'string' || !saved.task.id.trim()))
      await chrome.storage.local.set({invalidTaskBackup:saved.task,task:null});
    // Migrate the single backend slot from older extension releases.
    if (saved.task?.bridge && saved.task.id) {
      const key=bridgeTaskKey(saved.task.id);
      if (!saved[key]) await saveBridgeTask(saved.task);
      await chrome.storage.local.set({task:null});
      saved[key]=saved[key] || saved.task;
      saved.task=null;
    }
    const tasks=Object.entries(saved).filter(([key,value])=>
      key.startsWith('bridgeTask:') && value?.id && value.bridge).map(([,value])=>value);
    const manual=saved.task && ['prepared','sending','waiting'].includes(saved.task.status) ? saved.task : null;
    const videoTabIds=new Set(Object.entries(saved).filter(([key])=>key.startsWith('videoTask:'))
      .map(([,value])=>value?.tabId).filter(Number.isInteger));
    for(const [key,value] of Object.entries(saved))if(key.startsWith('videoDedicatedTabId:') && Number.isInteger(value))videoTabIds.add(value);
    const allTabs=(await chrome.tabs.query({}))
      .filter(tab=>!videoTabIds.has(tab.id));
    const clientId=saved.clientId || crypto.randomUUID();
    if (!saved.clientId) await chrome.storage.local.set({clientId});
    const advertised=Object.keys(PROVIDER_NAMES);
    const providerCapabilities=Object.fromEntries(advertised.map(id=>[id,TextProviderPolicy.policy(id).capabilities]));
    const browserType=/Edg\//.test(navigator.userAgent)?'Edge':'Chrome';
    const pool=await api(config,'/ping',{client_id:clientId,providers:advertised,
      browser:{type:browserType,name:saved.browserName || browserType+' '+clientId.slice(0,8),
        version:chrome.runtime.getManifest().version,channels:(await videoCatalog()).map(item=>item.id)},
      capabilities:['video_learning_attachments_v1'],provider_capabilities:providerCapabilities});
    if(await syncQueueReset(pool,saved))return;
    await chrome.storage.local.set({textEnabled:pool.text_enabled !== false});
    if(pool.text_enabled === false) {
      await chrome.storage.local.set({bridgeStatus:{message:'已连接 · 文字任务由主浏览器执行，当前浏览器接收指定视频任务',at:Date.now()}});
      return;
    }
    // Missing pool capability means an older backend: preserve serial mode.
    const limits=pool.limits || {total:3,deepseek:1,chatgpt:1,doubao:1};
    const occupied=new Set(tasks.filter(task=>!task.bridge.reported || task.quarantined).map(task=>task.tab_id));
    const unusable=new Set(tasks.filter(task=>task.quarantined || task.retired).map(task=>task.tab_id));
    for(const key of Object.keys(saved))if(key.startsWith('textRetiredTab:'))unusable.add(Number(key.split(':')[1]));
    for(const id of pageFlights.keys())occupied.add(id);
    const targets={},autoPools={},poolErrors=[];
    for (const [id,name] of Object.entries(PROVIDER_NAMES)) {
      // Migrate only IDs recorded by this extension, never arbitrary user chats.
      const recordedIds=new Set([...(saved.autoPools?.[id] || []),saved.autoBindings?.[id],
        ...tasks.filter(task=>task.provider===name).map(task=>task.tab_id)].filter(Number.isInteger));
      for(const tab of allTabs) {
        if(recordedIds.has(tab.id) && PromptBridge.provider(tab.pendingUrl || tab.url)===name)
          await textStore.registerOwnedTab(id,tab);
      }
      const ownedIds=await textStore.listOwnedTabs(id);
      const candidates=allTabs.filter(tab=>ownedIds.includes(tab.id) &&
        PromptBridge.provider(tab.pendingUrl || tab.url)===name && tab.id!==manual?.tab_id && !unusable.has(tab.id) &&
        // Completed pages are cleaned after acknowledgement, never recycled
        // while a close is pending or while another task owns their binding.
        !tasks.some(task=>task.tab_id===tab.id && task.status==='complete' && task.bridge.reported));
      const cap=Math.max(1,Math.min(4,limits[id] || 1));
      const selected=candidates.slice(0,cap);
      const running=tasks.filter(task=>!task.bridge.reported && task.provider===name).length;
      const desired=Math.min(cap,running+Number(pool.queued?.[id] || 0));
      // Text jobs use extension-owned background tabs. Never inject into a
      // user's existing chat or a tab reserved for browser video generation.
      while(selected.length<desired) {
        try {
          const tab=await chrome.tabs.create({url:PROVIDER_URLS[id],active:false});
          await textStore.registerOwnedTab(id,tab);
          selected.push(tab);
          await chrome.tabs.update(tab.id,{autoDiscardable:false});
        } catch(error) {poolErrors.push(name+'：'+error.message);break;}
      }
      if(selected.length)targets[id]=selected;
      autoPools[id]=selected.map(tab=>tab.id);
    }
    const autoBindings=Object.fromEntries(Object.entries(targets).map(([id,tabs])=>[id,tabs[0].id]));
    await chrome.storage.local.set({autoPools,autoBindings});
    await chrome.storage.local.set({bridgeStatus:{message:'后台已连接 · 标签页池：'+
      (Object.entries(targets).map(([id,tabs])=>`${PROVIDER_NAMES[id]} ${tabs.length}/${limits[id] || 1} 页`).join('、') || '请打开并登录所选服务网页')+
      ` · ${tasks.filter(task=>!task.bridge.reported).length} 项进行中`+
      (poolErrors.length?' · '+poolErrors.join('；'):''),at:Date.now()}});
    const available=Object.entries(targets).flatMap(([provider,tabs])=>tabs.filter(tab=>!occupied.has(tab.id) &&
      !scheduler.isTabRunning(tab.id)).map(tab=>[provider,tab]));
    for(const [provider,tab] of available) {
      scheduler.scheduleTask('claim:'+tab.id,tab.id,async()=>{
        const currentTab=await chrome.tabs.get(tab.id).catch(()=>null);
        if(!currentTab || !TextProviderPolicy.policy(provider)?.matchesUrl(currentTab.url || ''))return;
        const claimed=(await api(config,'/claim',{client_id:clientId,providers:[provider],tab_id:tab.id,
          capabilities:TextProviderPolicy.policy(provider).capabilities})).task;
        if(claimed) {
          try {await processNew(claimed,tab,saved.settings,config);}
          finally {scheduler.deferTask(claimed.id);}
        }
      });
    }
  } catch (error) {
    await chrome.storage.local.set({bridgeStatus:{message:error.message,error:true,at:Date.now()}});
  } finally {
    pumping=false;
  }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_RUNOW_SUBMISSION_PATCH') {
    runowPatchCommand(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message}));
    return true;
  }
  if(message?.type==='AICF_QIANWEN_INPUT') {
    trustedQianwenInput(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message}));
    return true;
  }
  if(message?.type==='AICF_TASK_ATTACHMENT') {
    taskAttachment(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (message?.type==='AICF_TASK_PATCH') {
    textStore.patchFromPage(sender,message).then(task=>respond({ok:true,task}))
      .catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_RUNOW_INSERT_TEXT') {
    runowInsertText(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_RUNOW_DURATION_KEY') {
    runowDurationKey(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_RUNOW_PROGRESS') {
    runowProgress(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_VIDEO_TRUSTED_CLICK') {
    videoTrustedClick(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_VIDEO_TRUSTED_KEY') {
    videoTrustedKey(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_VIDEO_CHOOSE_FILE') {
    videoTrustedChooseFile(sender,message).then(respond).catch(error=>respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_TRUSTED_CONTINUE_CLICK') {
    trustedContinueClick(sender, message).then(respond).catch(error => respond({ok:false,error:error.message || String(error)}));
    return true;
  }
  if (message?.type === 'AICF_TRUSTED_DOUBAO_SEND') {
    trustedDoubaoSendClick(sender, message).then(respond).catch(error => respond({ok:false,dispatched:false,error:error.message || String(error)}));
    return true;
  }
  if (sender.id === chrome.runtime.id && message?.type === 'AICF_RESULT_READY') {
    if(message.taskId)scheduler.forceTask(message.taskId);
    scheduler.requestTick('result-ready');
    respond({ok:true});
    return;
  }
  if (sender.id !== chrome.runtime.id || message?.type !== "AICF_BACKEND_POLL") return;
  scheduler.requestTick('clock');
  respond({ok:true,accepted:true});return;
});
