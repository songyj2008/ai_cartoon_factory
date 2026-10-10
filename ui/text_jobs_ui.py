"""Fast Gradio submission callbacks and an accessible persistent task panel."""
import gradio as gr


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
            if task.get('duplicate'):
                updates[status_index] = f"相同输入已有任务：{task['id'][:8]}。请在后台任务列表查看进度。"
            else:
                updates[status_index] = f"已加入后台任务：{task['title']} · {task['id'][:8]}。可继续操作，在上方任务列表查看进度。"
        except Exception as exc:
            updates[status_index] = f'提交失败：{exc}'
        return tuple(updates)
    return callback


PANEL_HTML = '''<div id="text-operations-toast" role="status" aria-live="polite" hidden></div><details id="text-operations" class="web-llm-box">
<summary class="text-operations-summary" aria-label="展开或收起后台任务">
  <span class="text-operations-summary-top"><span class="text-operations-brand"><span class="text-operations-mark">✦</span><span><strong>任务中心</strong><small>文字与网页视频任务</small></span></span><span class="text-operations-count"></span></span>
  <span class="text-operations-current"><span class="text-operations-current-dot"></span><span class="text-operations-current-title">正在读取任务…</span><span class="text-operations-current-percent">—</span></span>
  <progress class="text-operations-current-progress" max="100" value="0" aria-label="当前任务完成进度"></progress>
  <span class="text-operations-stats" aria-label="任务状态数量"><span data-count="running">执行中 <b>0</b></span><span data-count="queued">排队 <b>0</b></span><span data-count="ready">待应用 <b>0</b></span><span data-count="applied">已应用 <b>0</b></span><span data-count="cancelled">已取消 <b>0</b></span><span data-count="error">异常 <b>0</b></span></span>
</summary>
<div class="text-operations-body">
  <div class="text-operations-toolbar"><div><strong>后台任务</strong><p>查看文字任务与网页视频生成进度。</p></div><div class="text-operations-toolbar-actions"><span class="text-operations-live">自动更新</span><button class="text-operations-clear" type="button" disabled>清空文字任务</button><button class="text-operations-clear-video" type="button" disabled>清理视频任务</button></div></div>
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
  <details class="text-operations-result" hidden><summary>任务进度详情</summary>
  <div class="text-operations-result-content" role="status" aria-live="polite"></div></details>
</div>
</details>'''

