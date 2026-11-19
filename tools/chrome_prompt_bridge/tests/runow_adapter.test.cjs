const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const C=require('../video/runow_contract.js');
const page=fs.readFileSync(path.join(__dirname,'../video/runow.js'),'utf8');
const worker=fs.readFileSync(path.join(__dirname,'../video_worker.js'),'utf8');
const driver=fs.readFileSync(path.join(__dirname,'../runow_worker.js'),'utf8');
function extract(start,end,context) {
  context.actionPause ||= async()=>{};
  vm.runInNewContext(page.slice(page.indexOf(start),page.indexOf(end,page.indexOf(start))),context);
  return context;
}

test('exact filename and media type select uniquely; duplicate is an error',()=>{
  const records=[{name:'a.png',type:'image',id:1},{name:'a.png.backup',type:'image',id:2}];
  assert.equal(C.uniqueResource(records,'a.png','image').id,1);
  assert.equal(C.uniqueResource(records,'A.png','image'),null);
  assert.equal(C.uniqueResource(records,'a.png','audio'),null);
  assert.throws(()=>C.uniqueResource([...records,records[0]],'a.png','image'),/多个同名/);
  assert.throws(()=>C.validateSelection(records,[records[1],records[0]]),/顺序/);
});

for(const exists of [true,false])test(`resource library ${exists?'reuses filename':'uploads only the missing file'}`,async()=>{
  const uploads=[],target={name:'voice.mp3',type:'audio',path:'C:\\assets\\voice.mp3'};
  const match={name:target.name,type:target.type,element:{innerText:'音频',textContent:'音频'}};
  const upload={scrollIntoView(){},getBoundingClientRect:()=>({left:30,top:30,width:100,height:40})};
  const context={
    searchResource:async()=>exists?match:null,find:()=>upload,
    waitFor:async read=>read(),resource:()=>match,text:node=>node.innerText,
    innerWidth:1000,innerHeight:800,
    chrome:{runtime:{sendMessage:async message=>{uploads.push(message);return {ok:true};}}}
  };
  extract('  async function ensureResource(','  function selectionCount(',context);
  assert.equal(await context.ensureResource(target,{root:{}},{id:'task'}),match);
  assert.equal(uploads.length,exists?0:1);
  if(!exists) {
    assert.equal(uploads[0].type,'AICF_VIDEO_CHOOSE_FILE');
    assert.deepEqual(Array.from(uploads[0].files),[target.path]);
  }
});

test('duplicate resource search aborts before any upload',async()=>{
  let uploads=0;
  const context={searchResource:async()=>C.uniqueResource([{name:'a',type:'image'},{name:'a',type:'image'}],'a','image'),
    chrome:{runtime:{sendMessage:async()=>uploads++}}};
  extract('  async function ensureResource(','  function selectionCount(',context);
  await assert.rejects(context.ensureResource({}, {}, {}),/多个同名/);
  assert.equal(uploads,0);
});

test('search recognizes website empty-state headings and waits through loading or failure',async()=>{
  for(const heading of ['没有搜索结果','我的素材中还没有内容','还没有收集内容','暂无素材','正在加载素材','素材加载失败','']) {
    const missing=['没有搜索结果','我的素材中还没有内容','还没有收集内容','暂无素材'].includes(heading);
    const field={value:''};
    const context={setInput:(node,value)=>{node.value=value;},sleep:async()=>{},
      waitFor:async read=>{const value=read();if(!value)throw new Error('search unsettled');return value;},
      resource:()=>null,text:node=>node.label,visible:()=>true};
    extract('  async function searchResource(','  async function ensureResource(',context);
    const run=context.searchResource({name:'voice.mp3'},{search:field,root:{querySelectorAll:selector=>
      selector==='.qcv2-asset-library-state strong' && heading?[{label:heading}]:[]}});
    if(missing)assert.equal(await run,null);
    else await assert.rejects(run,/search unsettled/);
  }
});

