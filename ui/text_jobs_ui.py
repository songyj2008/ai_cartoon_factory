"""Fast Gradio submission callbacks and an accessible persistent task panel."""
from urllib.parse import quote

import gradio as gr

from services.context import BASE_DIR


_TASK_CENTER_ICON_URL = "/gradio_api/file=" + quote((BASE_DIR / "web" / "task-center.webp").as_posix(), safe="/:")


def submit_operation(kind, output_count, status_index, *, editor_tail=False):
    def callback(*values):
        from services.brand import is_brand_verified, brand_block_message
        from services.context import pin_runtime
        from services.text_jobs import submit
        updates = [gr.update() for _ in range(output_count)]
        try:
            if not is_brand_verified():
                raise ValueError(brand_block_message())
            args = list(values)
            editors = {}
            if editor_tail:
                editors = dict(zip(('story_editor','beats_editor'),args[-2:]))
                args = args[:-2]
            if kind == 'novel':
                editors['novel_text_editor'] = args[0]
            with pin_runtime():
                task = submit(kind,args,editors)
            if task.get('already_complete'):
                updates[status_index] = 'Beats 与全部 Segments 已完成，已保留现有结果。'
            elif task.get('resumed'):
                updates[status_index] = f"继续原批次：已保存 {task.get('segment_completed',0)}/{task.get('segment_total',0)} 个 Segment，只生成未完成部分。"
            elif task.get('duplicate'):
                updates[status_index] = f"相同输入已有任务：{task['id'][:8]}。请在后台任务列表查看进度。"
            else:
                updates[status_index] = f"已加入后台任务：{task['title']} · {task['id'][:8]}。可继续操作，在右侧任务中心查看进度。"
        except Exception as exc:
            updates[status_index] = f'提交失败：{exc}'
        return tuple(updates)
    return callback


PANEL_HTML = f'''<div id="text-operations-toast" role="status" aria-live="polite" hidden></div>
<div id="text-operations-dock">
<button id="text-operations-launcher" type="button" aria-label="任务中心：正在读取任务" aria-controls="text-operations" aria-expanded="false" aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight">
  <img class="text-operations-launcher-icon" src="{_TASK_CENTER_ICON_URL}" width="32" height="32" alt="" draggable="false">
  <span class="text-operations-badge" aria-hidden="true" hidden></span>
  <span class="text-operations-pip" aria-hidden="true" hidden></span>
</button>
<div class="text-operations-preview" aria-hidden="true"><strong>任务中心</strong><span class="text-operations-preview-active">正在读取任务…</span><span class="text-operations-preview-attention" hidden></span><span class="text-operations-preview-hint">按住图标可自由拖动</span></div>
<section id="text-operations" aria-labelledby="text-operations-heading" hidden>
<header class="text-operations-summary">
  <div class="text-operations-summary-top">
    <div><h2 id="text-operations-heading" tabindex="-1">任务中心</h2><p>文字与网页视频任务</p></div>
    <button class="text-operations-close" type="button" aria-label="收起任务中心" title="收起任务中心"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg></button>
  </div>
  <div class="text-extension-update">
    <button type="button" class="text-extension-update-button">一键更新插件</button>
    <span class="text-extension-update-summary" role="status" aria-live="polite"></span>
    <details><summary>插件更新详情</summary><div class="text-extension-update-details"></div></details>
  </div>
  <div class="text-operations-overview"><span class="text-operations-count">正在读取任务…</span><span class="text-operations-attention" hidden></span></div>
  <div class="text-operations-current" hidden><span class="text-operations-current-dot" aria-hidden="true"></span><div class="text-operations-current-copy"><strong class="text-operations-current-title"></strong><span class="text-operations-current-phase"></span></div><span class="text-operations-current-percent" hidden></span></div>
  <progress class="text-operations-current-progress" max="100" value="0" aria-label="当前任务完成进度" hidden></progress>
</header>
<div class="text-operations-body">
  <div class="text-operations-maintenance">
    <div class="text-operations-clear-actions" role="group" aria-label="清理任务记录">
      <button class="text-operations-clear" type="button" disabled>清理已完成任务</button>
      <button class="text-operations-clear-all" type="button" aria-controls="text-operations-clear-confirmation" disabled>一键清空</button>
    </div>
    <div id="text-operations-clear-confirmation" class="text-operations-clear-confirmation" role="group" aria-label="确认清空所有任务" aria-describedby="text-operations-clear-warning" hidden>
      <p id="text-operations-clear-warning">将停止并清空所有项目的任务和扩展等待队列，待应用结果也会删除。已经发到网页的请求可能继续，但结果不会回填。此操作不可撤销。</p>
      <div class="text-operations-clear-actions">
        <button class="text-operations-clear-cancel" type="button">取消</button>
        <button class="text-operations-clear-confirm" type="button">确认清空</button>
      </div>
    </div>
  </div>
  <nav class="text-operation-filters" aria-label="按任务用途筛选" role="group">
    <button type="button" class="is-selected" data-task-filter="all" aria-pressed="true">全部 <span>0</span></button>
    <button type="button" data-task-filter="creation" aria-pressed="false">创作 <span>0</span></button>
    <button type="button" data-task-filter="prompt" aria-pressed="false">提示词 <span>0</span></button>
    <button type="button" data-task-filter="structure" aria-pressed="false">结构 <span>0</span></button>
    <button type="button" data-task-filter="analysis" aria-pressed="false">分析 <span>0</span></button>
    <button type="button" data-task-filter="asset" aria-pressed="false">素材 <span>0</span></button>
    <button type="button" data-task-filter="video" aria-pressed="false">视频 <span>0</span></button>
  </nav>
  <div class="text-operations-message" role="status" aria-live="polite"></div>
  <div class="text-operations-list"></div>
  <nav class="text-operations-pagination text-operation-actions" aria-label="后台任务历史分页">
    <button type="button" class="text-history-previous" aria-label="上一页历史" disabled>上一页</button>
    <span class="text-history-page" role="status" aria-live="polite"></span>
    <button type="button" class="text-history-next" aria-label="下一页历史" disabled>下一页</button>
  </nav>
  <details class="text-operations-result" hidden><summary>任务进度详情</summary>
  <div class="text-operations-result-content" role="status" aria-live="polite"></div></details>
</div>
</section>
</div>'''

