"""Compound H3 content operations. Nothing is saved until the existing Save action."""
EDITOR_DRAFT_JS = r'''
(() => {
 const A=window.AICF, clone=v=>JSON.parse(JSON.stringify(v));
 async function api(ov,action,payload){
  const session=ov.dataset.h3Session,uid=ov._editorUid;
  const res=await fetch('/api/segment/editor/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-AICF-Runtime':encodeURIComponent(ov._editorRuntime),'X-AICF-Runtime-Encoding':'uri'},body:JSON.stringify(payload)});
  const r=await (A.readJsonResponse?A.readJsonResponse(res,'/api/segment/editor/'+action):res.json());if(!res.ok||!r.ok)throw Object.assign(Error(r.message||'编辑失败'),{code:r.code,details:r.details});
  if(r.optimization_status&&ov.dataset.h3Session===session&&ov._editorUid===uid)A.renderH3OptimizationHistory(ov,r.optimization_status);
  return r;
 }
 A.renderH3OptimizationHistory=(ov,value)=>{
  if(value)ov._optimizationStatus=value;
  const data=ov._optimizationStatus||{},line=ov.querySelector('.h3-optimization-history');if(!line)return;
  const time=value=>{const date=new Date(value);return Number.isNaN(date.getTime())?'时间未知':date.toLocaleString('zh-CN',{hour12:false});};
  if(!data.known){line.textContent='人物状态优化：历史未知（旧稿没有点击记录，无法确认是否优化过）。';return;}
  const attempt=data.last_attempt||{};
  const stages={clicked:'已点击，尚未提交生成',submitted:'已提交，正在生成候选',generated:'候选已生成，尚未应用',applied:'已应用到当前稿，尚未保存',saved:'已应用并保存',undone:'已撤销应用',failed:'本次未完成',unknown:'生成结果未知',content_saved:'当前文字已保存，未确认优化状态'};
  let text='人物状态优化：'+(stages[attempt.stage]||'已有操作记录')+'。最近点击：'+time(attempt.clicked_at)+'。';
  if(attempt.stage==='failed'&&attempt.message)text+='原因：'+attempt.message+'。';
  if(data.last_saved){
   text+='最近应用并保存：'+time(data.last_saved.saved_at)+'。';
   if(!data.current_matches_saved)text+='当前保存稿此后有修改，历史优化记录仍保留。';
   const saved=ov._editorSaved?.draft;
   if(saved&&(ov.querySelector('.aicf-prompt-modal-body').value!==saved.prompt||
    (A.h3ManifestSignature||JSON.stringify)(A.readH3Manifest(ov))!==(A.h3ManifestSignature||JSON.stringify)(saved.asset_manifest)))text+='编辑区另有未保存修改。';
  }else text+='尚无应用并保存的记录。';
  line.textContent=text;
 };
 function optimizationStage(ov,stage){
  if(!ov._optimizationStatus?.last_attempt)return;
  ov._optimizationStatus.last_attempt.stage=stage;A.renderH3OptimizationHistory(ov);
 }
 function recordApplication(ov,stage,attemptId){
  if(!attemptId)return;
  const session=ov.dataset.h3Session;
  if(ov._optimizationStatus?.last_attempt?.id===attemptId)optimizationStage(ov,stage);
  return api(ov,'optimization-event',{segment_uid:ov._editorUid,stage,attempt_id:attemptId}).catch(()=>{
   const line=ov.querySelector('.h3-optimization-history');if(ov.dataset.h3Session===session&&line)line.textContent+='操作记录同步失败，请重新打开后核对。';
  });
 }
 A.recordH3OptimizationContentSave=(ov,attemptId)=>recordApplication(ov,'content_saved',attemptId);
 function capture(ov){
  const saved=ov._editorDraft?.draft||ov._editorSaved.draft;
  const prompt=ov.querySelector('.aicf-prompt-modal-body').value;
  return {...clone(saved),prompt,
   duration_sec:prompt===saved.prompt?saved.duration_sec:(A.h3PromptDuration(prompt)||saved.duration_sec),
   scene_context_id:ov.querySelector('.h3-state-select')?.value||null,state_revision:ov._stateContext?.input_revision,asset_manifest:A.readH3Manifest(ov),source_excerpt:ov.querySelector('#aicf-prompt-source-excerpt').value};
 }
 function signature(d){return JSON.stringify(d);}
 function worldText(ov,world){
  const names=Object.fromEntries((ov._editorSaved?.scene_roles||[]).map(role=>[role.id,role.name]));
  for(const e of ov.querySelectorAll('.h3-scene-role'))names[e.value]=e.dataset.roleName||names[e.value]||e.value;
  const fields={position:'位置',posture:'姿势',present:'在场'};
  const display=value=>Object.entries(names).reduce((text,[id,name])=>text.split(id).join(name),String(value));
  const rows=Object.entries(world||{}).map(([id,facts])=>(names[id]||id)+'：'+Object.entries(fields).map(([key,label])=>{const fact=facts[key]||{},value=fact.value,relative=fact.relative_to_role_id;return label+' '+(value==null?'未知':value===true?'是':value===false?'否':display(value))+(relative?'（相对 '+(names[relative]||relative)+'）':'');}).join('；'));
  return rows.length?rows.join('\n'):'尚无人物状态记录。';
 }
 function sceneText(ov,data){
  const record=data?.[ov._editorUid]||data||{};
  if(record.initializing)return '场景初态尚未建立；可提取本场景首段状态或手动校准。';
  return worldText(ov,record.end_state||record.input_state);
 }

 A.h3FramingRows=record=>{
  const world=clone(record?.input_state||{});
  const count=value=>Object.values(value).filter(facts=>facts.present?.value===true).length;
  const unknown=value=>Object.values(value).filter(facts=>facts.present?.value==null).length;
  return (record?.shots||[]).map((shot,index)=>{
   const before=count(shot.state_before||world),beforeUnknown=unknown(shot.state_before||world);
   for(const event of shot.world_events||[])world[event.role_id]={...(world[event.role_id]||{}),...(event.facts||{})};
   const coverage=shot.role_coverage||[];
   return {index:index+1,shot_uid:shot.shot_uid,before,beforeUnknown,after:count(shot.state_after||world),afterUnknown:unknown(shot.state_after||world),
    visible:coverage.filter(role=>['visible','partial'].includes(role.visibility)).length,coverage};
  });
 };
 A.renderH3SceneFraming=(ov,record,stage='saved',status)=>{
  const host=ov.querySelector('.h3-framing-results');if(!host)return;
  host.replaceChildren();
  const line=document.createElement('p');line.className='h3-framing-status';line.setAttribute?.('role','status');
  const labels={candidate:'未应用候选',applied:'当前稿（尚未保存）',saved:'已保存稿'};
  const prefix=stage==='saved'&&record&&status?.ready===false?'历史取景安排（当前稿待核对）':labels[stage]||labels.saved;
  const message=record?(status?.message||'逐镜全员取景安排；这是文字计划，不代表成片已验证。'):'尚无全员取景核对记录。';
  line.textContent=prefix+'：'+message;host.appendChild(line);
  ov._framingView={record,stage,status,message,prefix,line,
   prompt:record?.source_prompt||(stage==='saved'?ov._editorSaved?.draft?.prompt:stage==='candidate'?ov._editorCandidate?.draft?.prompt:ov._editorDraft?.draft?.prompt)};
  if(!record)return;
  const names={...(record.role_names||{}),...Object.fromEntries((ov._editorSaved?.scene_roles||[]).map(role=>[role.id,role.name]))};
  const readable=value=>Object.entries(names).reduce((text,[id,name])=>text.replace(new RegExp('(?<![A-Za-z0-9_])'+id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?![A-Za-z0-9_])','g'),()=>name),String(value));
  const visibility={visible:'入画',partial:'局部入画',out_of_frame:'画外',occluded:'完全遮挡',not_present:'不在场'};
  for(const shot of A.h3FramingRows(record)){
   const section=document.createElement('section');section.className='h3-framing-shot';
   const heading=document.createElement('p');heading.className='h3-framing-shot-title';
   heading.textContent='Shot '+shot.index+' · 开场在场 '+shot.before+' 人'+(shot.beforeUnknown?'（另 '+shot.beforeUnknown+' 人状态未知）':'')+' → 镜末 '+shot.after+' 人'+(shot.afterUnknown?'（另 '+shot.afterUnknown+' 人状态未知）':'')+' · 整镜曾入画 '+shot.visible+' 人（不是同帧人数）';
   section.appendChild(heading);
   const list=document.createElement('dl');list.className='h3-framing-roles';
   for(const role of shot.coverage){
    const name=document.createElement('dt');name.textContent=(names[role.role_id]||role.role_id)+' · '+(visibility[role.visibility]||'待核对');
    const reason=document.createElement('dd');reason.textContent=readable(role.reason||'未提供取景依据');
    list.appendChild(name);list.appendChild(reason);
   }
   section.appendChild(list);host.appendChild(section);
  }
 };
 A.refreshH3FramingDirty=ov=>{
  const view=ov._framingView;if(!view?.record||!view.line)return;
  const changed=view.prompt!=null&&ov.querySelector('.aicf-prompt-modal-body').value!==view.prompt;
  view.line.textContent=view.prefix+'：'+(changed?'编辑区文字已修改，以上取景安排需重新核对。':view.message);
 };

 function note(ov,text){ov.querySelector('.h3-continuity-status').textContent=text;}
 function setBusy(ov,busy){
  ov._editorBusy=busy;const button=ov.querySelector('.h3-continuity-start');
  const restore=ov.querySelector('[data-action="restore-baseline"]');
  if(restore)restore.disabled=busy||!!ov._promptTaskBusy||!!A.archiveReadOnly;
  if(button){button.disabled=busy||!!ov._sceneStateBusy;button.setAttribute?.('aria-busy',String(busy||!!ov._sceneStateBusy));}
  for(const selector of ['.h3-state-inherit','.h3-state-extract','.h3-continuity-apply','.h3-continuity-undo','.h3-state-create','.h3-state-calibrate','.h3-state-reload','.h3-state-select']){const action=ov.querySelector(selector);if(action)action.disabled=busy||!!ov._sceneStateBusy||!!ov._promptTaskBusy;}
 }
 function stateSummary(ov,result){
  const status=result.state_status;
  const line=ov.querySelector('.h3-state-summary');
  if(line)line.textContent='当前状态：'+(status?.message||'未设置状态');
 }
 A.updateH3StateSummary=stateSummary;
 A.syncH3EditorBaseline=(ov,result)=>{
  if(!ov._editorDraft){
   ov._editorSaved={...ov._editorSaved,...result};
   if(!Object.hasOwn(result,'state_input_context')&&Object.hasOwn(result,'state_context'))ov._editorSaved.state_input_context=result.state_context;
  }
  if(Object.hasOwn(result,'state_context'))ov._stateContext=result.state_context;
  if(result.state_status)stateSummary(ov,result);
  if(result.optimization_status)A.renderH3OptimizationHistory(ov,result.optimization_status);
  if(result.scene_contexts)ov._syncSceneCalibration?.(result);
  if(!ov._editorDraft&&(Object.hasOwn(result,'scene_framing')||result.draft?.scene_framing))
   A.renderH3SceneFraming(ov,result.scene_framing||result.draft?.scene_framing,'saved',result.framing_status);
  const list=ov.querySelector('.h3-asset-list');
  if(!ov._editorDraft&&list&&result.draft?.reference_revision)list.dataset.baseRevision=result.draft.reference_revision;
  const excerpt=ov.querySelector('#aicf-prompt-source-excerpt');
  if(!ov._editorDraft&&excerpt&&result.draft)excerpt.dataset.savedValue=result.draft.source_excerpt||'';
  if(result.state_context||result.state_message||result.state_status){const preview=ov.querySelector('.h3-scene-preview');if(preview)preview.textContent=contextMessage(ov,result);}
 };
 A.refreshH3EditorBaseline=async(ov,verify=false)=>{
  if(!ov?._editorUid)return;
  const session=ov.dataset.h3Session,uid=ov._editorUid,runtime=ov._editorRuntime;
  const fresh=await api(ov,'snapshot',{segment_uid:uid});
  if(ov.dataset.h3Session!==session||ov._editorUid!==uid||ov._editorRuntime!==runtime)return;
  if(verify&&ov._editorSaved&&fresh.base_revision!==ov._editorSaved.base_revision){
   throw Error('后台内容已变化，当前编辑稿已保留。请核对后台最新稿后合并，或使用强制保存明确采用当前稿。');
  }
  // An uncommitted operation must keep the baseline it was generated against.
  A.syncH3EditorBaseline(ov,fresh);
  return fresh;
 };
 async function stateAction(ov,action,throwOnFailure=false){
  const actionSession=ov.dataset.h3Session,uid=ov._editorUid;
  try{
   const providers=action==='extract-state'?A.readPageLLMProviders(ov):null;
   await prepare(ov);
   if(ov._sceneStateBusy)throw Error('本段状态正在提取，请等待完成');
   if(ov._editorDraft){if(!await A.saveH3EditorDraft(ov))throw Error('当前稿未保存，未执行状态操作');}
   else if(A.saveH3GenerationConfig){if(!await A.saveH3GenerationConfig(ov.dataset.segmentIndex,null,ov,false,false))throw Error('当前稿未保存，未执行状态操作');}
   if(ov.dataset.h3Session!==actionSession||ov._editorUid!==uid)return;
   setBusy(ov,true);
   const session=ov.dataset.h3Session;
   const result=await api(ov,action,{segment_uid:uid,...(providers?{providers}:{})});
   if(ov.dataset.h3Session!==session)return;
   if(result.state_task){const pending=watchState(ov,result.state_task);if(throwOnFailure)await pending;return;}
   const fresh=await api(ov,'snapshot',{segment_uid:ov._editorUid});
   if(ov.dataset.h3Session!==session)return;
   A.syncH3EditorBaseline(ov,fresh);
   ov.querySelector('.h3-scene-preview').textContent=contextMessage(ov,fresh);
   if(!fresh.state_status?.ready||fresh.state_status.mode!=='inherit')throw Error(fresh.state_status?.detail||fresh.state_status?.message||'沿用状态尚未确认，请重试');
   note(ov,(fresh.state_status.previous_segment_uid===null?'沿用场景初态':'沿用上段状态')+'已保存；本段状态变化为空，不调用 LLM。');
  }catch(e){if(ov.dataset.h3Session===actionSession)note(ov,(action==='inherit-state'?'沿用上段未完成：':'')+e.message);if(throwOnFailure)throw e;}finally{if(ov.dataset.h3Session===actionSession)setBusy(ov,false);}
 }
 function watchState(ov,task){
  if(!task?.id||task.state==='applied'){ov._sceneStateTask=null;ov._sceneStateBusy=false;ov._sceneStatePending=Promise.resolve();setBusy(ov,!!ov._editorBusy);return ov._sceneStatePending;}
  if(ov._sceneStateTask===task.id&&ov._sceneStatePending)return ov._sceneStatePending;
  const session=ov.dataset.h3Session;ov._sceneStateTask=task.id;ov._sceneStateBusy=true;setBusy(ov,!!ov._editorBusy);
  ov._sceneStatePending=(async()=>{
   const line=ov.querySelector('.h3-state-summary');if(line)line.textContent='当前状态：正在提取本段状态…';
   note(ov,'正在提取本段人物状态，完成后独立保存；不会提取其他分段。');
   const started=Date.now();
   let result;
   do{await new Promise(resolve=>setTimeout(resolve,1500));if(ov.dataset.h3Session!==session||ov._sceneStateTask!==task.id)return;
    result=await api(ov,'state-result',{operation_id:task.id});
    if(ov.dataset.h3Session!==session||ov._sceneStateTask!==task.id)return;
    const web=result.web_task,provider={doubao:'豆包',deepseek:'DeepSeek',chatgpt:'ChatGPT'}[web?.provider]||'LLM';
    let phase={preparing:'正在准备提取任务',queued:'提取任务已排队',running:'正在提取本段状态',waiting_children:'等待提取子任务',applying:'正在保存本段状态'}[result.state];
    if(result.state==='running'&&web){
     if(web.status==='queued')phase='等待 '+provider+' 领取提取任务';
     else if(['claimed','waiting'].includes(web.status))phase='等待 '+provider+' 回复';
     else if(web.status==='complete')phase=provider+' 已返回，正在校验结果';
    }
    if(phase&&line)line.textContent='当前状态：'+phase+'（已等待 '+Math.floor((Date.now()-started)/1000)+' 秒）';
   }while(['preparing','queued','running','waiting_children','applying'].includes(result.state));
   if(result.state!=='applied')throw Object.assign(Error(result.message||'人物状态提取未完成'),{state:result.state});
   if(ov.dataset.h3Session!==session||ov._sceneStateTask!==task.id)return;
   const fresh=await api(ov,'snapshot',{segment_uid:ov._editorUid});
   if(ov.dataset.h3Session!==session||ov._sceneStateTask!==task.id)return;
   A.syncH3EditorBaseline(ov,fresh);
   if(!fresh.state_status&&line)line.textContent='当前状态：本段状态已提取 · 已保存';
   const selected=ov.querySelector('.h3-state-select')?.selectedOptions?.[0];if(selected)selected.textContent=selected.textContent.replace('（待自动初始化）','');
   ov.querySelector('.h3-scene-preview').textContent=contextMessage(ov,fresh);
   const retry=ov.querySelector('.h3-state-retry');if(retry)retry.hidden=true;
   note(ov,'人物状态已独立保存，可根据已记录状态优化提示词。');
  })().finally(()=>{if(ov.dataset.h3Session===session&&ov._sceneStateTask===task.id){ov._sceneStateBusy=false;setBusy(ov,!!ov._editorBusy);}});
  ov._sceneStatePending.catch(e=>{if(ov.dataset.h3Session===session&&ov._sceneStateTask===task.id){
   const line=ov.querySelector('.h3-state-summary');
   const state=e.code==='OPERATION_NOT_FOUND'?'提取任务已不存在':e.state==='cancelled'?'提取已取消':e.state==='needs_attention'?'提取需要处理':'提取未完成';
   if(line)line.textContent='当前状态：'+state+'；'+e.message;
   note(ov,e.message);const retry=ov.querySelector('.h3-state-retry');if(retry)retry.hidden=false;
  }});
  return ov._sceneStatePending;
 }
 A.monitorH3Scene=(root,id)=>{if(root?._editorUid)return watchState(root,{id,state:'queued'});};
 function contextMessage(ov,result){
  const saved=ov._editorSaved||{},data={...saved,...result};
  const scene=data.scene_contexts?.[data.context_id];
  const initial=data.scene_initial_state||scene?.initial_state;
  const hasInput=Object.hasOwn(data,'state_input_context');
  const incoming=hasInput?data.state_input_context:data.state_context;
  const record=data.scene_record||{};
  const ready=data.state_status?.ready===true;
  const sections=[];
  if(initial)sections.push('场景初态（同场景所有 Part 共用）\n'+worldText(ov,initial));
  const origin=incoming&&incoming.previous_segment_uid===null?'场景初态':'同场景上段';
  sections.push('本段开场状态（承接'+origin+'）\n'+(incoming?worldText(ov,incoming.input_state):data.state_input_message||data.state_message||'尚未取得有效的开场状态。'));
  if(ready&&data.state_status.mode==='inherit'&&incoming)sections.push('本段结束状态（已保存，沿用'+(origin==='场景初态'?origin:'上段')+'）\n'+worldText(ov,incoming.input_state));
  else if(ready&&record.end_state)sections.push('本段结束状态（已保存）\n'+worldText(ov,record.end_state));
  else{
   sections.push('本段结束状态（尚未取得有效记录）\n'+(data.state_status?.detail||data.state_status?.message||data.state_message||'请提取本段结束状态，或确认沿用上段。'));
   if(record.end_state)sections.push('历史结束状态（待重新确认，不代表当前有效状态）\n'+worldText(ov,record.end_state));
  }
  return sections.join('\n\n');
 }
 function operationMessage(result,stage){
  const referenceEdit=result?.operation==='remove_reference';
  const info=referenceEdit?null:result?.operation_result||result?.draft?.operation_result;
  const stateOnly=!referenceEdit&&(info?.kind==='state_only'||result?.state_only||result?.draft?.state_only);
  let summary=info?.summary;
  if(!summary){
   if(stateOnly)summary='仅更新人物状态，未修改构图';
   else if(info?.kind==='unchanged')summary='当前构图无需修改';
   else if(info?.kind==='composition')summary=info.changed_shots?.length?'已调整 Shot '+info.changed_shots.join('、')+' 的构图':'构图修改候选已生成';
   else summary=result?.changes?.join('；')||'编辑候选已生成';
  }
  summary=summary.replace(/[。；]+$/,'');
  const status={candidate:'候选已就绪，尚未应用或保存。',applied:'已应用到当前稿，点击顶部保存；可撤销。',saved:'已保存。'};
  return summary+'。'+status[stage];
 }
 function install(ov,draft,document){
  const list=ov.querySelector('.h3-asset-list');list.dataset.manifest=JSON.stringify(draft.asset_manifest);
  if(draft.reference_revision)list.dataset.baseRevision=draft.reference_revision;
  A.renderH3SelectedPreview(ov);A.refreshH3AssetCount(ov);
  ov.querySelector('.aicf-prompt-modal-body').value=draft.prompt;
  ov.querySelector('#aicf-prompt-source-excerpt').value=draft.source_excerpt;
  ov._shotEditor?.installContentDraft?.(draft,document);
  A.renderH3SceneFraming(ov,draft.scene_framing||(!ov._editorDraft?ov._editorSaved?.scene_framing:null),ov._editorDraft?'applied':'saved',!ov._editorDraft?ov._editorSaved?.framing_status:null);
 }
 async function prepare(ov){
  const session=ov.dataset.h3Session,uid=ov._editorUid;
  if(ov._editorBusy||ov._promptTaskBusy)throw Error('编辑操作正在执行');
  if(ov._sourceReplanPending)throw Error('时长规划尚未完成，请等待同步后再操作');
  if([...ov.querySelectorAll('.h3-asset-input')].some(el=>el.files?.length))throw Error('请先完成素材上传');
  const shots=ov._shotEditor;
  if(!shots||shots.reading||shots.saving)throw Error('分镜正在读取或保存，请稍候');
  if(shots?.documents?.some(d=>d.new||d.removed||d.structure_changed))throw Error('请先保存跨段 Shot 结构修改，再操作参考或承接');
  if(ov._h3AutosaveTimer){clearTimeout(ov._h3AutosaveTimer);ov._h3AutosaveTimer=null;}
  if(A.h3AutosavePending)await A.h3AutosavePending;
  if(ov._h3SavePending)await ov._h3SavePending;
  if(ov.dataset.h3Session!==session||ov._editorUid!==uid)throw Error('编辑分段已切换，操作已取消');
  await A.refreshH3EditorBaseline(ov,true);
  if(ov.dataset.h3Session!==session||ov._editorUid!==uid)throw Error('编辑分段已切换，操作已取消');
 }
 async function operate(ov,operation,asset_id,throwOnFailure=false){
  if(ov._operationPending)return;ov._operationPending=true;
  const operationSession=ov.dataset.h3Session;let attemptId;
  try{
   const providers=operation==='opening_continuity'?A.readPageLLMProviders(ov):null;
   if(operation==='opening_continuity'){
    note(ov,'正在记录并检查人物状态优化…');
    const start=ov.querySelector('.h3-continuity-start');if(start)start.disabled=true;
    const clicked=await api(ov,'optimization-event',{segment_uid:ov._editorUid,stage:'clicked'});
    if(ov.dataset.h3Session!==operationSession)return;
    attemptId=clicked.optimization_status.last_attempt.id;
   }
   await prepare(ov);if(operation!=='remove_reference'&&!ov.querySelector('.h3-state-select').value){if(!ov._ensureScene)throw Error('正在读取场景资料，请稍候');await ov._ensureScene();}if(operation!=='remove_reference'&&ov._sceneStatePending)await ov._sceneStatePending;setBusy(ov,true);
   const session=ov.dataset.h3Session,draft=capture(ov),expected=signature(draft);
   const payload={operation_contract:'scene_state_v3',segment_uid:ov._editorUid,base_revision:ov._editorSaved.base_revision,draft,operation,asset_id,...(providers?{providers}:{})};
   if(attemptId)payload.optimization_attempt_id=attemptId;
   note(ov,operation==='remove_reference'?'正在更新参考绑定…':'正在根据已记录人物状态优化提示词…');
   let result=await api(ov,operation==='remove_reference'?'preview':'continuity',payload);
   if(result.operation_id){
    if(attemptId)optimizationStage(ov,'submitted');
    const id=result.operation_id;ov._continuityOperation=id;
    localStorage.setItem('h3-state-v3-task:'+ov._editorRuntime+':'+ov._editorUid,JSON.stringify({id,expected}));
    do{await new Promise(resolve=>setTimeout(resolve,1500));result=await api(ov,'result',{operation_id:id});if(ov.dataset.h3Session!==session)return;note(ov,result.message||'等待回复');}
    while(['preparing','queued','running','waiting_children'].includes(result.state));
    if(result.state!=='ready')throw Error(result.message||'承接生成失败');result=result.candidate;
   }
   if(ov.dataset.h3Session!==session){if(throwOnFailure)throw Error('编辑分段已切换，组合操作已停止');return;}
   ov._editorCandidate={...result,operation,expected,session};
   if(attemptId)optimizationStage(ov,'generated');
   ov.querySelector('.h3-continuity-preview').value=result.draft.prompt;ov.querySelector('.h3-scene-preview').textContent='候选结束状态（尚未保存）\n'+sceneText(ov,result.scene_preview||ov._stateContext);
   A.renderH3SceneFraming(ov,result.draft.scene_framing,'candidate');
   note(ov,operationMessage(result,'candidate'));
   ov.querySelector('.h3-continuity-apply').hidden=false;
   if(operation==='remove_reference'&&ov.dataset.h3Session===session&&signature(capture(ov))===expected)apply(ov);
  }catch(e){
   if(ov.dataset.h3Session===operationSession){
    if(attemptId){try{await api(ov,'optimization-event',{segment_uid:ov._editorUid,stage:'failed',attempt_id:attemptId,message:e.message});}catch(_){}}
    note(ov,e.message);
   }
   if(throwOnFailure)throw e;
  }finally{if(ov.dataset.h3Session===operationSession){ov._operationPending=false;setBusy(ov,false);}}
 }
 function apply(ov,throwOnFailure=false){
  const c=ov._editorCandidate;
  if(!c||ov.dataset.h3Session!==c.session||signature(capture(ov))!==c.expected){const message='当前稿已变化，候选保留，请重新生成，未覆盖新稿';note(ov,message);if(throwOnFailure)throw Error(message);return;}
  ov._editorUndo={draft:capture(ov),previous:ov._editorDraft,priorUndo:ov._editorUndo,document:clone(ov._shotEditor?.documents.find(d=>d.uid===ov._shotEditor.sourceUid))};
  ov._editorDraft=c;install(ov,c.draft,c.document);ov.querySelector('.h3-continuity-apply').hidden=true;
  localStorage.setItem('h3-state-v3-draft:'+ov._editorRuntime+':'+ov._editorUid,JSON.stringify({candidate:c,base:ov._editorSaved}));
  note(ov,operationMessage(c,'applied'));
  recordApplication(ov,'applied',c.draft.optimization_attempt_id);
 }
 A.restoreH3PromptBaseline=async ov=>{
  if(A.archiveReadOnly||ov._operationPending)return;
  ov._operationPending=true;const session=ov.dataset.h3Session;
  try{
   await prepare(ov);await ov._shotEditor?.flushPreview?.();setBusy(ov,true);
   const draft=capture(ov),expected=signature(draft);
   note(ov,'正在还原底稿并保留最新台词…');
   const result=await api(ov,'restore-baseline',{segment_uid:ov._editorUid,base_revision:ov._editorSaved.base_revision,draft});
   if(ov.dataset.h3Session!==session)return;
   ov._editorCandidate={...result,operation:'restore_baseline',expected,session};
   apply(ov,true);
  }catch(e){if(ov.dataset.h3Session===session)note(ov,e.message);}
  finally{if(ov.dataset.h3Session===session){ov._operationPending=false;setBusy(ov,false);}}
 };
 A.editH3Reference=(root,id)=>{const ov=root.closest('.aicf-prompt-modal')||root;operate(ov,'remove_reference',id);};
 A.clearH3EditorDraft=ov=>{
  localStorage.removeItem('h3-state-v3-draft:'+ov._editorRuntime+':'+ov._editorUid);
  delete ov._editorDraft;delete ov._editorUndo;delete ov._editorCandidate;
  delete ov._staleOptimizationDraft;
  const contentSave=ov.querySelector('.h3-save-content-only');if(contentSave)contentSave.hidden=true;
  const applyButton=ov.querySelector('.h3-continuity-apply');if(applyButton)applyButton.hidden=true;
 };
 A.saveH3EditorDraft=async (ov,throwOnFailure=false)=>{
  if(ov._editorBusy){note(ov,'请等待当前操作完成');return;}
  const submittedSession=ov.dataset.h3Session;
  setBusy(ov,true);note(ov,'正在保存当前稿与承接信息…');
  try{
   if(ov._h3AutosaveTimer){clearTimeout(ov._h3AutosaveTimer);ov._h3AutosaveTimer=null;}
   if(A.h3AutosavePending)await A.h3AutosavePending;
   if(ov._h3SavePending)await ov._h3SavePending;
   if(ov.dataset.h3Session!==submittedSession)return;
   // Shot controls update the full prompt asynchronously. An optimized draft
   // must not capture the older textarea while its latest preview is pending.
   await ov._shotEditor?.flushPreview?.();
   if(ov.dataset.h3Session!==submittedSession)return;
   const submitted=capture(ov),submittedSignature=signature(submitted),submittedOperation=ov._editorDraft;
   const candidate=submittedOperation.draft,normalize=value=>String(value||'').replace(/\r\n?/g,'\n');
   const manualEdit=normalize(submitted.prompt)!==normalize(candidate.prompt)||normalize(submitted.source_excerpt)!==normalize(candidate.source_excerpt)||
    submitted.duration_sec!==candidate.duration_sec||signature(submitted.asset_manifest)!==signature(candidate.asset_manifest);
   const result=await api(ov,'commit',{segment_uid:ov._editorUid,base_revision:ov._editorSaved.base_revision,draft:submitted,operation_base:submittedOperation.operation_base,manual_edit:manualEdit});
   if(ov.dataset.h3Session!==submittedSession)return;
   const changedDuringSave=signature(capture(ov))!==submittedSignature;
   A.clearH3EditorDraft(ov);
   A.syncH3EditorBaseline(ov,result);
   if(changedDuringSave){
    // Only the operation captured by this request was committed. New edits are
    // ordinary content and must never resubmit its scene patch or state guard.
    if(ov._shotEditor){
     if(result.editing_fingerprint)ov._shotEditor.fingerprint=result.editing_fingerprint;
     ov._shotEditor.stale=ov.querySelector('.aicf-prompt-modal-body').value!==ov._shotEditor.lastPrompt;
     ov._shotEditor.refreshControls?.();
    }
    const message='请求时的版本已保存；期间的新修改仍保留，请再次保存。';
    note(ov,message);if(throwOnFailure)throw Error(message);return;
   }
   localStorage.removeItem('h3-state-v3-draft:'+ov._editorRuntime+':'+ov._editorUid);if(result.groups_html){const box=document.createElement('div');box.innerHTML=result.groups_html;const next=[...box.querySelectorAll('.group-card')].find(c=>c.dataset.segmentUid===ov._editorUid);const current=A.segmentCardFromButton(ov._sourceButton);if(next&&current){current.replaceWith(next);ov._sourceButton=next.querySelector('.h3-prompt-open-action')||next.querySelector('[onclick*="openPromptModalFromButton"]');}}
   if(result.scene_record)ov.querySelector('.h3-scene-preview').textContent=contextMessage(ov,result);
   if(result.state_context)ov._stateContext=result.state_context;
   if(result.state_status)stateSummary(ov,result);
   if(result.scene_initial_state&&!ov._sceneCalibrationDirty&&ov._loadInitial){const scene=ov._editorSaved?.scene_contexts?.[ov._editorSaved?.context_id];ov._loadInitial({...scene,initial_state:result.scene_initial_state});}
   const selected=ov.querySelector('.h3-state-select')?.selectedOptions?.[0];if(selected&&result.state_context&&!result.state_context.initializing)selected.textContent=selected.textContent.replace('（待自动初始化）','');
   const savedOutcome=submittedOperation?.operation==='remove_reference'?submittedOperation:result.operation_result||result.draft?.operation_result?result:submittedOperation;
   const savedSummary=manualEdit?'当前人工编辑稿已保存；旧优化状态已失效，请重新提取结束状态。':operationMessage(savedOutcome,'saved');
   note(ov,savedSummary+(result.scene_record_message?' '+result.scene_record_message:''));
   if(manualEdit)recordApplication(ov,'content_saved',submittedOperation.draft.optimization_attempt_id);
   install(ov,result.draft,result.document);if(ov._shotEditor&&result.editing_fingerprint)ov._shotEditor.fingerprint=result.editing_fingerprint;
   const pendingState=result.state_tasks?.find(task=>task?.id&&task.state!=='applied');if(pendingState)watchState(ov,pendingState);
   return result;
  }catch(e){
   if(ov.dataset.h3Session===submittedSession){
    if(e.code==='STALE_OPTIMIZATION'||/请重新优化|请重新生成承接候选/.test(e.message)){
     ov._staleOptimizationDraft=ov._editorDraft;
     const contentSave=ov.querySelector('.h3-save-content-only');if(contentSave)contentSave.hidden=false;
     e.message+='。当前文字尚未保存。可点击“仅保存当前文字和素材”（不确认过期优化状态），或重新优化后保存。';
    }
    note(ov,e.message);
   }
   if(throwOnFailure)throw e;
  }finally{if(ov.dataset.h3Session===submittedSession)setBusy(ov,false);}
 };
 const mount=A.mountShotEditor;
 A.mountShotEditor=function(ov,content){
  A.cancelH3Composite?.(ov);delete ov._h3CompositeSnapshot;delete ov._h3CompositeCheckpoint;
  mount(ov,content);
  const card=A.segmentCardFromButton(ov._sourceButton);if(!card)return;
  ov._editorUid=card.dataset.segmentUid;ov._editorRuntime=card.dataset.runtime;
  delete ov._editorSaved;delete ov._editorDraft;delete ov._editorCandidate;delete ov._editorUndo;delete ov._staleOptimizationDraft;delete ov._syncSceneCalibration;delete ov._loadInitial;
  ov.querySelector('.h3-continuity-card')?.remove();
  const panel=document.createElement('section');panel.className='h3-continuity-card';
  panel.innerHTML='<div class="h3-state-toolbar"><div class="h3-state-heading"><strong>场景与人物承接</strong><span>先优化本段，再确认结束状态</span></div><div class="h3-state-control-stack"><div class="h3-state-scene-controls"><label>本段归属<select class="h3-state-select"><option value="">不加入</option></select></label><button type="button" class="h3-state-create">创建并加入场景</button></div><div class="h3-composite-controls"><label>结束状态处理<select class="h3-composite-mode" title="仅设置组合流程；选择时不执行操作"><option value="inherit">沿用上段（无变化）</option><option value="extract" selected>提取本段结束状态（LLM）</option></select></label><button type="button" class="h3-composite-run" disabled>一键准备并生成</button></div></div></div><details class="h3-scene-manager"><summary class="h3-scene-manager-title">管理场景</summary><p class="h3-state-flow-help">仅可删除未使用的场景；正在使用的场景请先将分段移到其他场景或选择“不加入”。</p><div class="h3-scene-list"></div></details><p class="h3-composite-hint"></p><p class="h3-composite-status" role="status" aria-live="polite"></p><div class="h3-state-main"><div class="h3-state-actions"><span class="h3-action-label">本段提示词</span><button type="button" class="h3-continuity-start" title="结合有效人物状态与当前稿优化机位、景别和入画，同步参考用途；保留台词、剧情动作与时长">根据人物状态优化提示词</button><button type="button" class="h3-continuity-apply" hidden>应用到当前稿</button><button type="button" class="h3-continuity-undo" title="撤销本次应用，恢复之前的提示词与参考素材">撤销本次操作</button></div></div><div class="h3-state-registration"><div class="h3-state-actions"><span class="h3-action-label">结束状态登记（手动）</span><button type="button" class="h3-state-inherit" title="确认本段人物、位置、姿势和在场状态均未变化；无上段时沿用场景初态，不调用 LLM">沿用上段（无变化）</button><button type="button" class="h3-state-extract" title="提取本段状态变化并登记结束状态，供后续段承接；不改写提示词">提取结束状态（LLM）</button><button type="button" class="h3-state-retry" hidden>重试状态提取</button></div><p class="h3-state-summary" role="status" aria-live="polite">当前状态：未设置状态</p></div><p class="h3-continuity-status" role="status" aria-live="polite"></p><div class="h3-state-details"><details class="h3-state-help"><summary>操作说明</summary><p class="h3-state-flow-help">优化提示词会结合有效人物状态调整构图与入画，并同步本段参考用途；在场不强制入画，出画不等于离场。先查看候选再应用。沿用上段时，无上段则沿用场景初态，本段状态保持不变。手动状态按钮点击后立即执行；组合流程按上方选择登记结束状态，选择 LLM 时重新提取。</p></details><details><summary>查看候选全文</summary><textarea class="h3-continuity-preview" readonly></textarea></details><details><summary>场景初态（全场景共用）</summary><label>场景名称<input class="h3-state-name"></label><p class="h3-state-flow-help">此处设置整个场景开始前的状态，同场景所有 Part 共用。在场不等于入镜。修改后，相关结束状态需按顺序重新确认。</p><p class="h3-scene-scope h3-state-flow-help" role="status" aria-live="polite"></p><div class="h3-scene-roles"></div><label>场景布局<textarea class="h3-scene-layout" rows="4" aria-label="背景固定布局与座位关系" placeholder="描述背景图的观察方向、固定设施、座位与遮挡关系；不确定的部分留空"></textarea></label><p class="h3-scene-layout-status h3-state-flow-help" role="status" aria-live="polite"></p><p class="h3-state-flow-help">布局与保存时的背景绑定，供同场景各镜判断入画；人物初态单独保存，不由构图反推。</p><label>场景共用约束<textarea class="h3-scene-constraints" rows="4" aria-label="场景共用空间约束"></textarea></label><p class="h3-state-flow-help">同场景各段自动使用。修改后请重新优化相关段提示词；人物状态未变化时无需重新提取末态。</p><button type="button" class="h3-state-calibrate">保存全场景初态</button><button type="button" class="h3-state-reload" title="从后台读取共享初态，并替换表单中未保存的校准值">读取最新初态</button></details><details class="h3-framing-details"><summary>逐镜人物与构图</summary><div class="h3-framing-results"></div></details><details><summary>人物状态详情</summary><pre class="h3-scene-preview"></pre></details><details class="h3-state-history"><summary>优化记录</summary></details><details class="h3-state-recovery"><summary>草稿与恢复</summary><div class="h3-state-recovery-actions"></div></details></div>';
  ov.querySelector('.aicf-prompt-editor-head').before(panel);
  A.installH3Composite(ov,{
   snapshot:()=>api(ov,'snapshot',{segment_uid:ov._editorUid}),
   optimize:async()=>{await A.savePromptBeforeTask(ov);await operate(ov,'opening_continuity',null,true);apply(ov,true);},
   register:mode=>stateAction(ov,mode==='inherit'?'inherit-state':'extract-state',true)
  });
  const session=ov.dataset.h3Session;
  const history=document.createElement('p');history.className='h3-optimization-history';history.setAttribute?.('role','status');history.setAttribute?.('aria-live','polite');
  panel.querySelector('.h3-state-history').appendChild(history);
  const contentSave=document.createElement('button');contentSave.type='button';contentSave.className='h3-save-content-only';contentSave.textContent='仅保存当前文字和素材';contentSave.hidden=true;
  contentSave.onclick=()=>A.savePromptModal(false,true);panel.querySelector('.h3-continuity-status').before(contentSave);
  delete ov._optimizationStatus;A.renderH3OptimizationHistory(ov);
  const promptEditor=ov.querySelector('.aicf-prompt-modal-body');
  if(ov._optimizationInput)promptEditor.removeEventListener?.('input',ov._optimizationInput);
  ov._optimizationInput=()=>{A.renderH3OptimizationHistory(ov);A.refreshH3FramingDirty(ov);};promptEditor.addEventListener?.('input',ov._optimizationInput);
  api(ov,'snapshot',{segment_uid:ov._editorUid}).then(async r=>{if(ov.dataset.h3Session!==session)return;
   ov._editorSaved=r;ov._stateContext=r.state_context;stateSummary(ov,r);
   const select=panel.querySelector('.h3-state-select');
   for(const [id,scene] of Object.entries(r.scene_contexts||{})){if(!scene.initial_state)continue;const option=document.createElement('option');option.value=id;option.textContent=scene.display_name||scene.name||'未命名场景';select.appendChild(option);}
   select.value=r.context_id||'';A.refreshH3Composite(ov,r);
   function renderRoles(roles){
    panel.querySelector('.h3-scene-roles').replaceChildren();
    for(const role of roles||[]){const label=document.createElement('label');const box=document.createElement('input');box.type='checkbox';box.className='h3-scene-role';box.value=role.id;box.dataset.roleName=role.name;label.append(box,document.createTextNode(role.name));const pos=document.createElement('input');pos.className='state-position';pos.placeholder='位置（未知可留空）';const posture=document.createElement('select');posture.className='state-posture';for(const text of ['未知','站着','坐着','斜靠','仰卧','俯卧','侧卧','蹲着','跪着']){const o=document.createElement('option');o.value=text==='未知'?'':text;o.textContent=text;posture.appendChild(o);}const present=document.createElement('select');present.className='state-present';present.required=true;present.setAttribute('aria-label',role.name+'是否在场');const pending=document.createElement('option');pending.value='';pending.textContent='请选择在场状态';pending.disabled=true;pending.hidden=true;present.appendChild(pending);for(const [value,text] of [['true','在场'],['false','不在场']]){const o=document.createElement('option');o.value=value;o.textContent=text;present.appendChild(o);}label.append(pos,posture,present);panel.querySelector('.h3-scene-roles').appendChild(label);}
   }
   renderRoles(r.scene_roles);
   panel.querySelector('.h3-scene-preview').textContent=contextMessage(ov,r);
   panel.querySelector('.h3-state-inherit').title='确认本段无状态变化并登记结束状态；不调用 LLM';panel.querySelector('.h3-state-select').title='加入场景只保存归属';
   let calibrationScene={},calibrationRevision=null;
   async function configure(data){ov._h3SceneConfigBusy=true;A.refreshH3Composite(ov);try{
    await prepare(ov);if(ov.dataset.h3Session!==session)throw Error('编辑分段已切换，校准未提交');
    await api(ov,'scene-config',{segment_uid:ov._editorUid,...data});
    const fresh=await api(ov,'snapshot',{segment_uid:ov._editorUid});if(ov.dataset.h3Session!==session)return fresh;
    ov._sceneCalibrationDirty=false;ov._syncSceneCalibration(fresh,true);A.syncH3EditorBaseline(ov,fresh);A.refreshH3Composite(ov,fresh);
    panel.querySelector('.h3-scene-preview').textContent=contextMessage(ov,fresh);
    const detail=fresh.state_status?.ready===false?(fresh.state_status.detail||fresh.state_status.message):'';
    note(ov,('initial_state' in data?'全场景初态已保存，同场景所有 Part 共用。':'场景归属已保存。')+(detail?' '+detail:''));watchState(ov,null);return fresh;
   }finally{ov._h3SceneConfigBusy=false;renderSceneList(sceneCatalog);A.refreshH3Composite(ov);}}
   function loadInitial(scene){
    panel.querySelector('.h3-state-name').value=scene?.display_name||scene?.name||r.suggested_scene_name||'';
    panel.querySelector('.h3-scene-constraints').value=scene?.spatial_constraints??r.default_spatial_constraints??'';
    panel.querySelector('.h3-scene-layout').value=scene?.scene_layout?.description||'';
    const layout=ov._editorSaved?.scene_layout;
    panel.querySelector('.h3-scene-layout-status').textContent=!scene?.scene_layout?.description?'尚无独立布局文字，模型仍使用已有初态位置和背景说明。':layout?.current===false?'背景已更换，现有布局待核对；确认文字适用后保存全场景初态以绑定当前背景。':'布局已与保存时的背景绑定。';
    calibrationScene=clone(scene||{});calibrationRevision=scene?.config_revision??null;
    for(const box of panel.querySelectorAll('.h3-scene-role')){
     const facts=scene?.initial_state?.[box.value];box.checked=!!facts;const row=box.parentElement;
     row.querySelector('.state-position').value=facts?.position?.value||'';
     const posture=row.querySelector('.state-posture'),value=facts?.posture?.value||'';
     if(value&&![...posture.children].some(option=>option.value===value)){const option=document.createElement('option');option.value=value;option.textContent=value;posture.appendChild(option);}
     posture.value=value;const present=facts?.present?.value;row.querySelector('.state-present').value=present==null?'':String(present);
    }
   }
   if(r.context_id)api(ov,'scene-state',{segment_uid:ov._editorUid}).then(state=>{if(ov.dataset.h3Session===session&&select.value===r.context_id)watchState(ov,state.state_task);}).catch(e=>note(ov,e.message));
   ov._loadInitial=loadInitial;
   let scenes=r.scene_contexts||{},sceneCatalog=r;const suggestion={initial_state:r.initial_suggestion||{}};
   function renderSceneList(fresh){
    sceneCatalog=fresh;
    const list=panel.querySelector('.h3-scene-list'),entries=Object.entries(scenes);
    const usage=fresh.scene_members||{},names={};
    for(const [key,scene] of entries)names[scene.display_name||scene.name||'未命名场景']=(names[scene.display_name||scene.name||'未命名场景']||0)+1;
    select.replaceChildren();
    const empty=document.createElement('option');empty.value='';empty.textContent='不加入';select.appendChild(empty);
    list.replaceChildren();
    panel.querySelector('.h3-scene-manager-title').textContent='管理场景（'+entries.length+'）';
    for(const [key,scene] of entries){
     const name=scene.display_name||scene.name||'未命名场景',members=usage[key],known=Array.isArray(members);
     const status=known?(members.length?'已用于 '+members.map(p=>p.part_id||p.segment_index||p.segment_uid).join('、'):'未使用'):'使用情况待刷新';
     const option=document.createElement('option');option.value=key;
     option.textContent=name+(names[name]>1&&known?'（'+(members.length?members.length+' 段':'未使用')+'）':'');select.appendChild(option);
     const row=document.createElement('div');row.className='h3-scene-list-row';
     const label=document.createElement('span');label.textContent=name+' · '+status;row.appendChild(label);
     const remove=document.createElement('button');remove.type='button';remove.className='h3-scene-delete';remove.textContent='删除';
     remove.disabled=!known||members.length>0||!!A.archiveReadOnly||!!ov._h3SceneConfigBusy||!!ov._editorBusy||!!ov._sceneStateBusy||!!ov._h3CompositeRun;
     remove.title=known&&members.length?'请先移走该场景下的分段':'删除未使用的场景及其初态';
     remove.setAttribute?.('aria-label','删除场景 '+name+'，'+status);
     remove.onclick=async()=>{try{
      if(ov._h3SceneConfigBusy||ov._editorBusy||ov._sceneStateBusy||ov._promptTaskBusy||ov._h3CompositeRun)throw Error('当前还有操作正在执行，请完成后再删除场景');
      if(!globalThis.confirm('删除未使用的场景“'+name+'”及其初态？'))return;
      ov._h3SceneConfigBusy=true;remove.disabled=true;A.refreshH3Composite(ov);
      await api(ov,'delete-scene',{context_id:key,scene_initial_revision:scene.config_revision??null});
      if(ov.dataset.h3Session!==session)return;
      const updated=await api(ov,'snapshot',{segment_uid:ov._editorUid});
      if(ov.dataset.h3Session!==session)return;
      A.syncH3EditorBaseline(ov,updated);A.refreshH3Composite(ov,updated);
      note(ov,'已删除未使用的场景“'+name+'”。');
     }catch(e){if(ov.dataset.h3Session===session)note(ov,e.message);}
     finally{if(ov.dataset.h3Session===session){ov._h3SceneConfigBusy=false;renderSceneList(sceneCatalog);A.refreshH3Composite(ov);}}};
     row.appendChild(remove);list.appendChild(row);
    }
    select.value=fresh.context_id||'';
   }
   ov._sceneCalibrationDirty=false;
   ov._syncSceneCalibration=(fresh,force=false)=>{
    if(ov.dataset.h3Session!==session||!fresh.scene_contexts)return;
    scenes=clone(fresh.scene_contexts);const key=fresh.context_id||'',scene=scenes[key];
    renderSceneList(fresh);
    const scope=panel.querySelector('.h3-scene-scope');if(scope){const parts=(fresh.scene_member_parts||[]).map(part=>part.part_id||part.title||part.segment_uid);scope.textContent=scene?'当前场景：'+(scene.display_name||scene.name||'未命名场景')+'；'+(parts.length?'共用于 '+parts.join('、'):'同场景所有 Part 共用')+'。':'请先加入场景，再校准共享初态。';}
    if(ov._sceneCalibrationDirty&&!force)return;
    if(fresh.scene_roles)renderRoles(fresh.scene_roles);
    select.value=key;loadInitial(scene||suggestion);
   };
   ov._syncSceneCalibration(r,true);
   A.renderH3SceneFraming(ov,r.scene_framing||r.draft?.scene_framing,'saved',r.framing_status);
   const dirty=()=>{ov._sceneCalibrationDirty=true;};
   panel.querySelector('.h3-scene-constraints').addEventListener?.('input',dirty);
   panel.querySelector('.h3-scene-layout').addEventListener?.('input',dirty);
   panel.querySelector('.h3-scene-roles').addEventListener?.('input',dirty);panel.querySelector('.h3-scene-roles').addEventListener?.('change',dirty);
   panel.querySelector('.h3-state-reload').onclick=async()=>{try{
    if(ov._h3SceneConfigBusy)throw Error('场景设置正在保存，请稍候');
    const fresh=await api(ov,'snapshot',{segment_uid:ov._editorUid});if(ov.dataset.h3Session!==session)return;
    ov._sceneCalibrationDirty=false;ov._syncSceneCalibration(fresh,true);A.syncH3EditorBaseline(ov,fresh);
    note(ov,'已读取最新共享初态，表单中未保存的校准值已替换。');
   }catch(e){if(ov.dataset.h3Session===session)note(ov,e.message);}};
   select.onchange=()=>{const key=select.value,previous=ov._editorSaved?.context_id||'';ov._sceneCalibrationDirty=true;configure({context_id:key}).catch(e=>{select.value=previous;note(ov,e.message);});};
   panel.querySelector('.h3-state-calibrate').onclick=async()=>{try{
    if(!select.value)throw Error('请先选择场景');
    const initial={};for(const box of panel.querySelectorAll('.h3-scene-role:checked')){
     const row=box.parentElement,presence=row.querySelector('.state-present');if(!['true','false'].includes(presence.value)){presence.focus?.();throw Error('请明确选择'+(box.dataset.roleName||box.value)+'在场或不在场');}
     const prior=calibrationScene.initial_state?.[box.value]||{};
     const fact=(field,value)=>({value,...(prior[field]?.value===value&&Object.hasOwn(prior[field],'relative_to_role_id')?{relative_to_role_id:prior[field].relative_to_role_id}:{})});
     initial[box.value]={position:fact('position',row.querySelector('.state-position').value||null),posture:fact('posture',row.querySelector('.state-posture').value||null),present:fact('present',presence.value==='true')};
    }
    ov._sceneCalibrationDirty=true;
    await configure({context_id:select.value,initial_state:initial,scene_initial_revision:calibrationRevision,spatial_constraints:panel.querySelector('.h3-scene-constraints').value,constraints_revision:calibrationScene.constraints_revision??null,scene_layout:panel.querySelector('.h3-scene-layout').value,layout_revision:calibrationScene.layout_revision??null});
   }catch(e){note(ov,e.message);}};
   const createScene=async()=>{const name=r.suggested_scene_name||'新场景';const initial=r.initial_suggestion||{};if(!Object.keys(initial).length)throw Error('当前Beat没有可登记人物，请先完善Beat人物');const fresh=await configure({name,initial_state:initial,automatic:true});scenes=fresh.scene_contexts||{};const id=fresh.context_id;select.value=id;loadInitial(scenes[id]);A.refreshH3Composite(ov,fresh);return fresh;};
   ov._ensureScene=()=>ov._sceneCreatePending||(ov._sceneCreatePending=createScene().finally(()=>{ov._sceneCreatePending=null;}));
   panel.querySelector('.h3-state-create').onclick=()=>ov._ensureScene().catch(e=>note(ov,e.message));

  }).catch(e=>{if(ov.dataset.h3Session===session)note(ov,e.message);});
  const restore=document.createElement('button');restore.type='button';restore.textContent='恢复编辑操作草稿';panel.querySelector('.h3-state-recovery-actions').appendChild(restore);
  restore.onclick=async()=>{try{const cached=JSON.parse(localStorage.getItem('h3-state-v3-draft:'+ov._editorRuntime+':'+ov._editorUid)||'null');if(!cached)throw Error('没有未保存的编辑操作');await prepare(ov);if(cached.base.base_revision!==ov._editorSaved.base_revision)throw Error('后台稿已变化，旧草稿未覆盖当前稿');ov._editorCandidate={...cached.candidate,expected:signature(capture(ov)),session:ov.dataset.h3Session};apply(ov);}catch(e){note(ov,e.message);}};
  const recover=document.createElement('button');recover.type='button';recover.textContent='读取上次优化候选';panel.querySelector('.h3-state-recovery-actions').appendChild(recover);
  recover.onclick=async()=>{try{const cached=JSON.parse(localStorage.getItem('h3-state-v3-task:'+ov._editorRuntime+':'+ov._editorUid)||'null');if(!cached)throw Error('没有已保存的优化任务');await prepare(ov);const r=await api(ov,'result',{operation_id:cached.id});if(r.state!=='ready')throw Error(r.message||r.state);ov._editorCandidate={...r.candidate,expected:cached.expected,session:ov.dataset.h3Session};panel.querySelector('.h3-continuity-preview').value=r.candidate.draft.prompt;A.renderH3SceneFraming(ov,r.candidate.draft.scene_framing,'candidate');panel.querySelector('.h3-continuity-apply').hidden=false;note(ov,operationMessage(r.candidate,'candidate')+'应用前会核对当前稿。');}catch(e){note(ov,e.message);}};

  panel.querySelector('.h3-state-inherit').onclick=()=>stateAction(ov,'inherit-state');
  panel.querySelector('.h3-state-extract').onclick=()=>stateAction(ov,'extract-state');
  panel.querySelector('.h3-state-retry').onclick=()=>stateAction(ov,'extract-state');
  panel.querySelector('.h3-continuity-start').onclick=()=>operate(ov,'opening_continuity');
  panel.querySelector('.h3-continuity-apply').onclick=()=>apply(ov);
  panel.querySelector('.h3-continuity-undo').onclick=()=>{if(!ov._editorUndo)return;const attemptId=ov._editorDraft?.draft.optimization_attempt_id;const u=ov._editorUndo;ov._editorDraft=u.previous;install(ov,u.draft,u.document);ov._editorUndo=u.priorUndo;localStorage.removeItem('h3-state-v3-draft:'+ov._editorRuntime+':'+ov._editorUid);note(ov,'已撤销，正文与参考素材一并恢复');recordApplication(ov,'undone',attemptId);};
 };
})();
'''

