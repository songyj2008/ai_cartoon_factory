const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../video/doubao.js'),'utf8');
const start=source.indexOf('  async function ensureSettingsOpen(taskId)');
const end=source.indexOf('  const waitFor =',start);
function fixture(openDuringWait=false,timeout=false){
  let polls=0,open=false;const clicks=[];
  const context={settingsOpen:()=>open,settingsControl:()=>++polls>=4?{id:'settings'}:null,
    trustedClick:async(el,id)=>{clicks.push({el,id});open=true;},
    waitFor:async(getter,description,ms)=>{
      if(description==='视频参数入口')assert.equal(ms,15000);
      if(timeout)throw Error('入口超时');
      for(let i=0;i<6;i++){if(openDuringWait&&i===2)open=true;const value=getter();if(value)return value;}
      throw Error('unexpected timeout');
    },editor:()=>null,composerRoot:()=>null,videoModeReady:()=>true,
    document:{readyState:'complete',querySelectorAll:()=>[]},visible:()=>true,ownLabel:()=>'',nearComposer:()=>false};
  vm.runInNewContext(source.slice(start,end),context);
  return {run:()=>context.ensureSettingsOpen('task'),clicks};
}
test('waits for a delayed settings control before clicking once',async()=>{
  const f=fixture();await f.run();assert.equal(f.clicks.length,1);assert.equal(f.clicks[0].id,'task');
});
test('panel opening during wait is not clicked closed',async()=>{
  const f=fixture(true);await f.run();assert.equal(f.clicks.length,0);
});
test('missing settings reports page diagnostics without clicking',async()=>{
  const f=fixture(false,true);await assert.rejects(f.run(),/页面诊断.*readyState/);assert.equal(f.clicks.length,0);
});
