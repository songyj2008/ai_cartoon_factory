const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const page=fs.readFileSync(path.join(__dirname,'../video/runow.js'),'utf8');
const C=require('../video/runow_contract.js');
function extract(start,end,context) {
  context.actionPause ||= async()=>{};
  context.C ||= require('../video/runow_contract.js');
  vm.runInNewContext(page.slice(page.indexOf(start),page.indexOf(end,page.indexOf(start))),context);
}

test('Runow preparation clicks its actual DOM button without using stale coordinates',async()=>{
  let selected=0;
  const button={disabled:false,getAttribute:()=>null,click:()=>selected++};
  const label={disabled:false,getAttribute:()=>null,closest:()=>button,
    getBoundingClientRect:()=>{throw new Error('must not resolve click coordinates');}};
  const context={visible:()=>true,chrome:{runtime:{sendMessage:()=>{throw new Error('must use the DOM handler');}}}};
  extract('  async function click(','  async function clickText(',context);
  await context.click(label,{id:'task'});
  assert.equal(selected,1);
});

test('submission accepts the website task status containing an icon and elapsed time',()=>{
  const status={children:[{},{}],label:'任务已提交，正在获取生成进度02:29',matches:()=>true};
  const outer={children:[status],label:status.label,matches:()=>false};
  const context={document:{querySelectorAll:()=>[outer,status]},visible:()=>true,text:node=>node.label};
  extract('  const pending=', '  function sendButton(',context);
  assert.equal(vm.runInNewContext('pending()',context),1);
});

test('library discovery uses the observed modal root and named search field',()=>{
  const search={},root={querySelector:selector=>{
    assert.equal(selector,'input[aria-label="搜索我的素材"]');return search;
  }};
  const context={document:{querySelectorAll:selector=>{
    assert.equal(selector,'.qcv2-asset-library');return [root];
  }},visible:()=>true};
  extract('  function library(','  async function openLibrary(',context);
  assert.equal(context.library().root,root);
  assert.equal(context.library().search,search);
  context.visible=()=>false;
  assert.equal(context.library(),null);
});

test('matches real resource button filenames and the small media-type label',()=>{
  const makeCard=(name,label)=>({querySelector:selector=>selector==='span[title]'?
    {getAttribute:()=>name}:{label}});
  const correct=makeCard('a.png','图片');
  const context={C,visible:()=>true,text:n=>n.label};
  extract('  function resource(','  async function searchResource(',context);
  const root={querySelectorAll:selector=>{
    assert.equal(selector,'button.qcv2-asset-library-card-select');return [makeCard('b.png','图片'),correct];
  }};
  assert.equal(context.resource({name:'a.png',type:'image'},root).element,correct);
});

for(const kind of ['image','video','audio'])
for(const initial of ['closed','menu','wrong-menu','library'])test(kind+' uses its own resource entry from '+initial,async()=>{
  const label=({image:'图片',video:'视频',audio:'音频'})[kind],events=[];
  let open=initial==='library',expanded=initial==='menu',activeFilter='全部';
  const attach={getAttribute:()=>expanded?'true':'false'},choose={label:'从资源选择'};
  const filters=['全部','图片','视频','音频'].map(label=>({label,
    getAttribute:()=>activeFilter===label?'true':'false'}));
  const lib={root:{querySelectorAll:selector=>{
    assert.equal(selector,'.qcv2-asset-library-filters button');return filters;
  }},search:{value:''}};
  const context={library:()=>open?lib:null,composer:()=>({querySelector:selector=>{
    assert.equal(selector,'button.qcv2-direct-attachment-source[aria-label="'+label+'"]');return attach;
  }}),waitFor:async(read,description)=>{const result=read();if(!result)throw new Error(description);return result;},
    document:{querySelectorAll:()=>[choose]},visible:()=>true,text:node=>node.label,
    click:async node=>{
      if(node===attach){events.push('entry:'+kind);expanded=true;}
      else if(node===choose){assert.equal(expanded,true);events.push('library');open=true;}
      else {assert.equal(node.label,label);events.push('filter:'+kind);activeFilter=label;}
    },
    clickText:async pattern=>{
      if(pattern.test('取消')){events.push('cancel');open=false;}
      else {assert.ok(pattern.test('我的素材'));events.push('mine');}
    }};
  extract('  async function openLibrary(','  function resource(',context);
  assert.equal(await context.openLibrary({},kind),lib);
  assert.deepEqual(events,[...(initial==='library'?['cancel']:[]),
    ...(initial==='menu'?[]:['entry:'+kind]),'library','mine','filter:'+kind]);
});

