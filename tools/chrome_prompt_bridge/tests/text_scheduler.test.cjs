const test=require('node:test');
const assert=require('node:assert/strict');
const {createScheduler}=require('../text_scheduler.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('a task whose page never responds cannot prevent another task from completing',async()=>{
 let completeB=0;
 const scheduler=createScheduler({onTick:()=>{},defer:queueMicrotask});
 scheduler.scheduleTask('A',1,()=>new Promise(()=>{}));
 scheduler.scheduleTask('B',2,async()=>{completeB++;});
 await tick();
 assert.equal(completeB,1);
 assert.equal(scheduler.isTaskRunning('A'),true);
 assert.equal(scheduler.isTaskRunning('B'),false);
 assert.equal(scheduler.scheduleTask('A',1,()=>{}),false);
});

test('slow discovery does not block completed-task reporting',async()=>{
 let reported=false;
 const scheduler=createScheduler({onTick:()=>{}});
 scheduler.scheduleDiscovery(()=>new Promise(()=>{}));
 scheduler.scheduleTask('complete',7,async()=>{reported=true;});
 await tick();
 assert.equal(reported,true);
});

test('repeated ticks do not overlap one task or one tab',async()=>{
 let starts=0,finish;
 const scheduler=createScheduler({onTick:()=>{}});
 assert.equal(scheduler.scheduleTask('one',7,()=>{starts++;return new Promise(resolve=>{finish=resolve;});}),true);
 for(let i=0;i<10;i++){
   scheduler.requestTick();
   assert.equal(scheduler.scheduleTask('one',7,()=>{starts++;}),false);
   assert.equal(scheduler.scheduleTask('other',7,()=>{starts++;}),false);
 }
 await tick();assert.equal(starts,1);
 finish();await tick();
 assert.equal(scheduler.isTabRunning(7),false);
});

test('completion notification received during execution survives its normal cooldown',async()=>{
 let finish;const scheduler=createScheduler({onTick:()=>{},now:()=>100});
 scheduler.scheduleTask('result',1,()=>new Promise(resolve=>{finish=resolve;}));await tick();
 scheduler.forceTask('result');finish();await tick();
 assert.equal(scheduler.scheduleTask('result',1,async()=>{}),true);await tick();
});
