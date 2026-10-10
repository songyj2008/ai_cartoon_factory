"use strict";
// Separate video task lifecycle. Existing text-LLM tasks never enter this path.
let videoPumping = false;
const videoKey = id => `videoTask:${id}`;
let videoCatalogPromise;
function videoCatalog() {
  if(!videoCatalogPromise)videoCatalogPromise=fetch(chrome.runtime.getURL('video/channels.json')).then(response=>response.json());
  return videoCatalogPromise;
}
async function videoAdapter(channelId) {
  const adapter=(await videoCatalog()).find(item=>item.id===channelId);
  if(!adapter)throw new Error('扩展缺少视频渠道适配器：'+channelId);
  return adapter;
}
async function videoApi(config, path, body) {
  const response = await fetch(baseUrl(config.url) + '/api/web-video/bridge' + path, {
    method:'POST', headers:{Authorization:'Bearer ' + config.token,'Content-Type':'application/json'},
    body:JSON.stringify(body), signal:AbortSignal.timeout(20000)
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.message || '网页视频后台请求失败');
  return result;
}
async function videoTaskSave(task) { await chrome.storage.local.set({[videoKey(task.id)]:task}); }
async function videoAsset(config, task, image) {
  const url = baseUrl(config.url) + '/api/web-video/bridge/tasks/' + encodeURIComponent(task.id) +
    '/assets/' + encodeURIComponent(image.id) + '?lease=' + encodeURIComponent(task.lease);
  const response = await fetch(url,{headers:{Authorization:'Bearer '+config.token},signal:AbortSignal.timeout(30000)});
  if(!response.ok) throw new Error('参考图片读取失败：' + image.name);
  const blob=await response.blob();
  if(blob.size > 10*1024*1024) throw new Error('单张参考图片超过 10 MB');
  return {name:image.name,mime:blob.type || 'image/png',data:Array.from(new Uint8Array(await blob.arrayBuffer()))};
}
async function videoAssetLocalPath(config,task,image) {
  const url=baseUrl(config.url)+'/api/web-video/bridge/tasks/'+encodeURIComponent(task.id)+
    '/assets/'+encodeURIComponent(image.id)+'/local-path?lease='+encodeURIComponent(task.lease);
  const response=await fetch(url,{headers:{Authorization:'Bearer '+config.token},signal:AbortSignal.timeout(20000)});
  const result=await response.json();
  if(!response.ok || !result.ok || !result.path)throw new Error(result.message || '豆包参考图片路径读取失败：'+image.name);
  return {name:image.name,path:result.path};
}
async function videoTab(adapter) {
  if(adapter.id==='doubao_web')return chrome.tabs.create({url:adapter.url,active:false});
  const key='videoDedicatedTabId:'+adapter.id;
  const versionKey='videoAdapterVersion:'+adapter.id;
  const saved=await chrome.storage.local.get([key,versionKey]);
  const currentVersion=chrome.runtime.getManifest().version+':video-confirmation-v3';
  if(Number.isInteger(saved[key])) {
    try { const tab=await chrome.tabs.get(saved[key]);
      if(new URL(tab.url).hostname.endsWith(adapter.hostname)) {
        // An old injected content script may survive an extension reload in
        // this tab. Refresh this extension-owned tab once per adapter version.
        if(saved[versionKey]!==currentVersion) {
          await chrome.tabs.reload(tab.id);
          await chrome.storage.local.set({[versionKey]:currentVersion});
        }
        return tab;
      } } catch {}
  }
  const tab=await chrome.tabs.create({url:adapter.url,active:false});
  await chrome.storage.local.set({[key]:tab.id,[versionKey]:currentVersion});
  return tab;
}
async function videoPage(task,type) {
  const adapter=await videoAdapter(task.channel);
  const foreground=type==='AICF_VIDEO_SUBMIT' || type==='AICF_VIDEO_CONFIRM' || type==='AICF_VIDEO_DOWNLOAD';
  const operate=async()=>{
    let tab=await ensureTabReady(task.tabId);
    if(!new URL(tab.url).hostname.endsWith(adapter.hostname)) throw new Error('视频专用标签页已离开渠道网站');
    const versionKey='videoAdapterVersion:'+adapter.id;
    const expectedVersion=chrome.runtime.getManifest().version+':video-confirmation-v3';
    const version=(await chrome.storage.local.get(versionKey))[versionKey];
    if(version!==expectedVersion){
      await chrome.tabs.reload(tab.id);
      await chrome.storage.local.set({[versionKey]:expectedVersion});
      tab=await ensureTabReady(task.tabId);
    }
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:[adapter.script]});
    const result=await chrome.tabs.sendMessage(tab.id,{type,task});
    if(!result?.ok) {
      const error=new Error(result?.error || '视频网页未响应');
      error.beforeSend=result?.before_send===true;
      throw error;
    }
    return result;
  };
  if(foreground) {
    const {backend}=await chrome.storage.local.get('backend');
    return focus.withVideoForeground({taskId:task.id,tabId:task.tabId,returnUrl:backend?.url},operate);
  }
  return operate();
}
async function videoTrustedClick(sender,message) {
  const tabId=sender.tab?.id;
  const saved=await chrome.storage.local.get(videoKey(message.taskId));
  const task=saved[videoKey(message.taskId)];
  const allowedState=message.purpose==='download' || message.purpose==='confirmation' ? 'submitted' : 'claimed';
  if(!Number.isInteger(tabId) || sender.frameId!==0 || task?.tabId!==tabId || task.state!==allowedState)
    throw new Error('视频发送任务或标签页已变化');
  if(message.purpose==='confirmation' && task.channel!=='doubao_web')
    throw new Error('仅允许豆包视频任务自动回复生成确认');
  const adapter=await videoAdapter(task.channel);
  if(!new URL(sender.url).hostname.endsWith(adapter.hostname)) throw new Error('视频发送页面不是任务渠道');
  const {x,y,width,height}=message;
  if(![x,y,width,height].every(Number.isFinite) || x<0 || y<0 || x>=width || y>=height)
    throw new Error('视频发送按钮坐标无效');
  const target={tabId};
  await chrome.debugger.attach(target,'1.3');
  try {
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseMoved',x,y,button:'none'});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
  } finally {try{await chrome.debugger.detach(target);}catch{}}
  return {ok:true};
}
async function videoTrustedKey(sender,message) {
  const tabId=sender.tab?.id;
  const saved=await chrome.storage.local.get(videoKey(message.taskId));
  const task=saved[videoKey(message.taskId)];
  if(message.key!=='ArrowLeft' || !Number.isInteger(tabId) || sender.frameId!==0 ||
     task?.tabId!==tabId || task.state!=='claimed' || task.channel!=='doubao_web')
    throw new Error('豆包时长按键任务或方向无效');
  const adapter=await videoAdapter(task.channel);
  if(!new URL(sender.url).hostname.endsWith(adapter.hostname) || !(await chrome.tabs.get(tabId)).active)
    throw new Error('豆包视频专用页面未激活，时长未调整');
  const target={tabId};
  await chrome.debugger.attach(target,'1.3');
  try {
    const key={key:'ArrowLeft',code:'ArrowLeft',windowsVirtualKeyCode:37,nativeVirtualKeyCode:37};
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'rawKeyDown',...key});
    await chrome.debugger.sendCommand(target,'Input.dispatchKeyEvent',{type:'keyUp',...key});
  } finally {try{await chrome.debugger.detach(target);}catch{}}
  return {ok:true};
}
async function videoTrustedChooseFile(sender,message) {
  const tabId=sender.tab?.id;
  const saved=await chrome.storage.local.get(videoKey(message.taskId));
  const task=saved[videoKey(message.taskId)];
  const files=Array.isArray(message.files)?message.files:[];
  const allowed=new Set(task?.filePaths || []);
  if(!Number.isInteger(tabId) || sender.frameId!==0 || task?.tabId!==tabId ||
     task.state!=='claimed' || task.channel!=='doubao_web' || files.length!==1 || !allowed.has(files[0]))
    throw new Error('豆包参考图片选择任务无效');
  const adapter=await videoAdapter(task.channel);
  if(!new URL(sender.url).hostname.endsWith(adapter.hostname) || !(await chrome.tabs.get(tabId)).active)
    throw new Error('豆包视频专用页面未激活，参考图片未上传');
  const {x,y,width,height}=message;
  if(![x,y,width,height].every(Number.isFinite) || x<0 || y<0 || x>=width || y>=height)
    throw new Error('豆包上传菜单位置无效');
  const target={tabId};
  let chooserListener,opened;
  chooserListener=(source,method,params)=>{
    if(source.tabId===tabId && method==='Page.fileChooserOpened')opened=params;
  };
  chrome.debugger.onEvent.addListener(chooserListener);
  try {
    await chrome.debugger.attach(target,'1.3');
    await chrome.debugger.sendCommand(target,'Page.enable');
    await chrome.debugger.sendCommand(target,'DOM.enable');
    await chrome.debugger.sendCommand(target,'Page.setInterceptFileChooserDialog',{enabled:true});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseMoved',x,y,button:'none'});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
    await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
    const deadline=Date.now()+12000;
    let menuClicked=false;
    while(!opened && Date.now()<deadline) {
      if(!menuClicked) {
        const result=await chrome.tabs.sendMessage(tabId,{type:'AICF_VIDEO_UPLOAD_MENU'});
        const menu=result?.menu;
        if(!opened && menu && [menu.x,menu.y,menu.width,menu.height].every(Number.isFinite) &&
           menu.x>=0 && menu.y>=0 && menu.x<menu.width && menu.y<menu.height) {
          menuClicked=true;
          await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mousePressed',x:menu.x,y:menu.y,button:'left',clickCount:1});
          await chrome.debugger.sendCommand(target,'Input.dispatchMouseEvent',{type:'mouseReleased',x:menu.x,y:menu.y,button:'left',clickCount:1});
        }
      }
      if(!opened)await new Promise(resolve=>setTimeout(resolve,200));
    }
    if(!opened)throw new Error('点击添加按钮后未打开文件选择框或上传菜单');
    if(!opened?.backendNodeId)throw new Error('豆包上传控件没有关联文件输入框');
    await chrome.debugger.sendCommand(target,'DOM.setFileInputFiles',{files,backendNodeId:opened.backendNodeId});
  } finally {
    chrome.debugger.onEvent.removeListener(chooserListener);
    try {await chrome.debugger.sendCommand(target,'Page.setInterceptFileChooserDialog',{enabled:false});} catch {}
    try {await chrome.debugger.detach(target);} catch {}
  }
  return {ok:true};
}
async function videoReport(config,task,state,extra={}) {
  const result=await videoApi(config,'/tasks/'+encodeURIComponent(task.id)+'/report',
    {lease:task.lease,state,...extra});
  task.state=state;
  if(state==='submitted')task.submittedAt=Date.now();
  await videoTaskSave(task);
  if(state==='complete' && task.channel==='doubao_web' && task.ownedTab && result.task?.preview_ready) {
    const url=new URL(result.task.preview_url,baseUrl(config.url));
    if(url.origin!==baseUrl(config.url) || !url.pathname.startsWith('/gradio_api/file='))return result;
    const response=await fetch(url.href,{headers:{Range:'bytes=0-1023'},signal:AbortSignal.timeout(10000)});
    if(response.ok && /video\//i.test(response.headers.get('content-type') || '') && (await response.arrayBuffer()).byteLength>0) {
      const tab=await chrome.tabs.get(task.tabId).catch(()=>null);
      const adapter=await videoAdapter(task.channel);
      if(tab && new URL(tab.url).hostname.endsWith(adapter.hostname))await chrome.tabs.remove(task.tabId);
      task.tabClosed=true;await videoTaskSave(task);
    }
  }
  return result;
}
async function videoDownload(task,ready) {
  if(task.downloadId) return task;
  let downloadId;
  if(/^https?:\/\//i.test(ready.url || '')) {
    downloadId=await chrome.downloads.download({url:ready.url,filename:'AICartoonFactory/'+task.id+'.mp4',conflictAction:'uniquify'});
  } else {
    let started,listener,timeout;
    const created=new Promise((resolve,reject)=>{
      timeout=setTimeout(()=>{chrome.downloads.onCreated.removeListener(listener);reject(new Error('豆包未开始下载视频'));},15000);
      listener=item=>{if(item.tabId!==task.tabId || (item.startTime && Date.parse(item.startTime)<started-1000))return;
        clearTimeout(timeout);chrome.downloads.onCreated.removeListener(listener);resolve(item.id);};
      chrome.downloads.onCreated.addListener(listener);
      started=Date.now();
    });
    created.catch(()=>{}); // The page action can fail before the download promise is awaited.
    try {await videoPage(task,'AICF_VIDEO_DOWNLOAD');}
    catch(error){clearTimeout(timeout);chrome.downloads.onCreated.removeListener(listener);throw error;}
    downloadId=await created;
  }
  task.downloadId=downloadId;await videoTaskSave(task);return task;
}
async function videoResume(config,task) {
  if(task.submittedAt && Date.now()-task.submittedAt>45*60*1000)
    throw new Error('45 分钟内未确认视频完成；请检查豆包网页，任务不会自动重发');
  if(task.downloadId) {
    task.phase='downloading';
    const found=await chrome.downloads.search({id:task.downloadId});
    const item=found[0];
    if(!item) throw new Error('视频下载记录已丢失');
    if(item.state==='interrupted') throw new Error('豆包视频下载中断');
    if(item.state==='complete') {
      await videoReport(config,task,'complete',{output_path:item.filename});
      return;
    }
    return;
  }
  const result=await videoPage(task,'AICF_VIDEO_STATUS');
  task.phase=result.ready?'result_ready':result.confirmationRequired?'awaiting_confirmation':'generating';
  task.readFailures=0;
  task.nextReadAt=0;
  await videoTaskSave(task);
  if(result.confirmationRequired && !task.confirmationAttempted) {
    task.confirmationAttempted=true;
    await videoTaskSave(task);
    await videoPage(task,'AICF_VIDEO_CONFIRM');
    return;
  }
  if(result.ready) await videoDownload(task,result);
}
async function videoPumpFromStorage() {
  if(videoPumping)return;
  videoPumping=true;
  try {
    const saved=await chrome.storage.local.get(null);
    const config=saved.backend;
    if(!config?.enabled || !config.token)return;
    const tasks=Object.entries(saved).filter(([key,value])=>key.startsWith('videoTask:') && value?.id)
      .map(([,value])=>value);
    const cleanup=tasks.find(task=>task.state==='complete' && task.channel==='doubao_web' && task.ownedTab && !task.tabClosed);
    if(cleanup) {
      try {await videoReport(config,cleanup,'complete');} catch(error) {
        cleanup.lastCleanupError=error.message;await videoTaskSave(cleanup);
      }
    }
    const pending=tasks.find(task=>!['complete','error','uncertain'].includes(task.state));
    if(pending) {
      if(pending.state==='claimed') {
        await videoReport(config,pending,'uncertain',{error:'扩展在网页提交前中断；请检查豆包页面后手动决定是否重新生成'});
        return;
      }
      if(pending.state==='submitted') {
        if(pending.nextReadAt && Date.now()<pending.nextReadAt)return;
        try {await videoResume(config,pending);} catch(error) {
          pending.readFailures=(pending.readFailures || 0)+1;
          pending.lastReadError=error.message || String(error);
          if(pending.submittedAt && Date.now()-pending.submittedAt>=45*60*1000)
            await videoReport(config,pending,'uncertain',{error:pending.lastReadError});
          else {
            pending.nextReadAt=Date.now()+Math.min(30000,2000*2**Math.min(pending.readFailures-1,4));
            await videoTaskSave(pending);
          }
        }
      }
      return;
    }
    const clientId=saved.clientId || crypto.randomUUID();
    if(!saved.clientId) await chrome.storage.local.set({clientId});
    const catalog=await videoCatalog();
    const claimed=(await videoApi(config,'/claim',{client_id:clientId,channels:catalog.map(item=>item.id)})).task;
    if(!claimed)return;
    const task={...claimed,tabId:null,state:'claimed'};
    await videoTaskSave(task); // persist before any page action
    let sendAttempted=false;
    try {
      const tab=await videoTab(await videoAdapter(claimed.channel));
      task.tabId=tab.id;task.ownedTab=claimed.channel==='doubao_web';await videoTaskSave(task);
      if(claimed.channel==='doubao_web') {
        task.images=await Promise.all((claimed.input.images || []).map(image=>videoAssetLocalPath(config,task,image)));
        task.filePaths=task.images.map(image=>image.path);
        await videoTaskSave(task);
      } else {
        task.images=await Promise.all((claimed.input.images || []).map(image=>videoAsset(config,task,image)));
      }
      sendAttempted=true;
      const result=await videoPage(task,'AICF_VIDEO_SUBMIT');
      task.images=[];task.filePaths=[];task.baseline=result.baseline || [];
      task.baselineReadyCount=result.baselineReadyCount || 0;
      task.baselineConfirmationCount=result.baselineConfirmationCount || 0;
      task.phase='generating';
      await videoReport(config,task,'submitted');
    } catch(error) {
      task.images=[];task.filePaths=[];
      await videoReport(config,task,sendAttempted && !error.beforeSend?'uncertain':'error',{error:error.message || String(error)});
    }
  } catch(error) {
    await chrome.storage.local.set({videoBridgeStatus:{message:error.message || String(error),at:Date.now()}});
  } finally {videoPumping=false;}
}
