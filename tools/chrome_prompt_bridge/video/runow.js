"use strict";
// Runow website logic is isolated here. Fail closed if the visible UI changes.
if(!globalThis.__AICF_RUNOW_VIDEO_ADAPTER__) {
  globalThis.__AICF_RUNOW_VIDEO_ADAPTER__=true;
  const C=globalThis.RunowContract;
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const actionPause=()=>sleep(20+Math.floor(Math.random()*131));
  let preparationTask=null,lastHeartbeat=0;
  async function heartbeat(stage='准备素材与文案') {
    if(!preparationTask || Date.now()-lastHeartbeat<20000)return;
    const result=await chrome.runtime.sendMessage({type:'AICF_RUNOW_PROGRESS',taskId:preparationTask.id,stage});
    if(!result?.ok)throw new Error(result?.error || 'Runow 准备任务凭据失效');
    lastHeartbeat=Date.now();
  }
  const visible=node=>{
    if(!node?.isConnected)return false;
    const box=node.getBoundingClientRect(),style=getComputedStyle(node);
    return box.width>0 && box.height>0 && style.display!=='none' && style.visibility!=='hidden';
  };
  const text=node=>C.normalize(node?.innerText || node?.textContent);
  const own=node=>C.normalize([
    ...[...(node?.childNodes || [])].filter(child=>child.nodeType===3).map(child=>child.textContent),
    node?.getAttribute('aria-label'),node?.getAttribute('title')
  ].filter(Boolean).join(' '));
  const composer=()=>{
    const root=document.querySelector('section[data-testid="quick-create-composer"][data-kind="video"] [data-direct-creation="true"]');
    return visible(root)?root:null;
  };
  const editor=()=>{
    const field=composer()?.querySelector('.qcv2-hero-mention-editor[contenteditable="true"]');
    return visible(field)?field:null;
  };
  function find(pattern,scope=document) {
    const nodes=[...scope.querySelectorAll('button,[role="button"],[role="option"],[role="menuitem"],[role="tab"],label,span,div')];
    return nodes.filter(node=>visible(node) && !node.closest('[contenteditable="true"]') &&
      (pattern.test(own(node)) || node.matches('button,[role="option"],[role="menuitem"],[role="tab"]') && pattern.test(text(node))))
      .sort((a,b)=>text(a).length-text(b).length)[0] || null;
  }
  async function waitFor(read,description,timeout=15000) {
    const deadline=Date.now()+timeout;
    do {
      await heartbeat(description);
      const result=await read();
      if(result)return result;
      await sleep(150);
    } while(Date.now()<deadline);
    throw new Error('Runow 无法确认'+description+'；任务已停止');
  }
  async function click(node,task,purpose) {
    if(!visible(node) || node.disabled || node.getAttribute('aria-disabled')==='true')throw new Error('Runow 控件不可操作');
    const control=node.closest('button,[role="button"],[role="menuitem"],[role="option"],[role="tab"]') || node;
    if(control.disabled || control.getAttribute('aria-disabled')==='true')throw new Error('Runow 控件不可操作');
    control.click();
    await actionPause();
  }
  async function clickText(pattern,task,scope=document) {
    return click(await waitFor(()=>find(pattern,scope),'操作控件 '+pattern),task);
  }
  async function setInput(field,value) {
    const proto=field instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,'value').set.call(field,value);
    field.dispatchEvent(new Event('input',{bubbles:true}));
    field.dispatchEvent(new Event('change',{bubbles:true}));
    await actionPause();
  }
  const fieldText=field=>field?.value ?? field?.innerText ?? '';
  const escape=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  function library() {
    const root=[...document.querySelectorAll('.qcv2-asset-library')].find(visible);
    const search=root?.querySelector('input[aria-label="搜索我的素材"]');
    return root && visible(search)?{root,search}:null;
  }
  async function openProject(task) {
    await waitFor(()=>document.readyState==='complete','页面加载',30000);
    if(C.pagePath(location.pathname)==='/runow/home') {
      await clickText(/^新建项目$/,task);
      await waitFor(()=>/^\/runow\/\d+\/?$/.test(C.pagePath(location.pathname)),'新建空项目',30000);
    }
    if(!/^\/runow\/\d+\/?$/.test(C.pagePath(location.pathname)))throw new Error('Runow 未进入项目内创作页面');
    // Canvas toolbar actions create placeholder nodes with a different editor.
    // Our reference binding and prompt controls belong to the cards composer.
    // The creation entry is sufficient when the cards view is already active.
    // Some layouts expose it without the workspace switch's CSS class.
    const workspace=await waitFor(()=>{
      const entry=[...document.querySelectorAll('section[data-testid="quick-create-composer"]')]
        .find(visible) || find(/^继续创作(?:\s|$)/);
      if(entry)return entry;
      const control=document.querySelector('.qcv2-workspace-view-switch');
      return visible(control)?control:null;
    },'快捷创作入口或工作区切换控件',30000);
    if(workspace.getAttribute?.('data-active-view')==='canvas') {
      const cards=[...workspace.querySelectorAll('button')].find(node=>visible(node) && text(node)==='卡片');
      await click(cards,task);
      await waitFor(()=>document.querySelector('.qcv2-workspace-view-switch')?.getAttribute('data-active-view')==='cards',
        '卡片工作区');
    }
    const entry=await waitFor(()=>[...document.querySelectorAll('section[data-testid="quick-create-composer"]')]
      .find(visible) || find(/^继续创作(?:\s|$)/),'快捷创作表单入口',30000);
    if(!entry.matches('section[data-testid="quick-create-composer"]'))await click(entry,task);
    const panel=await waitFor(()=>[...document.querySelectorAll('section[data-testid="quick-create-composer"]')]
      .find(visible),'项目内快捷创作面板');
    if(panel.getAttribute('data-kind')!=='video') {
      const modeMenu=panel.querySelector('button.qcv2-composer-mode-menu');
      if(visible(modeMenu)) {
        await click(modeMenu,task);
        await click(await waitFor(()=>[...document.querySelectorAll('[role="menuitem"]')]
          .find(node=>visible(node) && text(node)==='视频'),'视频创作模式'),task);
      } else {
        await click(await waitFor(()=>[...panel.querySelectorAll('button')]
          .find(node=>visible(node) && /^(?:视频|视频创作)$/.test(text(node))),'视频创作模式'),task);
      }
    }
    const root=await waitFor(composer,'项目内视频创作表单');
    const model=root.querySelector('button.is-model');
    if(!C.enhancedModelName.test(model?.getAttribute('aria-label') || '')) {
      const option=await waitFor(async()=>{
        const option=[...document.querySelectorAll('button,[role="button"]')].find(button=>
          visible(button) && !button.disabled && button.getAttribute('aria-disabled')!=='true' &&
          C.enhancedModelName.test(text(button.querySelector('strong'))));
        // Switching creation mode can rebuild the toolbar and close its first popover.
        const current=composer()?.querySelector('button.is-model');
        if(!option && visible(current) && !current.disabled && !current.classList.contains('is-open'))await click(current,task);
        return option;
      },'Minimax H3-RH 增强版 / Enhanced 模型选项');
      await click(option,task);
    }
    await waitFor(()=>C.enhancedModelName.test(
      composer()?.querySelector('button.is-model')?.getAttribute('aria-label') || ''),'项目内 H3 增强版模型');
  }
  async function openLibrary(task,kind) {
    const label=({image:'图片',video:'视频',audio:'音频'})[kind];
    const existing=library();
    if(existing) {
      await clickText(/^取消$/,task,existing.root);
      await waitFor(()=>!library(),'关闭先前资源选择器');
    }
    const root=await waitFor(composer,'视频输入区域');
    const entry=await waitFor(()=>root.querySelector('button.qcv2-direct-attachment-source[aria-label="'+label+'"]'),
      '项目内'+label+'素材入口');
    if(entry.getAttribute('aria-expanded')!=='true')await click(entry,task);
    await click(await waitFor(()=>[...document.querySelectorAll('[role="menuitem"]')]
      .find(node=>visible(node) && text(node)==='从资源选择'),'从资源选择菜单项'),task);
    const lib=await waitFor(library,'我的素材资源选择器');
    await clickText(/^我的素材$/,task,lib.root);
    await click(await waitFor(()=>[...lib.root.querySelectorAll('.qcv2-asset-library-filters button')]
      .find(node=>visible(node) && text(node)===label),label+'素材筛选'),task);
    await waitFor(()=>[...lib.root.querySelectorAll('.qcv2-asset-library-filters button')]
      .some(node=>visible(node) && text(node)===label && node.getAttribute('aria-pressed')==='true'),
      label+'素材筛选生效');
    return lib;
  }
  function resource(item,root) {
    const cards=[...root.querySelectorAll('button.qcv2-asset-library-card-select')].filter(visible).map(card=>({
      name:card.querySelector('span[title]')?.getAttribute('title'),
      type:({图片:'image',视频:'video',音频:'audio'})[text(card.querySelector('small'))],
      element:card,
    }));
    return C.uniqueResource(cards,item.name,item.type);
  }
  async function searchResource(item,lib) {
    await setInput(lib.search,item.name);
    // The site's search is debounced. Do not interpret a transient empty list as a miss.
    await sleep(1000);
    const result=await waitFor(()=>{
      if(lib.search.value!==item.name || [...lib.root.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible))return null;
      const match=resource(item,lib.root);
      if(match)return {match};
      // An empty transient list is not evidence of absence: require the site's empty state.
      const missing=[...lib.root.querySelectorAll('.qcv2-asset-library-state strong')].some(node=>
        visible(node) && /^(没有搜索结果|我的素材中还没有内容|还没有收集内容|暂无素材)$/.test(text(node)));
      if(missing)return {missing:true};
      return null;
    },'文件名搜索结果：'+item.name,20000);
    return result.match || null;
  }
  async function ensureResource(item,lib,task) {
    // Match the visible full filenames first. Search only if the requested card is absent.
    if(lib.search && !lib.search.value) {
      const match=resource(item,lib.root);
      if(match)return match;
    }
    let match=await searchResource(item,lib);
    if(match)return match;
    const upload=await waitFor(()=>find(/^\+?\s*本地上传$/,lib.root),'资源库本地上传按钮');
    upload.scrollIntoView({block:'nearest'});
    await actionPause();
    const r=upload.getBoundingClientRect();
    const result=await chrome.runtime.sendMessage({type:'AICF_VIDEO_CHOOSE_FILE',taskId:task.id,files:[item.path],
      x:r.left+r.width/2,y:r.top+r.height/2,width:innerWidth,height:innerHeight});
    if(!result?.ok)throw new Error(result?.error || 'Runow 资源上传失败');
    await actionPause();
    match=await waitFor(()=>resource(item,lib.root),'素材上传入库：'+item.name,120000);
    if(/上传失败|处理失败/.test(text(match.element)))throw new Error('Runow 素材入库失败：'+item.name);
    return match;
  }
  function selectionCount(root) {
    const button=[...root.querySelectorAll('footer button')]
      .find(node=>visible(node) && /^添加已选/.test(text(node)));
    const count=text(button).match(/^添加已选\s*[（(]\s*(\d+)\s*[）)]$/);
    if(count)return Number(count[1]);
    // Some layouts omit the zero count on the disabled confirmation button.
    if(text(button)==='添加已选' && button.disabled &&
       ![...root.querySelectorAll('button.qcv2-asset-library-card-select[aria-pressed="true"]')].some(visible))return 0;
    return null;
  }
  function selectionControl(card) {
    return visible(card) && card.matches('button.qcv2-asset-library-card-select')?card:null;
  }
  async function addSelected(lib,count) {
    const label=new RegExp('^添加已选\\s*[（(]'+count+'[）)]$');
    const button=await waitFor(()=>[...lib.root.querySelectorAll('footer button')]
      .find(node=>visible(node) && !node.disabled && label.test(text(node))),'添加已选按钮（'+count+'）');
    // The footer is outside the card scroller. Trigger its actual button handler
    // synchronously, so a search rerender or scroll cannot invalidate coordinates.
    button.click();
    await actionPause();
    await waitFor(()=>!library(),'参考素材添加完成');
  }
  async function selectMediaGroup(task,items,kind,previous=[]) {
    const lib=await openLibrary(task,kind);
    const label=({image:'图片',video:'视频',audio:'音频'})[kind];
    // Each type has its own entry, but the picker edits the full reference list.
    // Preserve earlier groups because confirming replaces that list on the site.
    if(lib.search.value)await setInput(lib.search,'');
    const initial=await waitFor(()=>{
      const count=selectionCount(lib.root);
      const names=[...lib.root.querySelectorAll('.qcv2-asset-library-selected-item[title]')]
        .map(node=>node.getAttribute('title'));
      return count===null || names.length!==count?null:{count,names};
    },label+'资源选择器已选数量');
    if(initial.count!==previous.length || initial.names.some((name,index)=>name!==previous[index].name))
      throw new Error('Runow '+label+'资源选择器已选素材与前序参考不一致：'+initial.names.join('、'));
    let count=initial.count;
    const selected=[];
    for(const item of items) {
      const match=await ensureResource(item,lib,task);
      const checkbox=await waitFor(()=>{
        const current=resource(item,lib.root);
        return current && selectionControl(current.element);
      },'素材选择按钮：'+item.name);
      await click(checkbox,task);
      count++;
      await waitFor(()=>selectionCount(lib.root)===count,'已选 '+count+' 个素材');
      await waitFor(()=>{
        const current=resource(item,lib.root);
        if(!current)return false;
        return current.element.getAttribute('aria-pressed')==='true' &&
          text(current.element.querySelector('b.qcv2-asset-library-check'))===String(count);
      },'素材选择序号 '+count);
      selected.push({name:match.name,type:match.type});
    }
    C.validateSelection(items,selected);
    const expected=[...previous,...items];
    await waitFor(()=>{
      const names=[...lib.root.querySelectorAll('.qcv2-asset-library-selected-item[title]')]
        .map(node=>node.getAttribute('title'));
      return names.length===expected.length && names.every((name,index)=>name===expected[index].name);
    },label+'资源选择器完整文件名与顺序');
    await addSelected(lib,count);
  }
  async function selectMedia(task) {
    const previous=[];
    for(const kind of ['image','video','audio']) {
      const items=task.media.filter(item=>item.type===kind);
      if(items.length) {
        await selectMediaGroup(task,items,kind,previous);
        previous.push(...items);
      }
    }
    await waitFor(()=>{
      const previews=[...composer().querySelectorAll('.qcv2-ref-preview')].filter(visible);
      return previews.length===previous.length && previous.every((item,index)=>{
        const preview=previews[index];
        if('@'+text(preview.querySelector('.qcv2-ref-kind-mark')).replace(/\s/g,'')!==item.web_label)return false;
        // Audio chips expose an icon and reference number, without a filename
        // preview trigger. Their filename was verified in the picker before apply.
        const trigger=preview.querySelector('button[aria-label^="预览 "]');
        if(item.type==='audio' && !trigger)return true;
        return trigger?.getAttribute('aria-label')===`预览 ${item.name} 的素材详情`;
      });
    },'本次素材数量、顺序与引用编号');
  }
  const settingsSummary=()=>find(/\d+:\d+.*\/.*\d+\s*[pP].*\/.*\d+\s*(秒|s)/,composer());
  function durationControl() {
    const controls=[...document.querySelectorAll('input[type="range"],[role="slider"]')].filter(visible);
    const native=controls.filter(node=>node.matches('input[type="range"]'));
    const candidates=native.length?native:controls;
    if(candidates.length!==1)return null;
    const slider=candidates[0];
    for(let panel=slider.parentElement;panel && panel!==document.body;panel=panel.parentElement) {
      if(/生成参数/.test(text(panel)) && /视频时长/.test(text(panel)))return {slider,panel};
    }
    return null;
  }
  async function openSettings(task) {
    if(durationControl())return durationControl();
    await click(await waitFor(settingsSummary,'生成参数入口'),task);
    return waitFor(durationControl,'生成参数中的视频时长滑块');
  }
  function displayedDuration(control) {
    const values=new Set();
    const labels=[control.slider,...control.panel.querySelectorAll('span,div,output,input')];
    for(const node of labels) {
      if(!visible(node))continue;
      for(const label of [node.getAttribute('aria-valuetext'),text(node)]) {
        const match=C.normalize(label).match(/^(\d+(?:\.\d+)?)\s*(秒|s)$/i);
        if(match)values.add(Number(match[1]));
      }
    }
    // Read displayed seconds, not a slider index or an assumed 0–100 scale.
    return values.size===1?[...values][0]:null;
  }
  async function setDuration(task) {
    const target=task.input.duration_sec;
    if(!Number.isInteger(target) || target<4 || target>15)throw new Error('Runow 目标时长无效');
    await openSettings(task);
    let current=(await waitFor(()=>{
      const control=durationControl(),seconds=control && displayedDuration(control);
      return Number.isFinite(seconds)?{seconds}:null;
    },'滑块旁的当前秒数')).seconds;
    for(let steps=0;current!==target && steps<32;steps++) {
      const direction=target>current?1:-1;
      const control=await waitFor(durationControl,'时长滑块');
      control.slider.scrollIntoView({block:'nearest'});
      await actionPause();
      control.slider.focus();
      await actionPause();
      const response=await chrome.runtime.sendMessage({type:'AICF_RUNOW_DURATION_KEY',taskId:task.id,
        key:direction>0?'ArrowRight':'ArrowLeft'});
      if(!response?.ok)throw new Error(response?.error || 'Runow 时长滑块按键失败');
      await actionPause();
      const next=(await waitFor(()=>{
        const control=durationControl(),seconds=control && displayedDuration(control);
        return Number.isFinite(seconds) && seconds!==current?{seconds}:null;
      },'时长滑块响应（当前 '+current+' 秒，目标 '+target+' 秒）',3000)).seconds;
      if((next-current)*direction<=0 || (target-next)*direction<0)
        throw new Error('Runow 滑块步长无法到达 '+target+' 秒；不会近似修改时长');
      current=next;
    }
    if(current!==target)throw new Error('Runow 时长滑块未到达目标秒数');
    await waitFor(()=>new RegExp('/\\s*'+target+'\\s*(秒|s)(?:\\s|$)','i').test(text(settingsSummary())),
      '生成参数摘要回读 '+target+' 秒');
  }
  async function setMode(task) {
    await waitFor(()=>document.readyState==='complete' && editor(),'页面加载',30000);
    if(fieldText(editor()).trim())throw new Error('Runow 新任务页面存在未发送文案；不会覆盖');
    let root=await waitFor(composer,'H3 视频输入区域');
    const model=find(C.enhancedModelName,root);
    if(!model)throw new Error('Runow 未选中 Minimax H3-RH 增强版 / Enhanced');
    if(!find(/^多模态参考$/,root))throw new Error('Runow 未选中多模态参考');
    const ratio=task.input.aspect_ratio,resolution=task.input.resolution,duration=task.input.duration_sec;
    for(const [value,pattern] of [[ratio,new RegExp('^'+escape(ratio)+'(?:\\s|$)')],
      [resolution,new RegExp('^'+escape(resolution)+'$','i')]]) {
      const summary=await waitFor(settingsSummary,'生成参数入口');
      const already=value===ratio?text(summary).includes(ratio):text(summary).toLowerCase().includes(resolution);
      if(already)continue;
      const control=await openSettings(task);
      await clickText(pattern,task,control.panel);
    }
    const summary=await waitFor(settingsSummary,'生成参数入口');
    if(!new RegExp('/\\s*'+duration+'\\s*(秒|s)(?:\\s|$)','i').test(text(summary)))await setDuration(task);
    if(durationControl()) {
      // Close the real popover by its toggle. Focusing the editor need not dismiss it.
      await click(await waitFor(settingsSummary,'生成参数入口'),task);
      await waitFor(()=>!durationControl(),'生成参数面板关闭');
    }
    await waitFor(()=>{
      const summary=text(settingsSummary());
      return summary.includes(ratio) && summary.toLowerCase().includes(resolution) &&
        new RegExp('/\\s*'+duration+'\\s*(秒|s)').test(summary);
    },'比例、分辨率、时长与任务完全一致');
  }
  async function append(field,value,task) {
    if(!value)return;
    field.focus();
    await actionPause();
    const response=await chrome.runtime.sendMessage({type:'AICF_RUNOW_INSERT_TEXT',taskId:task.id,text:value});
    if(!response?.ok)throw new Error(response?.error || 'Runow 编辑器拒绝文本输入');
    await actionPause();
  }
  async function selectReference(task,part) {
    const option=await waitFor(()=>[...document.querySelectorAll('.qcv2-hero-mention-menu__list button')]
      .find(node=>visible(node) && text(node).replace(/\s/g,'')===part.label),'素材引用候选 '+part.label);
    await click(option,task);
  }
  async function fillPrompt(task) {
    const field=await waitFor(editor,'提示词编辑器');
    if(fieldText(field).trim())throw new Error('Runow 文案区不是空白，未覆盖');
    const parts=C.splitReferences(task.input.prompt_parts);
    if(field instanceof HTMLTextAreaElement) {
      if(parts.some(part=>part.reference_id))throw new Error('Runow 当前编辑器无法验证素材引用组件');
      await setInput(field,task.input.prompt);
    } else {
      field.focus();
      await actionPause();
      const range=document.createRange();range.selectNodeContents(field);range.collapse(false);
      const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
      for(const part of parts) {
        if(!part.reference_id) {await append(field,part.text,task);continue;}
        const mentionCount=()=>[...field.querySelectorAll('[contenteditable="false"],[data-type="mention"],[data-mention-id]')]
          .filter(node=>text(node).includes(part.label.slice(1))).length;
        const before=mentionCount();
        await append(field,'@',task);
        await selectReference(task,part);
        await waitFor(()=>mentionCount()>before,'有效素材引用 '+part.label);
        // The website owns the caret and mention node; never manufacture its HTML.
        field.focus();
        await actionPause();
      }
    }
    await sleep(250);
    const actual=C.normalize(fieldText(field).replace(/\u200b/g,'')).replace(/\s*(@(?:图片|音频|视频)\d+)\s*/g,'$1');
    const expected=C.normalize(task.input.prompt.replace(/\u200b/g,'')).replace(/\s*(@(?:图片|音频|视频)\d+)\s*/g,'$1');
    if(actual!==expected)throw new Error('Runow 回读提示词与本次文案不一致');
  }
  const outputs=()=>[...document.querySelectorAll('video')].filter(video=>visible(video) &&
    !composer()?.contains(video) && !library()?.root.contains(video)).map(video=>({
      url:video.currentSrc || video.src || video.querySelector('source')?.src || '',element:video,
      ready:video.readyState>=2 && video.videoWidth>0 && video.duration>0
    }));
  const pending=()=>[...document.querySelectorAll('span,p,div')].filter(node=>visible(node) &&
    (node.children.length===0 || node.matches('.qcv2-conversation-task-status[role="status"]')) &&
    /^(?:\d+\s*个\s*)?(?:排队中|生成中|正在生成|等待生成|任务已提交|正在获取生成进度)/.test(text(node))).length;
  function sendButton() {
    const button=composer()?.querySelector('button[data-testid="hero-send"]');
    return visible(button) && !button.disabled && button.getAttribute('aria-disabled')!=='true'?button:null;
  }
  async function submissionPatch(task,command) {
    const result=await chrome.runtime.sendMessage({type:'AICF_RUNOW_SUBMISSION_PATCH',taskId:task.id,command});
    if(!result?.ok)throw new Error(result?.error || 'Runow 多素材提交修复未生效');
    return result;
  }
  function quoteState() {
    const root=composer();
    if(!root)return {ready:false};
    const nodes=[...root.querySelectorAll('.qcv2-hero-fee-pill,.qcv2-integrated-fee')].filter(visible);
    const send=root.querySelector('button[data-testid="hero-send"]');
    const message=[...nodes.map(node=>[text(node),node.getAttribute('title')].filter(Boolean).join(' ')),
      send?.getAttribute('aria-label')].filter(Boolean).join(' ');
    if(/上传失败|upload failed/i.test(message))return {ready:false,uploadFailed:true,error:message};
    const ready=nodes.some(node=>!node.classList.contains('is-pending') &&
      !/计算中|未就绪|失败|calculating|unavailable|not ready/i.test(text(node)) &&
      /免费|free|⚡|积分|[¥$￥]|\d+\s*(?:RH|points|credits)/i.test(text(node)));
    return {ready,label:message};
  }
  async function waitForQuote(task,description,receiptCommand) {
    let readySince=0;
    return waitFor(async()=>{
      const state=quoteState();
      if(state.uploadFailed) {
        const names=(task.media || []).map(item=>item.name).join('、');
        throw new Error('Runow '+state.error+(names?'（本次参考素材：'+names+'）':''));
      }
      if(receiptCommand) {
        const receipt=await submissionPatch(task,receiptCommand);
        if(!state.ready || !receipt.ready)return null;
        return receipt;
      }
      if(!state.ready) {readySince=0;return null;}
      // The website debounces quotations for 500 ms after a model/parameter change.
      if(!readySince)readySince=Date.now();
      return Date.now()-readySince>=650?state:null;
    },description,120000);
  }
  async function submit(task) {
    let sent=false,stage='检查页面';
    try {
      preparationTask=task;lastHeartbeat=0;
      if(location.origin!==C.originForChannel(task.channel || 'runninghub_web') || !C.pagePath(location.pathname).startsWith('/runow/'))throw new Error('不是 Runow 页面');
      stage='打开项目内视频创作表单';await openProject(task);
      stage='等待 H3 增强版模型报价';await waitForQuote(task,'H3 增强版模型初始报价');
      stage='设置模型和生成参数';await setMode(task);
      stage='等待生成参数报价';await waitForQuote(task,'当前比例、分辨率和时长的报价');
      await submissionPatch(task,'start');
      stage='从资源库选择／上传素材';await selectMedia(task);
      stage='绑定全部素材参数';await submissionPatch(task,'arm');
      stage='等待已选素材报价';await waitForQuote(task,'填写提示词前的全部参考素材报价','media-quote');
      stage='填写文案与素材引用';await fillPrompt(task);
      stage='等待本次完整素材报价';await waitForQuote(task,'本次提示词及全部参考素材的报价','quote');
      const baseline=outputs().map(item=>item.url),before=pending();
      stage='发送视频生成任务';
      const send=await waitFor(sendButton,'可用的生成按钮',30000);
      preparationTask=null;
      sent=true;
      await click(send,task);
      await waitFor(()=>pending()>before || outputs().some(item=>!baseline.includes(item.url)) ||
        (editor() && !fieldText(editor()).trim()),'网页接受本次生成',15000);
      let receipt;
      const receiptDeadline=Date.now()+15000;
      while(Date.now()<receiptDeadline) {
        const result=await submissionPatch(task,'receipt');
        if(result.ready) {receipt=result;break;}
        await sleep(200);
      }
      if(!receipt?.taskId)throw new Error('未取得本次提交的远端任务 ID，请核对原任务');
      return {ok:true,baseline,pageUrl:location.href,receipt};
    } catch(error) {
      error.beforeSend=!sent;error.message='['+stage+'] '+error.message;throw error;
    } finally {
      await submissionPatch(task,'stop').catch(()=>{});
      preparationTask=null;
    }
  }
  async function status(task) {
    if(task.pageUrl && task.pageUrl!==location.href)throw new Error('Runow 任务页面已变化，停止读取其他页面结果');
    const output=C.newOutput(outputs().filter(item=>item.ready),task.baseline || []);
    if(output)return {ok:true,ready:true,url:/^https?:/i.test(output.url)?output.url:''};
    return {ok:true,ready:false};
  }
  async function download(task) {
    const output=C.newOutput(outputs().filter(item=>item.ready),task.baseline || []);
    if(!output)throw new Error('Runow 尚未找到本次新视频');
    for(let node=output.element.parentElement,depth=0;node && node!==document.body && depth<8;node=node.parentElement,depth++) {
      const button=find(/下载/,node);
      if(button) {await click(button,task,'download');return {ok:true};}
    }
    throw new Error('Runow 成片未显示下载按钮');
  }
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(!['AICF_VIDEO_SUBMIT','AICF_VIDEO_STATUS','AICF_VIDEO_DOWNLOAD'].includes(message?.type))return;
    const action=message.type==='AICF_VIDEO_SUBMIT'?submit(message.task):
      message.type==='AICF_VIDEO_STATUS'?status(message.task):download(message.task);
    Promise.resolve(action).then(respond).catch(error=>respond({ok:false,error:error.message,before_send:error.beforeSend===true}));
    return true;
  });
}