PANEL_JS = r'''
(() => {
  if(window.__AICF_TEXT_OPERATIONS_INIT__) return;
  window.__AICF_TEXT_OPERATIONS_INIT__=true;
  let busy=false, signature='', lastSummary='', entrySignature='', tasksCache=[], taskFilter='all', editorBaselines=new Map(), toastTimer=null;
  let seenStates={};try{seenStates=JSON.parse(localStorage.getItem('aicf_text_job_states_v1')||'{}')||{};}catch{}
  const labels={preparing:'准备中',queued:'排队中',running:'执行中',ready:'待应用',applied:'已应用',
    cancelled:'已取消',error:'失败',needs_attention:'需要处理',applying:'保存中'};
  const webLabels={queued:'网页任务排队中',claimed:'扩展已领取',waiting:'等待网页回复',complete:'网页回复完成',error:'网页任务失败',timeout:'网页任务超时',cancelled:'网页任务已取消',unknown:'网页任务状态未知'};
  const providerLabels={deepseek:'DeepSeek',chatgpt:'ChatGPT',doubao:'豆包',qianwen:'千问',api:'DeepSeek API'};
  const videoPhases={queued:'等待扩展领取',claimed:'准备网页提交',submitted:'豆包生成中',complete:'视频已保存',uncertain:'请检查原网页',error:'生成失败'};
  function videoView(row){
    const state={queued:'queued',claimed:'running',submitted:'running',complete:'applied',uncertain:'needs_attention',error:'error'}[row.state]||'needs_attention';
    const phase=videoPhases[row.state]||row.state;
    return {id:row.id,kind:'web_video',isVideo:true,title:`${row.channel==='doubao_web'?'豆包视频':row.channel} · 第 ${row.segment_index} 段`,
      project:row.project,episode:row.episode,segment:{segment_index:row.segment_index,segment_uid:row.segment_uid},
      state,phase,progress:{percent:row.state==='complete'?100:0,node:phase},
      created:row.created_at,updated:row.updated_at,elapsed_seconds:Math.max(0,Math.floor(Date.now()/1000-row.created_at)),
      queued_seconds:Math.max(0,Math.floor(Date.now()/1000-row.created_at)),message:row.error||'',output_path:row.output_path||''};
  }
  const taskKinds={
    story:{category:'creation',label:'创作',purpose:'生成或更新本集剧情稿',destination:'校验后自动写回；检测到冲突时保留候选'},
    beats:{category:'creation',label:'创作',purpose:'把剧情拆分为 Beats',destination:'校验后更新 Beats 与分段结构；冲突时保留候选'},
    novel:{category:'creation',label:'创作',purpose:'从小说生成 Beats 与视频分段',destination:'校验后写回本集；冲突时保留候选'},
    pipeline:{category:'creation',label:'创作',purpose:'一键准备剧情、Beats 和分段提示词',destination:'校验后写回本集；冲突时保留候选'},
    prompts:{category:'prompt',label:'提示词',purpose:'按当前 Beats 生成全部分段提示词',destination:'校验后更新本集；冲突时保留候选'},
    segment:{category:'prompt',label:'提示词',purpose:'只重新生成指定分段的提示词',destination:'目标分段提示词候选 · 不改其他分段'},
    source_replan:{category:'prompt',label:'提示词',purpose:'结合补充剧情上下文，重新规划指定分段',destination:'目标分段提示词候选 · 保留其他分段'},
    revision_wait:{category:'prompt',label:'提示词',purpose:'等待网页助手完成提示词修正',destination:'修正后的目标分段提示词候选'},
    split:{category:'structure',label:'结构',purpose:'拆分一个复杂 Beat 并调整关联分段',destination:'结构调整候选 · 应用后更新 Beats 与分段'},
    feedback:{category:'analysis',label:'分析',purpose:'分析所选视频的问题并生成改进建议',destination:'问题分析报告与候选规则 · 可单独查看'},
    metadata:{category:'asset',label:'素材',purpose:'为当前素材补全描述、标签等字段',destination:'素材字段候选 · 填入后可检查再保存'},
    web_video:{category:'video',label:'视频',purpose:'网页视频生成',destination:'完成后保存到所属分段'}
  };
  const applyLabels={story:'应用剧情候选',beats:'应用 Beats 候选',novel:'应用 Beats 与分段',pipeline:'应用整套候选',prompts:'应用提示词候选',segment:'更新目标分段',source_replan:'更新目标分段',revision_wait:'更新目标分段',split:'应用 Beat 拆分',feedback:'保存分析报告'};
  function taskMeta(task){return taskKinds[task.kind]||{category:'other',label:'其他',purpose:'后台处理任务',destination:'查看任务结果了解后续用途'};}
  const root=()=>document.getElementById('text-operations');
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
    const anchorByKind={story:'story_editor',pipeline:'story_editor',beats:'beats_editor',novel:'novel_text_editor',split:'beats_editor',feedback:'feedback_analyze_btn'};
    if(['segment','source_replan','revision_wait','web_video'].includes(task.kind)&&task.segment&&task.segment.segment_index){
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
    if(!task||task.state!=='applied'||['metadata','feedback'].includes(task.kind))return false;
    const activeProject=field('project_name')?.value||'',activeEpisode=field('project_episode')?.value||'';
    const workbench=document.getElementById('workbench_page');
    if(activeProject!==task.project||String(activeEpisode||'')!==String(task.episode||'')||
       !workbench||workbench.getBoundingClientRect().width===0||document.querySelector('#aicf-prompt-modal.open')||hasUnsavedEditors())return false;
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
      applied:'已完成',cancelled:'已取消',error:'失败',needs_attention:'需要处理'};
    const shown=tasks.filter(task=>{
      if(task.project!==activeProject||String(task.episode||'')!==String(activeEpisode||''))return false;
      if(['preparing','queued','running','applying','ready','needs_attention'].includes(task.state))return true;
      return ['applied','cancelled','error'].includes(task.state)&&Date.now()/1000-Number(task.updated||task.created||0)<3600;
    });
    const groups=new Map();
    for(const task of shown){
      const segmentTask=['segment','source_replan','revision_wait'].includes(task.kind)&&task.segment?.segment_index;
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
  function updateSummary(tasks){
    const r=root();if(!r)return;
    const counts={running:0,queued:0,ready:0,applied:0,cancelled:0,error:0};
    for(const t of tasks){if(t.state==='running'||t.state==='applying')counts.running++;else if(t.state==='queued')counts.queued++;else if(t.state==='ready')counts.ready++;else if(t.state==='applied')counts.applied++;else if(t.state==='cancelled')counts.cancelled++;else if(['error','needs_attention'].includes(t.state))counts.error++;}
    for(const [state,count] of Object.entries(counts)){const node=r.querySelector(`[data-count="${state}"] b`);if(node)node.textContent=String(count);}
    const active=tasks.find(t=>t.state==='running'||t.state==='applying')||tasks.find(t=>t.state==='queued');
    const title=r.querySelector('.text-operations-current-title'), percent=r.querySelector('.text-operations-current-percent'), bar=r.querySelector('.text-operations-current-progress'), dot=r.querySelector('.text-operations-current-dot');
    const value=Math.max(0,Math.min(100,Number(active?.progress?.percent)||0));
    const liveCount=counts.running+counts.queued;
    title.textContent=active?`${liveCount>1?liveCount+' 项运行或排队 · ':''}${active.title} · ${active.project}${active.episode?' / '+active.episode:''} · ${active.progress?.node||active.phase||labels[active.state]||active.state}`:'当前没有正在执行的任务';
    percent.textContent=active&&!active.isVideo?`${value}%`:'';bar.value=value;dot.classList.toggle('is-idle',!active);r.classList.toggle('has-active-task',Boolean(active));
    const countText=`${counts.running} 执行 · ${counts.queued} 排队 · ${counts.ready} 待应用 · ${counts.applied} 已应用 · ${counts.cancelled} 已取消 · ${counts.error} 异常`;
    const summary=active?.id+'|'+value+'|'+countText+'|'+active?.progress?.node;
    if(summary!==lastSummary){r.querySelector('.text-operations-count').textContent=countText;lastSummary=summary;}
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
        for(const child of data.web_tasks||[]){const p=document.createElement('p');p.textContent=(child.provider||'网页服务')+'（'+String(child.id||'').slice(0,8)+'）：'+(webLabels[child.status]||child.status)+'。'+(child.message||'');fragment.append(p);
          for(const late of child.late_results||[]){const lateNote=document.createElement('p');lateNote.className='text-operations-error';lateNote.textContent='迟到回复已保留（'+new Date(Number(late.received_at||0)*1000).toLocaleString()+'），不会自动应用。';fragment.append(lateNote);appendPreview(fragment,'迟到回复内容',late.result||late.raw||late.message);}}
        if(data.failure_reason){const error=document.createElement('p');error.className='text-operations-error';error.textContent='失败原因：'+data.failure_reason;fragment.append(error);}
        else if(data.message){const note=document.createElement('p');note.textContent=data.message;fragment.append(note);}
        appendPreview(fragment,'候选结果',data.candidate);
        for(const [name,value] of Object.entries(data.preview||{}))appendPreview(fragment,'候选文件：'+name,value);
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
    try{
      const {tasks:textTasks}=await api('');
      const tasks=[...textTasks,...videoTasks].sort((a,b)=>b.created-a.created);
      const r=root();if(!r)return;tasksCache=textTasks;bindClearButton();autoApplyMetadata(textTasks);observeTaskChanges(tasks);updateEntryStatuses(textTasks);
      updateSummary(tasks);
      updateFilters(tasks);
      const clearButton=r.querySelector('.text-operations-clear');
      clearButton.disabled=!textTasks.some(t=>['ready','applied','cancelled','error'].includes(t.state));
      const videoClear=r.querySelector('.text-operations-clear-video');
      if(videoClear){videoClear.disabled=!tasks.some(t=>t.isVideo&&['applied','error','needs_attention','cancelled'].includes(t.state));
        videoClear.onclick=async()=>{try{videoClear.disabled=true;const body=await videoCleanup('/clear','POST');message('已移除 '+body.dismissed+' 条视频任务，视频文件和生成历史保留');signature='';await refresh();}catch(e){message(e.message);videoClear.disabled=false;}};}
      const list=r.querySelector('.text-operations-list');
      const next=JSON.stringify(tasks.map(t=>[t.id,t.updated,t.state]));
      if(next===signature&&list.childElementCount)return;
      signature=next;const fragment=document.createDocumentFragment();
      const visibleTasks=taskFilter==='all'?tasks:tasks.filter(task=>taskMeta(task).category===taskFilter);
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
        const providers=(task.provider_candidates?.length?task.provider_candidates:[task.provider]).filter(value=>value&&value!=='inherit');
        const providerText=providers.length>1?'混合 LLM：'+providers.map(value=>providerLabels[value]||value).join('、'):
          providers.length===1?'LLM：'+(providerLabels[providers[0]]||providers[0]):'';
        contextLine.textContent=[task.project,task.episode,task.segment&&task.segment.segment_index?'分段 '+task.segment.segment_index:'',providerText].filter(Boolean).join(' · ');
        row.append(contextLine);
        const duration=task.state==='queued'?'排队 '+task.queued_seconds+' 秒':task.state==='needs_attention'?'等待处理 '+task.attention_seconds+' 秒':'执行 '+task.elapsed_seconds+' 秒';
        const status=document.createElement('p');status.textContent=[labels[task.state]||task.state,'当前节点：'+(task.progress?.node||task.phase||'处理中'),duration,new Date(task.created*1000).toLocaleString()].filter(Boolean).join(' · ');row.append(status);
        const progress=document.createElement('progress');progress.max=100;progress.value=task.progress?.percent||0;progress.setAttribute('aria-label',task.title+'完成进度');row.append(progress);
        if(task.state==='error'){const error=document.createElement('p');error.className='text-operations-error';error.textContent='失败原因：'+(task.message||'未提供原因');row.append(error);}
        if(task.state==='needs_attention'){const attention=document.createElement('p');attention.className='text-operations-error';attention.textContent=task.message||'后台状态无法确认，请检查原网页任务后再处理。';row.append(attention);}
        if(task.apply_error){const error=document.createElement('p');error.className='text-operations-error text-operation-apply-error';error.textContent='应用失败：'+task.apply_error+'。候选结果仍保留，可查看后手动合并，或基于最新原稿重新提交。';row.append(error);}
        const actions=document.createElement('div');actions.className='text-operation-actions';
        const choices=[['result','查看结果 / 进度'],['locate','定位原入口']];
        if(task.state==='ready')choices.push(['apply',task.kind==='metadata'?'填入素材字段':(applyLabels[task.kind]||'应用候选结果')]);
        if(['preparing','queued','running','needs_attention'].includes(task.state))choices.push(['cancel','取消']);
        if(task.state==='needs_attention'&&task.recovery_safe&&task.attention_reason!=='web_timeout')choices.push(['inspect','检查原网页任务']);
        if(['error','cancelled'].includes(task.state)&&task.kind!=='revision_wait')choices.push(['retry','重新提交']);
        if(['error','cancelled'].includes(task.state)&&task.kind==='revision_wait'){const hint=document.createElement('small');hint.textContent='请回到对应分段重新提交修正，避免重复发送旧请求。';actions.append(hint);}
        if(['ready','applied','cancelled','error'].includes(task.state))choices.push(['delete','删除任务']);
        for(const [verb,label]of choices){const button=document.createElement('button');button.type='button';button.dataset.action=verb;button.textContent=label;button.onclick=async()=>{button.disabled=true;try{await action(task,verb);}finally{button.disabled=false;}};actions.append(button);}
        row.append(actions);fragment.append(row);
      }
      if(!tasks.length){const p=document.createElement('p');p.textContent='暂无后台任务';fragment.append(p);}
      else if(!visibleTasks.length){const p=document.createElement('p');p.className='text-operation-empty';p.textContent='当前分类暂无任务';fragment.append(p);}
      list.replaceChildren(fragment);
    }catch(e){message(e.message);}finally{busy=false;if(refreshPending){refreshPending=false;queueMicrotask(refresh);}}
  }
  const filters=root()?.querySelector('.text-operation-filters');
  if(filters)filters.addEventListener('click',event=>{
    const button=event.target.closest('[data-task-filter]');if(!button)return;
    taskFilter=button.dataset.taskFilter||'all';signature='';refresh();
  });
  async function videoCleanup(path,method){
    const response=await fetch('/api/web-video/tasks'+path,{method});
    const body=await response.json();if(!response.ok||!body.ok)throw new Error(body.message||'视频任务清理失败');return body;
  }
  async function clearFinishedTasks(clearButton){
    const count=tasksCache.filter(t=>['ready','applied','cancelled','error'].includes(t.state)).length;
    if(!count)return;
    if(!window.confirm(`清空所有已结束任务？这会同时删除待应用候选结果；排队、执行中和需要处理的任务会保留。此操作无法恢复。`))return;
    clearButton.disabled=true;
    try{const body=await api('/clear',{confirm:true});message(`已清空 ${body.deleted} 条已结束任务`);signature='';const box=root().querySelector('.text-operations-result');box.hidden=true;box.open=false;await refresh();}
    catch(error){message(error.message);clearButton.disabled=false;}
  }
  function bindClearButton(){
    const clearButton=root()?.querySelector('.text-operations-clear');
    if(!clearButton||clearButton.dataset.bound==='true')return;
    clearButton.dataset.bound='true';
    clearButton.addEventListener('click',()=>clearFinishedTasks(clearButton));
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
#text-operations{--ops-accent:var(--theme-accent,#2563eb);--ops-success:var(--theme-success-text,#15803d);--ops-warning:var(--theme-warning-text,#b45309);--ops-danger:var(--theme-danger-text,#b91c1c);position:fixed;right:16px;bottom:16px;z-index:1000;width:min(420px,calc(100vw - 24px));margin:0;padding:0;border:1px solid var(--theme-border-strong,var(--border,#e5e7eb));border-radius:16px;background:var(--theme-surface,#fff);color:var(--theme-text-body,var(--text-body,#374151));box-shadow:var(--theme-shadow-overlay,0 18px 44px rgba(15,23,42,.16));overflow:hidden;transition:box-shadow var(--theme-motion,170ms ease),border-color var(--theme-motion,170ms ease)}
#text-operations:hover{border-color:var(--theme-accent-border-strong,#93c5fd);box-shadow:0 12px 34px rgba(15,23,42,.19)}
#text-operations>.text-operations-summary{display:block;list-style:none;cursor:pointer;padding:14px 16px 13px;user-select:none}#text-operations>.text-operations-summary::-webkit-details-marker{display:none}#text-operations>.text-operations-summary:focus-visible{outline:3px solid var(--theme-focus,rgba(37,99,235,.2));outline-offset:-3px}
.text-operations-summary-top,.text-operations-brand{display:flex;align-items:center}.text-operations-summary-top{justify-content:space-between;gap:10px}.text-operations-brand{gap:10px}.text-operations-brand>span:last-child{display:grid;gap:1px}.text-operations-brand strong{font-size:13px;color:var(--theme-text-title,#0f172a);letter-spacing:.01em}.text-operations-brand small{font-size:11px;color:var(--theme-text-secondary,#64748b);font-weight:400}.text-operations-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:10px;background:var(--theme-accent-soft,#eff6ff);color:var(--ops-accent);font-size:17px}.text-operations-count{font-size:10px;line-height:1.5;color:var(--theme-text-secondary,#4b5563);background:var(--theme-surface-muted,#f1f5f9);border:1px solid var(--theme-border,#e5e7eb);padding:4px 8px;border-radius:999px;white-space:nowrap}
.text-operations-current{display:flex;align-items:center;gap:8px;margin-top:12px;min-width:0;font-size:12px}.text-operations-current-dot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--theme-success,#22c55e);box-shadow:0 0 0 3px var(--theme-success-soft,#f0fdf4)}.text-operations-current-dot.is-idle{background:var(--theme-text-disabled,#cbd5e1);box-shadow:0 0 0 3px var(--theme-surface-muted,#f1f5f9)}.text-operations-current-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;color:var(--theme-text-card,#1f2937)}.text-operations-current-percent{font-size:11px;font-weight:700;color:var(--ops-accent);font-variant-numeric:tabular-nums}.text-operations-current-progress{display:block;width:100%;height:5px;margin:8px 0 9px;border:0;border-radius:999px;overflow:hidden;accent-color:var(--ops-accent)}.text-operations-current-progress::-webkit-progress-bar{background:var(--theme-surface-muted,#f1f5f9);border-radius:999px}.text-operations-current-progress::-webkit-progress-value{background:var(--ops-accent);border-radius:999px;transition:width .25s ease}.text-operations-stats{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.text-operations-stats span{font-size:9px;white-space:nowrap;padding:3px 6px;border:1px solid var(--theme-border,#e5e7eb);border-radius:999px;color:var(--theme-text-secondary,#64748b);background:var(--theme-surface-subtle,#f9fafb)}.text-operations-stats b{color:var(--theme-text-title,#0f172a);margin-left:2px}.text-operations-stats [data-count=running] b{color:var(--ops-accent)}.text-operations-stats [data-count=ready] b,.text-operations-stats [data-count=applied] b{color:var(--ops-success)}.text-operations-stats [data-count=error] b{color:var(--ops-danger)}
#text-operations[open]{max-height:min(78vh,800px)}#text-operations[open]>.text-operations-summary{border-bottom:1px solid var(--theme-border,#e5e7eb);background:var(--theme-surface-subtle,#f9fafb)}.text-operations-body{display:flex;flex-direction:column;max-height:calc(min(78vh,800px) - 112px);padding:0 14px 12px}.text-operations-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 2px 8px}.text-operations-toolbar strong{font-size:13px;color:var(--theme-text-title,#0f172a)}.text-operations-toolbar p{margin:2px 0 0;font-size:11px;color:var(--theme-text-secondary,#64748b)}.text-operations-live{font-size:10px;color:var(--ops-success);background:var(--theme-success-soft,#f0fdf4);padding:4px 7px;border-radius:999px;white-space:nowrap}.text-operations-message:empty{display:none}.text-operations-message{font-size:12px;padding:8px 10px;margin:4px 0;border-radius:8px;background:var(--theme-accent-soft,#eff6ff);color:var(--theme-text-body,#374151)}
.text-operation-filters{display:flex;gap:6px;flex-wrap:wrap;padding:2px 0 8px}.text-operation-filters button{min-height:34px;padding:5px 9px;border:1px solid var(--theme-border,#e5e7eb);border-radius:999px;background:var(--theme-surface,#fff);color:var(--theme-text-secondary,#64748b);font-size:11px;font-weight:600;cursor:pointer;transition:background var(--theme-motion,170ms ease),border-color var(--theme-motion,170ms ease),color var(--theme-motion,170ms ease)}.text-operation-filters button:hover{border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}.text-operation-filters button.is-selected{background:var(--theme-accent-soft,#eff6ff);border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}.text-operation-filters button span{margin-left:3px;font-variant-numeric:tabular-nums;opacity:.78}.text-operation-filters button:focus-visible{outline:2px solid var(--ops-accent);outline-offset:2px}
.text-operations-list{overflow:auto;min-height:0;padding:2px 3px 4px 0;scrollbar-color:var(--theme-scrollbar-thumb,#cbd5e1) transparent}.text-operation-row{padding:12px;margin:7px 0;border:1px solid var(--theme-border,#e5e7eb);border-radius:12px;background:var(--theme-surface,#fff);box-shadow:var(--theme-shadow,0 1px 2px rgba(15,23,42,.035))}.text-operation-heading{display:flex;align-items:flex-start;gap:8px}.text-operation-heading>strong{flex:1;line-height:1.45;color:var(--theme-text-title,#0f172a);font-size:12px}.text-operation-kind{flex:none;padding:3px 7px;border-radius:999px;background:var(--theme-accent-soft,#eff6ff);color:var(--ops-accent);font-size:11px;font-weight:700;line-height:1.35}.category-creation .text-operation-kind{background:var(--theme-success-soft,#f0fdf4);color:var(--ops-success)}.category-prompt .text-operation-kind{background:var(--theme-accent-soft,#eff6ff);color:var(--ops-accent)}.category-structure .text-operation-kind{background:var(--theme-warning-soft,#fffbeb);color:var(--ops-warning)}.category-analysis .text-operation-kind{background:var(--theme-surface-muted,#f1f5f9);color:var(--theme-text-body,#374151)}.category-asset .text-operation-kind{background:var(--theme-info-soft,#eff6ff);color:var(--theme-info-text,#1d4ed8)}.text-operation-destination{margin:8px 0;padding:7px 9px;border-radius:8px;background:var(--theme-surface-subtle,#f9fafb);border:1px solid var(--theme-border,#e5e7eb)}.text-operation-destination p{margin:2px 0!important;color:var(--theme-text-secondary,#4b5563);font-size:11px!important}.text-operation-destination p:first-child{color:var(--theme-text-card,#1f2937);font-weight:600}.text-operation-row p{margin:7px 0;overflow-wrap:anywhere;font-size:11px;line-height:1.55;color:var(--theme-text-secondary,#4b5563)}.text-operation-row .text-operation-context{margin:5px 0 2px;color:var(--theme-text-card,#334155);font-weight:600}.text-operation-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.text-operation-row progress,.text-operations-result-content progress{display:block;width:100%;height:7px;margin:8px 0 10px;accent-color:var(--ops-accent)}.text-operation-result-empty,.text-operation-empty{padding:10px 4px;color:var(--theme-text-secondary,#64748b);font-size:11px}.text-operation-result-empty{font-size:12px}.text-operations-result{flex:none;margin-top:8px;border-top:1px solid var(--theme-border,#e5e7eb);padding-top:4px}.text-operations-result>summary{font-size:12px;color:var(--theme-text-secondary,#4b5563)}.text-operations-result-content{max-height:32vh;overflow:auto;padding:8px 2px;font-size:12px}.text-operation-timeline{margin:8px 0;padding-left:22px}.text-operation-timeline li{padding:3px 0}.text-operations-error{color:var(--ops-danger)!important;font-weight:600}.text-operation-actions button{min-height:34px;padding:6px 10px;border:1px solid var(--theme-border,#e5e7eb);border-radius:8px;background:var(--theme-surface,#fff);color:var(--theme-text-body,#374151);font-size:11px;font-weight:600;cursor:pointer;transition:background var(--theme-motion,170ms ease),border-color var(--theme-motion,170ms ease)}.text-operation-actions button:hover{background:var(--theme-accent-soft,#eff6ff);border-color:var(--theme-accent-border-strong,#93c5fd)}.text-operation-actions button:focus-visible{outline:2px solid var(--ops-accent);outline-offset:2px}
.text-operations-toolbar-actions{display:flex;align-items:center;gap:7px;flex:none}.text-operations-clear{min-height:32px;padding:5px 9px;border:1px solid var(--theme-danger-border,#fecaca);border-radius:8px;background:var(--theme-danger-soft,#fef2f2);color:var(--ops-danger);font-size:10px;font-weight:650;cursor:pointer}.text-operations-clear:hover:not(:disabled){background:var(--theme-danger-soft,#fef2f2);border-color:var(--theme-danger,#ef4444)}.text-operations-clear:disabled{opacity:.45;cursor:not-allowed}.text-operation-actions button[data-action=delete]{border-color:var(--theme-danger-border,#fecaca);color:var(--ops-danger)}.text-operation-actions button[data-action=delete]:hover{background:var(--theme-danger-soft,#fef2f2);border-color:var(--theme-danger,#ef4444)}
.text-operation-row{opacity:1;transform:translateY(0);transition:opacity 420ms ease,transform 420ms ease}.text-operation-row.is-expiring{opacity:0;transform:translateY(6px);pointer-events:none}.text-operation-apply-error{padding:8px 9px;border:1px solid var(--theme-danger-border,#fecaca);border-radius:8px;background:var(--theme-danger-soft,#fef2f2)}
@media(max-width:480px){#text-operations{right:8px;bottom:8px;width:calc(100vw - 16px);border-radius:13px}.text-operations-count{font-size:9px;padding:3px 6px}.text-operations-stats{gap:4px}.text-operations-stats span{padding:3px 5px;font-size:9px}.text-operations-body{padding-right:10px;padding-left:10px}}
@media(prefers-reduced-motion:reduce){.text-operation-row,.text-operation-filters button{transition:none}}
#text-operations-toast{position:fixed;top:14px;right:16px;z-index:1200;width:min(420px,calc(100vw - 28px));box-sizing:border-box;padding:12px 15px;border:1px solid var(--theme-accent-border-strong,#93c5fd);border-radius:12px;background:var(--theme-surface,#fff);color:var(--theme-text-title,#0f172a);box-shadow:var(--theme-shadow-overlay,0 12px 30px rgba(15,23,42,.18));font-size:14px;line-height:1.5}
.text-operation-preview{max-height:34vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:10px;border:1px solid var(--theme-border,#e5e7eb);border-radius:8px;background:var(--theme-surface-subtle,#f9fafb);font-size:12px;line-height:1.55}
.text-operation-target{outline:3px solid var(--theme-accent,#2563eb)!important;outline-offset:2px;border-radius:12px}
.text-operation-entry-state{display:block;width:max-content;max-width:100%;box-sizing:border-box;margin-top:4px;padding:3px 7px;border:1px solid var(--theme-border,#cbd5e1);border-radius:999px;background:var(--theme-surface,#fff);color:var(--theme-text-secondary,#475569);font-size:10px;line-height:1.35;white-space:normal;overflow-wrap:anywhere;pointer-events:none}
.text-operation-entry-state.state-queued,.text-operation-entry-state.state-running,.text-operation-entry-state.state-applying{border-color:var(--theme-accent-border-strong,#93c5fd);color:var(--ops-accent)}
.text-operation-entry-state.state-ready,.text-operation-entry-state.state-applied{border-color:var(--theme-success-border,#bbf7d0);color:var(--ops-success)}
.text-operation-entry-state.state-error,.text-operation-entry-state.state-needs_attention{border-color:var(--theme-danger-border,#fecaca);color:var(--ops-danger)}
.segment-title-block>.text-operation-entry-state{margin-top:6px}
.text-operation-actions button,.text-operation-filters button{min-height:44px}
.text-operation-row.is-expiring{opacity:1;transform:none;pointer-events:auto}
@media(prefers-reduced-motion:reduce){#text-operations-toast{scroll-behavior:auto}}
#text-operations[open] .text-operations-list{overscroll-behavior:contain;scroll-padding-bottom:16px}
'''
