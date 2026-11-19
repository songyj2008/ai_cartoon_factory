"use strict";
// Runow-specific task payload preparation. Loaded once by the service worker.
globalThis.VideoChannelDrivers = globalThis.VideoChannelDrivers || {};
function createRunowDriver(origin, prefix = '') {
  return {
    nativeUpload:true,
    freshTab:true,
    closeAfterComplete:true,
    matchesUrl(value) {
      const url=new URL(value);
      return url.origin===origin && url.pathname.replace(/^\/zh-cn(?=\/)/,'').startsWith('/runow/');
    },
    async status(task) {
      if (!task.receipt?.taskId || task.receipt.source !== 'runninghub_commit') {
        throw new Error('缺少本次提交的可靠远端任务 ID，请人工核对原任务；禁止按最新任务下载');
      }
      const now = Date.now();
      // Persist the schedule so extension restarts and read failures keep it.
      if (!task.runowResultStartedAt) {
        task.runowResultStartedAt = task.submittedAt || now;
        await videoTaskSave(task);
      }
      const dueAt = task.runowResultNextAt || task.runowResultStartedAt + 5 * 60 * 1000;
      if (now < dueAt) return {ok: true, ready: false};
      task.runowResultNextAt = now + 30 * 1000;
      await videoTaskSave(task);
      const resultUrl = origin + prefix + '/call-api/bill-task';
      let tab = task.resultTabId ? await chrome.tabs.get(task.resultTabId).catch(() => null) : null;
      if (!tab || tab.url !== resultUrl) {
        tab = await chrome.tabs.create({url: resultUrl, active: false});
        task.resultTabId = tab.id;
        await videoTaskSave(task);
      } else {
        await chrome.tabs.reload(tab.id);
      }
      await ensureTabReady(tab.id);
      await chrome.scripting.executeScript({target: {tabId: tab.id},
        files: ['video/runow_contract.js', 'video/runow_results.js']});
      const result = await chrome.tabs.sendMessage(tab.id, {type: 'AICF_RUNOW_RESULT_STATUS', channel:task.channel,
        taskId:task.receipt.taskId});
      if (!result?.ok) throw new Error(result?.error || 'RunningHub 任务列表读取失败');
      if ((result.receipt && result.receipt.taskId !== task.receipt.taskId) ||
          (result.ready && (!result.receipt || result.receipt.taskId !== task.receipt.taskId))) {
        throw new Error('RunningHub 返回结果与本次提交任务 ID 不一致');
      }
      if (result.receipt) result.receipt = {...task.receipt, ...result.receipt, source:'runninghub_commit'};
      if (result.ready) result.receipt.resultUrl = result.url;
      return result;
    },
    async cleanup(task) {
      if (!task.resultTabId) return;
      const tab = await chrome.tabs.get(task.resultTabId).catch(() => null);
      if (tab?.url === origin + prefix + '/call-api/bill-task') await chrome.tabs.remove(tab.id);
      delete task.resultTabId;
      await videoTaskSave(task);
    },
    async prepare(config,task) {
      task.media=[];
      for(const item of task.input.media || []) {
        const local=await videoAssetLocalPath(config,task,item);
        task.media.push({...item,...local});
      }
      task.filePaths=task.media.map(item=>item.path);
      await videoTaskSave(task);
    }
  };
}
globalThis.VideoChannelDrivers.runninghub_web = createRunowDriver('https://www.runninghub.cn');
globalThis.VideoChannelDrivers.runninghub_international_web = createRunowDriver('https://www.runninghub.ai','/zh-cn');
function runowDriver(task) {
  return ['runninghub_web','runninghub_international_web'].includes(task?.channel)
    ? VideoChannelDrivers[task.channel] : null;
}

async function runowProgress(sender,message) {
  const saved=await chrome.storage.local.get([videoKey(message.taskId),'backend']);
  const task=saved[videoKey(message.taskId)],config=saved.backend;
  if(!runowDriver(task) || task.state!=='claimed' || task.tabId!==sender.tab?.id ||
     sender.frameId!==0 || !runowDriver(task).matchesUrl(sender.url) || !config?.enabled)
    throw new Error('Runow 进度回报与任务页面不匹配');
  const response=await fetch(baseUrl(config.url)+'/api/runow-web/tasks/'+encodeURIComponent(task.id)+'/progress',{
    method:'POST',headers:{Authorization:'Bearer '+config.token,'Content-Type':'application/json'},
    body:JSON.stringify({lease:task.lease}),signal:AbortSignal.timeout(10000)});
  const result=await response.json();
  if(!response.ok || !result.ok)throw new Error(result.message || 'Runow 准备任务已失效');
  task.stage=String(message.stage || '').slice(0,150);
  await videoTaskSave(task);
  return {ok:true};
}