EDITOR_DRAFT_CSS = """
.h3-framing-results{display:grid;gap:10px;min-width:0}
.h3-framing-status{margin:6px 0;color:var(--text-secondary,#475569)}
.h3-framing-shot{border:1px solid var(--border,#e2e8f0);border-radius:8px;padding:8px 10px;min-width:0}
.h3-framing-shot-title{margin:0 0 6px;font-weight:600;color:var(--text-title,#0f172a)}
.h3-framing-roles{display:grid;grid-template-columns:minmax(100px,auto) minmax(0,1fr);gap:6px 12px;margin:0;font-size:13px;line-height:1.6}
.h3-framing-roles dt{font-weight:600;overflow-wrap:anywhere}
.h3-framing-roles dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}
@media(max-width:600px){.h3-framing-roles{grid-template-columns:minmax(0,1fr);gap:2px}.h3-framing-roles dd{margin-bottom:8px}}
.h3-continuity-card{padding:10px 12px;margin:8px 0;border:1px solid var(--border,#e2e8f0);border-radius:10px;background:var(--surface,#fff);color:var(--text-body,#334155)}
.h3-continuity-card p{font-size:13px;line-height:1.5;overflow-wrap:anywhere}
.h3-continuity-card .h3-state-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px 16px;flex-wrap:wrap;margin-bottom:6px}
.h3-state-heading{display:grid;gap:2px;align-self:center;flex-shrink:0}
.h3-state-heading strong{font-size:14px;color:var(--text-title,#0f172a);font-weight:600}
.h3-state-heading>span{font-size:12px;color:var(--text-secondary,#475569)}
.h3-state-scene-controls{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap;min-width:0}
.h3-scene-manager{margin:4px 0 8px;font-size:13px}
.h3-scene-manager>summary{cursor:pointer;width:fit-content;color:var(--text-secondary,#475569)}
.h3-scene-manager>summary:focus-visible{outline:2px solid var(--primary,#4f46e5);outline-offset:3px;border-radius:3px}
.h3-scene-list{display:grid;gap:6px}
.h3-scene-list-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 10px;border:1px solid var(--border,#e2e8f0);border-radius:6px}
.h3-scene-list-row>span{min-width:0;overflow-wrap:anywhere}
.h3-continuity-card .h3-scene-delete{flex-shrink:0;color:var(--danger-text,#be123c)}
.h3-continuity-card .h3-scene-delete:disabled{opacity:.5;cursor:not-allowed}
.h3-state-control-stack{display:flex;flex:1;justify-content:flex-end;align-items:center;gap:8px 16px;flex-wrap:wrap;min-width:0;max-width:100%}
.h3-composite-controls{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}
.h3-composite-controls label{min-width:0}
.h3-continuity-card .h3-composite-mode{width:190px}
.h3-continuity-card .h3-composite-hint{margin:0 0 6px;color:var(--text-secondary,#475569);font-size:12px}
.h3-continuity-card .h3-composite-status{margin:0 0 6px;color:var(--danger-text,#be123c) !important;font-weight:600;white-space:normal}
.h3-composite-status:empty{display:none}
.h3-composite-progress{margin:8px 0;padding:10px 12px;border:1px solid var(--border,#dbe2ea);border-radius:8px;background:var(--surface-muted,#f8fafc)}
.h3-composite-progress-summary{margin:0 0 6px;font-size:13px;color:var(--text-body,#334155);line-height:1.6}
.h3-composite-progress ol{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0;padding:0;list-style:none}
.h3-composite-progress li{font-size:13px;line-height:1.6;color:var(--text-secondary,#475569);overflow-wrap:anywhere}
.h3-composite-progress li[data-state=done]{color:var(--text-body,#334155);font-weight:600}
.h3-composite-progress li:is([data-state=running],[data-state=error],[data-state=interrupted]){color:var(--danger-text,#be123c) !important;font-weight:700}
.h3-composite-progress li[data-state=previous-error]{color:var(--danger-text,#be123c) !important;font-weight:700}
#aicf-prompt-modal button[data-composite-failed]{color:var(--danger-text,#be123c) !important;background:var(--danger-soft,#fff1f2) !important;border-color:var(--danger-text,#be123c) !important;box-shadow:0 0 0 1px var(--danger-border,#fecdd3);font-weight:700 !important}
#aicf-prompt-modal button[data-composite-failed]:hover:not(:disabled){background:var(--danger-soft,#fff1f2) !important;outline:2px solid var(--danger-border,#fecdd3);outline-offset:1px}
.h3-composite-status.is-error{color:var(--danger-text,#be123c)}
.aicf-prompt-modal-head .h3-composite-live{font-size:13px;color:var(--danger-text,#be123c) !important;font-weight:600;white-space:normal}
.h3-continuity-card label{display:flex;flex-direction:column;align-items:flex-start;gap:4px;margin:0;font-size:13px;font-weight:500;line-height:1.5;color:var(--text-label,#475569)}
.h3-continuity-card .h3-state-control-stack label{flex-direction:row;align-items:center;gap:6px;white-space:nowrap}
.h3-continuity-card :is(input:not([type=checkbox]),select){box-sizing:border-box;height:32px !important;min-height:32px !important;max-width:100%;margin:0 !important;padding:4px 8px !important;border:1px solid var(--border-strong,#8492a6) !important;border-radius:8px !important;background:var(--surface,#fff) !important;color:var(--text-body,#334155) !important;font-size:13px !important}
.h3-continuity-card .h3-state-select{width:160px;max-width:100%}
.h3-continuity-card .h3-state-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.h3-continuity-card .h3-action-label{width:auto;flex-shrink:0;font-size:12px;font-weight:500;color:var(--text-secondary,#475569)}
.h3-continuity-card.h3-continuity-card button{box-sizing:border-box;min-height:32px !important;margin:0 !important;padding:5px 10px !important;max-width:100%;border:1px solid var(--border-strong,#8492a6) !important;border-radius:8px !important;background:var(--surface,#fff) !important;color:var(--text-body,#334155) !important;font-size:13px !important;font-weight:500 !important;line-height:20px !important;cursor:pointer;box-shadow:none !important;transition:background-color 170ms ease,border-color 170ms ease}
.h3-continuity-card.h3-continuity-card button:hover:not(:disabled){background:var(--surface-muted,#f1f5f9) !important;border-color:var(--text-secondary,#475569) !important}
.h3-continuity-card.h3-continuity-card button.h3-composite-run{background:var(--accent,#4f46e5) !important;color:var(--accent-foreground,#fff) !important;border-color:var(--accent,#4f46e5) !important;font-weight:600 !important}
.h3-continuity-card.h3-continuity-card button.h3-composite-run:hover:not(:disabled){background:var(--accent-hover,#4338ca) !important;border-color:var(--accent-hover,#4338ca) !important}
.h3-continuity-card.h3-continuity-card button.h3-continuity-apply{color:var(--accent,#4f46e5) !important;border-color:var(--accent,#4f46e5) !important;background:var(--accent-soft,#eef2ff) !important}
.h3-continuity-card.h3-continuity-card button.h3-continuity-undo{color:var(--danger-text,#be123c) !important;background:var(--danger-soft,#fff1f2) !important;border-color:var(--danger-border,#fecdd3) !important}
.h3-continuity-card.h3-continuity-card button.h3-continuity-undo:hover:not(:disabled){border-color:var(--danger-text,#be123c) !important}
.h3-continuity-card button:disabled{opacity:.5 !important;cursor:not-allowed !important}
.h3-continuity-card [hidden]{display:none !important}
.h3-state-registration{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px 12px;margin-top:8px;padding:8px;border:1px solid var(--border,#e2e8f0);border-radius:8px;background:var(--surface-subtle,#f8fafc)}
.h3-state-registration .h3-action-label{width:auto}
.h3-continuity-card .h3-state-summary{margin:0;font-size:12px;font-weight:500;color:var(--text-body,#334155)}
.h3-continuity-card .h3-state-flow-help{margin:6px 0 0;font-size:12px;color:var(--text-secondary,#475569)}
.h3-continuity-card .h3-continuity-status{margin:6px 0 0;color:var(--danger-text,#be123c) !important;font-weight:600}
.h3-continuity-status:empty{display:none}
.h3-state-details{display:flex;align-items:flex-start;gap:2px 14px;flex-wrap:wrap;border-top:1px solid var(--border,#e2e8f0);padding-top:4px;margin-top:8px}
.h3-state-details details{min-width:0;max-width:100%}
.h3-state-details details[open]{flex-basis:100%}
.h3-state-details summary{cursor:pointer;font-size:12px;line-height:24px;color:var(--text-secondary,#475569)}
.h3-state-details>details:nth-child(1){--entry-color:#1d4ed8;--entry-hover:#1e40af}
.h3-state-details>details:nth-child(2){--entry-color:#7e22ce;--entry-hover:#6b21a8}
.h3-state-details>details:nth-child(3){--entry-color:#047857;--entry-hover:#065f46}
.h3-state-details>details:nth-child(4){--entry-color:#c2410c;--entry-hover:#9a3412}
.h3-state-details>details:nth-child(5){--entry-color:#be185d;--entry-hover:#9d174d}
.h3-state-details>details:nth-child(6){--entry-color:#0e7490;--entry-hover:#155e75}
.h3-state-details>details>summary{display:list-item;box-sizing:border-box;padding:4px 12px;margin:4px 0;min-height:34px;border:1px solid var(--entry-color);border-radius:7px;background:var(--entry-color);color:#fff;font-weight:600;transition:background-color 170ms ease,box-shadow 170ms ease}
.h3-state-details>details>summary:hover,.h3-state-details>details[open]>summary{background:var(--entry-hover);border-color:var(--entry-hover);box-shadow:0 2px 5px #0f172a26}
.h3-state-details>details>summary:focus-visible{outline:3px solid var(--entry-color) !important;outline-offset:3px !important}
@media(prefers-reduced-motion:reduce){.h3-state-details>details>summary{transition:none}}
.h3-state-details details>label,.h3-state-details .h3-scene-roles{margin:8px 0}
.h3-scene-roles{display:grid;gap:8px}
.h3-scene-roles>label{display:flex;flex-direction:row;align-items:center;flex-wrap:wrap;gap:8px}
.h3-state-recovery-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.h3-continuity-card :is(button,input,select,textarea,summary):focus-visible{outline:2px solid var(--accent,#4f46e5) !important;outline-offset:2px !important}
.h3-optimization-history{padding:10px 12px;border-left:3px solid var(--accent,#4f46e5);background:var(--surface-muted,#f1f5f9);color:var(--text-body,#334155);white-space:normal}
.h3-scene-preview{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;font-size:12px}
.h3-continuity-preview{box-sizing:border-box;width:100%;min-height:200px;resize:vertical}
@media(max-width:600px){
 .h3-continuity-card{padding:12px}
 .h3-state-heading{width:100%}
 .h3-state-control-stack,.h3-composite-controls{width:100%}
 .h3-state-control-stack{flex-basis:100%;justify-content:flex-start}
 .h3-state-scene-controls{width:100%}
 .h3-state-scene-controls label{flex:1;min-width:120px}
 .h3-continuity-card .h3-state-control-stack label{flex-direction:column;align-items:flex-start;white-space:normal}
 .h3-composite-controls label{flex:1;min-width:120px}
 .h3-continuity-card .h3-composite-mode{width:100%}
 .h3-continuity-card .h3-state-summary{width:100%}
 .h3-continuity-card .h3-state-select{width:100%}
 .h3-continuity-card .h3-action-label{width:100%}
 .h3-continuity-card.h3-continuity-card button{min-height:44px !important}
 .h3-continuity-card input:not([type=checkbox]),
 .h3-continuity-card select{height:44px !important;min-height:44px !important}
 .h3-state-details summary{min-height:44px;line-height:44px}
}
@media(prefers-reduced-motion:reduce){.h3-continuity-card button{transition:none !important}}
"""