test('failed library upload is surfaced without selecting or submitting',async()=>{
  const context={searchResource:async()=>null,waitFor:async read=>read(),find:()=>({scrollIntoView(){},
    getBoundingClientRect:()=>({left:0,top:0,width:20,height:20})}),innerWidth:1000,innerHeight:800,
    chrome:{runtime:{sendMessage:async()=>({ok:false,error:'upload rejected'})}}};
  extract('  async function ensureResource(','  function selectionCount(',context);
  await assert.rejects(context.ensureResource({path:'voice.mp3'}, {root:{}}, {id:'one'}),/upload rejected/);
});

test('prompt insertion clicks website candidates including the third reference and keeps all text',async()=>{
  const mentions=[],chosen=[];
  let nodes=[];
  const field={innerText:'',focus(){},querySelectorAll:()=>mentions};
  const labels=['@图片1','@图片2','@图片3','@音频1'];
  const parts=[{text:'形象 '},{reference_id:'image',label:'@图片1'},
    {text:'，声音 '},{reference_id:'audio',label:'@音频1'},
    {text:'，场景 '},{reference_id:'scene',label:'@图片3'},
    {text:'，再次 '},{reference_id:'image',label:'@图片1'},{text:'。<d>说 @图片9</d>'}];
  const context={C,visible:()=>true,text:node=>node.innerText,
    waitFor:async read=>{const result=read();if(!result)throw new Error('verification failed');return result;},
    editor:()=>field,fieldText:node=>node?.innerText || '',sleep:async()=>{},HTMLTextAreaElement:class {},
    getSelection:()=>({removeAllRanges(){},addRange(){}}),
    document:{createRange:()=>({selectNodeContents(){},collapse(){}}),querySelectorAll:()=>nodes},
    chrome:{runtime:{sendMessage:async message=>{
      assert.equal(message.type,'AICF_RUNOW_INSERT_TEXT');
      field.innerText+=message.text;
      if(message.text==='@')nodes=labels.map(innerText=>({innerText}));
      return {ok:true};
    }}},
    click:async option=>{
      const label=option.innerText;
      chosen.push(label);field.innerText=field.innerText.slice(0,-1)+label+'\u200b';
      mentions.push({innerText:label});nodes=[];
    }
  };
  extract('  async function append(','  const outputs=',context);
  await context.fillPrompt({input:{prompt_parts:parts,prompt:parts.map(p=>p.text || p.label).join('')}});
  assert.deepEqual(chosen,['@图片1','@音频1','@图片3','@图片1']);
  assert.match(field.innerText,/<d>说 @图片9<\/d>/);
  assert.equal(mentions.length,4);
});

test('status ignores baseline and refuses ambiguous new videos or repurposed page',async()=>{
  let items=[{url:'old.mp4',ready:true}],href='https://www.runninghub.cn/runow/project/one';
  const context={C,outputs:()=>items,location:{get href(){return href;}}};
  extract('  async function status(','  async function download(',context);
  const task={baseline:['old.mp4'],pageUrl:href};
  assert.equal((await context.status(task)).ready,false);
  items.push({url:'https://cdn.example/new.mp4',ready:true});
  assert.equal((await context.status(task)).url,'https://cdn.example/new.mp4');
  items.push({url:'another.mp4',ready:true});
  await assert.rejects(context.status(task),/多个新视频/);
  href='https://www.runninghub.cn/runow/project/two';
  await assert.rejects(context.status(task),/页面已变化/);
});

