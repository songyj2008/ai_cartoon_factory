/* Independent task execution; the clock only requests work and never waits for it. */
(function(root){
 'use strict';
 function createScheduler({onTick,onError=()=>{},now=()=>Date.now(),defer=queueMicrotask}) {
   const tasks=new Set(),tabs=new Set(),due=new Map(),forced=new Set();
   let discovery=false,video=false,scan=false,pending=false,requested=false;
   const backoff=[2000,5000,10000,30000];
   function report(error,scope){try{onError(error,scope);}catch{}}
   function requestTick(reason='clock') {
     pending=true;
     if(requested)return;
     requested=true;
     defer(()=>{
       requested=false;
       if(scan)return;
       if(!pending)return;
       pending=false;scan=true;
       Promise.resolve().then(()=>onTick(reason)).catch(error=>report(error,'scan'))
         .finally(()=>{scan=false;if(pending)requestTick('pending');});
     });
   }
   function scheduleTask(taskId,tabId,run,{force=false}={}) {
     if(!taskId || !Number.isInteger(tabId) || tasks.has(taskId) || tabs.has(tabId))return false;
     const state=due.get(taskId);
     if(!force && state && now()<state.at)return false;
     tasks.add(taskId);tabs.add(tabId);
     Promise.resolve().then(run).then(()=>{
       due.set(taskId,{at:now()+2000,failures:0});
     },error=>{
       const failures=Math.min((due.get(taskId)?.failures || 0)+1,backoff.length);
       due.set(taskId,{at:now()+backoff[failures-1],failures});
       report(error,`task:${taskId}`);
     }).finally(()=>{tasks.delete(taskId);tabs.delete(tabId);if(forced.delete(taskId))due.delete(taskId);requestTick('operation-finished');});
     return true;
   }
   function single(kind,run){
     if(kind==='discovery' && discovery || kind==='video' && video)return false;
     if(kind==='discovery')discovery=true;else video=true;
     Promise.resolve().then(run).catch(error=>report(error,kind)).finally(()=>{
       if(kind==='discovery')discovery=false;else video=false;
     });
     return true;
   }
   return {requestTick,scheduleTask,
     scheduleDiscovery:run=>single('discovery',run),scheduleVideo:run=>single('video',run),
     forceTask:id=>{due.delete(id);if(tasks.has(id))forced.add(id);},deferTask:(id,ms=2000)=>due.set(id,{at:now()+ms,failures:0}),
     isTaskRunning:id=>tasks.has(id),isTabRunning:id=>tabs.has(id)};
 }
 root.TextScheduler={createScheduler};
 if(typeof module!=='undefined')module.exports={createScheduler};
})(globalThis);