test('project image resources select in reference order, then add selected',async()=>{
  const events=[],lib={root:{},search:{value:''}},root={};let opened=true,count=0;
  const selectedNames=[];
  const media=[{id:'a',type:'image',name:'a.png',web_label:'@图片1'},
    {id:'b',type:'image',name:'b.png',web_label:'@图片2'},
    {id:'c',type:'image',name:'c.png',web_label:'@图片3'}];
  const makeNode=(name,left,top,width,height)=>({name,isConnected:true,
    getBoundingClientRect:()=>({left,top,width,height}),matches:()=>false});
  const cards=new Map();
  function makeCard(name,ordinal=0) {
    const card=makeNode(name,10,100,280,310),circle=makeNode(name,250,110,32,32);
    circle.label=ordinal?String(ordinal):'';
    const remove=makeNode('delete',20,110,28,28);remove.label='×';
    card.querySelectorAll=()=>[remove,circle];
    card.matches=selector=>selector==='button.qcv2-asset-library-card-select';
    card.getAttribute=()=>ordinal?'true':'false';
    card.querySelector=selector=>{assert.equal(selector,'b.qcv2-asset-library-check');return circle;};
    return {name,type:'image',element:card,circle};
  }
  // Visible ordering deliberately differs from reference order, with one irrelevant extra asset.
  for(const name of ['extra.png','b.png','a.png','c.png'])cards.set(name,makeCard(name));
  const markers=media.map(item=>({label:item.web_label.slice(1),closest:()=>null}));
  root.querySelectorAll=()=>media.map((item,index)=>({querySelector:selector=>selector==='.qcv2-ref-kind-mark'?
    {label:'图片 '+(index+1)}:{getAttribute:()=>`预览 ${item.name} 的素材详情`}}));
  const add={disabled:false,get label(){return '添加已选 ('+count+')';},click(){events.push('add:'+count);opened=false;}};
  lib.root.querySelectorAll=selector=>{
    if(selector==='footer button')return [add];
    assert.equal(selector,'.qcv2-asset-library-selected-item[title]');
    return selectedNames.map(name=>({getAttribute:()=>name}));
  };
  const context={C,visible:node=>node.isConnected!==false,text:node=>node?.label || '',
    getComputedStyle:()=>({borderRadius:'50%'}),openLibrary:async()=>lib,library:()=>opened?lib:null,
    resource:item=>cards.get(item.name) || null,
    searchResource:async()=>{throw new Error('must reuse visible resource by filename');},
    composer:()=>root,escape:value=>value,
    find:(pattern,scope)=>scope===lib.root?(pattern.test(add.label)?add:null):markers.find(n=>pattern.test(n.label)),
    setInput:(field,value)=>{field.value=value;},
    waitFor:async(read,label)=>{const result=read();if(!result)throw new Error('missing '+label);return result;},
    click:async node=>{
      const match=cards.get(node.name);assert.equal(node,match.element,'must click the actual selection button');
      events.push('select:'+node.name);count++;
      selectedNames.push(node.name);
      match.element.isConnected=false;
      cards.set(node.name,makeCard(node.name,count)); // real frameworks may replace the card after selection
    },
    clickText:async()=>{throw new Error('must use the footer button directly');}
  };
  extract('  async function ensureResource(','  const settingsSummary=',context);
  await context.selectMedia({media,id:'task'});
  assert.deepEqual(events,['select:a.png','select:b.png','select:c.png','add:3']);
});

test('generation button waits for the real enabled send control after prompt insertion',()=>{
  let button={disabled:true,getAttribute:()=>null};
  const context={composer:()=>({querySelector:selector=>{
    assert.equal(selector,'button[data-testid="hero-send"]');return button;
  }}),visible:node=>Boolean(node)};
  extract('  function sendButton()','  async function submit(',context);
  assert.equal(context.sendButton(),null);
  button.disabled=false;
  assert.equal(context.sendButton(),button);
  button={disabled:false,getAttribute:()=> 'true'};
  assert.equal(context.sendButton(),null);
});

