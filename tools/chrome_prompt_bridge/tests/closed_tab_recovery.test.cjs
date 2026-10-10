const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function fixture(task) {
  const calls=[];
  const context={
    chrome:{tabs:{get:async()=>{throw Error('No tab');},create:async options=>{calls.push(options);return {id:42,...options};}}},
    TextProviderPolicy:{fromName:()=>({id:'chatgpt'})},PROVIDER_URLS:{chatgpt:'https://chatgpt.com/'},
    PromptBridge:{provider:()=> 'ChatGPT'},pageFlights:new Map(),
    textStore:{registerOwnedTab:async()=>{},rebind:async(id,b)=>{task.tab_id=b.tabId;},patchFromWorker:async(id,p)=>Object.assign(task,p)},
    ensureTabReady:async()=>{},
  };
  const source=fs.readFileSync(path.join(__dirname,'../worker.js'),'utf8');
  const start=source.indexOf('async function recoverConversation(task) {');
  const end=source.indexOf('async function processExisting(',start);
  vm.runInNewContext(source.slice(start,end),context);
  return {calls,recover:()=>context.recoverConversation(task)};
}
test('closed unsent ChatGPT page is replaced by an owned background tab',async()=>{
 const task={id:'task',provider:'ChatGPT',tab_id:9,status:'prepared',page_operation:{kind:'send',phase:'started'}};
 const f=fixture(task);await f.recover();
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].active,false);
 assert.equal(task.tab_id,42);assert.equal(task.read_only_recovery,false);assert.equal(task.page_operation,null);
});
test('closed page with dispatch evidence is never replaced with a new request',async()=>{
 for(const evidence of [{sent_at:'2026-10-09'},{dispatch_attempted_at:123},{read_only_recovery:true}]) {
  const f=fixture({id:'task',provider:'ChatGPT',tab_id:9,status:'prepared',...evidence});
  await f.recover();assert.equal(f.calls.length,0);
 }
});
