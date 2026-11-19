const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const page=fs.readFileSync(path.join(__dirname,'../video/runow.js'),'utf8');
const worker=fs.readFileSync(path.join(__dirname,'../runow_worker.js'),'utf8');
const C=require('../video/runow_contract.js');

function fixture({current=10,ratio='16:9',resolution='1080p',native=false,step=1,stalled=false,
  closeOnChoice=false,alreadyOpen=false,staleSummary=false,modelName='Minimax H3-RH增强版'}={}) {
  let open=alreadyOpen,seconds=current,focused=false;
  const keys=[],clicks=[],lookups=[];
  const body={},field={};
  const node=label=>({label,getAttribute:()=>null,scrollIntoView(){}});
  const badge=node('');Object.defineProperty(badge,'label',{get:()=>seconds+' 秒'});
  const panel=node('');Object.defineProperty(panel,'label',{get:()=> '生成参数 视频比例 视频质量 视频时长 '+seconds+' 秒'});
  panel.parentElement=body;panel.querySelectorAll=()=>[badge];
  const slider=node('');slider.parentElement=panel;slider.focus=()=>{focused=true;};
  slider.matches=selector=>native && selector==='input[type="range"]';
  // aria-valuenow deliberately uses an index. The adapter must use displayed seconds.
  slider.getAttribute=attribute=>attribute==='aria-valuenow'?'6':null;
  const summary=node('');Object.defineProperty(summary,'label',{get:()=>
    ratio+' (Widescreen) / '+resolution+' / '+(staleSummary?current:seconds)+'秒'});
  const root={};const model=node(modelName),mode=node('多模态参考'),free=node('免费');
  const choices=[node('16:9 (Widescreen)'),node('1:1 (Square)'),node('480p'),node('768p'),node('1080p')];
  const context={C,Number,actionPause:async()=>{},document:{body,readyState:'complete',querySelectorAll:()=>open?[slider]:[]},
    visible:()=>true,text:node=>node?.label || '',editor:()=>field,fieldText:()=>'',composer:()=>root,
    escape:value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),
    find:(pattern,scope)=>{
      lookups.push(String(pattern));
      return (scope===panel?choices:[model,mode,summary,free]).find(n=>pattern.test(n.label)) || null;
    },
    waitFor:async(read,label)=>{
      for(let i=0;i<3;i++){const result=read();if(result)return result;await Promise.resolve();}
      throw new Error('无法确认'+label);
    },
    click:async node=>{
      clicks.push(node.label);
      if(node===summary)open=!open;
      else if(node.label.includes(':')){ratio=node.label.split(' ')[0];if(closeOnChoice)open=false;}
      else {resolution=node.label;if(closeOnChoice)open=false;}
    },
    chrome:{runtime:{sendMessage:async message=>{
      assert.equal(focused,true,'must focus the slider before dispatching a trusted key');
      assert.equal(message.type,'AICF_RUNOW_DURATION_KEY');
      keys.push(message.key);
      if(!stalled)seconds+=(message.key==='ArrowRight'?step:-step);
      return {ok:true};
    }}}
  };
  context.clickText=async(pattern,task,scope)=>{
    const node=context.find(pattern,scope);if(!node)throw new Error('option missing '+pattern);
    await context.click(node,task);
  };
  vm.runInNewContext(page.slice(page.indexOf('  const settingsSummary='),page.indexOf('  async function append(')),context);
  return {keys,clicks,lookups,get seconds(){return seconds;},get open(){return open;},
    run:(duration=11,aspect='16:9',quality='1080p')=>context.setMode({id:'task',input:{duration_sec:duration,aspect_ratio:aspect,resolution:quality}})};
}

test('international Enhanced title passes parameter validation', async()=>{
  const run=fixture({modelName:'Minimax H3-RH Enhanced',current:11});
  await run.run(11);
  assert.deepEqual(run.clicks,[]);
});

for(const modelName of ['Seedance 2.5','MiniMax H3 Max','MiniMax H3 Max Turbo','Vidu Q4 Preview','Kling 4.0'])
test('parameter validation rejects '+modelName,async()=>{
  const run=fixture({modelName});
  await assert.rejects(run.run(11),/未选中 Minimax H3-RH/);
  assert.deepEqual(run.clicks,[]);
  assert.deepEqual(run.keys,[]);
});

for(const native of [false,true])test(`10 → 11 seconds uses the ${native?'native range':'ARIA'} slider, not an option`,async()=>{
  const run=fixture({native});await run.run(11);
  assert.equal(run.seconds,11);assert.deepEqual(run.keys,['ArrowRight']);
  assert.equal(run.open,false);
  assert.ok(!run.lookups.some(label=>label.startsWith('/^11')));
});

