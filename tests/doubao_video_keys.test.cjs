const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'tools', 'chrome_prompt_bridge', 'video_worker.js'), 'utf8');

function keyFixture(active = true) {
  const commands = [];
  const task = {id:'task-1',tabId:7,state:'claimed',channel:'doubao_web'};
  const context = {
    URL,
    fetch:async()=>({json:async()=>[{id:'doubao_web',hostname:'doubao.com'}]}),
    chrome:{
      runtime:{getURL:()=> 'chrome-extension://test/video/channels.json'},
      storage:{local:{get:async()=>({'videoTask:task-1':task})}},
      tabs:{get:async()=>({id:7,active})},
      debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_target,method,options)=>commands.push({method,options})},
    },
  };
  vm.runInNewContext(source,context);
  const sender={tab:{id:7},frameId:0,url:'https://www.doubao.com/chat/'};
  return {context,sender,commands};
}

test('Doubao duration sends only trusted ArrowLeft key down and up', async () => {
  const fixture=keyFixture();
  const result=await fixture.context.videoTrustedKey(fixture.sender,{taskId:'task-1',key:'ArrowLeft'});
  assert.equal(result.ok,true);
  assert.deepEqual(fixture.commands.map(item=>item.options.key),['ArrowLeft','ArrowLeft']);
  assert.deepEqual(fixture.commands.map(item=>item.options.type),['rawKeyDown','keyUp']);
});

test('Doubao duration refuses rightward input and inactive tabs', async () => {
  const fixture=keyFixture();
  await assert.rejects(fixture.context.videoTrustedKey(fixture.sender,{taskId:'task-1',key:'ArrowRight'}),/方向无效/);
  assert.equal(fixture.commands.length,0);
  const inactive=keyFixture(false);
  await assert.rejects(inactive.context.videoTrustedKey(inactive.sender,{taskId:'task-1',key:'ArrowLeft'}),/未激活/);
  assert.equal(inactive.commands.length,0);
});

for(const direct of [true,false])test('file chooser is armed before plus click: '+(direct?'direct':'menu'),async()=>{
  const commands=[];
  let listener,intercept=false,clicks=0,queries=0;
  const task={id:'task-1',tabId:7,state:'claimed',channel:'doubao_web',filePaths:['C:\\ref.png']};
  const context={URL,setTimeout,clearTimeout,
    fetch:async()=>({json:async()=>[{id:'doubao_web',hostname:'doubao.com'}]}),
    chrome:{runtime:{getURL:()=>''},storage:{local:{get:async()=>({'videoTask:task-1':task})}},
      tabs:{get:async()=>({active:true}),sendMessage:async()=>{
        queries++;return {menu:{x:200,y:200,width:1000,height:1000}};
      }},
      debugger:{attach:async()=>{},detach:async()=>{},
        onEvent:{addListener:fn=>{listener=fn;},removeListener:()=>{listener=null;}},
        sendCommand:async(_target,method,params)=>{
          commands.push({method,params});
          if(method==='Page.setInterceptFileChooserDialog')intercept=params.enabled;
          if(method==='Input.dispatchMouseEvent' && params.type==='mouseReleased') {
            assert.equal(intercept,true);clicks++;
            if(direct || clicks===2)listener({tabId:7},'Page.fileChooserOpened',{backendNodeId:42});
          }
        }}}};
  vm.runInNewContext(source,context);
  const result=await context.videoTrustedChooseFile({tab:{id:7},frameId:0,url:'https://www.doubao.com/chat/'},
    {taskId:'task-1',x:50,y:50,width:1000,height:1000,files:['C:\\ref.png']});
  assert.equal(result.ok,true);
  assert.equal(clicks,direct?1:2);
  assert.equal(queries,direct?0:1);
  assert.equal(commands.find(item=>item.method==='DOM.setFileInputFiles').params.backendNodeId,42);
  assert.equal(intercept,false);
  assert.equal(listener,null);
});

for(const success of [true,false])test('foreground video returns only after accepted submission: '+success,async()=>{
  const changes=[];
  const tab={id:7,windowId:1,url:'https://www.doubao.com/chat/',active:false};
  const context={URL,
    fetch:async()=>({json:async()=>[{id:'doubao_web',hostname:'doubao.com',script:'video/doubao.js'}]}),
    ensureBackgroundTab:async()=>{assert.equal(tab.active,true);return tab;},
    chrome:{runtime:{getURL:()=>''},
      tabs:{get:async()=>tab,query:async()=>[{id:3}],
        update:async(id)=>{changes.push(id);tab.active=id===7;return tab;},
        sendMessage:async()=>{assert.equal(tab.active,true);return {ok:success,error:'upload failed',before_send:true};}},
      scripting:{executeScript:async()=>{}}}};
  vm.runInNewContext(source,context);
  const run=context.videoPage({tabId:7,channel:'doubao_web'},'AICF_VIDEO_SUBMIT');
  if(success)await run;
  else await assert.rejects(run,/upload failed/);
  assert.equal(tab.active,!success);
  assert.equal(changes.at(-1),success?3:7);
});
