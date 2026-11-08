const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('queue reset clears text, video and manual tasks and never closes unrelated tabs',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../worker.js'),'utf8');
  const script=source.slice(source.indexOf('async function saveBridgeTask('),source.indexOf('async function cleanupTextHistory('));
  const stored={
    backend:{token:'keep'},task:{id:'manual',tab_id:999},
    'bridgeTask:waiting':{id:'waiting',status:'waiting'},
    'videoTask:video':{id:'video',state:'submitted',ownedTab:true,tabId:22},
    'textOwnedTab:11':{providerId:'chatgpt'},
    'textRetiredTab:11':{},autoBindings:{chatgpt:11},autoPools:{chatgpt:[11]}
  };
  const closed=[];let created=0;
  const local={get:async()=>({...stored}),set:async data=>Object.assign(stored,data),
    remove:async keys=>keys.forEach(key=>delete stored[key])};
  const context={chrome:{storage:{local,session:{remove:async()=>{}}},tabs:{remove:async id=>closed.push(id)}},
    textStore:{get:async()=>null,create:async()=>{created++}},Date};
  vm.createContext(context);vm.runInContext(script,context);
  assert.equal(await context.syncQueueReset({reset_epoch:100},{...stored}),true);
  assert.deepEqual(closed.sort(),[11,22]);
  assert.equal(stored.backend.token,'keep');assert.equal(stored.task,null);
  assert.equal(stored['bridgeTask:waiting'],undefined);assert.equal(stored['videoTask:video'],undefined);
  assert.equal(stored.taskResetEpoch,100);
  await context.saveBridgeTask({id:'waiting',status:'complete'});
  assert.equal(created,0,'late replies cannot recreate cleared tasks');
  await context.saveBridgeTask({id:'new',status:'prepared'});
  assert.equal(created,1,'new tasks still work');
  assert.equal(await context.syncQueueReset({reset_epoch:100},{...stored}),false);
});

test('late video callbacks cannot recreate tasks after reset',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../video_worker.js'),'utf8');
  const script=source.slice(source.indexOf('async function videoTaskSave('),source.indexOf('async function videoAsset('));
  let writes=0;
  const context={videoKey:id=>'videoTask:'+id,chrome:{storage:{local:{
    get:async()=>({clearedTaskIds:{old:100}}),set:async()=>{writes++}
  }}}};
  vm.createContext(context);vm.runInContext(script,context);
  await assert.rejects(context.videoTaskSave({id:'old'}),/一键清空/);
  await context.videoTaskSave({id:'new'});assert.equal(writes,1);
});