test('selection count distinguishes zero, a loading footer and retained selections',()=>{
  let button=null,checked=[];
  const root={querySelectorAll:selector=>selector==='footer button'?(button?[button]:[]):checked};
  const context={visible:()=>true,text:node=>node?.label || ''};
  extract('  function selectionCount(','  function selectionControl(',context);
  assert.equal(context.selectionCount(root),null);
  button={label:'添加已选',disabled:true};
  assert.equal(context.selectionCount(root),0);
  checked=[{}];
  assert.equal(context.selectionCount(root),null);
  button={label:'添加已选（ 2 ）',disabled:false};
  assert.equal(context.selectionCount(root),2);
  button={label:'添加已选 (0)',disabled:true};
  assert.equal(context.selectionCount(root),0);
});

for(const includeVideo of [false,true])test('separate media entries preserve the full reference list; video='+includeVideo,async()=>{
  const media=[{type:'image',name:'actor.png',web_label:'@图片1'},
    {type:'image',name:'inspector.png',web_label:'@图片2'},
    ...(includeVideo?[{type:'video',name:'motion.mp4',web_label:'@视频1'}]:[]),
    {type:'audio',name:'voice.mp3',web_label:'@音频1'}];
  const events=[],previews=[],selected=[];
  let opened=false,footerLoading=false,currentKind='image';
  const cards=media.map(item=>({...item,element:{
    matches:selector=>selector==='button.qcv2-asset-library-card-select',
    getAttribute:()=>selected.includes(item.name)?'true':'false',
    querySelector:()=>({label:String(selected.indexOf(item.name)+1)})
  }}));
  const add={disabled:false,get label(){return '添加已选（'+selected.length+'）';},click(){
    events.push('add:'+selected.join(','));
    // The real site replaces its library-owned references when confirming.
    previews.splice(0,previews.length,...selected.map(name=>media.find(item=>item.name===name)));
    opened=false;
  }};
  const lib={search:{value:''},root:{querySelectorAll:selector=>{
    if(selector==='footer button') {
      if(footerLoading){footerLoading=false;return [];}
      return [add];
    }
    assert.equal(selector,'.qcv2-asset-library-selected-item[title]');
    return selected.map(name=>({getAttribute:()=>name}));
  }}};
  const context={C,visible:()=>true,text:node=>node?.label || '',
    openLibrary:async(_task,kind)=>{currentKind=kind;events.push('open:'+kind);opened=true;footerLoading=true;return lib;},
    library:()=>opened?lib:null,
    resource:item=>cards.find(card=>card.name===item.name && card.type===currentKind),
    setInput:(field,value)=>{field.value=value;},
    waitFor:async(read,description)=>{
      for(let attempt=0;attempt<4;attempt++){const result=read();if(result)return result;}
      throw new Error(description);
    },
    click:async node=>{
      const card=cards.find(card=>card.element===node),index=selected.indexOf(card.name);
      assert.equal(index,-1,'previous groups must remain selected');
      assert.equal(card.type,currentKind);
      selected.push(card.name);events.push('check:'+card.name);
    },
    composer:()=>({querySelectorAll:()=>previews.map(item=>({querySelector:selector=>{
      if(selector==='.qcv2-ref-kind-mark')return {label:item.web_label.slice(1)};
      if(item.type==='audio')return selector==='button[aria-label^="预览 "]'?null:
        {getAttribute:()=> '清除参考图'};
      return {getAttribute:()=>`预览 ${item.name} 的素材详情`};
    }}))})
  };
  extract('  async function ensureResource(','  const settingsSummary=',context);
  await context.selectMedia({id:'task',media});
  assert.deepEqual(events,['open:image','check:actor.png','check:inspector.png',
    'add:actor.png,inspector.png',
    ...(includeVideo?['open:video','check:motion.mp4','add:actor.png,inspector.png,motion.mp4']:[]),
    'open:audio','check:voice.mp3','add:'+media.map(item=>item.name).join(',')]);
  assert.deepEqual(previews,media);
});

test('unreadable selection count reports the audio stage before searching or uploading',async()=>{
  const context={openLibrary:async()=>({search:{value:''},root:{querySelectorAll:()=>[]}}),selectionCount:()=>null,
    waitFor:async(read,description)=>{assert.equal(read(),null);throw new Error(description);}};
  extract('  async function selectMediaGroup(','  async function selectMedia(task)',context);
  await assert.rejects(context.selectMediaGroup({},[{type:'audio',name:'voice.mp3'}],'audio'),
    /音频资源选择器已选数量/);
});

test('unrelated preselected references stop before changing any selection',async()=>{
  const context={openLibrary:async()=>({search:{value:''},root:{querySelectorAll:()=>[
    {getAttribute:()=> 'unrelated.png'}]}}),selectionCount:()=>1,
    waitFor:async read=>read()};
  extract('  async function selectMediaGroup(','  async function selectMedia(task)',context);
  await assert.rejects(context.selectMediaGroup({},[{name:'voice.mp3'}],'audio',[{name:'actor.png'}]),
    /音频资源选择器已选素材与前序参考不一致/);
});

