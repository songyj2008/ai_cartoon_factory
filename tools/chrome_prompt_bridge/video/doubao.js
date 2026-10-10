"use strict";
// DOM adapter for Doubao video creation. No model/workflow assumptions belong here.
if (!globalThis.__AICF_DOUBAO_VIDEO_ADAPTER__) {
  globalThis.__AICF_DOUBAO_VIDEO_ADAPTER__ = true;
  const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
  const visible = element => {
    if(!element || !element.isConnected)return false;
    const rect=element.getBoundingClientRect();
    const style=getComputedStyle(element);
    return rect.width>0 && rect.height>0 && style.display!=='none' && style.visibility!=='hidden';
  };
  const label = element => [element.innerText,element.getAttribute('aria-label'),element.getAttribute('title'),element.getAttribute('data-testid')]
    .filter(Boolean).join(' ').trim();
  const controls = () => [...document.querySelectorAll('button,[role="button"],[role="option"],label')].filter(visible);
  const find = (pattern,scope) => (scope ? [...scope.querySelectorAll('button,[role="button"],[role="option"],label')] : controls())
    .find(element=>visible(element) && pattern.test(label(element)));
  const ownLabel = element => [
    [...element.childNodes].filter(node=>node.nodeType===3).map(node=>node.textContent).join(''),
    element.getAttribute('aria-label'), element.getAttribute('title'), element.getAttribute('data-testid')
  ].filter(Boolean).join(' ').replace(/\s+/g,' ').trim();
  const findText = (pattern,scope=document,predicate=()=>true) => {
    const nodes=[...scope.querySelectorAll('button,[role="button"],[role="option"],label,span,div,p,li')];
    if(scope!==document)nodes.unshift(scope);
    return nodes.filter(element=>visible(element) && predicate(element) && pattern.test(ownLabel(element)))
      .sort((a,b)=>ownLabel(a).length-ownLabel(b).length)[0] || null;
  };
  const nearComposer = element => {
    const root=composerRoot();
    return Boolean(root && (root===element || root.contains(element)));
  };
  const composerRoot = () => {
    const field=editor();
    if(!field)return null;
    const surface=field.closest?.('.guidance-input-surface');
    if(surface)return surface;
    const form=field.closest?.('form,[data-testid*="chat_input"],[class*="chat-input"],[class*="chat_input"]');
    if(form && form!==field && form.getBoundingClientRect().height<innerHeight*.5)return form;
    for(let parent=field.parentElement,depth=0;parent && depth<5;parent=parent.parentElement,depth++) {
      if(parent.getBoundingClientRect().height>=innerHeight*.5)break;
      if(parent.querySelectorAll('button,[role="button"],img').length)return parent;
    }
    return field.parentElement || field;
  };
  const composerImages = () => {
    const root=composerRoot();
    return root ? [...root.querySelectorAll('img')].filter(visible) : [];
  };
  const uploadButton = () => {
    const root=composerRoot();
    const structural=root ? [...root.querySelectorAll(
      '.guidance-input-actions div.relative.flex.h-36.w-36.items-center.justify-center > button'
    )].filter(item=>visible(item) && !item.disabled && item.getAttribute('aria-disabled')!=='true') : [];
    if(structural.length===1)return structural[0];
    const text=findText(/^\+$/,document,nearComposer);
    if(text)return text;
    const field=editor(),mode=findText(/^视频生成$/,document,nearComposer);
    if(!field || !mode)return null;
    const box=field.getBoundingClientRect(),chip=mode.getBoundingClientRect();
    const candidates=[...document.querySelectorAll('button,[role="button"],[tabindex="0"],svg')]
      .map(item=>item.closest?.('button,[role="button"],[tabindex="0"]') || item)
      .filter(item=>{
        if(!visible(item))return false;
        const rect=item.getBoundingClientRect();
        return rect.left>=box.left-32 && rect.left+rect.width<=chip.left+4 &&
          Math.abs((rect.top+rect.height/2)-(chip.top+chip.height/2))<24 &&
          rect.width<=64 && rect.height<=64;
      });
    const unique=[...new Set(candidates)];
    const named=unique.find(item=>/添加|上传|add|upload|plus/i.test(label(item)));
    return named || (unique.length===1 ? unique[0] : null);
  };
  const uploadComplete = expected => {
    const images=composerImages(),root=composerRoot();
    return images.length>=expected && images.every(item=>item.complete!==false &&
      (typeof item.naturalWidth!=='number' || item.naturalWidth>0)) &&
      ![...root.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].some(visible) &&
      !findText(/上传中|正在上传|上传失败|重试上传/,root);
  };
  const editor = () => {
    const fields=[...document.querySelectorAll('textarea[placeholder],textarea,input[placeholder],[role="textbox"],[contenteditable],[data-placeholder]')]
      .filter(element=>visible(element) && !element.disabled && !element.readOnly &&
        (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement ||
         element.isContentEditable || element.getAttribute('contenteditable')==='true'));
    const video=fields.filter(element=>/视频/.test([element.getAttribute('placeholder'),element.getAttribute('data-placeholder'),element.getAttribute('aria-label')].join(' ')));
    return (video.length===1 ? video : fields.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top))[0] || null;
  };
  const videoModeReady = () => {
    const field=editor();
    return Boolean(field && /描述你想要的视频|视频提示词/.test([
      field.getAttribute('placeholder'),field.getAttribute('data-placeholder'),field.getAttribute('aria-label')
    ].join(' '))) || Boolean(findText(/描述你想要的视频/,document,nearComposer) &&
      findText(/^视频生成$/,document,nearComposer)) || Boolean(
      findText(/^视频生成$/,document,nearComposer) && settingsControl());
  };
  async function trustedClick(element,taskId,description) {
    if(!element || !visible(element))throw new Error('豆包页面缺少'+description);
    const rect=element.getBoundingClientRect();
    const x=Math.min(innerWidth-1,Math.max(0,rect.left+rect.width/2));
    const y=Math.min(innerHeight-1,Math.max(0,rect.top+rect.height/2));
    const clicked=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_CLICK',taskId,
      x,y,width:innerWidth,height:innerHeight});
    if(!clicked?.ok)throw new Error(clicked?.error || '豆包'+description+'点击失败');
  }
  async function ensureVideoMode(taskId) {
    if(videoModeReady())return;
    const control=await waitFor(()=>videoModeReady() ||
      findText(/^视频生成$/,document,nearComposer) || find(/视频生成/),
      '视频生成入口或模式',12000);
    if(control===true || videoModeReady())return;
    await trustedClick(control,taskId,'视频生成入口');
    await waitFor(videoModeReady,'视频生成模式',10000);
  }
  const durationPanel = () => {
    const sliders=[...document.querySelectorAll('input[type="range"],[role="slider"]')].filter(visible);
    const panels=sliders.map(slider=>{
      for(let root=slider.parentElement,depth=0;root && depth<7;root=root.parentElement,depth++) {
        if([...root.querySelectorAll('button,span,div,p')].some(element=>visible(element) && /^16\s*[:：]\s*9$/.test(element.textContent.trim())))return {root,slider};
      }
      return null;
    }).filter(Boolean);
    return panels.length===1 ? panels[0] : null;
  };
  const visibleSlider = () => durationPanel()?.slider;
  const settingsOpen = () => Boolean(visibleSlider() && findText(/^16\s*[:：]\s*9$/));
  const settingsControl = () => findText(/(?:自动|\d+\s*[:：]\s*\d+).*\d+\s*(?:s|秒)/i,document,nearComposer)
    || findText(/自动\s*[·•]?\s*\d+\s*s|比例|时长/,composerRoot() || document,nearComposer);
  async function ensureSettingsOpen(taskId) {
    if(settingsOpen())return;
    let control;
    try {
      control=await waitFor(()=>settingsOpen() || settingsControl(),'视频参数入口',15000);
    } catch(error) {
      const field=editor(),root=composerRoot();
      const candidates=[...document.querySelectorAll('button,[role="button"],span')]
        .filter(element=>visible(element) && /比例|时长|自动|\d+\s*[:：]\s*\d+|\d+\s*(?:s|秒)/i.test(ownLabel(element)))
        .slice(0,8).map(element=>({text:ownLabel(element).slice(0,80),inComposer:nearComposer(element)}));
      error.message+='；页面诊断：'+JSON.stringify({readyState:document.readyState,
        videoModeReady:videoModeReady(),editorPlaceholder:field?.getAttribute('placeholder') || field?.getAttribute('data-placeholder') || '',
        composerTag:root?.tagName || '',composerClass:String(root?.className || '').slice(0,120),candidates});
      throw error;
    }
    // The panel may have opened during the wait. Do not toggle it closed.
    if(control===true || settingsOpen())return;
    await trustedClick(control,taskId,'视频参数入口');
    await waitFor(settingsOpen,'比例和时长参数面板',8000);
  }
  const waitFor = async (getter,description,timeout=12000) => {
    const end=Date.now()+timeout;
    while(Date.now()<end){const value=getter();if(value)return value;await delay(250);}
    throw new Error('豆包页面缺少'+description+'；网页结构可能已更新');
  };
  async function waitForComposer({video=false,empty=false}={}) {
    let previous=null,stableSince=0;
    return waitFor(()=>{
      const field=editor();
      const ready=document.readyState==='complete' && field &&
        (!video || videoModeReady()) &&
        (!empty || (!String(field.value || field.innerText || '').trim() &&
                    !composerImages().length && doubaoMessageCount()===0));
      if(!ready){previous=null;stableSince=0;return null;}
      const rect=field.getBoundingClientRect();
      const geometry=[rect.left,rect.top,rect.width,rect.height].join(',');
      if(!previous || previous.field!==field || previous.geometry!==geometry) {
        previous={field,geometry};stableSince=Date.now();return null;
      }
      return Date.now()-stableSince>=750 ? field : null;
    },empty?'已加载完成的空白对话输入框':'已加载完成的视频输入组件',30000);
  }
  const mediaSources = () => [...document.querySelectorAll('video,a[href]')]
    .map(element=>element.currentSrc || element.src || element.href || '')
    .filter(url=>url && (/^blob:/i.test(url) || /\.(mp4|webm|mov)(\?|$)/i.test(url) || /\/video\//i.test(url)));
  const readyCards = () => [...document.querySelectorAll('div,span,p')]
    .filter(element=>visible(element) && element.children.length===0 && /你的视频生成好了/.test(element.textContent || ''));
  const confirmationPattern=/(?:确认后.{0,48}(?:开始|生成视频)|(?:请|需要|是否).{0,16}确认.{0,32}(?:开始|生成视频|发送))/s;
  const confirmationRequests=()=>{
    const root=composerRoot();
    return [...document.querySelectorAll('div,span,p')].filter(element=>
      visible(element) && element.children.length===0 && !root?.contains(element) &&
      confirmationPattern.test(element.innerText || element.textContent || ''));
  };
  async function setDuration(seconds,taskId) {
    let range=await waitFor(visibleSlider,'参数面板内唯一时长滑块');
    const readValue=()=>{
      const panel=durationPanel();
      if(!panel)return NaN;
      range=panel.slider;
      const sliderRect=range.getBoundingClientRect();
      const labels=[...panel.root.querySelectorAll('span,div,p,output')].filter(element=>visible(element))
        .map(element=>({element,match:String(element.textContent || '').trim().match(/^(\d{1,2})\s*(?:s|秒)$/i)}))
        .filter(item=>item.match && (()=>{
          const rect=item.element.getBoundingClientRect();
          return rect.bottom<=sliderRect.top+8 && sliderRect.top-rect.bottom<80 && rect.top<sliderRect.top;
        })())
        .sort((a,b)=>{
          const center=item=>{const rect=item.element.getBoundingClientRect();return rect.left+rect.width/2;};
          const sliderCenter=sliderRect.left+sliderRect.width/2;
          return Math.abs(center(a)-sliderCenter)-Math.abs(center(b)-sliderCenter);
        });
      if(labels.length)return Number(labels[0].match[1]);
      // Numeric aria values can be slider indices, not seconds.
      const text=String(range.getAttribute('aria-valuetext') || '').match(/^\s*(\d+)\s*(?:s|秒)\s*$/i);
      return text ? Number(text[1]) : NaN;
    };
    if(seconds<4 || seconds>10)throw new Error('豆包免费版仅支持 4–10 秒时长');
    let lastValue=null,stableSince=0;
    let current=await waitFor(()=>{
      const value=readValue();
      if(!Number.isFinite(value)){lastValue=null;return null;}
      if(value!==lastValue){lastValue=value;stableSince=Date.now();return null;}
      return Date.now()-stableSince>=500 ? value : null;
    },'参数面板内稳定显示的时长秒数（排除滑块下方端点）',15000);
    if(!Number.isFinite(current))throw new Error('豆包时长滑块没有可核对的当前值');
    if(current>10)throw new Error('豆包时长已进入付费区，已停止操作');
    if(current<seconds)return false;
    if(current>seconds) {
      if(typeof range.focus==='function')range.focus({preventScroll:true});
      if(document.activeElement!==range) {
        if(range.getBoundingClientRect().width>40)throw new Error('豆包时长滑块无法聚焦，已停止以避免误触付费时长');
        await trustedClick(range,taskId,'时长滑块手柄');
      }
      if(readValue()!==current)throw new Error('豆包时长滑块聚焦时发生变化，已停止操作');
      for(let attempts=0;current>seconds && attempts<30;attempts++) {
        const result=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_KEY',taskId,key:'ArrowLeft'});
        if(!result?.ok)throw new Error(result?.error || '豆包时长左方向键输入失败');
        const previous=current;
        const changed=await waitFor(()=>{
          const value=readValue();
          return Number.isFinite(value) && value!==previous ? value : null;
        },'时长滑块响应左方向键',1500);
        current=changed;
        if(current>=previous || current<seconds)throw new Error('豆包时长滑块移动异常，已停止操作');
      }
      if(current!==seconds)throw new Error('豆包时长滑块未到达目标时长');
    }
    const displayed=Boolean(findText(new RegExp('^'+seconds+'\\s*(s|秒)$','i')))
      || Boolean(findText(new RegExp('(?:自动|16\\s*[:：]\\s*9).*'+seconds+'\\s*(s|秒)','i'),document,nearComposer));
    if(!displayed)throw new Error('豆包页面未显示目标时长 '+seconds+' 秒');
    return true;
  }
  function doubaoMessageCount() {
    return [...document.querySelectorAll('[data-testid*="message"],[class*="message-content"]')]
      .filter(item=>visible(item)).length;
  }
  async function startNewChat(taskId) {
    const field=editor();
    if(String(field?.value || field?.innerText || '').trim())throw new Error('豆包输入框有草稿，不能自动新建对话；未上传素材');
    if(composerImages().length)
      throw new Error('豆包输入框已有参考图，不能自动新建对话；未上传新任务素材');
    const labels=[...document.querySelectorAll('a,button,[role="button"],[tabindex="0"],div,span')]
      .filter(element=>visible(element) && /^(新对话|新建对话|新聊天|new chat)(?:\s|$)/i.test(ownLabel(element)));
    const leaves=labels.filter(element=>!labels.some(child=>child!==element && element.contains(child)));
    const sidebar=leaves.filter(element=>element.getBoundingClientRect().left<=innerWidth*0.35);
    const firstTop=sidebar.length?Math.min(...sidebar.map(element=>element.getBoundingClientRect().top)):Infinity;
    const firstRow=sidebar.filter(element=>element.getBoundingClientRect().top<=firstTop+24);
    const target=firstRow.length===1?firstRow[0]:sidebar.length===1?sidebar[0]:null;
    if(!target)throw new Error('无法唯一识别豆包“新对话”入口；请手动新建对话后重试');
    await trustedClick(target,taskId,'新对话入口');
    // Doubao can open a fresh composer without changing its SPA URL.
    await waitForComposer({empty:true});
  }
  async function chooseLandscape(taskId) {
    await ensureVideoMode(taskId);
    await ensureSettingsOpen(taskId);
    const ratio=await waitFor(()=>findText(/^16\s*[:：]\s*9$/),'16:9 比例');
    await trustedClick(ratio,taskId,'16:9 比例');
    await ensureSettingsOpen(taskId);
  }
  async function upload(images,taskId) {
    if(!images.length)throw new Error('视频任务没有参考图片');
    if(settingsOpen() && editor())await trustedClick(editor(),taskId,'视频提示词区域');
    const before=composerImages().length;
    if(images.every(image=>image.path)) {
      let uploaded=0;
      for(const image of images) {
        const plus=await waitFor(uploadButton,'参考图添加按钮');
        const rect=plus.getBoundingClientRect();
        const selected=await chrome.runtime.sendMessage({type:'AICF_VIDEO_CHOOSE_FILE',taskId,
          x:Math.min(innerWidth-1,Math.max(0,rect.left+rect.width/2)),
          y:Math.min(innerHeight-1,Math.max(0,rect.top+rect.height/2)),
          width:innerWidth,height:innerHeight,files:[image.path]});
        if(!selected?.ok)throw new Error(selected?.error || '豆包参考图片文件选择失败');
        uploaded++;
        const expected=before+uploaded;
        await waitFor(()=>uploadComplete(expected),
          '第 '+uploaded+' 张参考图上传完成',60000);
      }
      return;
    }
    // Older queued payloads contain bytes only. Keep their direct-file fallback
    // so a task already claimed by an earlier extension can finish safely.
    const files=images.map(image=>new File([new Uint8Array(image.data)],image.name,{type:image.mime}));
    const fileInput=()=>[...document.querySelectorAll('input[type="file"]')]
      .find(element=>/image|png|jpg|jpeg|webp|\*/i.test(element.accept || 'image/*'));
    let input=fileInput();
    if(!input) {
      const plus=uploadButton();
      await trustedClick(plus,taskId,'参考图添加按钮');
      input=fileInput();
    }
    if(!input) {
      const uploadChoice=findText(/上传图片|上传文件|本地上传|从电脑上传/,document,nearComposer);
      if(uploadChoice)await trustedClick(uploadChoice,taskId,'本地图片上传选项');
      input=await waitFor(fileInput,'参考图上传控件');
    }
    if(input.multiple || files.length===1) {
      const transfer=new DataTransfer();files.forEach(file=>transfer.items.add(file));
      input.files=transfer.files;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));
    } else {
      for(const [index,file] of files.entries()) {
        const transfer=new DataTransfer();transfer.items.add(file);
        input.files=transfer.files;
        input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));
        await delay(800);
        if(index<files.length-1) {
          const nextPlus=uploadButton();
          if(nextPlus)await trustedClick(nextPlus,taskId,'下一张参考图添加按钮');
          input=await waitFor(fileInput,'下一张参考图上传控件');
        }
      }
    }
    await waitFor(()=>{
      return uploadComplete(before+images.length);
    },'参考图上传完成',60000);
  }
  async function setPrompt(prompt) {
    const input=await waitForComposer({video:true});
    if(!input)throw new Error('豆包视频提示词输入框不存在');
    input.focus();
    if(input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {
      const setter=Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,'value').set;
      setter.call(input,prompt);input.dispatchEvent(new Event('input',{bubbles:true}));
    } else {
      if(input.getAttribute('contenteditable')!=='true' && input.isContentEditable!==true)
        throw new Error('豆包视频提示词控件不是可编辑区域');
      const selection=getSelection(),range=document.createRange();
      range.selectNodeContents(input);selection.removeAllRanges();selection.addRange(range);
      if(!document.execCommand('insertText',false,prompt))throw new Error('豆包网页拒绝填入视频提示词');
    }
  }
  async function submit(task) {
    let sendStarted=false;
    let stage='等待网页加载';
    try {
    if(task.input.aspect_ratio!=='16:9' || !Number.isInteger(task.input.duration_sec) ||
       task.input.duration_sec<4 || task.input.duration_sec>10)throw new Error('豆包任务规格无效');
    await waitForComposer();
    stage='设置视频比例和时长';
    await chooseLandscape(task.id);
    let durationSet=await setDuration(task.input.duration_sec,task.id);
    if(!durationSet) {
      stage='新建对话并恢复默认时长';
      await startNewChat(task.id);
      await chooseLandscape(task.id);
      durationSet=await setDuration(task.input.duration_sec,task.id);
      if(!durationSet)throw new Error('豆包新对话仍无法恢复到目标时长以上；未尝试向右调整，也未上传素材');
    }
    stage='等待视频输入组件';
    await waitForComposer({video:true});
    stage='上传参考图片';
    await upload(task.images || [],task.id);
    stage='填入视频提示词';
    await setPrompt(task.input.prompt);
    stage='提交视频生成';
    const baseline=mediaSources();
    const baselineReadyCount=readyCards().length;
    const baselineConfirmationCount=confirmationRequests().length;
    const send=await waitFor(()=>{
      const button=[...document.querySelectorAll('button[data-testid="chat_input_send_button"]')].find(visible)
        || find(/发送|生成视频|开始生成|chat_input_send_button/);
      return button && !button.hasAttribute('disabled') && button.getAttribute('aria-disabled')!=='true' ? button : null;
    },'可用的视频发送按钮',20000);
    const rect=send.getBoundingClientRect();
    const editor=[...document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')].filter(visible).at(-1);
    const pendingBefore=[...document.querySelectorAll('span,div')].filter(element=>visible(element) &&
      element.children.length===0 && /生成中|排队中|正在生成/.test(element.textContent || '')).length;
    sendStarted=true;
    const dispatched=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_CLICK',taskId:task.id,
      x:rect.left+rect.width/2,y:rect.top+rect.height/2,width:innerWidth,height:innerHeight});
    if(!dispatched?.ok)throw new Error(dispatched?.error || '豆包视频发送失败');
    await waitFor(()=>{
      const value=(editor?.value || editor?.textContent || '').trim();
      const pending=[...document.querySelectorAll('span,div')].filter(element=>visible(element) &&
        element.children.length===0 && /生成中|排队中|正在生成/.test(element.textContent || '')).length;
      return value!==task.input.prompt || pending>pendingBefore ||
        mediaSources().some(source=>!baseline.includes(source));
    },'已接受的视频生成任务',10000);
    return {ok:true,baseline,baselineReadyCount,baselineConfirmationCount};
    } catch(error) {error.message='['+stage+'] '+error.message;error.beforeSend=!sendStarted;throw error;}
  }
  async function status(task) {
    const candidates=mediaSources().filter(source=>!(task.baseline || []).includes(source));
    const cards=readyCards();
    const newCard=cards.length>Number(task.baselineReadyCount || 0);
    // A previous confirmation request remains in chat after generation starts.
    // Completion must win over that historical message.
    if(candidates.length || newCard)return {ok:true,ready:true,url:candidates.at(-1) || '',cardReady:newCard};
    if(!task.confirmationAttempted &&
       confirmationRequests().length>Number(task.baselineConfirmationCount || 0))
      return {ok:true,ready:false,confirmationRequired:true};
    return {ok:true,ready:false};
  }
  async function confirmGeneration(task) {
    const input=await waitForComposer();
    const answer='确认，立即执行生成';
    input.focus();
    if(input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {
      const prototype=input instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,answer);
      input.dispatchEvent(new Event('input',{bubbles:true}));
    } else {
      const selection=getSelection(),range=document.createRange();
      range.selectNodeContents(input);selection.removeAllRanges();selection.addRange(range);
      if(!document.execCommand('insertText',false,answer))throw new Error('豆包确认回复写入失败');
    }
    const send=await waitFor(()=>{
      const button=[...document.querySelectorAll('button[data-testid="chat_input_send_button"]')].find(visible)
        || find(/发送|chat_input_send_button/);
      return button && !button.disabled && button.getAttribute('aria-disabled')!=='true' ? button : null;
    },'豆包确认回复发送按钮');
    const rect=send.getBoundingClientRect();
    const result=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_CLICK',purpose:'confirmation',
      taskId:task.id,x:rect.left+rect.width/2,y:rect.top+rect.height/2,width:innerWidth,height:innerHeight});
    if(!result?.ok)throw new Error(result?.error || '豆包确认回复发送失败');
    return {ok:true};
  }
  async function download(task) {
    const source=mediaSources().find(url=>!(task.baseline || []).includes(url));
    const video=[...document.querySelectorAll('video')].find(element=>(element.currentSrc || element.src)===source);
    let button=null;
    const card=readyCards().at(-1);
    for(let parent=video?.parentElement || card?.parentElement,depth=0;parent && depth<8;parent=parent.parentElement,depth++) {
      button=find(/下载|download/i,parent);
      if(button)break;
    }
    if(!button && card){
      let preview=null;
      for(let parent=card.parentElement,depth=0;parent && depth<6;parent=parent.parentElement,depth++){
        preview=[...parent.querySelectorAll('video,img,[aria-label*="播放"],[aria-label*="play"]')].find(visible);
        if(preview)break;
      }
      if(preview){
        const rect=preview.getBoundingClientRect();
        const opened=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_CLICK',purpose:'download',taskId:task.id,
          x:rect.left+rect.width/2,y:rect.top+rect.height/2,width:innerWidth,height:innerHeight});
        if(!opened?.ok)throw new Error(opened?.error || '豆包成片预览打开失败');
        button=await waitFor(()=>find(/下载|download/i),'生成结果下载按钮',10000);
      }
    }
    if(!button)throw new Error('生成结果未显示下载按钮');
    const rect=button.getBoundingClientRect();
    const clicked=await chrome.runtime.sendMessage({type:'AICF_VIDEO_TRUSTED_CLICK',purpose:'download',taskId:task.id,
      x:rect.left+rect.width/2,y:rect.top+rect.height/2,width:innerWidth,height:innerHeight});
    if(!clicked?.ok)throw new Error(clicked?.error || '豆包下载按钮点击失败');
    return {ok:true};
  }
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(message?.type==='AICF_VIDEO_UPLOAD_MENU') {
      const choice=findText(/上传文件或图片|上传图片或文件|上传图片|上传文件/);
      const rect=choice?.getBoundingClientRect();
      respond({ok:true,menu:rect ? {x:rect.left+rect.width/2,y:rect.top+rect.height/2,
        width:innerWidth,height:innerHeight} : null});
      return;
    }
    if(!message || !['AICF_VIDEO_SUBMIT','AICF_VIDEO_STATUS','AICF_VIDEO_CONFIRM','AICF_VIDEO_DOWNLOAD'].includes(message.type))return;
    const action=message.type==='AICF_VIDEO_SUBMIT' ? submit(message.task) :
      message.type==='AICF_VIDEO_STATUS' ? status(message.task) :
      message.type==='AICF_VIDEO_CONFIRM' ? confirmGeneration(message.task) : download(message.task);
    Promise.resolve(action).then(respond).catch(error=>respond({ok:false,error:error.message || String(error),before_send:error.beforeSend===true}));
    return true;
  });
}
