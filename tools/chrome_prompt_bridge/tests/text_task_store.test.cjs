const test=require('node:test');
const assert=require('node:assert/strict');
const {createStore}=require('../text_task_store.js');
const providers=require('../text_provider_policy.js');

function fixture(){
 const local={},session={};
 const area=values=>({get:async key=>key===null?{...values}:typeof key==='string'?{[key]:values[key]}:{...values},
   set:async patch=>Object.assign(values,patch)});
 const chrome={runtime:{id:'extension'},storage:{local:area(local),session:area(session)}};
 return {store:createStore(chrome,providers),local,session};
}
const base=id=>({id,provider:'ChatGPT',tab_id:7,status:'waiting',request:'request',
  bridge:{url:'http://127.0.0.1:7860',lease:'secret',reported:false}});
const sender=(documentId='document-7',tabId=7)=>({id:'extension',frameId:0,tab:{id:tabId},
  documentId,url:'https://chatgpt.com/c/abc'});

test('old page cannot replace a migrated task or its complete result',async()=>{
 const f=fixture();await f.store.create(base('one'));
 const old=await f.store.bindDocument('one',7,'document-7');
 await f.store.rebind('one',{tabId:8});
 await f.store.bindDocument('one',8,'document-8');
 await assert.rejects(f.store.patchFromPage(sender(),{taskId:'one',bindingEpoch:old.binding_epoch,
   patch:{status:'complete',result:{data:'old'},raw:'old'}}),/STALE_BINDING/);
 const current=await f.store.get('one');
 assert.equal(current.tab_id,8);assert.equal(current.result,undefined);
});

test('content changes are serialized and protected fields cannot be changed',async()=>{
 const f=fixture();await f.store.create(base('two'));
 const bound=await f.store.bindDocument('two',7,'document-7');
 await assert.rejects(f.store.patchFromPage(sender(),{taskId:'two',bindingEpoch:bound.binding_epoch,
   patch:{tab_id:99}}),/受保护/);
 await Promise.all([
   f.store.patchFromPage(sender(),{taskId:'two',bindingEpoch:bound.binding_epoch,patch:{message:'正在读取'}}),
   f.store.patchFromPage(sender(),{taskId:'two',bindingEpoch:bound.binding_epoch,patch:{status:'complete',result:{data:'ok'},raw:'answer'}})
 ]);
 assert.equal((await f.store.get('two')).status,'complete');
 await assert.rejects(f.store.patchFromPage(sender(),{taskId:'two',bindingEpoch:bound.binding_epoch,
   patch:{status:'waiting'}}),/已经结束/);
});

test('dedicated tab ownership is per tab and stored only for this Chrome session',async()=>{
 const f=fixture();
 await Promise.all([f.store.registerOwnedTab('deepseek',{id:10,windowId:1}),
   f.store.registerOwnedTab('deepseek',{id:11,windowId:1})]);
 assert.deepEqual((await f.store.listOwnedTabs('deepseek')).sort(),[10,11]);
 assert.equal(Object.keys(f.local).some(key=>key.startsWith('textOwnedTab:')),false);
});