test('Runow driver prepares images and audio via leased local paths',async()=>{
  const prepared=[];
  const context={videoAssetLocalPath:async(_config,_task,item)=>({name:item.name,path:'C:\\'+item.name}),
    videoTaskSave:async task=>prepared.push(task.filePaths.slice())};
  vm.runInNewContext(driver,context);
  const task={input:{media:[{id:'image',name:'a.png',type:'image'},{id:'voice',name:'a.mp3',type:'audio'}]}};
  await context.VideoChannelDrivers.runninghub_web.prepare({},task);
  assert.deepEqual(Array.from(task.media,item=>item.type),['image','audio']);
  assert.deepEqual(Array.from(task.filePaths),['C:\\a.png','C:\\a.mp3']);
  assert.equal(prepared.length,1);
});

test('Runow native chooser only accepts this task allowlisted file',async()=>{
  const task={id:'one',channel:'runninghub_web',tabId:7,state:'claimed',filePaths:['C:\\voice.mp3']};
  const commands=[];let listener;
  const context={URL,setTimeout,clearTimeout,
    fetch:async()=>({json:async()=>[{id:'runninghub_web',hostname:'www.runninghub.cn'}]}),
    chrome:{runtime:{getURL:()=>''},storage:{local:{get:async()=>({'videoTask:one':task})}},
      tabs:{get:async()=>({active:true}),sendMessage:async()=>{throw new Error('must not use Doubao menu');}},
      debugger:{attach:async()=>{},detach:async()=>{},
        onEvent:{addListener:fn=>{listener=fn;},removeListener:()=>{}},
        sendCommand:async(_target,method,params)=>{
          commands.push({method,params});
          if(method==='Input.dispatchMouseEvent' && params.type==='mouseReleased')
            listener({tabId:7},'Page.fileChooserOpened',{backendNodeId:42});
        }}}};
  vm.runInNewContext(worker,context);vm.runInNewContext(driver,context);
  const sender={tab:{id:7},frameId:0,url:'https://www.runninghub.cn/runow/home'};
  const message={taskId:'one',x:30,y:30,width:1000,height:800,files:['C:\\private.mp3']};
  await assert.rejects(context.videoTrustedChooseFile(sender,message),/无效/);
  assert.equal(commands.length,0);
  message.files=['C:\\voice.mp3'];
  await assert.rejects(context.videoTrustedChooseFile({...sender,url:'https://www.runninghub.cn/call-api/'},message),/不是/);
  assert.equal(commands.length,0);
  await context.videoTrustedChooseFile(sender,message);
  assert.equal(commands.find(c=>c.method==='DOM.setFileInputFiles').params.backendNodeId,42);
  const intercept=commands.findIndex(c=>c.method==='Page.setInterceptFileChooserDialog');
  const click=commands.findIndex(c=>c.method==='Input.dispatchMouseEvent');
  assert.ok(intercept<click);
});

test('Runow tab cleanup preserves repurposed pages and closes owned matching page',async()=>{
  for(const changed of [true,false]) {
    const removed=[];
    const context={URL,AbortSignal,
      videoApi:async()=>({task:{preview_ready:true,preview_url:'/gradio_api/file=one.mp4'}}),
      videoTaskSave:async()=>{},baseUrl:()=> 'http://127.0.0.1:7860',
      fetch:async()=>({ok:true,headers:{get:()=> 'video/mp4'},arrayBuffer:async()=>new Uint8Array([1]).buffer}),
      chrome:{tabs:{get:async()=>({url:changed?'https://www.runninghub.cn/runow/project/user':'https://www.runninghub.cn/runow/home'}),
        remove:async id=>removed.push(id)}},videoAdapter:async()=>({hostname:'www.runninghub.cn'})};
    vm.runInNewContext(driver,context);
    vm.runInNewContext(worker.slice(worker.indexOf('async function videoReport('),worker.indexOf('async function videoDownload(')),context);
    const task={id:'one',channel:'runninghub_web',ownedTab:true,tabId:7,pageUrl:'https://www.runninghub.cn/runow/home'};
    await context.videoReport({},task,'complete');
    assert.equal(removed.length,changed?0:1);
    assert.equal(Boolean(task.cleanupSkipped),changed);
  }
});
