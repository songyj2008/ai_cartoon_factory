const test=require('node:test');
const assert=require('node:assert/strict');
const {createCoordinator}=require('../focus_coordinator.js');

function fixture(){
 const tabs=[{id:1,windowId:7,active:true},{id:2,windowId:7,active:false},
   {id:3,windowId:7,active:false}];
 const listeners=[],session={};let timer,focused=true;
 const chrome={tabs:{onActivated:{addListener:fn=>listeners.push(fn)},
   get:async id=>{const tab=tabs.find(t=>t.id===id);if(!tab)throw new Error('No tab');return {...tab};},
   query:async query=>tabs.filter(t=>!query.active || t.active).map(t=>({...t})),
   update:async(id,patch)=>{if(patch.active){tabs.forEach(t=>t.active=false);tabs.find(t=>t.id===id).active=true;
     listeners.forEach(fn=>fn({tabId:id,windowId:7}));}return {...tabs.find(t=>t.id===id)};}},
   windows:{getLastFocused:async()=>({id:7,focused}),onFocusChanged:{addListener:()=>{}}},
   storage:{session:{get:async key=>({[key]:session[key]}),set:async patch=>Object.assign(session,patch),
     remove:async key=>{delete session[key];}}}};
 const focus=createCoordinator(chrome,{sleep:async()=>{},setTimer:fn=>{timer=fn;return 1;},clearTimer:()=>{}});
 return {focus,tabs,session,expire:()=>timer(),setFocused:value=>{focused=value;},userSwitch:id=>{
   tabs.forEach(t=>t.active=t.id===id);listeners.forEach(fn=>fn({tabId:id,windowId:7}));}};
}

test('text wake restores focus on failure and does not override a user switch',async()=>{
 const f=fixture();
 await assert.rejects(f.focus.withTextWake({taskId:'a',tabId:2,reason:'stalled'},async()=>{
   assert.equal(f.tabs.find(t=>t.active).id,2);throw new Error('page failed');}),/page failed/);
 assert.equal(f.tabs.find(t=>t.active).id,1);
 await f.focus.withTextWake({taskId:'b',tabId:2,reason:'stalled'},async()=>{
   f.userSwitch(3);
 });
 assert.equal(f.tabs.find(t=>t.active).id,3);
});

test('video returns to project tab instead of the previously active LLM',async()=>{
 const f=fixture();
 f.tabs[0].url='https://chat.deepseek.com/';
 f.tabs[1].url='http://127.0.0.1:7860/';
 f.tabs[2].url='https://www.doubao.com/chat';
 await f.focus.withVideoForeground({taskId:'video',tabId:3,returnUrl:'http://127.0.0.1:7860'},async()=>{});
 assert.equal(f.tabs.find(t=>t.active).id,2);
 await f.focus.withVideoForeground({taskId:'next',tabId:3,returnUrl:'http://127.0.0.1:7860'},async()=>{
   f.userSwitch(1);f.userSwitch(3);
 });
 assert.equal(f.tabs.find(t=>t.active).id,3);
});

test('video foreground defers text wake and releases after submission',async()=>{
 const f=fixture();let release;
 const gate=new Promise(resolve=>{release=resolve;});
 const video=f.focus.withVideoForeground({taskId:'video',tabId:3},async()=>{await gate;return 'done';});
 await Promise.resolve();await Promise.resolve();
 const read=await f.focus.withTextWake({taskId:'text',tabId:2,reason:'stalled'},async()=>{});
 assert.equal(read.deferred,true);
 release();assert.equal(await video,'done');
 assert.equal(f.tabs.find(t=>t.active).id,1);
});
