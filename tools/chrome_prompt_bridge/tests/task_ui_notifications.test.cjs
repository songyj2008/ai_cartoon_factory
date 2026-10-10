const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../../ui/text_jobs_ui.py'),'utf8');
function fixture(api){
 let applied=0;
 const node={childElementCount:1,disabled:false};
 const context={busy:false,signature:'[]',root:()=>({querySelector:()=>node}),api,
 fetch:()=>new Promise(()=>{}),AbortSignal,queueMicrotask,videoView:x=>x,
 bindClearButton:()=>{},autoApplyMetadata:()=>applied++,observeTaskChanges:()=>{},updateEntryStatuses:()=>{},updateSummary:()=>{},updateFilters:()=>{},message:()=>{}};
 vm.runInNewContext(source.slice(source.indexOf('  let refreshPending='),source.indexOf('  const filters=')),context);
 return {context,get applied(){return applied;}};
}
test('stalled video request does not block text processing',async()=>{
 const f=fixture(async()=>({tasks:[]}));f.context.refreshVideos();
 await f.context.refresh();assert.equal(f.applied,1);
});
test('notification during refresh triggers a followup fetch',async()=>{
 let release,calls=0;
 const f=fixture(()=>{calls++;return calls===1?new Promise(resolve=>{release=resolve;}):Promise.resolve({tasks:[]});});
 const first=f.context.refresh();await f.context.refresh();
 release({tasks:[]});await first;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,2);assert.equal(f.applied,2);
});