PANEL_JS = r'''
(() => {
  if(window.__AICF_TEXT_OPERATIONS_INIT__) return;
  window.__AICF_TEXT_OPERATIONS_INIT__=true;
  const positionKey='aicf_task_dock_position_v1';
  const dockMargin=12;
  const panelGap=10;
  const dragThreshold=6;
  const taskDock=()=>document.getElementById('text-operations-dock');
  const root=()=>document.getElementById('text-operations');
  let dockPosition=readTaskDockPosition();
  let extensionUpdateRefreshing=false;
  async function refreshExtensionUpdate(start=false) {
    const panel=root();
    if(!panel || extensionUpdateRefreshing)return;
    extensionUpdateRefreshing=true;
    const button=panel.querySelector('.text-extension-update-button');
    const summary=panel.querySelector('.text-extension-update-summary');
    button.disabled=true;
    try {
      if(start)summary.textContent='正在通知浏览器更新…';
      const response=await fetch('/api/web-llm/extension-update',{method:start?'POST':'GET',signal:AbortSignal.timeout(15000)});
      const value=await response.json();
      if(!response.ok || !value.ok)throw new Error(value.message || '插件更新请求失败');
      const browsers=value.browsers || [];
      const completed=browsers.filter(item=>item.update?.phase==='complete').length;
      const failed=browsers.filter(item=>item.update?.phase==='failed').length;
      const pending=browsers.filter(item=>item.update && !['complete','failed'].includes(item.update.phase)).length;
      summary.textContent=browsers.length?'目标版本 '+value.source.version+' · 已更新 '+completed+' · 等待 '+pending+(failed?' · 需处理 '+failed:''):'请先在浏览器插件中配对并启用连接';
      const details=panel.querySelector('.text-extension-update-details');details.replaceChildren();
      const path=document.createElement('p');path.textContent='插件目录 '+value.source.path;details.append(path);
      const copy=document.createElement('button');copy.type='button';copy.textContent='复制插件目录';
      copy.onclick=async()=>{try{await navigator.clipboard.writeText(value.source.path);summary.textContent='插件目录已复制';}catch{summary.textContent='复制失败，请从详情中选择目录文本复制';}};
      details.append(copy);
      const phases={requested:'等待更新',waiting:'当前任务完成回传后更新',reloading:'正在重新加载',complete:'已更新',failed:'需要处理'};
      for(const browser of browsers) {
        const row=document.createElement('p');
        const phase=browser.update?phases[browser.update.phase]:'尚未请求更新';
        row.textContent=browser.name+' · '+(browser.online?'在线':'离线')+' · '+phase+
          (browser.update?.message && browser.update.message!==phase?' · '+browser.update.message:'');
        details.append(row);
      }
    }catch(error){summary.textContent=error.message;}
    finally{button.disabled=false;extensionUpdateRefreshing=false;}
  }
  document.addEventListener('click',event=>{
    if(event.target.closest('.text-extension-update-button'))refreshExtensionUpdate(true);
  });
  setInterval(()=>{if(!document.hidden && root() && !root().hidden)refreshExtensionUpdate();},5000);

  function setTaskPanelOpen(open, restoreFocus=false){
    const dock=taskDock(),panel=root();
    if(!dock||!panel)return;
    const trigger=dock.querySelector('#text-operations-launcher');
    panel.hidden=!open;
    trigger.setAttribute('aria-expanded',String(open));
    if(open){panel.querySelector('#text-operations-heading').focus({preventScroll:true});return;}
    setClearAllConfirmation(panel,false);
    dock.classList.add('is-preview-dismissed');
    if(restoreFocus)trigger.focus({preventScroll:true});
  }
  function readTaskDockPosition(){
    try{
      const saved=JSON.parse(localStorage.getItem(positionKey));
      const position=typeof saved==='number'?{x:1,y:saved}:saved;
      if(position&&[position.x,position.y].every(value=>Number.isFinite(value)&&value>=0&&value<=1))return position;
    }catch{}
    return null;
  }
  function taskDockBounds(dock){
    const {clientWidth:width,clientHeight:height}=document.documentElement;
    const dockWidth=dock.offsetWidth,dockHeight=dock.offsetHeight;
    return {width,height,dockWidth,dockHeight,maxLeft:Math.max(0,width-dockWidth),
      maxTop:Math.max(dockMargin,height-dockHeight-dockMargin)};
  }
  function positionTaskDock(dock, requested, bounds){
    const {left:currentLeft,top:currentTop}=requested??dock.getBoundingClientRect();
    const left=Math.round(Math.max(0,Math.min(bounds.maxLeft,currentLeft)));
    const top=Math.round(Math.max(dockMargin,Math.min(bounds.maxTop,currentTop)));
    const below=bounds.height-bounds.dockHeight-top;
    const right=bounds.maxLeft-left;
    if(requested){
      dock.style.setProperty('--ops-drag-left',left+'px');
      dock.style.setProperty('--ops-drag-bottom',Math.max(dockMargin,below)+'px');
    }
    dock.dataset.dockEdge=left===0?'left':right===0?'right':'free';
    dock.dataset.panelDirection=top<below?'down':'up';
    dock.dataset.previewEdge=top<64?'top':below<64?'bottom':'';
    dock.dataset.previewSide=right>left?'right':'left';
    dock.style.setProperty('--ops-preview-room',Math.max(0,Math.max(left,right)-panelGap-dockMargin)+'px');
    const panelWidth=Math.max(0,Math.min(420,bounds.width-2*dockMargin));
    const alongSide=Math.max(left,right)>=panelWidth+panelGap+dockMargin;
    const panelLeft=alongSide
      ?(right>left?left+bounds.dockWidth+panelGap:left-panelGap-panelWidth)
      :Math.max(dockMargin,Math.min(bounds.width-panelWidth-dockMargin,left+(bounds.dockWidth-panelWidth)/2));
    const down=top<below;
    const anchor=(down?top:below)+(alongSide?0:bounds.dockHeight+panelGap);
    const panelHeight=Math.max(0,Math.min(690,bounds.height-anchor-dockMargin));
    dock.style.setProperty('--ops-panel-width',panelWidth+'px');
    dock.style.setProperty('--ops-panel-left',panelLeft+'px');
    dock.style.setProperty('--ops-panel-anchor',anchor+'px');
    dock.style.setProperty('--ops-panel-max',panelHeight+'px');
    dock.classList.toggle('is-panel-compact',panelHeight<420);
    return {left,top};
  }
  function restoreTaskDock(dock){
    const bounds=taskDockBounds(dock);
    const position=dockPosition&&{
      left:dockPosition.x*bounds.maxLeft,top:dockMargin+dockPosition.y*(bounds.maxTop-dockMargin)};
    positionTaskDock(dock,position,bounds);
  }
  function rememberTaskDock({left,top}, bounds){
    dockPosition={
      x:bounds.maxLeft?left/bounds.maxLeft:(dockPosition?.x??1),
      y:bounds.maxTop>dockMargin?(top-dockMargin)/(bounds.maxTop-dockMargin):(dockPosition?.y??1)};
    try{localStorage.setItem(positionKey,JSON.stringify(dockPosition));}catch{}
  }
  function bindTaskDockDrag(dock,trigger){
    let drag=null, suppressClick=false;
    trigger.addEventListener('pointerdown',event=>{
      if(drag||event.button!==0||!event.isPrimary)return;
      suppressClick=false;
      const {left,top}=dock.getBoundingClientRect();
      drag={id:event.pointerId,x:event.clientX,y:event.clientY,left,top,bounds:taskDockBounds(dock),lastPosition:null};
      trigger.setPointerCapture(event.pointerId);
    });
    trigger.addEventListener('pointermove',event=>{
      if(!drag||event.pointerId!==drag.id)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(drag.lastPosition===null&&Math.hypot(dx,dy)<dragThreshold)return;
      dock.classList.add('is-dragging','is-preview-dismissed');
      drag.lastPosition=positionTaskDock(dock,{left:drag.left+dx,top:drag.top+dy},drag.bounds);
    });
    function finishDrag(event){
      if(!drag||event.pointerId!==drag.id)return;
      const moved=drag.lastPosition!==null;
      const released=event.type==='pointerup';
      const touchTap=released&&!moved&&event.pointerType==='touch';
      suppressClick=touchTap||(released&&moved);
      if(moved){
        dock.classList.remove('is-dragging');
        if(released){
          rememberTaskDock(drag.lastPosition,drag.bounds);
          restoreTaskDock(dock);
        }else{
          if(dockPosition===null){
            dock.style.removeProperty('--ops-drag-left');
            dock.style.removeProperty('--ops-drag-bottom');
          }
          restoreTaskDock(dock);
        }
      }
      drag=null;
      if(touchTap)setTaskPanelOpen(root().hidden,true);
    }
    for(const type of ['pointerup','pointercancel','lostpointercapture'])trigger.addEventListener(type,finishDrag);
    trigger.addEventListener('click',event=>{
      if(suppressClick&&event.detail!==0){event.preventDefault();return;}
      setTaskPanelOpen(root().hidden,true);
    });
    trigger.addEventListener('keydown',event=>{
      const move={ArrowUp:[0,-24],ArrowDown:[0,24],ArrowLeft:[-24,0],ArrowRight:[24,0]}[event.key];
      if(!event.altKey||event.ctrlKey||event.shiftKey||event.metaKey||!Array.isArray(move))return;
      event.preventDefault();
      const bounds=taskDockBounds(dock);
      const {left,top}=dock.getBoundingClientRect();
      const position=positionTaskDock(dock,{left:left+move[0],top:top+move[1]},bounds);
      rememberTaskDock(position,bounds);
    });
  }
  function initializeTaskDock(){
    const dock=taskDock();
    if(!dock||dock.dataset.initialized)return;
    dock.dataset.initialized='true';
    const trigger=dock.querySelector('#text-operations-launcher');
    bindTaskDockDrag(dock,trigger);
    trigger.addEventListener('pointerenter',()=>dock.classList.remove('is-preview-dismissed'));
    dock.querySelector('.text-operations-close').addEventListener('click',()=>setTaskPanelOpen(false,true));
    dock.querySelector('.text-operation-filters').addEventListener('click',event=>{
      const button=event.target.closest('[data-task-filter]');if(!button)return;
      taskFilter=button.dataset.taskFilter||'all';signature='';refresh();
    });
    dock.addEventListener('focusout',event=>{
      if(!dock.contains(event.relatedTarget))dock.classList.remove('is-preview-dismissed');
    });
    restoreTaskDock(dock);
  }
  new MutationObserver(initializeTaskDock).observe(document.documentElement,{childList:true,subtree:true});
  initializeTaskDock();
  window.addEventListener('resize',()=>{
    const dock=taskDock();
    if(dock&&!dock.classList.contains('is-dragging'))restoreTaskDock(dock);
  });
  document.addEventListener('pointerdown',event=>{
    const dock=taskDock(),panel=root();
    if(!dock||!panel||panel.hidden)return;
    if(!dock.contains(event.target))setTaskPanelOpen(false);
  });
  document.addEventListener('keydown',event=>{
    const dock=taskDock(),panel=root();
    if(event.key!=='Escape'||!dock||!panel)return;
    const inDock=dock.contains(document.activeElement);
    const confirmation=panel.querySelector('.text-operations-clear-confirmation');
    if(!panel.hidden&&!confirmation.hidden&&inDock){
      setClearAllConfirmation(panel,false,true);
    }else if(!panel.hidden){
      setTaskPanelOpen(false,inDock);
    }else if(inDock||dock.matches(':hover')){
      dock.classList.add('is-preview-dismissed');
    }else return;
    if(inDock){event.preventDefault();event.stopPropagation();}
  },true);
  let busy=false, signature='', entrySignature='', taskFilter='all', editorBaselines=new Map(), toastTimer=null;
  let taskCleanupPending=false;
  let historyOffset=0;
  let seenStates={};try{seenStates=JSON.parse(localStorage.getItem('aicf_text_job_states_v1')||'{}')||{};}catch{}
  const labels={preparing:'准备中',queued:'排队中',running:'执行中',ready:'待应用',applied:'已应用',
    cancelled:'已取消',error:'失败',needs_attention:'需要处理',applying:'保存中',waiting_children:'分步执行中'};
  const webLabels={queued:'网页任务排队中',claimed:'扩展已领取',waiting:'等待网页回复',complete:'网页回复完成',error:'网页任务失败',timeout:'网页任务超时',cancelled:'网页任务已取消',unknown:'网页任务状态未知'};
  const providerLabels={deepseek:'DeepSeek',chatgpt:'ChatGPT',doubao:'豆包',qianwen:'千问',api:'DeepSeek API'};
  const videoPhases={queued:'等待扩展领取',claimed:'准备网页提交',submitted:'豆包生成中',complete:'视频已保存',uncertain:'请检查原网页',error:'生成失败'};
  function videoView(row){
    const state={queued:'queued',claimed:'running',submitted:'running',complete:'applied',uncertain:'needs_attention',error:'error'}[row.state]||'needs_attention';
    const basePhase=row.state==='submitted' && row.channel!=='doubao_web'?'网页视频生成中':videoPhases[row.state]||row.state;
    const phase=basePhase+(row.browser_name?' · '+row.browser_name+(row.state==='queued' && !row.browser_online?'（离线，等待连接）':''):'');
    return {id:row.id,kind:'web_video',isVideo:true,title:`${({doubao_web:'豆包视频',runninghub_web:'RunningHub 网页视频',runninghub_international_web:'RunningHub 国际网页视频'})[row.channel] || row.channel} · 第 ${row.segment_index} 段`,
      project:row.project,episode:row.episode,segment:{segment_index:row.segment_index,segment_uid:row.segment_uid},
      state,phase,progress:{percent:row.state==='complete'?100:0,node:phase},
      created:row.created_at,updated:row.updated_at,elapsed_seconds:Math.max(0,Math.floor(Date.now()/1000-row.created_at)),
      queued_seconds:Math.max(0,Math.floor(Date.now()/1000-row.created_at)),message:row.error||'',output_path:row.output_path||''};
  }
  const taskKinds={
    opening_continuity:{category:'prompt',label:'承接',purpose:'提取上一段结束状态并生成开场候选',destination:'只生成候选；到目标段全屏编辑器读取并应用，再保存'},
    story:{category:'creation',label:'创作',purpose:'生成或更新本集剧情稿',destination:'校验后自动写回；检测到冲突时保留候选'},
    beats:{category:'creation',label:'创作',purpose:'把剧情拆分为 Beats',destination:'校验后更新 Beats 与分段结构；冲突时保留候选'},
    novel:{category:'creation',label:'创作',purpose:'从小说生成 Beats 与视频分段',destination:'校验后写回本集；冲突时保留候选'},
    novel_beats:{category:'creation',label:'Beats',purpose:'从小说生成并保存 Beats',destination:'完成后立即显示 Beats，再开始逐段生成'},
    novel_segment:{category:'prompt',label:'分段',purpose:'生成一个 Beat 对应的 Segment',destination:'只更新目标分段，成功后立即显示'},
    pipeline:{category:'creation',label:'创作',purpose:'一键准备剧情、Beats 和分段提示词',destination:'校验后写回本集；冲突时保留候选'},
    prompts:{category:'prompt',label:'提示词',purpose:'按当前 Beats 生成全部分段提示词',destination:'校验后更新本集；冲突时保留候选'},
    segment:{category:'prompt',label:'提示词',purpose:'只重新生成指定分段的提示词',destination:'目标分段提示词候选 · 不改其他分段'},
    source_replan:{category:'prompt',label:'提示词',purpose:'按当前提示词的台词、动作和情绪重新规划时长',destination:'目标分段提示词与 Beat 时长候选 · 保留其他分段'},
    revision_wait:{category:'prompt',label:'提示词',purpose:'等待网页助手完成提示词修正',destination:'修正后的目标分段提示词候选'},
    split:{category:'structure',label:'结构',purpose:'拆分一个复杂 Beat 并调整关联分段',destination:'结构调整候选 · 应用后更新 Beats 与分段'},
    feedback:{category:'analysis',label:'分析',purpose:'分析所选视频的问题并生成改进建议',destination:'问题分析报告与候选规则 · 可单独查看'},
    video_learning:{category:'analysis',label:'经验',purpose:'分析人工认可的台词节奏、时长安排及镜头衔接',destination:'持久经验库 · 台词节奏与镜头规划参考'},
    metadata:{category:'asset',label:'素材',purpose:'为当前素材补全描述、标签等字段',destination:'素材字段候选 · 填入后可检查再保存'},
    web_video:{category:'video',label:'视频',purpose:'网页视频生成',destination:'完成后保存到所属分段'}
  };
  const applyLabels={story:'应用剧情候选',beats:'应用 Beats 候选',novel:'应用 Beats 与分段',pipeline:'应用整套候选',prompts:'应用提示词候选',segment:'更新目标分段',source_replan:'更新目标分段',revision_wait:'更新目标分段',split:'应用 Beat 拆分',feedback:'保存分析报告'};
  function taskMeta(task){return taskKinds[task.kind]||{category:'other',label:'其他',purpose:'后台处理任务',destination:'查看任务结果了解后续用途'};}
  const field=id=>document.querySelector('#'+id+' textarea, #'+id+' input');
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function message(text){const r=root();if(r)r.querySelector('.text-operations-message').textContent=text;}
  function toast(text){
    const node=document.getElementById('text-operations-toast');if(!node)return;
    node.textContent=text;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},5000);
  }
  function resetEditorBaselines(){
    editorBaselines.clear();
    for(const id of ['story_editor','beats_editor','novel_text_editor']){const el=field(id);if(el)editorBaselines.set(id,el.value);}
  }
  function hasUnsavedEditors(){
    for(const [id,value] of editorBaselines){const el=field(id);if(el&&el.value!==value)return true;}
    return hasUnsavedSegmentEditors();
  }
  function hasUnsavedSegmentEditors(){
    for(const el of document.querySelectorAll('.segment-director-editor')){
      const value=el._aicfCmState?.cm?.getValue?.() ?? el.value;
      if(value!==el.defaultValue)return true;
    }
    return false;
  }
  setTimeout(resetEditorBaselines,2500);
  for(const id of ['project_name','project_episode']){
    const input=field(id);input?.addEventListener('change',()=>setTimeout(resetEditorBaselines,1200));
  }
  async function waitForRuntime(project,episode){
    for(let i=0;i<40;i++){
      try{const state=await fetch('/api/project/current_state',{cache:'no-store'}).then(r=>r.json());
        if(state.project_name===project&&(episode===undefined||String(state.current_episode||'')===String(episode||'')))return true;
      }catch{}
      await delay(200);
    }
    return false;
  }
  async function chooseProject(project){
    const wrap=document.getElementById('project_name'),input=field('project_name');if(!wrap||!input)return false;
    if(input.value===project)return true;
    input.click();input.focus();await delay(120);
    const options=[...document.querySelectorAll('[role="option"],li')];
    const option=options.find(node=>node.textContent.trim()===project);
    if(!option)throw new Error('项目选择器中找不到“'+project+'”');
    option.click();return waitForRuntime(project,undefined);
  }
  async function chooseEpisode(episode){
    const input=field('project_episode');if(!input)return false;
    if(String(input.value||'')===String(episode||''))return true;
    if(window.AICF?.selectEpisode){window.AICF.selectEpisode(episode||'');return waitForRuntime(field('project_name')?.value||'',episode||'');}
    throw new Error('集数选择器尚未就绪');
  }
  function switchMainPage(value){
    const radio=document.querySelector('#main_page_tabs input[type="radio"][value="'+value+'"]');
    if(radio&&!radio.checked)radio.click();
    if(window.AICF?.syncPageTabs)window.AICF.syncPageTabs();
  }
  async function locateTask(task){
    if(task.kind==='metadata'){
      document.getElementById('project_assets_open_btn')?.click();await delay(250);window.AICF?.openAssetManagement(true);document.querySelectorAll('#asset_management_tabs button').forEach(button=>{if(button.textContent.trim()==='素材库 / 上传')button.click();});document.getElementById('asset_metadata_parse_btn')?.scrollIntoView({behavior:'smooth',block:'center'});return;
    }
    const projectInput=field('project_name'),episodeInput=field('project_episode');
    const different=projectInput?.value!==task.project||String(episodeInput?.value||'')!==String(task.episode||'');
    if(different&&hasUnsavedEditors()&&!window.confirm('当前剧情、Beats、小说或分段提示词有未保存修改。切换项目后这些修改不会带过去，仍要定位任务入口吗？'))return;
    if(different){const changed=await chooseProject(task.project);if(!changed)throw new Error('项目切换未完成，请手动切换后重试');
      if(!(await chooseEpisode(task.episode||'')))throw new Error('集数切换未完成，请手动切换后重试');
      await delay(500);resetEditorBaselines();}
    switchMainPage('workbench');await delay(150);
    const anchorByKind={story:'story_editor',pipeline:'story_editor',beats:'beats_editor',novel:'novel_text_editor',novel_beats:'beats_editor',split:'beats_editor',feedback:'feedback_analyze_btn'};
    if(['segment','source_replan','revision_wait','web_video','novel_segment'].includes(task.kind)&&task.segment&&task.segment.segment_index){
      const current=await fetch('/api/project/current_state',{cache:'no-store'}).then(r=>r.json());
      let index=Number(task.segment.segment_index);
      try{const jobs=typeof current.video_jobs_text==='string'?JSON.parse(current.video_jobs_text):current.video_jobs_text;
        const rows=jobs?.segments||[];const match=rows.find(row=>(task.segment.segment_uid&&row.segment_uid===task.segment.segment_uid)||(task.segment.beat_uid&&row.beat_uid===task.segment.beat_uid));
        if(match)index=Number(match.segment_index||index);
        else if(task.segment.segment_uid||task.segment.beat_uid)throw new Error('原分段编号已不存在，无法定位');
      }catch(error){if(error.message==='原分段编号已不存在，无法定位')throw error;}
      for(let i=0;i<24&&!document.querySelector('.group-card[data-segment-index="'+index+'"]');i++){
        const pager=document.querySelectorAll('#segment_pager button');const next=pager[pager.length-1];
        if(!next||next.disabled)break;next.click();await delay(380);
      }
      const card=document.querySelector('.group-card[data-segment-index="'+index+'"]');
      if(!card)throw new Error('任务分段当前未显示；请在分段分页中查找第 '+index+' 段');
      card.scrollIntoView({behavior:'smooth',block:'center'});card.classList.add('text-operation-target');
      setTimeout(()=>card.classList.remove('text-operation-target'),4500);
      const editor=card.querySelector('.segment-director-editor');
      const editorEntry=editor||card.querySelector('.h3-prompt-open');editorEntry?.focus();return;
    }
    const anchor=document.getElementById(anchorByKind[task.kind]||'story_editor');
    const details=anchor?.closest('details');if(details)details.open=true;
    anchor?.scrollIntoView({behavior:'smooth',block:'center'});anchor?.focus?.();
  }
  const metadataAutoAttempts=new Set();
  function requestMetadataApply(task){
    const input=field('text_job_metadata_id');
    const button=document.querySelector('button#text_job_metadata_apply, #text_job_metadata_apply button');
    if(!input||!button)throw new Error('素材回填入口尚未就绪，请刷新页面');
    const setter=Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;
    setter.call(input,task.id);input.dispatchEvent(new Event('input',{bubbles:true}));
    input.dispatchEvent(new Event('change',{bubbles:true}));button.click();
  }
  function autoApplyMetadata(tasks){
    // Only the latest request may update the current asset; the Gradio callback
    // verifies all original fields and the asset path before accepting it.
    const latest=tasks.filter(t=>t.kind==='metadata').sort((a,b)=>b.created-a.created)[0];
    if(!latest||latest.state!=='ready'||latest.apply_error||metadataAutoAttempts.has(latest.id))return;
    if(!field('text_job_metadata_id')||!document.querySelector('button#text_job_metadata_apply, #text_job_metadata_apply button'))return;
    metadataAutoAttempts.add(latest.id);requestMetadataApply(latest);
  }
  async function maybeRefreshTask(task){
    if(!task||task.state!=='applied'||['metadata','feedback','video_learning'].includes(task.kind))return false;
    const activeProject=field('project_name')?.value||'',activeEpisode=field('project_episode')?.value||'';
    const workbench=document.getElementById('workbench_page');
    if(activeProject!==task.project||String(activeEpisode||'')!==String(task.episode||'')||
       !workbench||workbench.getBoundingClientRect().width===0||document.querySelector('#aicf-prompt-modal.open'))return false;
    if(task.kind==='novel_segment'||task.batch_version===1){
      if(hasUnsavedSegmentEditors())return false;
      const button=document.querySelector('button#operation_segment_refresh_btn, #operation_segment_refresh_btn button');
      if(!button)return false;button.click();return true;
    }
    if(task.kind==='novel_beats'){
      if(hasUnsavedSegmentEditors())return false;
      const data=(await api('/'+task.id)).result;
      const beats=field('beats_editor');
      const expected=data.editors?.beats_editor??editorBaselines.get('beats_editor');
      if(beats&&beats.value!==expected)return false;
      const button=document.querySelector('button#operation_beats_refresh_btn, #operation_beats_refresh_btn button');
      if(!button)return false;button.click();
      setTimeout(()=>{const current=field('beats_editor');if(current&&current.value===data.published_beats_text)editorBaselines.set('beats_editor',current.value);},900);
      return true;
    }
    if(hasUnsavedEditors())return false;
    const data=(await api('/'+task.id)).result;
    for(const [key,value] of Object.entries(data.editors||{})){const el=field(key);if(el&&el.value!==value)return false;}
    if(hasUnsavedEditors())return false;
    const button=document.querySelector('button#operation_refresh_btn, #operation_refresh_btn button');if(!button)return false;
    button.click();setTimeout(resetEditorBaselines,900);return true;
  }
  async function api(path,data,method){
    const response=await fetch('/api/web-llm/operations'+path,{method:method||(data===undefined?'GET':'POST'),
      headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});
    const body=await response.json(); if(!response.ok||!body.ok)throw new Error(body.message||'任务请求失败');return body;
  }
  function updateEntryStatuses(tasks){
    const activeProject=field('project_name')?.value||'',activeEpisode=field('project_episode')?.value||'';
    const entryIds={story:'gen_story_btn',beats:'split_beats_btn',novel:'generate_novel_segments_btn',
      pipeline:'one_click_full_btn',prompts:'generate_segment_prompts_btn',split:'split_complex_beat_btn',
      feedback:'feedback_analyze_btn',metadata:'asset_metadata_parse_btn'};
    const statusText={preparing:'准备中',queued:'排队中',running:'执行中',applying:'保存中',ready:'待应用',
      applied:'已完成',cancelled:'已取消',error:'失败',needs_attention:'需要处理',waiting_children:'分步执行中'};
    const shown=tasks.filter(task=>{
      if(task.project!==activeProject||String(task.episode||'')!==String(activeEpisode||''))return false;
      if(['preparing','queued','running','applying','ready','needs_attention','waiting_children'].includes(task.state))return true;
      return ['applied','cancelled','error'].includes(task.state)&&Date.now()/1000-Number(task.updated||task.created||0)<3600;
    });
    const groups=new Map();
    for(const task of shown){
      const segmentTask=['segment','source_replan','revision_wait','novel_segment'].includes(task.kind)&&task.segment?.segment_index;
      const target=segmentTask?(task.segment.segment_uid?'segmentUid:'+task.segment.segment_uid:
        task.segment.beat_uid?'beatUid:'+task.segment.beat_uid:'segment:'+Number(task.segment.segment_index)):entryIds[task.kind];if(!target)continue;
      if(!groups.has(target))groups.set(target,[]);groups.get(target).push(task);
    }
    const next=[...groups.entries()].map(([target,items])=>[target,...items.map(task=>[task.id,task.state,task.phase,task.progress?.node])]);
    const nextSignature=JSON.stringify([activeProject,activeEpisode,next]);
    if(nextSignature===entrySignature&&document.querySelectorAll('.text-operation-entry-state').length===groups.size)return;
    entrySignature=nextSignature;
    document.querySelectorAll('.text-operation-entry-state').forEach(node=>node.remove());
    for(const [target,items] of groups){
      const task=items.find(item=>item.state==='running'||item.state==='applying')||
        items.find(item=>item.state==='preparing')||
        items.find(item=>item.state==='queued')||items.find(item=>item.state==='needs_attention')||items[0];
      const label=statusText[task.state]||task.state;
      const phase=task.state==='running'?(task.progress?.node||task.phase||''):
        task.state==='queued'?(task.queued_seconds+' 秒'):
        task.state==='needs_attention'?(task.phase||''):
        task.state==='ready'?'可查看候选':'';
      const badge=document.createElement('span');badge.className='text-operation-entry-state state-'+task.state;
      const liveCount=items.filter(item=>['queued','running','applying'].includes(item.state)).length;
      badge.textContent=[label,liveCount>1?liveCount+' 项':null,phase].filter(Boolean).join(' · ');
      badge.title=items.map(item=>item.title+'：'+(statusText[item.state]||item.state)).join('\n');
      if(target.startsWith('segment:')||target.startsWith('segmentUid:')||target.startsWith('beatUid:')){
        const card=[...document.querySelectorAll('.group-card')].find(node=>
          target.startsWith('segmentUid:')?node.dataset.segmentUid===target.slice(11):
          target.startsWith('beatUid:')?node.dataset.beatUid===target.slice(8):
          Number(node.dataset.segmentIndex)===Number(target.slice(8)));
        const slot=card?.querySelector('.segment-title-block')||card;
        slot?.append(badge);
        continue;
      }
      const host=document.getElementById(target);if(host)host.append(badge);
    }
  }
  const runningStates=new Set(['running','applying','preparing','waiting_children']);
  function taskPriority(task){
    if(task.state==='running'||task.state==='applying')return 0;
    if(runningStates.has(task.state))return 1;
    if(task.state==='queued')return 2;
    if(['ready','needs_attention','error'].includes(task.state))return 3;
    return 4;
  }
  function hasTaskProgress(task){
    return Boolean(task&&!task.isVideo&&task.state!=='queued'&&Number.isFinite(task.progress?.percent));
  }
  function setTaskText(node,text){
    if(node.textContent!==text)node.textContent=text;
  }
  function renderDockStatus(activity, attention='', activeCount=0, hasError=false){
    const dock=taskDock(),panel=root();
    if(!dock||!panel)return;
    const trigger=dock.querySelector('#text-operations-launcher');
    const name=['任务中心',activity,attention].filter(Boolean).join('，');
    if(trigger.getAttribute('aria-label')!==name)trigger.setAttribute('aria-label',name);
    const badge=dock.querySelector('.text-operations-badge');
    badge.hidden=!activeCount;
    setTaskText(badge,activeCount>99?'99+':String(activeCount));
    const pip=dock.querySelector('.text-operations-pip');
    pip.hidden=!attention;
    pip.classList.toggle('has-error',hasError);
    setTaskText(dock.querySelector('.text-operations-preview-active'),activity);
    setTaskText(panel.querySelector('.text-operations-count'),activity);
    for(const node of [dock.querySelector('.text-operations-preview-attention'),panel.querySelector('.text-operations-attention')]){
      node.hidden=!attention;
      node.classList.toggle('has-error',hasError);
      setTaskText(node,attention);
    }
  }
  function updateSummary(tasks){
    const panel=root();if(!panel)return;
    panel.dataset.summaryReady='true';
    const counts={running:0,queued:0,ready:0,attention:0,error:0};
    for(const task of tasks){
      if(task.batch_version===1)continue;
      if(runningStates.has(task.state))counts.running++;
      else if(task.state==='needs_attention')counts.attention++;
      else if(Object.hasOwn(counts,task.state))counts[task.state]++;
    }
    const active=tasks.find(task=>task.state==='running'||task.state==='applying')||
      tasks.find(task=>runningStates.has(task.state))||tasks.find(task=>task.state==='queued');
    const activeCount=counts.running+counts.queued;
    const activity=activeCount?`${counts.running} 执行 · ${counts.queued} 排队`:active?'批次处理中':'当前无运行任务';
    const attention=[counts.ready&&`${counts.ready} 待应用`,counts.attention&&`${counts.attention} 需检查`,counts.error&&`${counts.error} 异常`].filter(Boolean).join(' · ');
    renderDockStatus(activity,attention,activeCount,Boolean(counts.error||counts.attention));
    panel.querySelector('.text-operations-current').hidden=!active;
    setTaskText(panel.querySelector('.text-operations-current-title'),active?.title||'');
    setTaskText(panel.querySelector('.text-operations-current-phase'),active?
      [active.progress?.node||active.phase||labels[active.state],active.project,active.episode].filter(Boolean).join(' · '):'');
    const percent=panel.querySelector('.text-operations-current-percent'),bar=panel.querySelector('.text-operations-current-progress');
    const showProgress=hasTaskProgress(active);
    percent.hidden=bar.hidden=!showProgress;
    if(showProgress){
      const value=Math.max(0,Math.min(100,active.progress.percent));
      setTaskText(percent,`${value}%`);
      bar.value=value;
    }
  }
  function updateFilters(tasks){
    const r=root();if(!r)return;
    const counts={all:tasks.length,creation:0,prompt:0,structure:0,analysis:0,asset:0,video:0};
    for(const task of tasks){const category=taskMeta(task).category;if(Object.prototype.hasOwnProperty.call(counts,category))counts[category]++;}
    for(const button of r.querySelectorAll('[data-task-filter]')){
      const key=button.dataset.taskFilter;
      button.querySelector('span').textContent=String(counts[key]||0);
      const selected=key===taskFilter;
      button.classList.toggle('is-selected',selected);
      button.setAttribute('aria-pressed',selected?'true':'false');
    }
  }
  function appendPreview(fragment,title,value){
    if(value===undefined||value===null)return;
    const heading=document.createElement('strong');heading.textContent=title;fragment.append(heading);
    const pre=document.createElement('pre');pre.className='text-operation-preview';
    let text=typeof value==='string'?value:JSON.stringify(value,null,2);
    if(text.length>24000)text=text.slice(0,24000)+'\n…结果过长，面板只显示前 24,000 个字符。';
    pre.textContent=text;fragment.append(pre);
  }
  async function action(task,verb){
    try{
      if(task.isVideo){
        if(verb==='locate'){await locateTask(task);message('已定位到视频分段。');return;}
        if(verb==='delete'){
          await videoCleanup('/'+task.id,'DELETE');message('已从列表移除，视频和生成历史保留');signature='';await refresh();return;
        }
        const box=root().querySelector('.text-operations-result');box.hidden=false;box.open=true;
        const content=box.querySelector('.text-operations-result-content');
        content.textContent=[task.title,task.phase,task.message,task.output_path].filter(Boolean).join(' · ');
        return;
      }
      if(verb==='delete'){
        const warning=task.state==='ready'?'这会删除待应用候选结果及任务记录，无法恢复。':'这会删除此任务记录，无法恢复。';
        if(!window.confirm(`删除“${task.title}”？${warning}`))return;
        await api('/'+task.id,undefined,'DELETE');
        const box=root().querySelector('.text-operations-result');box.hidden=true;box.open=false;box.querySelector('.text-operations-result-content').replaceChildren();
        message('任务已删除');signature='';await refresh();return;
      }
      if(verb==='result'){
        const body=await api('/'+task.id);
        const box=root().querySelector('.text-operations-result');box.hidden=false;box.open=true;
        const data=body.result, content=box.querySelector('.text-operations-result-content');
        const fragment=document.createDocumentFragment();
        const heading=document.createElement('strong');heading.textContent=labels[data.state]||data.state;fragment.append(heading);
        const progress=document.createElement('progress');progress.max=100;progress.value=data.progress?.percent||0;progress.setAttribute('aria-label','任务完成进度');fragment.append(progress);
        const node=document.createElement('p');node.textContent='当前节点：'+(data.progress?.node||data.phase||'未知')+'（'+(data.progress?.step||0)+'/'+(data.progress?.total||0)+'） · '+(progress.value||0)+'%';fragment.append(node);
        const timeline=document.createElement('ol');timeline.className='text-operation-timeline';
        for(const stage of data.history||[]){const li=document.createElement('li');li.textContent=stage.node+(stage.at?' · '+new Date(stage.at*1000).toLocaleTimeString():'');timeline.append(li);}
        if(timeline.children.length)fragment.append(timeline);
        for(const child of data.stage_tasks||[]){const line=document.createElement('p');line.textContent=child.title+'：'+(labels[child.state]||child.state);fragment.append(line);}
        for(const child of data.web_tasks||[]){const p=document.createElement('p');p.textContent=(child.provider||'网页服务')+'（'+String(child.id||'').slice(0,8)+'）：'+(webLabels[child.status]||child.status)+'。'+(child.message||'');fragment.append(p);
          for(const late of child.late_results||[]){const lateNote=document.createElement('p');lateNote.className='text-operations-error';lateNote.textContent='迟到回复已保留（'+new Date(Number(late.received_at||0)*1000).toLocaleString()+'），不会自动应用。';fragment.append(lateNote);appendPreview(fragment,'迟到回复内容',late.result||late.raw||late.message);}}
        if(data.failure_reason){const error=document.createElement('p');error.className='text-operations-error';error.textContent='失败原因：'+data.failure_reason;fragment.append(error);}
        else if(data.message){const note=document.createElement('p');note.textContent=data.message;fragment.append(note);}
        appendPreview(fragment,'候选结果',data.candidate);
        for(const [name,value] of Object.entries(data.preview||{}))appendPreview(fragment,(data.batch_version===1?'已保存文件：':'候选文件：')+name,value);
        content.replaceChildren(fragment);return;
      }
      if(verb==='locate'){
        await locateTask(task);message('已定位到“'+task.title+'”的提交入口和目标内容。');return;
      }
      if(verb==='apply'){
        if(document.querySelector('#aicf-prompt-modal.open'))throw new Error('请先保存或关闭分段编辑窗口，再应用后台结果。');
        if(task.kind==='metadata'){
          requestMetadataApply(task);return;
        }
        const editors={};for(const key of (task.editor_keys||[])){const el=field(key);if(el)editors[key]=el.value;}
        await api('/'+task.id+'/apply',{editors});message('结果已保存到提交时所属的项目和集数。');
        const refreshed=await maybeRefreshTask({...task,state:'applied'});
        if(refreshed)message('结果已保存，并已刷新当前未修改的内容。');
      } else {await api('/'+task.id+'/'+verb,{});message(verb==='retry'?'已重新排队':verb==='inspect'?'正在检查原网页任务':'任务已取消');}
      signature=''; await refresh();
    }catch(e){message(e.message);if(verb==='apply'){signature='';await refresh();}}
  }
  let refreshPending=false, videoBusy=false, videoTasks=[];
  async function refreshVideos(){
    if(videoBusy)return;videoBusy=true;
    try{
      const response=await fetch('/api/web-video/tasks',{cache:'no-store',signal:AbortSignal.timeout(5000)});
      const body=await response.json();
      if(body.ok){videoTasks=(body.tasks||[]).map(videoView);refresh();}
    }catch{}finally{videoBusy=false;}
  }
  async function refresh(){
    if(!root())return;
    if(busy){refreshPending=true;return;}busy=true;
    const requestedOffset=historyOffset;
    try{
      const {tasks:textTasks,pagination}=await api('?limit=50&offset='+requestedOffset);
      if(requestedOffset!==historyOffset){refreshPending=true;return;}
      const tasks=[...textTasks,...videoTasks].sort((a,b)=>b.created-a.created);
      const r=root();if(!r)return;bindClearButton();autoApplyMetadata(textTasks);observeTaskChanges(tasks);updateEntryStatuses(textTasks);
      updateSummary(tasks);
      updateCleanupButtons();
      updateFilters(tasks);
      if(pagination){
        historyOffset=pagination.offset;
        const previous=r.querySelector('.text-history-previous'),nextPage=r.querySelector('.text-history-next');
        const pageLabel=r.querySelector('.text-history-page');
        if(pageLabel){
          const page=Math.floor(historyOffset/50)+1,totalPages=Math.max(1,Math.ceil(pagination.total/50));
          setTaskText(pageLabel,`历史 ${page}/${totalPages} 页 · 共 ${pagination.total} 条`);
          pageLabel.title='进行中的任务不受分页影响；分类筛选当前页。';
          r.querySelector('.text-operations-pagination').hidden=!pagination.total;
        }
        for(const [button,step,disabled] of [[previous,-50,historyOffset===0],[nextPage,50,!pagination.has_more]]){
          if(!button)continue;button.disabled=disabled;
          button.onclick=()=>{historyOffset=Math.max(0,historyOffset+step);signature='';button.disabled=true;refresh();};
        }
      }
      const list=r.querySelector('.text-operations-list');
      const next=JSON.stringify([historyOffset,taskFilter,tasks.map(t=>[t.id,t.updated,t.state])]);
      if(next===signature&&list.childElementCount)return;
      signature=next;const fragment=document.createDocumentFragment();
      const visibleTasks=(taskFilter==='all'?tasks.slice():tasks.filter(task=>taskMeta(task).category===taskFilter))
        .sort((left,right)=>taskPriority(left)-taskPriority(right));
      for(const task of visibleTasks){
        const meta=taskMeta(task);
        const row=document.createElement('article');row.className='text-operation-row category-'+meta.category;
        if(task.isVideo){
          const heading=document.createElement('div');heading.className='text-operation-heading';
          const badge=document.createElement('span');badge.className='text-operation-kind';badge.textContent='视频';
          const title=document.createElement('strong');title.textContent=task.title;heading.append(badge,title);row.append(heading);
          const info=document.createElement('p');info.textContent=[task.project,task.episode,task.phase].filter(Boolean).join(' · ');row.append(info);
          if(task.message){const error=document.createElement('p');error.className='text-operations-error';error.textContent=task.message;row.append(error);}
          const actions=document.createElement('div');actions.className='text-operation-actions';
          for(const [verb,label] of [['result','查看进度'],['locate','定位分段'],...(['applied','error','needs_attention','cancelled'].includes(task.state)?[['delete','移除任务']]:[])]){
            const button=document.createElement('button');button.type='button';button.textContent=label;
            button.onclick=()=>action(task,verb);actions.append(button);
          }
          row.append(actions);fragment.append(row);continue;
        }
        const heading=document.createElement('div');heading.className='text-operation-heading';
        const badge=document.createElement('span');badge.className='text-operation-kind';badge.textContent=meta.label;
        const title=document.createElement('strong');title.textContent=task.title;heading.append(badge,title);row.append(heading);
        const destination=document.createElement('div');destination.className='text-operation-destination';
        const purpose=document.createElement('p');purpose.textContent='用途：'+meta.purpose;
        const resultTarget=document.createElement('p');resultTarget.textContent='结果：'+meta.destination;
        destination.append(purpose,resultTarget);row.append(destination);
        const contextLine=document.createElement('p');contextLine.className='text-operation-context';
        const timingSource=task.timing_result;
        const providers=(timingSource?.provider?[timingSource.provider]:task.provider_candidates?.length?task.provider_candidates:[task.provider]).filter(value=>value&&value!=='inherit');
        const providerText=providers.length>1?'混合 LLM：'+providers.map(value=>providerLabels[value]||value).join('、'):
          providers.length===1?'LLM：'+(providerLabels[providers[0]]||providers[0]):'';
        contextLine.textContent=[task.project,task.episode,task.segment&&task.segment.segment_index?'分段 '+task.segment.segment_index:'',providerText,timingSource?.model||''].filter(Boolean).join(' · ');
        row.append(contextLine);
        const duration=task.batch_version===1?'批次累计 '+task.elapsed_seconds+' 秒':task.state==='queued'?'排队 '+task.queued_seconds+' 秒':task.state==='needs_attention'?'等待处理 '+task.attention_seconds+' 秒':'执行 '+task.elapsed_seconds+' 秒';
        const status=document.createElement('p');status.textContent=[labels[task.state]||task.state,'当前节点：'+(task.progress?.node||task.phase||'处理中'),duration,new Date(task.created*1000).toLocaleString()].filter(Boolean).join(' · ');row.append(status);
        if(hasTaskProgress(task)){
          const progress=document.createElement('progress');progress.max=100;progress.value=task.progress.percent;
          progress.setAttribute('aria-label',task.title+'完成进度');row.append(progress);
        }
        if(task.batch_version===1){
          const detail=document.createElement('p');detail.setAttribute('role','status');detail.setAttribute('aria-live','polite');
          detail.textContent=`Beats：${task.beats_task_applied?'已保存':'等待完成'} · 已处理 ${task.segment_processed??task.segment_completed??0}/${task.segment_total||'?'} 段 · 有效 ${task.segment_completed||0} · 有提示 ${task.segment_warnings||0} · 待重试 ${task.segment_failed||0}；单段问题不阻止后续规划`;
          row.append(detail);
        }
        if(task.state==='error'){const error=document.createElement('p');error.className='text-operations-error';error.textContent='失败原因：'+(task.message||'未提供原因');row.append(error);}
        if(task.state==='needs_attention'){const attention=document.createElement('p');attention.className='text-operations-error';attention.textContent=task.message||'后台状态无法确认，请检查原网页任务后再处理。';row.append(attention);}
        if(task.apply_error){const error=document.createElement('p');error.className='text-operations-error text-operation-apply-error';error.textContent='应用失败：'+task.apply_error+'。候选结果仍保留，可查看后手动合并，或基于最新原稿重新提交。';row.append(error);}
        const actions=document.createElement('div');actions.className='text-operation-actions';
        const choices=[['result','查看结果 / 进度'],['locate','定位原入口']];
        if(task.state==='ready'&&task.kind!=='opening_continuity')choices.push(['apply',task.kind==='metadata'?'填入素材字段':(applyLabels[task.kind]||'应用候选结果')]);
        if(['preparing','queued','running','needs_attention','waiting_children'].includes(task.state))choices.push(['cancel','取消']);
        if(task.state==='needs_attention'&&task.recovery_safe&&task.attention_reason!=='web_timeout')choices.push(['inspect','检查原网页任务']);
        if(['error','cancelled'].includes(task.state)&&task.kind!=='revision_wait')choices.push(['retry',task.batch_version===1?'继续未完成分段':task.batch_id?'重试本步骤':'重新提交']);
        if(task.kind==='novel_segment'&&task.state==='applied')choices.push(['retry','重新生成本段']);
        if(['error','cancelled'].includes(task.state)&&task.kind==='revision_wait'){const hint=document.createElement('small');hint.textContent='请回到对应分段重新提交修正，避免重复发送旧请求。';actions.append(hint);}
        if(['ready','applied','cancelled','error'].includes(task.state)&&!task.batch_id)choices.push(['delete','删除任务']);
        for(const [verb,label]of choices){const button=document.createElement('button');button.type='button';button.dataset.action=verb;button.textContent=label;button.onclick=async()=>{button.disabled=true;try{await action(task,verb);}finally{button.disabled=false;}};actions.append(button);}
        row.append(actions);fragment.append(row);
      }
      if(!tasks.length){const p=document.createElement('p');p.textContent='暂无后台任务';fragment.append(p);}
      else if(!visibleTasks.length){const p=document.createElement('p');p.className='text-operation-empty';p.textContent='当前分类暂无任务';fragment.append(p);}
      list.replaceChildren(fragment);
    }catch(e){
      if(root()&&!root().dataset.summaryReady)renderDockStatus('暂时无法读取任务');
      message(e.message);
    }finally{busy=false;if(refreshPending){refreshPending=false;queueMicrotask(refresh);}}
  }
  async function videoCleanup(path,method){
    const response=await fetch('/api/web-video/tasks'+path,{method});
    const body=await response.json();if(!response.ok||!body.ok)throw new Error(body.message||'视频任务清理失败');return body;
  }
  function setClearAllConfirmation(panel,show,focus=false){
    panel.querySelector('.text-operations-maintenance > .text-operations-clear-actions').hidden=show;
    panel.querySelector('.text-operations-clear-confirmation').hidden=!show;
    if(focus)panel.querySelector(show?'.text-operations-clear-cancel':'.text-operations-clear-all').focus({preventScroll:true});
  }
  function updateCleanupButtons(){
    const panel=root();if(!panel)return;
    const disabled=taskCleanupPending||panel.dataset.summaryReady!=='true';
    for(const button of panel.querySelectorAll('.text-operations-clear,.text-operations-clear-all,.text-operations-clear-confirm')){
      button.disabled=disabled;
    }
  }
  async function clearCompletedTasks(button){
    if(button.disabled||taskCleanupPending)return;
    taskCleanupPending=true;updateCleanupButtons();button.textContent='清理中…';
    try{
      const body=await api('/clear-completed',{},'POST');
      message(body.deleted||body.video_tasks
        ?`已清理 ${body.deleted} 条文字任务、${body.video_tasks} 条视频任务。生成结果和视频文件仍保留。`
        :'没有可清理的已完成任务。');
      signature='';
      await refreshVideos();await refresh();
    }catch(error){message('清理失败：'+error.message);}
    finally{taskCleanupPending=false;button.textContent='清理已完成任务';updateCleanupButtons();}
  }
  async function clearAllTasks(button,propagate=false){
    if(button.disabled||taskCleanupPending){if(propagate)throw Error('任务清理正在执行，请稍候');return;}
    taskCleanupPending=true;updateCleanupButtons();button.textContent='清空中…';
    const modal=document.getElementById('aicf-prompt-modal'),pendingReplan=modal?._sourceReplanPending;
    try{
      const body=await api('/clear-all',{confirm:true});
      if(pendingReplan && modal._sourceReplanPending===pendingReplan)delete modal._sourceReplanPending;
      videoTasks=[];signature='';seenStates={};
      try{localStorage.removeItem('aicf_text_job_states_v1');}catch{}
      const box=root().querySelector('.text-operations-result');box.hidden=true;box.open=false;
      const notice=`已清空 ${body.deleted} 条文字任务、${body.video_tasks} 条视频任务。扩展连接后自动清空等待队列；网页已发送请求可能继续生成，结果不再回填。`;
      message(notice);
      await refreshVideos();await refresh();
    }catch(error){message('清空失败：'+error.message);if(propagate)throw error;}
    finally{taskCleanupPending=false;button.textContent='一键清空';updateCleanupButtons();}
  }
  function bindClearButton(){
    const panel=root();if(!panel||panel.dataset.cleanupBound)return;
    panel.dataset.cleanupBound='true';
    const completed=panel.querySelector('.text-operations-clear'),all=panel.querySelector('.text-operations-clear-all');
    completed.addEventListener('click',()=>clearCompletedTasks(completed));
    all.addEventListener('click',()=>setClearAllConfirmation(panel,true,true));
    panel.querySelector('.text-operations-clear-cancel').addEventListener('click',()=>setClearAllConfirmation(panel,false,true));
    panel.querySelector('.text-operations-clear-confirm').addEventListener('click',()=>{
      setClearAllConfirmation(panel,false,true);
      clearAllTasks(all);
    });
  }
  function observeTaskChanges(tasks){
    const now=Date.now()/1000;let changed=false;
    for(const task of tasks){
      const previous=seenStates[task.id];
      const terminal=['ready','applied','error','needs_attention'].includes(task.state);
      const isNewRecent=!previous&&terminal&&now-Number(task.created||now)<600;
      const completed=previous&&['queued','running'].includes(previous)&&terminal;
      if(isNewRecent||completed){
        const notice=task.state==='applied'?'“'+task.title+'”已完成并写入原项目。':
          task.state==='ready'?'“'+task.title+'”已完成，结果待处理。':
          task.state==='needs_attention'?'“'+task.title+'”需要人工检查。':'“'+task.title+'”执行失败：'+(task.message||'请查看任务详情。');
        toast(notice);message(notice);
        if(task.state==='applied')maybeRefreshTask(task).then(ok=>{message(ok?'结果已完成，并已刷新当前未修改的内容。':'结果已保存；当前页面存在编辑或不是任务原项目，未自动覆盖。可在任务中定位原入口查看。');}).catch(()=>{});
      }
      if(previous!==task.state){seenStates[task.id]=task.state;changed=true;}
    }
    if(changed){const keys=Object.keys(seenStates);for(const id of keys.slice(0,Math.max(0,keys.length-200)))delete seenStates[id];
      try{localStorage.setItem('aicf_text_job_states_v1',JSON.stringify(seenStates));}catch{}}
  }
  let eventStream=null;
  function connectEvents(){
    if(eventStream||typeof EventSource==='undefined')return;
    eventStream=new EventSource('/api/web-llm/operations/events');
    eventStream.addEventListener('changed',()=>refresh());
    eventStream.onopen=()=>refresh();
  }
  window.addEventListener('pagehide',()=>{eventStream?.close();eventStream=null;});
  window.addEventListener('pageshow',()=>{connectEvents();refresh();});
  connectEvents();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();refreshVideos();}});
  setInterval(()=>{if(!document.hidden){refresh();refreshVideos();}},2500);
  refresh();refreshVideos();
})();
'''