async function runowPatchCommand(sender, message) {
  const task = (await chrome.storage.local.get(videoKey(message.taskId)))[videoKey(message.taskId)];
  if (!runowDriver(task) || task.state !== 'claimed' || task.tabId !== sender.tab?.id ||
      sender.frameId !== 0 || !runowDriver(task).matchesUrl(sender.url)) {
    throw new Error('Runow 提交补丁与任务不匹配');
  }
  const results = await chrome.scripting.executeScript({target: {tabId: task.tabId}, world: 'MAIN',
    func: runowSubmissionPatch, args: [message.command, {id: task.id,
      media: task.input.media, promptParts: task.input.prompt_parts,origin:new URL(sender.url).origin}]});
  const result = results[0]?.result || {ok: false, error: 'Runow 提交补丁未响应'};
  if (message.command === 'receipt' && result.ready && result.taskId) {
    task.receipt = result;
    await videoTaskSave(task);
  }
  return result;
}

async function runowDurationKey(sender,message) {
  const task=(await chrome.storage.local.get(videoKey(message.taskId)))[videoKey(message.taskId)];
  if(!['ArrowLeft','ArrowRight'].includes(message.key) || !runowDriver(task) ||
     task.state!=='claimed' || task.tabId!==sender.tab?.id || sender.frameId!==0 ||
     !runowDriver(task).matchesUrl(sender.url))throw new Error('Runow 时长按键与任务不匹配');
  const tab=await chrome.tabs.get(task.tabId);
  if(!tab.active || !runowDriver(task).matchesUrl(tab.url))throw new Error('Runow 时长任务页面未激活');
  const target={tabId:task.tabId};
  // Only a focused slider may receive these keys, never the prompt editor or the page.
  const focused=await chrome.scripting.executeScript({target,func:()=>{
    const element=document.activeElement;
    return Boolean(element?.matches('input[type="range"],[role="slider"]'));
  }});
  if(focused.length!==1 || focused[0].result!==true)throw new Error('Runow 时长滑块未获得焦点');
  await chrome.debugger.attach(target,'1.3');
  try {
    const code=message.key==='ArrowRight'?39:37;
    const key={key:message.key,code:message.key,windowsVirtualKeyCode:code,nativeVirtualKeyCode:code};
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'rawKeyDown',...key});
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'keyUp',...key});
  } finally {try{await chrome.debugger.detach(target);}catch{}}
  return {ok:true};
}

async function runowInsertText(sender,message) {
  const task=(await chrome.storage.local.get(videoKey(message.taskId)))[videoKey(message.taskId)];
  const allowed=new Set((task?.input?.prompt_parts || []).filter(part=>typeof part.text==='string').map(part=>part.text));
  allowed.add('@');
  if(!runowDriver(task) || task.state!=='claimed' || task.tabId!==sender.tab?.id ||
     sender.frameId!==0 || !runowDriver(task).matchesUrl(sender.url) || !allowed.has(message.text))
    throw new Error('Runow 文案输入与任务不匹配');
  const tab=await chrome.tabs.get(task.tabId);
  if(!tab.active || !runowDriver(task).matchesUrl(tab.url))throw new Error('Runow 文案任务页面未激活');
  const target={tabId:task.tabId};
  const focused=await chrome.scripting.executeScript({target,func:()=>
    Boolean(document.activeElement?.matches('.qcv2-hero-mention-editor[contenteditable="true"]'))});
  if(focused.length!==1 || focused[0].result!==true)throw new Error('Runow 提示词框未获得焦点');
  await chrome.debugger.attach(target,'1.3');
  try {
    if(message.text==='@') {
      const key={key:'@',code:'Digit2',modifiers:8,windowsVirtualKeyCode:50,nativeVirtualKeyCode:50};
      await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'keyDown',text:'@',...key});
      await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'keyUp',...key});
    } else {
      await chrome.debugger.sendCommand(target,'Input.insertText',{text:message.text});
    }
  } finally {try{await chrome.debugger.detach(target);}catch{}}
  return {ok:true};
}
