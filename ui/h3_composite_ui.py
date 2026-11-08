"""Fullscreen orchestration of existing H3 editor operations."""

COMPOSITE_JS = r'''
(() => {
 const A=window.AICF;
 const sleep=()=>new Promise(resolve=>setTimeout(resolve,1500));
 const historyKey=ov=>'aicf-composite-history:'+ov._editorRuntime+':'+ov._editorUid;
 const runKey=(ov,scene,mode,providers,timingApi=false)=>JSON.stringify([ov._editorRuntime,ov._editorUid,scene,mode,providers,!!timingApi]);
 const stepButtons={
  'LLM 提取首段并建立场景初态':'.h3-state-extract',
  '优化提示词':'.h3-continuity-start','保存当前稿':'.aicf-prompt-modal-save',
  '规划时长并保存最终稿':'.aicf-prompt-modal-replan',
  '登记沿用上段状态':'.h3-state-inherit','LLM 提取并保存结束状态':'.h3-state-extract',
  '提交视频生成':'.h3-config-actions button'
 };
 function lastFailure(record){
  return record?.lastFailure||(record?.state==='error'&&record.next<record.steps.length?
   {name:record.steps[record.next],index:record.next,total:record.steps.length}:null);
 }
 function markFailedButton(ov,failure){
  for(const button of ov.querySelectorAll('button[data-composite-failed]')){
   delete button.dataset.compositeFailed;button.title=button.dataset.compositeOriginalTitle||'';
   delete button.dataset.compositeOriginalTitle;
  }
  const selector=failure&&stepButtons[failure.name],button=selector&&ov.querySelector(selector);
  if(button){
   button.dataset.compositeOriginalTitle=button.title||'';button.dataset.compositeFailed='true';
   button.title='上次一键流程失败于第 '+(failure.index+1)+'/'+failure.total+' 步：'+failure.name+'。可单独点击此按钮处理，也可点击一键准备并生成。'+(button.title?' '+button.title:'');
  }
 }
 function saveProgress(ov,record){
  record.updatedAt=new Date().toISOString();
  const run=ov._h3CompositeCheckpoint;
  if(run&&record.state!=='complete')record.checkpoint=JSON.parse(JSON.stringify(run));
  else delete record.checkpoint;
  ov._h3CompositeHistory=JSON.parse(JSON.stringify(record));
  try{globalThis.localStorage?.setItem(historyKey(ov),JSON.stringify(record));}catch(_){}
  renderProgress(ov);
 }
 function stepNames(scene,mode,initializeScene){
  const steps=[];
  if(scene&&initializeScene)steps.push('LLM 提取首段并建立场景初态');
  if(scene)steps.push('优化提示词');
  steps.push('保存当前稿','规划时长并保存最终稿');
  if(scene)steps.push(mode==='inherit'?'登记沿用上段状态':'LLM 提取并保存结束状态');
  steps.push('提交视频生成');return steps;
 }
 function plannedSteps(ov){
  return stepNames(ov.querySelector('.h3-state-select').value,
   ov.querySelector('.h3-composite-mode').value,ov._h3CompositeSnapshot?.scene_initial_pending);
 }
 function restoredCheckpoint(ov,record){
  const run=record?.checkpoint;
  if(!run||run.version!==2||record.state==='complete'||run.runtime!==ov._editorRuntime||
   run.uid!==ov._editorUid||!['error','interrupted','running'].includes(record.state)||
   !Number.isInteger(run.next)||run.next!==record.next||run.next<0||run.next>=record.steps.length||
   typeof run.stamp!=='string'||typeof run.scene!=='string'||!['inherit','extract'].includes(run.mode)||
   !Array.isArray(run.providers)||!run.providers.every(p=>typeof p==='string')||
   run.key!==runKey(ov,run.scene,run.mode,run.providers,run.timing_api)||
   JSON.stringify(record.steps)!==JSON.stringify(stepNames(run.scene,run.mode,run.initializeScene)))return null;
  // A refreshed page cannot know whether an in-flight video request was accepted.
  if(record.state!=='error'&&run.next===record.steps.length-1)return null;
  return JSON.parse(JSON.stringify(run));
 }
 function renderProgress(ov){
  const host=ov.querySelector('.h3-composite-progress');if(!host)return;
  const current=ov._h3CompositeProgress,record=current||ov._h3CompositeHistory;
  const steps=record?.steps||plannedSteps(ov),next=record?.next||0;
  const historical=!current&&!!record;
  const failure=lastFailure(record);
  markFailedButton(ov,failure);
  const summary=host.querySelector('.h3-composite-progress-summary');
  const continuation=!!ov._h3CompositeCheckpoint&&record?.state!=='complete';
  summary.textContent=historical
   ?'上次记录：共 '+steps.length+' 步，已完成 '+next+' 步（'+new Date(record.updatedAt).toLocaleString('zh-CN',{hour12:false})+'）。'+
    (continuation?'点击后核对版本；未变化时从第 '+(next+1)+' 步继续。':
     record.state==='complete'?'本次从第 1 步重新准备（预计 '+plannedSteps(ov).length+' 步）。':
     '旧记录缺少可核验断点；可单独点击标红步骤处理，或从第 1 步重新准备。')
   :record?('本次共 '+steps.length+' 步，已完成 '+next+' 步。'+(record.startMessage||'')+
    (record.state==='error'&&continuation?' 再次点击将核对版本；未变化时从第 '+(next+1)+' 步重试，内容或配置变化则从第 1 步重新开始。':
     record.state==='complete'?' 视频任务已提交；再次点击会从头准备并再次提交。':''))
   :'本次预计 '+steps.length+' 步，将从第 1 步开始。';
  if(failure)summary.textContent+=' 上次失败：第 '+(failure.index+1)+'/'+failure.total+' 步“'+failure.name+'”。可单独点击标红按钮处理，也可点击一键准备并生成。';
  const list=host.querySelector('ol');list.replaceChildren();
  steps.forEach((name,index)=>{
   let state=index<next?'done':index===next&&record?.state==='running'?'running':index===next&&record?.state==='error'?'error':index===next&&record?.state==='interrupted'?'interrupted':'pending';
   if(state==='pending'&&failure?.name===name)state='previous-error';
   const row=document.createElement('li');row.dataset.state=state;
   row.textContent=(index+1)+'. '+name+' · '+({done:'已完成',running:'执行中',error:'失败，待重试','previous-error':'上次失败',interrupted:'已中断，需核对后台任务',pending:'待执行'}[state]);
   if(state==='running')row.setAttribute('aria-current','step');list.appendChild(row);
  });
 }
 async function request(ov,url){
  const response=await fetch(url,{headers:{'X-AICF-Runtime':encodeURIComponent(ov._editorRuntime),'X-AICF-Runtime-Encoding':'uri'}});
  const data=await (A.readJsonResponse?A.readJsonResponse(response,url):response.json());
  if(!response.ok||!data.ok)throw Error(data.message||'无法读取组合任务');
  return data.result;
 }
 function stamp(ov,snapshot){
  const incoming=Object.hasOwn(snapshot,'state_input_context')?snapshot.state_input_context:snapshot.state_context;
  return JSON.stringify({base:snapshot.base_revision,context:incoming?.input_revision,
   prompt:ov.querySelector('.aicf-prompt-modal-body').value,
   excerpt:ov.querySelector('#aicf-prompt-source-excerpt').value,
   duration:ov.querySelector('.review-duration')?Number(ov.querySelector('.review-duration').value):null,
   shotDraft:ov._shotEditor?.dirty?ov._shotEditor.documents:null,
   manifest:(A.h3ManifestSignature||JSON.stringify)(A.readH3Manifest(ov))});
 }
 function restartReason(previous,key,currentStamp){
  if(previous.key!==key)return '场景归属、结束状态处理或 LLM 配置';
  try{
   const before=JSON.parse(previous.stamp),after=JSON.parse(currentStamp);
   const labels={prompt:'提示词',excerpt:'剧情上下文',duration:'时长',shotDraft:'镜头草稿',
    manifest:'参考素材',context:'前序人物状态',base:'后台保存稿'};
   return Object.keys(labels).filter(k=>JSON.stringify(before[k])!==JSON.stringify(after[k])).map(k=>labels[k]).join('、');
  }catch(_){return '断点校验信息';}
 }
 A.refreshH3Composite=(ov,snapshot)=>{
  if(snapshot)ov._h3CompositeSnapshot=snapshot;
  const picker=ov.querySelector('.h3-composite-mode'),button=ov.querySelector('.h3-composite-run');
  if(!picker||!button)return;
  const scene=ov.querySelector('.h3-state-select').value;
  const first=!!scene&&ov._h3CompositeSnapshot?.scene_has_predecessor===false;
  const inherit=picker.querySelector('option[value="inherit"]');
  inherit.disabled=false;inherit.title='无上段时沿用场景初态；确认本段状态无变化';
  picker.disabled=!scene||!!ov._h3CompositeRun||!!ov._h3SceneConfigBusy;
  button.disabled=!ov._h3CompositeSnapshot||!!A.archiveReadOnly||!!ov._h3CompositeRun||!!ov._h3SceneConfigBusy;
  const initialization=first&&ov._h3CompositeSnapshot?.scene_initial_pending?'首次处理先由 LLM 建立场景初态 → ':'';
  ov.querySelector('.h3-composite-hint').textContent=!scene
   ?'未加入场景：保存 → 规划时长并保存 → 生成视频'
   :initialization+'优化并保存 → 规划时长并保存 → '+(picker.value==='inherit'?'沿用上段（确认无变化）':'重新 LLM 提取结束状态')+' → 生成视频';
  if(!ov._h3CompositeRun)button.textContent=ov._h3CompositeCheckpoint?'重试一键准备并生成':ov._h3CompositeHistory?'重新准备并生成':'一键准备并生成';
  renderProgress(ov);
 };
 A.installH3Composite=(ov,actions)=>{
  ov._h3CompositeActions=actions;
  delete ov._h3CompositeProgress;delete ov._h3CompositeHistory;delete ov._h3CompositeCheckpoint;
  try{
   const record=JSON.parse(globalThis.localStorage?.getItem(historyKey(ov))||'null');
   if(Array.isArray(record?.steps)&&record.steps.every(s=>typeof s==='string')&&Number.isInteger(record.next)&&record.next>=0&&record.next<=record.steps.length)ov._h3CompositeHistory=record;
  }catch(_){}
  const restored=restoredCheckpoint(ov,ov._h3CompositeHistory);
  if(restored){ov._h3CompositeCheckpoint=restored;ov.querySelector('.h3-composite-mode').value=restored.mode;}
  const status=ov.querySelector('.h3-composite-status');
  if(status?.parentElement&&!ov.querySelector('.h3-composite-progress')){
   const box=document.createElement('section');box.className='h3-composite-progress';box.setAttribute('aria-label','一键准备生成步骤');
   box.innerHTML='<p class="h3-composite-progress-summary" role="status" aria-live="polite"></p><ol></ol>';status.before(box);
  }
  const picker=ov.querySelector('.h3-composite-mode');
  picker.onchange=()=>{
   delete ov._h3CompositeCheckpoint;delete ov._h3CompositeProgress;
   if(ov._h3CompositeHistory)saveProgress(ov,ov._h3CompositeHistory);
   A.refreshH3Composite(ov);
  };
  ov.querySelector('.h3-composite-run').onclick=()=>A.runH3Composite(ov);
  A.refreshH3Composite(ov);
 };
 A.cancelH3Composite=ov=>{
  const active=ov._h3CompositeRun;if(!active)return;
  if(ov._h3CompositeProgress?.state==='running'){
   ov._h3CompositeProgress.state='interrupted';saveProgress(ov,ov._h3CompositeProgress);
  }
  active.cancelled=true;active.release?.();delete ov._h3CompositeRun;
 };
 A.runH3Composite=async ov=>{
  if(ov._h3CompositeRun)return;
  const actions=ov._h3CompositeActions,session=ov.dataset.h3Session,uid=ov._editorUid;
  const runtime=String(A.promptRuntimeRevision||0),scene=ov.querySelector('.h3-state-select').value;
  const mode=ov.querySelector('.h3-composite-mode').value;
  const button=ov.querySelector('.h3-composite-run'),line=ov.querySelector('.h3-composite-status');
  const active={};ov._h3CompositeRun=active;
  const valid=()=>!active.cancelled&&ov.dataset.h3Session===session&&ov._editorUid===uid&&
   String(A.promptRuntimeRevision||0)===runtime&&ov.classList.contains('open');
  const guard=()=>{
   if(!valid())throw Error('项目或编辑分段已切换，组合操作已停止；已完成的步骤已保留');
   if(A.archiveReadOnly)throw Error('当前分段只读，不能执行组合操作');
   if(ov.querySelector('.h3-state-select').value!==scene)throw Error('场景归属已变化，组合操作已停止');
  };
  // Native inert blocks user edits without changing the disabled flags read by
  // existing operations. A live status in the header remains accessible.
  const locked=[ov.querySelector('.aicf-prompt-modal-content'),
   ...ov.querySelectorAll('.aicf-prompt-modal-save,.aicf-prompt-modal-replan,.aicf-prompt-modal-force-save')]
   .filter(Boolean).map(node=>[node,node.inert]);
  let live,stepName='检查输入',completed=false,display;
  let released=false;
  active.release=()=>{if(released)return;released=true;locked.forEach(([node,inert])=>{node.inert=inert;});live?.remove();};
  const progress=text=>{if(!valid())return;line.textContent=text;if(live)live.textContent=text;};
  try{
   guard();
   line.classList.toggle('is-error',false);
   if(ov._editorBusy||ov._operationPending||ov._promptTaskBusy||ov._sceneStateBusy||ov._h3SceneConfigBusy)
    throw Error('当前编辑器还有操作正在执行，请完成后再启动组合处理');
   if(!actions)throw Error('组合操作尚未加载，请重新打开编辑器');
   const options=A.readPromptReplanOptions(ov);
   if(scene&&!options.providers.length)throw Error('一键准备的优化和状态提取还需要勾选网页 LLM；API 仅用于规划时长');
   active.providers=options.providers.slice();
   const fresh=await actions.snapshot();guard();
   A.refreshH3Composite(ov,fresh);
   if((fresh.context_id||'')!==scene)throw Error('场景归属尚未保存，请等待保存完成');
   if(scene&&fresh.scene_initial_pending&&fresh.scene_has_predecessor){
    const first=fresh.scene_first_segment;
    const name=first?.part_id||(first?.segment_index?'第 '+first.segment_index+' 段':'本场景首段');
    throw Error('场景初态尚未建立，请先打开'+name+'执行一键准备并生成，或提取其状态／校准初态');
   }
   locked.forEach(([node])=>{node.inert=true;});
   button.disabled=true;button.setAttribute('aria-busy','true');
   const heading=ov.querySelector('.aicf-prompt-modal-context');
   if(heading){live=document.createElement('span');live.className='h3-composite-live';live.setAttribute('role','status');live.setAttribute('aria-live','polite');heading.appendChild(live);}
   const key=runKey(ov,scene,mode,options.providers,options.timing_api);
   const previous=ov._h3CompositeCheckpoint,currentStamp=stamp(ov,fresh);
   const run=previous?.key===key&&previous.stamp===currentStamp?previous:
    {version:2,key,runtime:ov._editorRuntime,uid,scene,mode,providers:options.providers.slice(),timing_api:!!options.timing_api,
     next:0,stamp:currentStamp,initializeScene:!!(scene&&fresh.scene_initial_pending)};
   ov._h3CompositeCheckpoint=run;
   async function retime(){
    let id=run.retimeId||ov._sourceReplanPending?.id;
    if(!id){
     const result=await A.runSavedPromptAction(ov,()=>A.savePromptSourceExcerpt(true,options));
     guard();id=result?.operation_id;
     if(!id)throw Error('未取得时长规划任务，尚未提交视频');
     run.retimeId=id;
    }
    while(true){
     guard();const result=await request(ov,'/api/web-llm/operations/'+encodeURIComponent(id));guard();
     if(result.state==='applied')break;
     if(!['preparing','queued','running','waiting_children','applying','ready'].includes(result.state)||
        (result.state==='ready'&&(result.apply_error||result.auto_apply_pending===false))){
      delete run.retimeId;
      if(ov._sourceReplanPending?.id===id){
       await ov._syncSourceReplan?.();guard();
       if(ov._sourceReplanPending?.id===id)delete ov._sourceReplanPending;
      }
      throw Error(result.message||result.apply_error||'时长规划未成功保存，请在任务面板处理');
     }
     progress('正在规划时长并保存：'+(result.phase||result.state));await sleep();
    }
    if(!ov._syncSourceReplan)throw Error('时长结果同步尚未加载，请重新打开本段');
    await ov._syncSourceReplan();guard();
    if(ov._sourceReplanPending)throw Error('时长结果尚未同步到编辑器，请稍后重试');
    const saved=await actions.snapshot();guard();
    if(saved.draft?.prompt!==ov.querySelector('.aicf-prompt-modal-body').value)
     throw Error('时长结果与编辑稿不一致，请核对后继续');
    delete run.retimeId;
   }
   const steps=[];
   if(run.initializeScene)steps.push(['LLM 提取首段并建立场景初态',async()=>{
    await A.savePromptBeforeTask(ov);guard();
    await actions.register('extract');guard();
    const initialized=await actions.snapshot();guard();
    if(initialized.scene_initial_pending||!initialized.state_status?.ready||initialized.state_status.mode!=='extract')
     throw Error(initialized.state_status?.detail||initialized.state_status?.message||'场景初态尚未成功保存');
    A.refreshH3Composite(ov,initialized);
   }]);
   if(scene)steps.push(['优化提示词',actions.optimize]);
   steps.push(['保存当前稿',()=>A.savePromptBeforeTask(ov)],['规划时长并保存最终稿',retime]);
   if(scene)steps.push([mode==='inherit'?'登记沿用上段状态':'LLM 提取并保存结束状态',async()=>{
    await actions.register(mode);guard();
    const state=(await actions.snapshot()).state_status;guard();
    if(!state?.ready||state.mode!==(mode==='inherit'?'inherit':'extract'))throw Error(state?.detail||state?.message||'结束状态尚未保存');
   }]);
   steps.push(['提交视频生成',async()=>{
    guard();const result=await A.regenerateH3Segment(Number(ov.dataset.segmentIndex),button,
     {prepared:true,throwOnFailure:true,guard,runtime:ov._editorRuntime});
    if(!result)throw Error('视频生成尚未提交');return result;
   }]);
   const resuming=run===previous;
   display={steps:steps.map(step=>step[0]),next:run.next,state:'running',
    lastFailure:lastFailure(ov._h3CompositeProgress||ov._h3CompositeHistory),
    startMessage:resuming?'本次从第 '+(run.next+1)+' 步继续，前 '+run.next+' 步已完成。':
     previous?'输入或配置已变化（'+restartReason(previous,key,currentStamp)+'），本次从第 1 步重新开始。':'本次从第 1 步开始。'};
   ov._h3CompositeProgress=display;saveProgress(ov,display);
   for(let i=run.next;i<steps.length;i++){
    guard();stepName=steps[i][0];progress('步骤 '+(i+1)+'/'+steps.length+'：'+stepName+'…');
    await steps[i][1]();guard();
    run.next=i+1;display.next=run.next;
    if(display.lastFailure?.name===stepName)delete display.lastFailure;
    if(i<steps.length-1){const saved=await actions.snapshot();guard();run.stamp=stamp(ov,saved);}
    saveProgress(ov,display);
   }
   display.state='complete';delete display.lastFailure;saveProgress(ov,display);
   delete ov._h3CompositeCheckpoint;progress('准备完成，视频生成任务已提交。');
   completed=true;
  }catch(error){
   if(display&&valid()){
    display.state='error';display.lastFailure={name:stepName,index:display.next,total:display.steps.length,message:error.message};
    saveProgress(ov,display);
   }
   progress('已停止在“'+stepName+'”：'+error.message+'。已完成步骤保留，可处理后重试。');
   line?.classList.toggle('is-error',true);
  }finally{
   active.release();
   if(ov._h3CompositeRun===active)delete ov._h3CompositeRun;
   if(valid()){
    button.textContent='一键准备并生成';button.setAttribute('aria-busy','false');A.refreshH3Composite(ov);
    if(completed)A.closePromptModal();
   }
  }
 };
})();
'''