test('duration can decrease and each key is checked against the displayed seconds',async()=>{
  const run=fixture();await run.run(5);
  assert.deepEqual(run.keys,['ArrowLeft','ArrowLeft','ArrowLeft','ArrowLeft','ArrowLeft']);
  assert.equal(run.seconds,5);
});

test('already-correct duration does not open the panel or press keys',async()=>{
  const run=fixture({current:11});await run.run(11);
  assert.deepEqual(run.keys,[]);assert.deepEqual(run.clicks,[]);
});

for(const closeOnChoice of [false,true])test('ratio, resolution and duration work with '+(closeOnChoice?'closing':'persistent')+' popovers',async()=>{
  const run=fixture({ratio:'1:1',resolution:'768p',closeOnChoice});await run.run(11);
  assert.equal(run.seconds,11);assert.equal(run.open,false);
  assert.ok(run.clicks.includes('16:9 (Widescreen)'));
  assert.ok(run.clicks.includes('1080p'));
});

test('an already-open parameter panel is not toggled shut before adjusting duration',async()=>{
  const run=fixture({alreadyOpen:true});await run.run(11);
  assert.equal(run.clicks.length,1);assert.equal(run.open,false);
});

test('stalled slider fails without retrying blindly or proceeding',async()=>{
  const run=fixture({stalled:true});await assert.rejects(run.run(11),/时长滑块响应/);
  assert.deepEqual(run.keys,['ArrowRight']);assert.equal(run.seconds,10);
});

test('unsupported slider steps never round the requested duration',async()=>{
  const run=fixture({step:2});await assert.rejects(run.run(11),/步长无法到达/);
  assert.equal(run.seconds,12);assert.deepEqual(run.keys,['ArrowRight']);
});

test('slider value alone is insufficient when the saved parameter summary stays stale',async()=>{
  const run=fixture({staleSummary:true});await assert.rejects(run.run(11),/摘要回读/);
});

function workerFixture({channel='runninghub_web',state='claimed',active=true,focus=true,
  url='https://www.runninghub.cn/runow/home',taskTab=7}={}) {
  const commands=[];
  const context={URL,videoKey:id=>'videoTask:'+id,
    document:{activeElement:{matches:()=>focus}},
    chrome:{storage:{local:{get:async()=>({'videoTask:one':{id:'one',tabId:taskTab,channel,state,input:{prompt_parts:[{text:'正文'}]}}})}},
      tabs:{get:async()=>({active,url})},scripting:{executeScript:async({func})=>[{result:func()}]},
      debugger:{attach:async()=>{},detach:async()=>{},sendCommand:async(_target,method,params)=>commands.push({method,params})}}};
  vm.runInNewContext(worker,context);
  const sender={frameId:0,tab:{id:7},url:'https://www.runninghub.cn/runow/home'};
  return {commands,run:(key='ArrowRight',pageSender=sender)=>context.runowDurationKey(pageSender,{taskId:'one',key}),
    insert:text=>context.runowInsertText(sender,{taskId:'one',text})};
}

test('Runow duration sends trusted keydown and keyup only on its focused slider',async()=>{
  const run=workerFixture();await run.run();
  assert.deepEqual(run.commands.map(item=>item.params.type),['rawKeyDown','keyUp']);
  assert.deepEqual(run.commands.map(item=>item.params.key),['ArrowRight','ArrowRight']);
});

test('duration input rejects unrelated channel, task state, tab, page and focus',async()=>{
  for(const options of [{channel:'doubao_web'},{state:'submitted'},{active:false},{focus:false},
    {url:'https://www.runninghub.cn/call-api/'},{taskTab:8}]) {
    const run=workerFixture(options);await assert.rejects(run.run());assert.equal(run.commands.length,0);
  }
  for(const key of ['Enter','Home','ArrowUp']) {
    const run=workerFixture();await assert.rejects(run.run(key));assert.equal(run.commands.length,0);
  }
});

test('Runow inserts only task prompt text and uses genuine @ key events for suggestions',async()=>{
  const run=workerFixture();
  await run.insert('正文');
  assert.equal(run.commands[0].method,'Input.insertText');
  assert.equal(run.commands[0].params.text,'正文');
  await run.insert('@');
  assert.deepEqual(run.commands.slice(1).map(c=>c.params.type),['keyDown','keyUp']);
  assert.equal(run.commands[1].params.text,'@');
  const before=run.commands.length;
  await assert.rejects(run.insert('unrelated text'),/任务不匹配/);
  assert.equal(run.commands.length,before);
  await assert.rejects(workerFixture({focus:false}).insert('@'),/焦点/);
});
