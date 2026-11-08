"""All-segment scene assignment and durable preparation progress."""
SCENE_BATCH_JS = r'''
(()=>{
 const A=window.AICF;let dialog,view,batchId,polling=false;
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
 async function api(action,payload={}){
  const headers={'Content-Type':'application/json'};
  if(view?.runtime){headers['X-AICF-Runtime']=encodeURIComponent(view.runtime);headers['X-AICF-Runtime-Encoding']='uri';}
  const response=await fetch('/api/scene-batches/'+action,{method:'POST',headers,body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok||!data.ok)throw Error(data.message||'场景批次请求失败');return data;
 }
 function message(text){dialog.querySelector('.scene-batch-message').textContent=text;}
 function choices(select,value){
  select.replaceChildren();
  for(const [key,name] of [['__pending__','请选择'],['','独立生成'],...Object.entries(view.scenes).map(([k,s])=>[k,s.display_name||s.name||'未命名场景']),...Object.entries(view.newScenes)]){
   const option=el('option',name);option.value=key;select.appendChild(option);
  }select.value=value;
 }
 function renderInitials(scenes){
  const host=dialog.querySelector('.scene-batch-initials');host.replaceChildren();
  for(const [key,scene] of Object.entries(scenes)){
   const detail=el('details'),title=el('summary',(scene.display_name||scene.name||'未命名场景')+(scene.initial_pending?' · 待建立初态':' · 初态已建立'));detail.appendChild(title);
   const text=Object.entries(scene.initial_state||{}).map(([role,facts])=>(view.roles?.[role]||role)+'：'+[['position','位置'],['posture','姿势'],['present','在场']].map(([f,label])=>label+' '+(facts[f]?.value??'未知')).join('；')).join('\n');
   detail.appendChild(el('pre',text||'启动后由 LLM 从本组开场上下文提取，未知内容不猜测。'));host.appendChild(detail);
  }
 }
 function rowValues(){return [...dialog.querySelectorAll('tbody tr')].map(tr=>{
  const selected=tr.querySelector('input').checked,saved=view.rows.find(r=>r.segment_uid===tr.dataset.uid);
  return {uid:tr.dataset.uid,selected,
   scene:selected?tr.querySelector('.scene-batch-scene').value:saved.scene_context_id||'',
   mode:selected?tr.querySelector('.scene-batch-mode').value:saved.h3_state_mode==='inherit'?'inherit':'extract'};
 });}
 function showSources(){
  const rows=rowValues(),previous={};for(const tr of dialog.querySelectorAll('tbody tr')){
   const row=rows.find(r=>r.uid===tr.dataset.uid),scene=row.scene;
   tr.querySelector('.scene-batch-scene').disabled=!row.selected;
   tr.querySelector('.scene-batch-mode').disabled=!row.selected;
   tr.querySelector('.scene-batch-source').textContent=!row.selected?'—':scene==='__pending__'?'待分组':!scene?'独立生成':previous[scene]||'场景初态';
   if(!batchId)tr.querySelector('.scene-batch-progress').textContent=row.selected?'等待准备':'未勾选，已跳过';
   if(scene)previous[scene]=tr.dataset.part;
  }
  dialog.querySelector('.scene-batch-summary').textContent='本次仅处理勾选的 '+rows.filter(r=>r.selected).length+' 段；未勾选段完全跳过。承接只读取已保存状态，缺少状态时提示处理，不自动重做前段。';
 }
 const clockFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
 function formatClock(value){
  if(value==null)return '未记录';
  const p=Object.fromEntries(clockFormat.formatToParts(new Date(value*1000)).map(part=>[part.type,part.value]));
  return p.year+'-'+p.month+'-'+p.day+' '+p.hour+':'+p.minute+':'+p.second;
 }
 function formatElapsed(value){
  if(value==null)return '—';
  const seconds=Math.max(0,Math.floor(value)),h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;
  return (h?h+' 小时 ':'')+(h||m?m+' 分 ':'')+s+' 秒';
 }
 function renderTiming(tr,row={}){
  if(row.timing_state==='skipped'){
   tr.querySelector('.scene-batch-started').textContent='开始：—';
   tr.querySelector('.scene-batch-finished').textContent='结束：—';
   tr.querySelector('.scene-batch-elapsed').textContent='累计执行：—';return;
  }
  const waiting=!row.started_at&&['waiting','queued',undefined].includes(row.timing_state);
  tr.querySelector('.scene-batch-started').textContent='开始：'+(row.started_at?formatClock(row.started_at):waiting?'未开始':'未记录');
  tr.querySelector('.scene-batch-finished').textContent='结束：'+(row.finished_at?formatClock(row.finished_at):waiting?'—':['complete','error','cancelled','needs_attention'].includes(row.timing_state)?'未记录':'处理中');
  tr.querySelector('.scene-batch-elapsed').textContent='累计执行：'+formatElapsed(row.elapsed_seconds);
 }
 async function refreshBatch(){
  if(!batchId||polling||!dialog?.open)return;polling=true;
  const owner=view,id=batchId;
  try{const state=await api('status',{id});if(view!==owner||batchId!==id)return;
   message(state.phase+'：'+(state.message||''));if(state.scenes)renderInitials(state.scenes);
   for(const tr of dialog.querySelectorAll('tbody tr')){
    const row=state.progress_rows?.[tr.dataset.uid]||(tr.querySelector('input').checked?{}:{phase:'未勾选，已跳过',timing_state:'skipped'});
    tr.querySelector('.scene-batch-progress').textContent=row.phase||'等待';renderTiming(tr,row);
   }
   dialog.querySelector('.scene-batch-resume').hidden=state.state!=='error';
   dialog.querySelector('.scene-batch-start').disabled=['queued','running','waiting_children','needs_attention','preparing'].includes(state.state);
  }catch(e){message(e.message);}finally{polling=false;}
 }
 A.openSceneVideoBatch=async()=>{
  if(dialog?.open){dialog.focus();return;}
   view=null;batchId=null;const data=await api('catalog');view={...data,newScenes:{}};
  if(!data.rows.length)throw Error('请先生成分段提示词');
  dialog?.remove();dialog=el('dialog');dialog.className='scene-batch-dialog';
  dialog.innerHTML='<header><strong>连续场景分组与视频生成</strong><button type="button" class="scene-batch-close">关闭</button></header><p>全部分段按原顺序展示，仅保存和处理勾选段；未勾选段完全跳过。相同地点不同时间请建不同连续场景；承接只读取同组前段已保存末态，缺少或失效时提示处理。准备和进度由后台保存。使用已保存稿，未保存的编辑请先保存。整批沿用第一个勾选段已保存的工作流视频渠道。</p><div class="scene-batch-initials"></div><div class="scene-batch-tools"><label>新场景名称 <input class="scene-batch-name"></label><button type="button" class="scene-batch-create">添加连续场景</button><label>批量设置场景 <select class="scene-batch-group"></select></label><label>结束状态 <select class="scene-batch-default"><option value="inherit">沿用，无变化</option><option value="extract">提取状态（LLM）</option></select></label><button type="button" class="scene-batch-assign">应用到勾选段</button></div><div class="scene-batch-scroll"><table><thead><tr><th><input type="checkbox" class="scene-batch-all" aria-label="全选生成段"></th><th>分段</th><th>内容</th><th>连续场景</th><th>承接来源</th><th>结束状态处理</th><th>后台进度 / 执行计时</th></tr></thead><tbody></tbody></table></div><p class="scene-batch-summary"></p><p class="scene-batch-message" role="status" aria-live="polite"></p><footer><fieldset class="scene-batch-provider-picker"><legend>本次 LLM（可多选）</legend><div class="scene-batch-provider-options"></div><label><input type="checkbox" class="scene-batch-timing-api" checked> DeepSeek API（仅规划时长）</label><p>网页 LLM 用于其他准备步骤；API 仅在勾选时用于规划时长，模型为 deepseek-v4-pro。</p></fieldset><button type="button" class="scene-batch-start">保存分组并一键生成</button><button type="button" class="scene-batch-resume" hidden>继续失败批次</button></footer>';
  document.body.appendChild(dialog);
  const providerHost=dialog.querySelector('.scene-batch-provider-options');
  for(const [value,name] of [['inherit','跟随全局'],['chatgpt','ChatGPT'],['deepseek','DeepSeek'],['doubao','豆包']]){
   const label=el('label'),check=el('input');check.type='checkbox';check.value=value;check.checked=value==='inherit';
   check.onchange=()=>{
    if(!check.checked)return;
    for(const other of providerHost.querySelectorAll('input')){
     if(other!==check&&(['inherit','api'].includes(check.value)||['inherit','api'].includes(other.value)))other.checked=false;
    }
   };
   label.appendChild(check);label.appendChild(el('span',name));providerHost.appendChild(label);
  }
  const tbody=dialog.querySelector('tbody');
  for(const row of data.rows){
   const tr=el('tr');tr.dataset.uid=row.segment_uid;tr.dataset.part=row.part_id||String(row.segment_index);
   const check=el('input');check.type='checkbox';check.checked=row.selected;check.onchange=showSources;check.setAttribute('aria-label','生成 '+tr.dataset.part);
   const scene=el('select');scene.className='scene-batch-scene';choices(scene,row.scene_context_id||'__pending__');scene.onchange=showSources;scene.setAttribute('aria-label',tr.dataset.part+' 连续场景');
   const mode=el('select');mode.className='scene-batch-mode';for(const [v,t] of [['inherit','沿用，无变化'],['extract','提取状态（LLM）']]){const o=el('option',t);o.value=v;mode.appendChild(o);}mode.value=(row.planned_mode||row.h3_state_mode)==='inherit'?'inherit':'extract';mode.setAttribute('aria-label',tr.dataset.part+' 结束状态处理');
   const source=el('span');source.className='scene-batch-source';const progress=el('span');progress.className='scene-batch-progress';
   const progressHost=el('div');progressHost.appendChild(progress);
   const timing=el('div');timing.className='scene-batch-timing';
   timing.setAttribute('aria-label',tr.dataset.part+' 流程执行时间');
   timing.setAttribute('title','北京时间；累计后台步骤的执行时间，包含模型等待，不含排队、暂停及提交后的云端渲染。');
   for(const [className,text] of [['scene-batch-started','开始：未开始'],['scene-batch-finished','结束：—'],['scene-batch-elapsed','累计执行：—']]){
    const line=el('div',text);line.className=className;timing.appendChild(line);
   }progressHost.appendChild(timing);
   const content=el('div');content.className='scene-batch-content';
   content.appendChild(el('span',(row.title||'')+' · '+(row.duration_sec||'?')+' 秒'));
   const promptView=el('details');promptView.className='scene-batch-prompt-view';
   const promptToggle=el('summary','浏览提示词');promptToggle.setAttribute('aria-label','浏览 '+tr.dataset.part+' 已保存提示词');
   promptView.appendChild(promptToggle);
   promptView.appendChild(el('p','已保存提示词（打开分组页时的版本），仅供浏览。'));
   const promptText=el('pre',row.prompt||'该段尚未保存提示词，请先生成或保存分段提示词。');
   promptText.className='scene-batch-prompt-text';promptText.setAttribute('tabindex','0');
   promptText.setAttribute('aria-label',tr.dataset.part+' 提示词全文');
   promptView.appendChild(promptText);content.appendChild(promptView);
   for(const value of [check,tr.dataset.part,content,scene,source,mode,progressHost]){
    const td=el('td');if(typeof value==='string')td.textContent=value;else td.appendChild(value);tr.appendChild(td);
   }tbody.appendChild(tr);
  }
  choices(dialog.querySelector('.scene-batch-group'),'__pending__');showSources();renderInitials(data.scenes);
  dialog.querySelector('.scene-batch-all').onchange=e=>{for(const tr of tbody.children)tr.querySelector('input').checked=e.target.checked;showSources();};
  dialog.querySelector('.scene-batch-close').onclick=()=>dialog.close();
  dialog.querySelector('.scene-batch-create').onclick=()=>{
   const name=dialog.querySelector('.scene-batch-name').value.trim();if(!name){message('请填写场景名称');return;}
   const id='new:'+crypto.randomUUID();view.newScenes[id]=name;
   for(const select of dialog.querySelectorAll('.scene-batch-scene,.scene-batch-group'))choices(select,select.value);
   dialog.querySelector('.scene-batch-group').value=id;message('已添加到本次方案；勾选分段后应用场景，启动时才保存。');
  };
  dialog.querySelector('.scene-batch-assign').onclick=()=>{
   const group=dialog.querySelector('.scene-batch-group').value,mode=dialog.querySelector('.scene-batch-default').value;
   if(group==='__pending__'){message('请选择批量设置的场景');return;}
   for(const tr of tbody.children)if(tr.querySelector('input').checked){tr.querySelector('.scene-batch-scene').value=group;tr.querySelector('.scene-batch-mode').value=mode;}
   showSources();
  };
  dialog.querySelector('.scene-batch-start').onclick=async()=>{
   const button=dialog.querySelector('.scene-batch-start'),owner=view,modal=dialog;let started=false;button.disabled=true;
   try{
    const rows=rowValues();if(rows.some(r=>r.selected&&r.scene==='__pending__'))throw Error('请为勾选分段选择连续场景或明确选择独立生成');
    if(!rows.some(r=>r.selected))throw Error('请勾选要生成的视频段');
    const providers=[...providerHost.querySelectorAll('input')].filter(input=>input.checked).map(input=>input.value);
    if(!providers.length)throw Error('请至少勾选一个本次 LLM 服务，或选择跟随全局');
    const first=view.rows.find(r=>r.segment_uid===rows.find(r=>r.selected).uid);
    const channel=await A.chooseVideoChannel(first.segment_index);if(!channel||view!==owner||!modal.open)return;
    const task=await api('start',{revision:view.revision,rows,new_scenes:view.newScenes,backend:channel.id,
     providers,timing_api:dialog.querySelector('.scene-batch-timing-api').checked});
    batchId=task.id;started=true;try{localStorage.setItem('scene-video-batch:'+view.runtime,batchId);}catch(_){}
    await refreshBatch();
   }catch(e){message(e.message);}finally{if(!started)button.disabled=false;}
  };
  dialog.querySelector('.scene-batch-resume').onclick=async()=>{try{await api('resume',{id:batchId,modes:Object.fromEntries(rowValues().map(r=>[r.uid,r.mode]))});await refreshBatch();}catch(e){message(e.message);}};
  try{batchId=data.batch_id||localStorage.getItem('scene-video-batch:'+view.runtime);}catch(_){batchId=null;}
  dialog.showModal();if(batchId)await refreshBatch();
 };
 document.addEventListener('click',event=>{
  if(!event.target.closest?.('#render_video_btn'))return;
  event.preventDefault();event.stopImmediatePropagation();
  A.openSceneVideoBatch().catch(e=>alert(e.message));
 },true);
 setInterval(()=>{refreshBatch();},2500);
})();
'''

