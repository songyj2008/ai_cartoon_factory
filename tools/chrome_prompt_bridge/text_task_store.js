/* The service worker owns every bridgeTask record. Content scripts send patches. */
(function(root) {
  'use strict';
  const terminal=new Set(['complete','cancelled','error','timeout']);
  const pageFields=new Set(['status','message','result','raw','observation','sent_at',
    'dispatch_attempted_at','dispatch_url','dispatch_count','conversation_url','conversation_id',
    'conversation_renamed_id','json_repair','continuation_diagnostics','continue_pending',
    'continue_scan_state','continued_reply','continued_at','continue_attempts','continue_events',
    'updated_at','page_operation']);
  function createStore(chromeApi, providers) {
    const taskLocks=new Map();
    const ownedLocks=new Map();
    const taskKey=id=>`bridgeTask:${id}`;
    const ownedKey=id=>`textOwnedTab:${id}`;
    function serialize(map,key,work) {
      const previous=map.get(key) || Promise.resolve();
      const running=previous.catch(()=>{}).then(work);
      map.set(key,running);
      running.finally(()=>{if(map.get(key)===running)map.delete(key);}).catch(()=>{});
      return running;
    }
    async function get(id) {return (await chromeApi.storage.local.get(taskKey(id)))[taskKey(id)] || null;}
    async function write(id,task) {await chromeApi.storage.local.set({[taskKey(id)]:task});return task;}
    function defaults(task) {return {...task,binding_epoch:task.binding_epoch || 1,
      record_revision:task.record_revision || 1,
      document_id:task.document_id || null,
      recovery:task.recovery || {next_attempt_at:0,consecutive_failures:0,wake_count:0,last_wake_at:0,last_error_code:''},
      delivery:task.delivery || {state:'pending',backend_status:''}};}
    async function create(task) {
      if(!task?.id)throw new Error('任务编号缺失');
      return serialize(taskLocks,task.id,async()=>{
        const existing=await get(task.id);
        return existing || write(task.id,defaults(task));
      });
    }
    async function patchFromWorker(id,patch) {
      return serialize(taskLocks,id,async()=>{
        const old=await get(id);if(!old)throw new Error('后台任务不存在');
        if(terminal.has(old.status) && patch.status && !terminal.has(patch.status))return old;
        if(patch.bridge?.reported)
          return write(id,{...patch,record_revision:(old.record_revision || 0)+1});
        return write(id,{...old,...patch,record_revision:(old.record_revision || 0)+1});
      });
    }
    async function rebind(id,binding) {
      return serialize(taskLocks,id,async()=>{
        const old=await get(id);if(!old)throw new Error('后台任务不存在');
        if(terminal.has(old.status))return old;
        const next={...old,tab_id:binding.tabId,document_id:null,
          binding_epoch:(old.binding_epoch || 1)+1,record_revision:(old.record_revision || 0)+1,
          read_only_recovery:true};
        return write(id,next);
      });
    }
    async function bindDocument(id,tabId,documentId) {
      if(!documentId)throw new Error('Chrome 没有返回页面 documentId，无法安全绑定任务');
      return serialize(taskLocks,id,async()=>{
        const old=await get(id);if(!old || old.tab_id!==tabId)throw new Error('任务绑定页面已变化');
        if(old.document_id===documentId)return old;
        if(terminal.has(old.status))return old;
        const next={...old,document_id:documentId,
          binding_epoch:old.document_id ? (old.binding_epoch || 1)+1 : (old.binding_epoch || 1),
          record_revision:(old.record_revision || 0)+1};
        return write(id,next);
      });
    }
    async function patchFromPage(sender,payload) {
      const id=payload?.taskId;
      if(!id || sender.id!==chromeApi.runtime.id || sender.frameId!==0 ||
          !Number.isInteger(sender.tab?.id) || !sender.documentId)
        throw new Error('STALE_BINDING: 页面身份不可验证');
      return serialize(taskLocks,id,async()=>{
        const old=await get(id);
        const site=providers.fromName(old?.provider);
        if(!old || old.tab_id!==sender.tab.id || old.document_id!==sender.documentId ||
            old.binding_epoch!==payload.bindingEpoch || !site?.matchesUrl(sender.url || ''))
          throw new Error('STALE_BINDING: 页面或任务绑定已变化');
        if(terminal.has(old.status)) {
          if(payload.patch?.status===old.status && old.status==='complete' &&
              JSON.stringify(payload.patch.result)===JSON.stringify(old.result))return old;
          throw new Error('任务已经结束，拒绝旧页面回写');
        }
        const patch=payload.patch || {};
        // sender.url may describe the document's initial route on SPA sites.
        // Resolve the tab's live route while retaining the document/epoch checks.
        const liveTab=patch.conversation_url ? await chromeApi.tabs.get(sender.tab.id) : null;
        const liveUrl=liveTab?.url || sender.url;
        if(Object.keys(patch).some(key=>!pageFields.has(key)))throw new Error('页面修改了受保护的任务字段');
        // Sending evidence is persisted before clicking. The subsequent
        // waiting patch contains the assigned conversation URL, not another
        // copy of sent_at. Accept that first acknowledgement on the same
        // bound document; later conversation changes still require evidence.
        const initialSendAcknowledgement=old.status==='sending' && patch.status==='waiting' &&
          !old.conversation_url && Boolean(old.sent_at) && Number.isFinite(old.dispatch_attempted_at);
        const unchangedConversation=patch.conversation_url===old.conversation_url;
        if(patch.conversation_url && (patch.conversation_url!==liveUrl ||
            !site.matchesUrl(patch.conversation_url) ||
            !(payload.requestEvidence || initialSendAcknowledgement || unchangedConversation)))
          throw new Error('CONVERSATION_BINDING_PENDING: 会话地址尚未确认，继续只读等待本任务发送证据');
        if(patch.status==='sending' && (old.sent_at || old.dispatch_attempted_at))
          throw new Error('任务已有发送记录，不会重复发送');
        if(patch.status==='complete' && (!patch.result || !patch.raw))
          throw new Error('完成结果不完整');
        const next={...old,...patch,record_revision:(old.record_revision || 0)+1};
        return write(id,next);
      });
    }
    async function registerOwnedTab(providerId,tab) {
      if(!providers.policy(providerId) || !Number.isInteger(tab?.id))throw new Error('专用标签页无效');
      return serialize(ownedLocks,tab.id,async()=>{
        await chromeApi.storage.local.set({[ownedKey(tab.id)]:{providerId,windowId:tab.windowId,createdAt:Date.now()}});
        return tab.id;
      });
    }
    async function listOwnedTabs(providerId) {
      const legacy=await chromeApi.storage.session.get(null);
      const local=await chromeApi.storage.local.get(null);
      const migrations=Object.fromEntries(Object.entries(legacy).filter(([key])=>
        key.startsWith('textOwnedTab:') && !local[key]));
      if(Object.keys(migrations).length)await chromeApi.storage.local.set(migrations);
      const all={...legacy,...local};
      return Object.entries(all).filter(([key,value])=>key.startsWith('textOwnedTab:') &&
        value?.providerId===providerId).map(([key])=>Number(key.slice('textOwnedTab:'.length)))
        .filter(Number.isInteger);
    }
    async function ownsTab(tabId) {return Boolean((await chromeApi.storage.local.get(ownedKey(tabId)))[ownedKey(tabId)] || (await chromeApi.storage.session.get(ownedKey(tabId)))[ownedKey(tabId)]);}
    return {create,get,patchFromPage,patchFromWorker,rebind,bindDocument,
      registerOwnedTab,listOwnedTabs,ownsTab};
  }
  root.TextTaskStore={createStore};
  if(typeof module!=='undefined')module.exports={createStore};
})(globalThis);
