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

function modeFixture(becomesVisible) {
  const document={visibilityState:'hidden'};
  const control={};
  let ready=false,clicks=0;
  const context={document,nearComposer:()=>true,videoModeReady:()=>ready,findText:()=>control,
    trustedClick:async element=>{
      assert.equal(document.visibilityState,'visible');
      assert.equal(element,control);
      clicks++;
      ready=true;
    },
    waitFor:async(getter,description)=>{
      if(description==='可见的豆包视频标签页') {
        assert.equal(getter(),false);
        assert.equal(clicks,0);
        if(!becomesVisible)throw Error('timeout');
        document.visibilityState='visible';
      }
      const result=getter();
      assert.ok(result);
      return result;
    }};
  const start=source.indexOf('  async function ensureVideoMode(taskId)');
  const end=source.indexOf('  const durationPanel',start);
  vm.runInNewContext(source.slice(start,end),context);
  return {run:()=>context.ensureVideoMode('task'),get clicks(){return clicks;}};
}

test('video mode waits for page visibility before clicking',async()=>{
  const page=modeFixture(true);
  await page.run();
  assert.equal(page.clicks,1);
});

test('video mode reports a hidden page without dispatching a click',async()=>{
  const page=modeFixture(false);
  await assert.rejects(page.run(),/视频标签页不可见/);
  assert.equal(page.clicks,0);
});

test('reference upload ignores the model switcher busy state and waits for actual progress',()=>{
  const node=attributes=>({getAttribute:name=>attributes[name]||null});
  const modelSwitcher=node({'data-testid':'chat_input_action_model','aria-busy':'true'});
  const image={complete:true,naturalWidth:1376};
  let busyNodes=[modelSwitcher],loadingText=null;
  const context={composerImages:()=>[image],composerRoot:()=>({querySelectorAll:()=>busyNodes}),
    visible:()=>true,findText:()=>loadingText};
  const start=source.indexOf('  const uploadComplete =');
  const end=source.indexOf('  const editor =',start);
  const uploadComplete=vm.runInNewContext(`${source.slice(start,end)}\nuploadComplete`,context);
  assert.equal(uploadComplete(1),true);
  assert.equal(uploadComplete(2),false);
  busyNodes=[modelSwitcher,node({role:'progressbar'})];
  assert.equal(uploadComplete(1),false);
  busyNodes=[modelSwitcher,node({'aria-busy':'true'})];
  assert.equal(uploadComplete(1),false);
  busyNodes=[modelSwitcher];
  image.complete=false;
  assert.equal(uploadComplete(1),false);
  image.complete=true;
  loadingText={};
  assert.equal(uploadComplete(1),false);
});