test('changed audio filename in the picker is caught before applying references',async()=>{
  let confirmed=false,selected=false;
  const audio={name:'voice.mp3',type:'audio'},button={matches:()=>true,
    getAttribute:()=> 'true',querySelector:()=>({label:'1'})};
  const context={C,visible:()=>true,text:node=>node.label,selectionControl:node=>node,
    openLibrary:async()=>({search:{value:''},root:{querySelectorAll:()=>selected?
      [{getAttribute:()=> 'wrong-voice.mp3'}]:[]}}),selectionCount:()=>selected?1:0,
    ensureResource:async()=>({...audio,element:button}),resource:()=>({element:button}),
    click:async()=>{selected=true;},addSelected:async()=>{confirmed=true;},
    waitFor:async(read,description)=>{const result=read();if(!result)throw new Error(description);return result;}};
  extract('  async function selectMediaGroup(','  async function selectMedia(task)',context);
  await assert.rejects(context.selectMediaGroup({},[audio],'audio'),/音频资源选择器完整文件名与顺序/);
  assert.equal(confirmed,false);
});

for(const changed of ['missing-audio','wrong-audio-number','wrong-image-filename','reordered'])
test('composer still rejects '+changed,async()=>{
  const media=[{type:'image',name:'actor.png',web_label:'@图片1'},
    {type:'image',name:'inspector.png',web_label:'@图片2'},
    {type:'audio',name:'voice.mp3',web_label:'@音频1'}];
  let displayed=media.map(item=>({...item}));
  if(changed==='missing-audio')displayed.pop();
  if(changed==='wrong-audio-number')displayed[2].web_label='@音频2';
  if(changed==='wrong-image-filename')displayed[0].name='other.png';
  if(changed==='reordered')displayed=[displayed[1],displayed[0],displayed[2]];
  const context={visible:()=>true,text:node=>node?.label || '',selectMediaGroup:async()=>{},
    composer:()=>({querySelectorAll:()=>displayed.map(item=>({querySelector:selector=>
      selector==='.qcv2-ref-kind-mark'?{label:item.web_label.slice(1)}:item.type==='audio'?null:
        {getAttribute:()=>`预览 ${item.name} 的素材详情`}}))}),
    waitFor:async(read,description)=>{if(!read())throw new Error(description);}};
  extract('  async function selectMedia(task)','  const settingsSummary=',context);
  await assert.rejects(context.selectMedia({media}),/本次素材数量、顺序与引用编号/);
});

for(const failSelection of [false,true])test('submission orders selection before injection and final send; selection failure='+failSelection,async()=>{
  const events=[];let sent=false;
  const context={preparationTask:null,lastHeartbeat:0,
    location:{origin:'https://www.runninghub.cn',hostname:'www.runninghub.cn',pathname:'/runow/home',href:'https://www.runninghub.cn/runow/home'},
    openProject:async()=>events.push('open-project'),
    waitForQuote:async(_task,label)=>events.push('quote:'+label),
    setMode:async()=>events.push('parameters'),
    selectMedia:async()=>{events.push('select-and-add');if(failSelection)throw new Error('filename not matched');},
    fillPrompt:async()=>events.push('inject-converted-prompt'),
    submissionPatch:async()=>({ok:true,ready:true,taskId:'submitted-id'}),
    find:()=>{throw new Error('submission must not inspect pricing labels');},
    composer:()=>({}),outputs:()=>[],pending:()=>sent?1:0,sendButton:()=>({}),
    waitFor:async read=>read(),click:async()=>{sent=true;events.push('send');},editor:()=>({}),fieldText:()=>''};
  extract('  async function submit(','  async function status(',context);
  if(failSelection) {
    await assert.rejects(context.submit({}),error=>error.beforeSend===true);
    assert.deepEqual(events,['open-project','quote:H3 增强版模型初始报价','parameters','quote:当前比例、分辨率和时长的报价','select-and-add']);
  } else {
    assert.equal((await context.submit({})).ok,true);
    assert.deepEqual(events,['open-project','quote:H3 增强版模型初始报价','parameters','quote:当前比例、分辨率和时长的报价','select-and-add','quote:填写提示词前的全部参考素材报价','inject-converted-prompt','quote:本次提示词及全部参考素材的报价','send']);
  }
});