PANEL_CSS = '''
.text-extension-update{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:8px 0}.text-extension-update details{flex-basis:100%;font-size:13px}.text-extension-update-summary{font-size:13px;line-height:1.6}.text-extension-update-details{overflow-wrap:anywhere}.text-extension-update-details p{margin:6px 0}.text-extension-update button{min-height:36px;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text-body);cursor:pointer}
/* Persistent task controls sit above full-text editing (9999), previews
   (10000), and the asset library (12000); notifications sit above controls. */
#text-operations-dock{
  --ops-accent:var(--theme-accent,#2563eb);--ops-success:var(--theme-success-text,#15803d);--ops-warning:var(--theme-warning-text,#b45309);--ops-danger:var(--theme-danger-text,#b91c1c);
  --ops-default-bottom:clamp(20px,calc(33vh - 24px),220px);--ops-trigger-size:48px;--ops-gap:10px;
  --ops-dock-bottom:clamp(max(12px,env(safe-area-inset-bottom)),var(--ops-drag-bottom,var(--ops-default-bottom)),calc(100dvh - var(--ops-trigger-size) - max(12px,env(safe-area-inset-top))));
  position:fixed;left:var(--ops-drag-left,auto);right:0;bottom:var(--ops-dock-bottom);width:var(--ops-trigger-size);
  z-index:20000;color:var(--theme-text-body,#374151);font-size:13px;line-height:1.5
}
#text-operations-dock [hidden]{display:none!important}
#text-operations-launcher{
  position:relative;display:grid;place-items:center;width:var(--ops-trigger-size);height:var(--ops-trigger-size);
  box-sizing:border-box;margin:0;padding:0 2px 0 0;border:1px solid var(--theme-border-strong,#cbd5e1);border-right:0;border-radius:15px 0 0 15px;
  background:var(--theme-surface,#fff);color:var(--ops-accent);box-shadow:-3px 4px 16px rgba(15,23,42,.12);
  cursor:grab;touch-action:none;user-select:none;transition:color 170ms ease,background-color 170ms ease,box-shadow 170ms ease
}
.text-operations-launcher-icon{display:block;object-fit:contain;transition:transform 140ms ease}
#text-operations-dock[data-dock-edge=left] #text-operations-launcher{border-right:1px solid var(--theme-border-strong,#cbd5e1);border-left:0;border-radius:0 15px 15px 0}
#text-operations-dock[data-dock-edge=free] #text-operations-launcher{border-right:1px solid var(--theme-border-strong,#cbd5e1);border-radius:15px;padding:0}
#text-operations-launcher:active .text-operations-launcher-icon{transform:scale(.9)}
#text-operations-dock.is-dragging #text-operations-launcher{cursor:grabbing}
#text-operations-dock.is-dragging .text-operations-launcher-icon{transform:none}
#text-operations-launcher[aria-expanded=true]{color:var(--theme-accent-text,var(--ops-accent));background:var(--theme-accent-soft,#eff6ff);border-color:var(--theme-accent-border-strong,#93c5fd)}
#text-operations-dock :is(button,summary,input):focus-visible{outline:2px solid var(--ops-accent);outline-offset:2px}
#text-operations-dock #text-operations-launcher:focus-visible{outline-offset:-4px}
.text-operations-badge,.text-operations-pip{position:absolute;box-sizing:border-box;border:2px solid var(--theme-surface,#fff);border-radius:999px}
.text-operations-badge{display:grid;place-items:center;min-width:19px;height:19px;top:2px;right:3px;padding:0 3px;background:var(--ops-accent);color:var(--theme-accent-foreground,#fff);font-variant-numeric:tabular-nums;font-size:10px;font-weight:750;line-height:1}
.text-operations-pip{width:10px;height:10px;right:4px;bottom:3px;background:var(--ops-warning)}
.text-operations-pip.has-error{background:var(--ops-danger)}
.text-operations-preview{
  --ops-preview-y:-50%;--ops-preview-x:3px;
  position:absolute;right:calc(100% + var(--ops-gap));top:50%;width:max-content;max-width:min(270px,var(--ops-preview-room,270px));box-sizing:border-box;
  display:grid;gap:2px;padding:8px 12px;border:1px solid var(--theme-border,#e5e7eb);border-radius:11px;
  background:var(--theme-surface,#fff);box-shadow:0 6px 20px rgba(15,23,42,.12);font-size:12px;
  visibility:hidden;opacity:0;transform:translate(var(--ops-preview-x),var(--ops-preview-y));transition:opacity 160ms ease,transform 160ms ease,visibility 160ms
}
#text-operations-dock[data-preview-side=right] .text-operations-preview{left:calc(100% + var(--ops-gap));right:auto;--ops-preview-x:-3px}
#text-operations-dock[data-preview-edge=top] .text-operations-preview{top:0;--ops-preview-y:0px}
#text-operations-dock[data-preview-edge=bottom] .text-operations-preview{top:auto;bottom:0;--ops-preview-y:0px}
.text-operations-preview::after{content:"";position:absolute;inset:0 calc(-1 * var(--ops-gap)) 0 100%}
#text-operations-dock[data-preview-side=right] .text-operations-preview::after{inset:0 100% 0 calc(-1 * var(--ops-gap))}
.text-operations-preview strong{font-size:13px;color:var(--theme-text-title,#0f172a)}
.text-operations-preview-hint{margin-top:3px;font-size:11px;color:var(--theme-text-secondary,#475569)}
#text-operations-launcher[aria-expanded=true]+.text-operations-preview,#text-operations-dock.is-preview-dismissed .text-operations-preview{visibility:hidden;opacity:0;pointer-events:none;transition:none}
#text-operations-dock:not(.is-preview-dismissed):has(#text-operations-launcher[aria-expanded=false]:focus-visible) .text-operations-preview{visibility:visible;opacity:1;transform:translate(0,var(--ops-preview-y))}
@media(hover:hover) and (pointer:fine){
  #text-operations-launcher:hover{background:var(--theme-accent-soft,#eff6ff);box-shadow:-4px 5px 18px rgba(15,23,42,.16)}
  #text-operations-dock:not(.is-preview-dismissed):has(#text-operations-launcher[aria-expanded=false]):hover .text-operations-preview{visibility:visible;opacity:1;transform:translate(0,var(--ops-preview-y));transition-delay:180ms}
  #text-operations-dock:has(#text-operations-launcher[aria-expanded=false]:focus-visible) .text-operations-preview{transition-delay:0s}
}
#text-operations{
  position:fixed;left:var(--ops-panel-left);bottom:var(--ops-panel-anchor);width:var(--ops-panel-width);
  max-height:var(--ops-panel-max);
  box-sizing:border-box;display:flex;flex-direction:column;overflow:hidden;margin:0;padding:0;
  border:1px solid var(--theme-border-strong,#cbd5e1);border-radius:16px;
  background:var(--theme-surface,#fff);box-shadow:var(--theme-shadow-overlay,0 18px 44px rgba(15,23,42,.16))
}
#text-operations-dock[data-panel-direction=down] #text-operations{
  top:var(--ops-panel-anchor);bottom:auto
}
#text-operations:not([hidden]){animation:text-operations-reveal 170ms ease-out}
@keyframes text-operations-reveal{from{opacity:0}to{opacity:1}}
.text-operations-summary{position:relative;flex:none;padding:12px 14px;border-bottom:1px solid var(--theme-border,#e5e7eb)}
.text-operations-summary-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
#text-operations-heading{font-size:16px;font-weight:750;color:var(--theme-text-title,#0f172a);margin:0;line-height:1.35}
#text-operations-heading:focus{outline:none}
.text-operations-summary-top p{font-size:12px;margin:2px 0 0;color:var(--theme-text-secondary,#475569)}
#text-operations .text-operations-close{
  display:grid;place-items:center;box-sizing:border-box;flex:none;width:44px;height:44px;
  padding:0;border:1px solid var(--theme-danger-border,#fecaca);border-radius:9px;
  background:var(--theme-danger-soft,#fef2f2);color:var(--ops-danger);cursor:pointer
}
#text-operations .text-operations-close svg{color:var(--ops-danger)}
#text-operations .text-operations-close:hover{border-color:var(--ops-danger)}
.text-operations-maintenance{flex:none;margin-bottom:10px}
.text-operations-clear-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end}
#text-operations .text-operations-clear-actions button{
  min-height:44px;padding:6px 10px;border:1px solid var(--theme-border-strong,#cbd5e1);border-radius:8px;
  background:var(--theme-surface,#fff);color:var(--theme-text-body,#374151);font-size:12px;font-weight:600;cursor:pointer
}
#text-operations .text-operations-clear-actions button:hover:not(:disabled){background:var(--theme-surface-muted,#f1f5f9)}
#text-operations .text-operations-clear-actions button:disabled{opacity:.55;cursor:not-allowed}
#text-operations .text-operations-clear-actions :is(.text-operations-clear-all,.text-operations-clear-confirm){
  border-color:var(--theme-danger-border,#fecaca);color:var(--ops-danger)
}
#text-operations .text-operations-clear-actions :is(.text-operations-clear-all,.text-operations-clear-confirm):hover:not(:disabled){
  background:var(--theme-danger-soft,#fef2f2);border-color:var(--ops-danger)
}
.text-operations-clear-confirmation{
  padding:9px 10px;border:1px solid var(--theme-danger-border,#fecaca);border-radius:10px;background:var(--theme-danger-soft,#fef2f2)
}
#text-operations-clear-warning{margin:0 0 8px;color:var(--ops-danger);font-size:12px;line-height:1.5}
.text-operations-overview{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:8px;font-size:12px;font-weight:600}
.text-operations-count,.text-operations-preview-active{color:var(--theme-text-secondary,#475569)}
.text-operations-attention,.text-operations-preview-attention{color:var(--ops-warning)}
.text-operations-attention.has-error,.text-operations-preview-attention.has-error{color:var(--ops-danger)}
.text-operations-current{display:flex;align-items:flex-start;gap:8px;margin-top:9px;font-size:12px}
.text-operations-current-dot{flex:none;width:7px;height:7px;margin:6px 1px 0 2px;border-radius:50%;background:var(--ops-accent)}
.text-operations-current-copy{display:grid;gap:2px;flex:1;min-width:0}
.text-operations-current-title,.text-operations-current-phase{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.text-operations-current-title{color:var(--theme-text-title,#0f172a)}
.text-operations-current-phase{color:var(--theme-text-secondary,#475569)}
.text-operations-current-percent{font-weight:700;color:var(--ops-accent);font-variant-numeric:tabular-nums}
.text-operations-current-progress{display:block;width:100%;height:5px;margin:7px 0 0;border:0;border-radius:999px;overflow:hidden;accent-color:var(--ops-accent)}
.text-operations-current-progress::-webkit-progress-bar{background:var(--theme-surface-muted,#f1f5f9);border-radius:999px}
.text-operations-current-progress::-webkit-progress-value{background:var(--ops-accent);border-radius:999px;transition:width 170ms ease}
.text-operations-body{display:flex;flex-direction:column;flex:1;min-height:0;padding:9px 14px 12px}
#text-operations .text-operations-pagination{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:5px;flex:none;border-top:1px solid var(--theme-border,#e5e7eb);padding-top:7px;margin-top:4px}
.text-history-page{max-width:100%;text-align:center;font-size:11px;color:var(--theme-text-secondary,#475569);font-variant-numeric:tabular-nums}
.text-operations-message:empty{display:none}
.text-operations-message{font-size:12px;padding:8px 10px;margin:4px 0;border-radius:8px;background:var(--theme-accent-soft,#eff6ff);color:var(--theme-text-body,#374151)}
@media(max-width:600px),(max-height:440px){
  #text-operations-dock{--ops-default-bottom:max(12px,env(safe-area-inset-bottom));right:env(safe-area-inset-right)}
  .text-operations-body{padding-inline:10px}
}
#text-operations-dock.is-panel-compact #text-operations{overflow:auto;overscroll-behavior:contain}
#text-operations-dock.is-panel-compact #text-operations>.text-operations-body{flex:none}
@media(prefers-reduced-motion:reduce){
  #text-operations-dock #text-operations,#text-operations-launcher,.text-operations-launcher-icon,.text-operations-preview,
  #text-operations-dock .text-operations-current-progress::-webkit-progress-value{animation:none;transition:none}
  #text-operations-launcher:active .text-operations-launcher-icon{transform:none}
}
.text-operation-filters{display:flex;gap:6px;flex-wrap:wrap;padding:2px 0 8px}.text-operation-filters button{min-height:34px;padding:5px 9px;border:1px solid var(--theme-border,#e5e7eb);border-radius:999px;background:var(--theme-surface,#fff);color:var(--theme-text-secondary,#64748b);font-size:11px;font-weight:600;cursor:pointer;transition:background var(--theme-motion,170ms ease),border-color var(--theme-motion,170ms ease),color var(--theme-motion,170ms ease)}.text-operation-filters button:hover{border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}.text-operation-filters button.is-selected{background:var(--theme-accent-soft,#eff6ff);border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}.text-operation-filters button span{margin-left:3px;font-variant-numeric:tabular-nums;opacity:.78}.text-operation-filters button:focus-visible{outline:2px solid var(--ops-accent);outline-offset:2px}
.text-operations-list{overflow:auto;min-height:0;padding:2px 3px 4px 0;scrollbar-color:var(--theme-scrollbar-thumb,#cbd5e1) transparent}.text-operation-row{padding:12px;margin:7px 0;border:1px solid var(--theme-border,#e5e7eb);border-radius:12px;background:var(--theme-surface,#fff);box-shadow:var(--theme-shadow,0 1px 2px rgba(15,23,42,.035))}.text-operation-heading{display:flex;align-items:flex-start;gap:8px}.text-operation-heading>strong{flex:1;line-height:1.45;color:var(--theme-text-title,#0f172a);font-size:12px}.text-operation-kind{flex:none;padding:3px 7px;border-radius:999px;background:var(--theme-accent-soft,#eff6ff);color:var(--ops-accent);font-size:11px;font-weight:700;line-height:1.35}.category-creation .text-operation-kind{background:var(--theme-success-soft,#f0fdf4);color:var(--ops-success)}.category-prompt .text-operation-kind{background:var(--theme-accent-soft,#eff6ff);color:var(--ops-accent)}.category-structure .text-operation-kind{background:var(--theme-warning-soft,#fffbeb);color:var(--ops-warning)}.category-analysis .text-operation-kind{background:var(--theme-surface-muted,#f1f5f9);color:var(--theme-text-body,#374151)}.category-asset .text-operation-kind{background:var(--theme-info-soft,#eff6ff);color:var(--theme-info-text,#1d4ed8)}.text-operation-destination{margin:8px 0;padding:7px 9px;border-radius:8px;background:var(--theme-surface-subtle,#f9fafb);border:1px solid var(--theme-border,#e5e7eb)}.text-operation-destination p{margin:2px 0!important;color:var(--theme-text-secondary,#4b5563);font-size:11px!important}.text-operation-destination p:first-child{color:var(--theme-text-card,#1f2937);font-weight:600}.text-operation-row p{margin:7px 0;overflow-wrap:anywhere;font-size:11px;line-height:1.55;color:var(--theme-text-secondary,#4b5563)}.text-operation-row .text-operation-context{margin:5px 0 2px;color:var(--theme-text-card,#334155);font-weight:600}.text-operation-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.text-operation-row progress,.text-operations-result-content progress{display:block;width:100%;height:7px;margin:8px 0 10px;accent-color:var(--ops-accent)}.text-operation-result-empty,.text-operation-empty{padding:10px 4px;color:var(--theme-text-secondary,#64748b);font-size:11px}.text-operation-result-empty{font-size:12px}.text-operations-result{flex:none;margin-top:8px;border-top:1px solid var(--theme-border,#e5e7eb);padding-top:4px}.text-operations-result>summary{font-size:12px;color:var(--theme-text-secondary,#4b5563)}.text-operations-result-content{max-height:32vh;overflow:auto;padding:8px 2px;font-size:12px}.text-operation-timeline{margin:8px 0;padding-left:22px}.text-operation-timeline li{padding:3px 0}.text-operations-error{color:var(--ops-danger)!important;font-weight:600}.text-operation-actions button{min-height:34px;padding:6px 10px;border:1px solid var(--theme-border,#e5e7eb);border-radius:8px;background:var(--theme-surface,#fff);color:var(--theme-text-body,#374151);font-size:11px;font-weight:600;cursor:pointer;transition:background var(--theme-motion,170ms ease),border-color var(--theme-motion,170ms ease)}.text-operation-actions button:hover{background:var(--theme-accent-soft,#eff6ff);border-color:var(--theme-accent-border-strong,#93c5fd)}.text-operation-actions button:focus-visible{outline:2px solid var(--ops-accent);outline-offset:2px}
.text-operation-actions button[data-action=delete]{border-color:var(--theme-danger-border,#fecaca);color:var(--ops-danger)}.text-operation-actions button[data-action=delete]:hover{background:var(--theme-danger-soft,#fef2f2);border-color:var(--theme-danger,#ef4444)}
.text-operation-row{opacity:1;transform:translateY(0);transition:opacity 420ms ease,transform 420ms ease}.text-operation-row.is-expiring{opacity:0;transform:translateY(6px);pointer-events:none}.text-operation-apply-error{padding:8px 9px;border:1px solid var(--theme-danger-border,#fecaca);border-radius:8px;background:var(--theme-danger-soft,#fef2f2)}
@media(prefers-reduced-motion:reduce){.text-operation-row,.text-operation-filters button{transition:none}}
#text-operations-toast{position:fixed;top:14px;left:50%;right:auto;transform:translateX(-50%);pointer-events:none;overflow-wrap:anywhere;z-index:20010;width:min(420px,calc(100vw - 28px));box-sizing:border-box;padding:12px 15px;border:1px solid var(--theme-accent-border-strong,#93c5fd);border-radius:12px;background:var(--theme-surface,#fff);color:var(--theme-text-title,#0f172a);box-shadow:var(--theme-shadow-overlay,0 12px 30px rgba(15,23,42,.18));font-size:14px;line-height:1.5}
.text-operation-preview{max-height:34vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:10px;border:1px solid var(--theme-border,#e5e7eb);border-radius:8px;background:var(--theme-surface-subtle,#f9fafb);font-size:12px;line-height:1.55}
.text-operation-target{outline:3px solid var(--theme-accent,#2563eb)!important;outline-offset:2px;border-radius:12px}
.text-operation-entry-state{display:block;width:max-content;max-width:100%;box-sizing:border-box;margin-top:4px;padding:3px 7px;border:1px solid var(--theme-border,#cbd5e1);border-radius:999px;background:var(--theme-surface,#fff);color:var(--theme-text-secondary,#475569);font-size:10px;line-height:1.35;white-space:normal;overflow-wrap:anywhere;pointer-events:none}
.text-operation-entry-state.state-queued,.text-operation-entry-state.state-running,.text-operation-entry-state.state-applying{border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}
.text-operation-entry-state.state-ready,.text-operation-entry-state.state-applied{border-color:var(--theme-success-border,#bbf7d0);color:var(--ops-success)}
.text-operation-entry-state.state-error,.text-operation-entry-state.state-needs_attention{border-color:var(--theme-danger-border,#fecaca);color:var(--ops-danger)}
.segment-title-block>.text-operation-entry-state{margin-top:6px}
.text-operation-actions button,.text-operation-filters button{min-height:44px}
@media(pointer:coarse){#text-operations-dock button{min-height:44px!important}}
.text-operation-row.is-expiring{opacity:1;transform:none;pointer-events:auto}
@media(prefers-reduced-motion:reduce){#text-operations-toast{scroll-behavior:auto}}
#text-operations .text-operations-list{overscroll-behavior:contain;scroll-padding-bottom:16px}
'''
