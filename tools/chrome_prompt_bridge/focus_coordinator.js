/* Coordinates the rare visible wake with foreground browser video actions. */
(function(root){
 'use strict';
 function createCoordinator(chromeApi,{now=()=>Date.now(),sleep=ms=>new Promise(r=>setTimeout(r,ms)),
   setTimer=setTimeout,clearTimer=clearTimeout}={}) {
   let lease=null,expectedTab=null,releaseWaiters=[];
   const leaseKey='aicfFocusLease';
   const wakeWait=()=>new Promise(resolve=>releaseWaiters.push(resolve));
   function released(){for(const resolve of releaseWaiters.splice(0))resolve();}
   chromeApi.tabs.onActivated?.addListener(info=>{
     if(!lease)return;
     if(expectedTab===info.tabId){expectedTab=null;return;}
     if(info.windowId===lease.windowId)lease.userInterrupted=true;
   });
   chromeApi.windows?.onFocusChanged?.addListener(windowId=>{
     if(lease && windowId!==lease.windowId)lease.userInterrupted=true;
   });
   async function restore(current) {
     if(!current || current.restored)return;
     current.restored=true;
     clearTimer(current.timer);
     try {
       const target=await chromeApi.tabs.get(current.tabId);
       const original=Number.isInteger(current.originalTabId) ?
         await chromeApi.tabs.get(current.originalTabId) : null;
       if(!current.userInterrupted && target.active && original &&
           target.windowId===current.windowId && original.windowId===current.windowId) {
         expectedTab=original.id;
         await chromeApi.tabs.update(original.id,{active:true});
       }
     } catch {} finally {
       if(lease===current)lease=null;
       await chromeApi.storage.session.remove(leaseKey).catch(()=>{});
       released();
     }
   }
   async function withTextWake({taskId,tabId,reason},action) {
     if(lease?.type==='text' && lease.taskId===taskId && lease.tabId===tabId)return action();
     if(lease)return {deferred:true,reason:'FOCUS_BUSY'};
     const target=await chromeApi.tabs.get(tabId);
     const window=await chromeApi.windows.getLastFocused();
     if(!window?.focused || window.id!==target.windowId)return {deferred:true,reason:'WINDOW_NOT_FOCUSED'};
     const active=(await chromeApi.tabs.query({active:true,windowId:target.windowId}))[0];
     if(target.active)return action();
     const current={type:'text',taskId,tabId,reason,windowId:target.windowId,
       originalTabId:active?.id,startedAt:now(),deadlineAt:now()+1800,userInterrupted:false,restored:false};
     lease=current;
     await chromeApi.storage.session.set({[leaseKey]:{
       type:'text',taskId,tabId,windowId:current.windowId,originalTabId:current.originalTabId,
       deadlineAt:current.deadlineAt}});
     // Arm before activation; loading, injection, and message failures cannot
     // prolong visible focus beyond this independent deadline.
     current.timer=setTimer(()=>restore(current).catch(()=>{}),1800);
     try {
       expectedTab=tabId;
       await chromeApi.tabs.update(tabId,{active:true});
       await sleep(400);
       return await action();
     } finally {await restore(current);}
   }
   async function withVideoForeground({taskId,tabId,returnUrl},action) {
     if(lease?.type==='video' && lease.taskId===taskId && lease.tabId===tabId)return action();
     while(lease?.type==='text')await wakeWait();
     if(lease)return {deferred:true,reason:'FOCUS_BUSY'};
     const target=await chromeApi.tabs.get(tabId);
     let original=(await chromeApi.tabs.query({active:true,windowId:target.windowId}))[0];
     if(returnUrl) {
       try {
         const origin=new URL(returnUrl).origin;
         const tabs=await chromeApi.tabs.query({windowId:target.windowId});
         const projectTabs=tabs.filter(tab=>{try{return new URL(tab.url).origin===origin;}catch{return false;}});
         original=projectTabs.find(tab=>tab.active) || projectTabs[0] || original;
       } catch {}
     }
     const current={type:'video',taskId,tabId,windowId:target.windowId,
       originalTabId:original?.id,startedAt:now(),userInterrupted:false};
     lease=current;
     await chromeApi.storage.session.set({[leaseKey]:current});
     let completed=false;
     try {
       if(!target.active){expectedTab=tabId;await chromeApi.tabs.update(tabId,{active:true});}
       const value=await action();completed=true;return value;
     } finally {
       if(completed && !current.userInterrupted && original && original.id!==tabId) {
         try {if((await chromeApi.tabs.get(tabId)).active)
           {expectedTab=original.id;await chromeApi.tabs.update(original.id,{active:true});}}catch{}
       }
       lease=null;await chromeApi.storage.session.remove(leaseKey).catch(()=>{});released();
     }
   }
   async function recoverInterruptedLease(){
     const saved=(await chromeApi.storage.session.get(leaseKey))[leaseKey];
     if(!saved || lease || saved.type!=='text' || now()<saved.deadlineAt)return;
     const current={...saved,restored:false,userInterrupted:false};
     try {
       const window=await chromeApi.windows.getLastFocused();
       if(window?.focused && window.id===saved.windowId)await restore(current);
       else await chromeApi.storage.session.remove(leaseKey);
     } catch {await chromeApi.storage.session.remove(leaseKey).catch(()=>{});}
   }
   return {withTextWake,withVideoForeground,recoverInterruptedLease,
     isVideoForeground:()=>lease?.type==='video'};
 }
 root.FocusCoordinator={createCoordinator};
 if(typeof module!=='undefined')module.exports={createCoordinator};
})(globalThis);