SCENE_BATCH_CSS = '''
.scene-batch-dialog{width:min(1400px,94vw);max-height:90vh;overflow:auto;padding:18px;color:var(--text-body,#334155);background:var(--surface,#fff);border:1px solid var(--border,#cbd5e1);border-radius:12px}
.scene-batch-dialog::backdrop{background:#0f172a80}
.scene-batch-dialog header,.scene-batch-dialog footer,.scene-batch-tools{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:12px}
.scene-batch-dialog header{justify-content:space-between}.scene-batch-dialog p{font-size:13px;line-height:1.6}
.scene-batch-dialog button,.scene-batch-dialog select,.scene-batch-dialog input:not([type=checkbox]){min-height:36px;padding:6px 10px;border:1px solid var(--border,#94a3b8);border-radius:6px;background:var(--surface,#fff);color:inherit}
.scene-batch-dialog button{cursor:pointer}.scene-batch-dialog button:disabled{opacity:.5;cursor:wait}.scene-batch-dialog :focus-visible{outline:2px solid #4f46e5;outline-offset:2px}
.scene-batch-start{background:#4f46e5!important;color:white!important}.scene-batch-scroll{overflow:auto}.scene-batch-dialog table{width:100%;border-collapse:collapse;font-size:13px}.scene-batch-dialog th,.scene-batch-dialog td{text-align:left;border-bottom:1px solid var(--border,#e2e8f0);padding:8px;vertical-align:top}.scene-batch-dialog th{white-space:nowrap}.scene-batch-message{overflow-wrap:anywhere}
.scene-batch-provider-picker{border:1px solid var(--border,#cbd5e1);border-radius:8px;padding:4px 10px 8px;margin:0;min-width:0}
.scene-batch-provider-picker legend{font-size:13px;padding:0 4px}
.scene-batch-provider-options{display:flex;flex-wrap:wrap;gap:4px 14px}
.scene-batch-provider-options label{display:inline-flex;align-items:center;gap:6px;min-height:44px;cursor:pointer;font-size:13px}
.scene-batch-provider-options input{accent-color:var(--primary,#4f46e5);width:16px;height:16px;margin:0;flex-shrink:0;cursor:pointer}
.scene-batch-provider-picker>p{margin:0;color:var(--text-secondary,#475569)}
.scene-batch-timing{margin-top:6px;min-width:210px;font-size:12px;line-height:1.65;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.scene-batch-content{min-width:240px;max-width:600px;overflow-wrap:anywhere}
.scene-batch-prompt-view{margin-top:6px}
.scene-batch-prompt-view>summary{width:fit-content;color:var(--primary,#4338ca);cursor:pointer;text-decoration:underline;text-underline-offset:3px;padding:4px 0}
.scene-batch-prompt-view>p{color:var(--text-secondary,#475569);margin:6px 0}
.scene-batch-prompt-text{max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.65;margin:0;padding:10px;border:1px solid var(--border,#cbd5e1);border-radius:6px;background:var(--surface-muted,#f8fafc);color:var(--text-body,#334155);user-select:text}
'''
