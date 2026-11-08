"""Manual H3 Shot controls, isolated from the LLM revision workspace."""

SHOT_EDITOR_JS = r'''
(() => {
  const A = window.AICF;
  const clone = value => JSON.parse(JSON.stringify(value));
  const fmt = cs => (cs / 100).toFixed(2);
  A.h3PromptDuration=function(text){
    const detail=String(text||'').split('【详细镜头描述】')[1]?.split(/【[^】]+】/)[0]||'';
    const shots=[...detail.matchAll(/\[Shot\s+\d+\][^\n]*?[（(]\s*(\d+(?:\.\d+)?)\s*[–—－-]\s*(\d+(?:\.\d+)?)\s*秒[）)]/g)];
    if(!shots.length)return null;
    let end=0;
    for(const shot of shots){const start=Number(shot[1]),next=Number(shot[2]);if(Math.abs(start-end)>.0001||next<=start)return null;end=next;}
    return end;
  };
  function ranges(doc) {
    let start = 0;
    return doc.shots.map(shot => {const row={...shot,start_cs:start,end_cs:start+shot.duration_cs};start=row.end_cs;return row;});
  }
  function align(doc) {
    const total=doc.shots.reduce((n,s)=>n+s.duration_cs,0);
    if (!doc.shots.length || total>1500) return false;
    doc.shots[doc.shots.length-1].duration_cs += Math.max(400,Math.ceil(total/100)*100)-total;
    return true;
  }
  function snapTotalDuration(doc,shot,duration,max) {
    const others=doc.shots.reduce((total,item)=>total+(item===shot?0:item.duration_cs),0);
    const snapped=Math.round((others+duration)/100)*100-others;
    return snapped>=1&&snapped<=max&&Math.abs(snapped-duration)<=12?snapped:duration;
  }
  function move(docs, sourceUid, shotUid, tail, destination='next') {
    const source=docs.find(d=>d.uid===sourceUid);
    if(!source||source.removed)throw new Error('当前 Part 已变化');
    const pos=source.shots.findIndex(s=>s.uid===shotUid);
    if(pos<0)throw new Error('Shot 已变化');
    const index=docs.indexOf(source);
    if(!['new','next'].includes(destination))throw new Error('无效的 Shot 操作');
    let target=destination==='next'?docs.slice(index+1).find(d=>!d.removed):null;
    if(!target) {
      target={uid:'segment_'+crypto.randomUUID().replaceAll('-',''),new:true,touched:true,
        removed:false,shots:[],title:source.title+' · 续段',structure_changed:true,
        model_id:source.model_id,config:clone(source.config),sections:clone(source.sections||[]),
        preamble:source.preamble||'',source_excerpt:source.source_excerpt||'',saved_duration_cs:0};
      docs.splice(index+1,0,target);
    } else {
      if(target.error || target.model_id!=='minimax_h3_local_ref2va')throw new Error(target.error || '下一段不是 H3，请新建下一段');
    }
    const moved=source.shots.splice(pos,tail?source.shots.length-pos:1);
    target.shots.unshift(...moved);
    source.touched=target.touched=true;
    source.structure_changed=target.structure_changed=true;
    return target;
  }
  function hasDraftChanges(draft,current){
    const contents=docs=>docs.map(doc=>({uid:doc.uid,new:!!doc.new,removed:!!doc.removed,
      title:doc.title,source_excerpt:doc.source_excerpt,sections:doc.sections,preamble:doc.preamble,
      config:Object.fromEntries(Object.entries(doc.config||{}).filter(([key])=>!['channel_id','prompt_override','prompt_version_info','latest_prompt_revision'].includes(key))),
      shots:(doc.shots||[]).map(shot=>({uid:shot.uid,body:shot.body,duration_cs:shot.duration_cs,
        dialogues:shot.dialogues,reference_manifest:shot.reference_manifest}))}));
    return JSON.stringify(contents(draft))!==JSON.stringify(contents(current));
  }
  A.shotEditorMath={ranges,align,move,fmt,hasDraftChanges,snapTotalDuration};
  async function api(action,payload) {
    const res=await fetch('/api/segment/shot-editor/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const value=await (A.readJsonResponse?A.readJsonResponse(res,'/api/segment/shot-editor/'+action):res.json());if(!res.ok||!value.ok)throw new Error(value.message||'Shot 编辑失败');return value;
  }
  A.mountShotEditor=function(ov,content) {
    if(String(ov.dataset.modelId)!=='minimax_h3_local_ref2va')return;
    if(ov._shotEditor?.stop)ov._shotEditor.stop();
    const host=document.createElement('section');host.className='h3-shot-editor';
    host.setAttribute('aria-label','Shot 台词与时长编辑');
    host.innerHTML='<div class="shot-editor-head"><strong>Shot 台词与时长</strong><button type="button" data-action="read">读取当前稿</button></div><p class="shot-editor-info" role="status" aria-live="polite">正在读取镜头…</p><div class="shot-editor-toolbar"><button type="button" data-action="undo">撤销</button><button type="button" data-action="redo">重做</button><span class="shot-editor-total"></span></div><div class="shot-editor-docs"></div>';
    content.querySelector('.review-selected').after(host);
    const baselineButton=document.createElement('button');baselineButton.type='button';
    baselineButton.dataset.action='restore-baseline';baselineButton.textContent='还原底稿';
    baselineButton.title='恢复优化前的镜头与站位，保留最新台词；检查后保存';
    host.querySelector('.shot-editor-head').appendChild(baselineButton);
    baselineButton.onclick=async()=>{
      if(state.reading||state.saving||ov._promptTaskBusy||ov._editorBusy||A.archiveReadOnly)return;
      baselineButton.disabled=true;
      try{await A.restoreH3PromptBaseline(ov);}finally{if(valid())updateAvailability();}
    };
    const editor=ov.querySelector('.aicf-prompt-modal-body');
    const session=String(ov.dataset.h3Session), runtimeRevision=String(A.promptRuntimeRevision||0);
    const state={dirty:false,documents:[],history:[],future:[],stopped:false,sequence:0,own:false,stale:false,saving:false};
    ov._shotEditor=state;
    const valid=()=>!state.stopped && ov.classList.contains('open') && String(ov.dataset.h3Session)===session && String(A.promptRuntimeRevision||0)===runtimeRevision;
    const info=(text,error=false)=>{host.querySelector('.shot-editor-info').textContent=text;host.querySelector('.shot-editor-info').classList.toggle('is-error',error);};
    const manifest=()=>A.readH3Manifest?A.readH3Manifest(ov):JSON.parse(ov.querySelector('.h3-asset-list')?.dataset.manifest||'[]');
    function persist(){if(!state.key)return;try{if(state.dirty)localStorage.setItem(state.key,JSON.stringify({fingerprint:state.fingerprint,documents:state.documents}));else localStorage.removeItem(state.key);}catch(_){}}
    function remember(){state.history.push(clone(state.documents));if(state.history.length>50)state.history.shift();state.future=[];}
    function payload(){return {runtime:state.runtime,fingerprint:state.fingerprint,documents:state.documents};}
    function mark(){state.dirty=true;persist();host.querySelector('[data-action="undo"]').disabled=!state.history.length;host.querySelector('[data-action="redo"]').disabled=!state.future.length;}
    function syncPrompt(doc) {
      if(doc.uid!==state.sourceUid || !doc.prompt)return;
      state.lastPrompt=doc.prompt;
      if(editor.value===doc.prompt)return;
      state.own=true;
      try{editor.value=doc.prompt;editor.dispatchEvent(new Event('input',{bubbles:true}));}
      finally{state.own=false;}
      if(A.scheduleH3PromptCount)A.scheduleH3PromptCount(ov);
    }
    let previewTimer;
    async function preview() {
      if(!valid()||state.stale||state.saving||state.reading)return;
      const sequence=++state.sequence;
      try {
        const result=await api('preview',payload());
        if(!valid()||state.sequence!==sequence||state.stale)return;
        for(const updated of result.documents){const doc=state.documents.find(d=>d.uid===updated.uid);if(doc){doc.prompt=updated.prompt;doc.errors=updated.errors;doc.warnings=updated.warnings;doc.config=updated.config;}}
        state.previewErrors=result.errors;
        state.total=result.total_duration_sec;
        const source=state.documents.find(d=>d.uid===state.sourceUid && !d.removed);
        if(source)syncPrompt(source);
        const warnings=result.documents.flatMap(doc=>doc.warnings||[]);
        info(result.errors.length?result.errors.join('；'):warnings.length?'草稿可保存；请核对：'+warnings.join('；'):'镜头和台词已联动，点击顶部“保存”同步到 Beat。',!!result.errors.length);
        updateTimes();persist();return true;
      }catch(error){if(valid()&&sequence===state.sequence)info(error.message,true);}
    }
    function schedule(){clearTimeout(previewTimer);state.sequence++;previewTimer=setTimeout(preview,80);}
    function updateAvailability(){
      const blocked=!!(state.stale||state.saving||state.reading||ov._promptTaskBusy||A.archiveReadOnly);
      const restore=host.querySelector('[data-action="restore-baseline"]');
      if(restore)restore.disabled=!!(state.saving||state.reading||ov._promptTaskBusy||ov._editorBusy||ov._operationPending||A.archiveReadOnly);
      host.querySelectorAll('.shot-editor-docs input,.shot-editor-docs textarea,.shot-editor-docs select,.shot-editor-docs button').forEach(el=>{
        if(el.dataset.action!=='align')el.disabled=blocked||el.dataset.unavailable==='true';
      });
      host.querySelector('[data-action="undo"]').disabled=blocked||!state.history.length;
      host.querySelector('[data-action="redo"]').disabled=blocked||!state.future.length;
    }
    function updateTimes(){
      let index=0;
      for(const doc of state.documents){
        if(doc.removed)continue;index++;
        const rows=ranges(doc),total=rows.length?rows[rows.length-1].end_cs:0;
        const box=[...host.querySelectorAll('.shot-editor-document')].find(el=>el.dataset.uid===doc.uid);
        if(!box)continue;
        box.querySelector('.shot-doc-title').textContent='第 '+index+' 段 · '+doc.title;
        box.querySelector('.shot-doc-total').textContent=fmt(total)+' 秒'+(total>1500?' · 超出 '+fmt(total-1500)+' 秒':total<400?' · 不足 '+fmt(400-total)+' 秒':'');
        box.querySelector('[data-action="align"]').disabled=!rows.length||total>1500||(total>=400&&total%100===0)||!!(A.archiveReadOnly||state.stale||state.saving||state.reading||ov._promptTaskBusy);
        box.querySelector('[data-action="align"]').textContent='补齐末 Shot'+(rows.length&&total<1500?' +'+fmt(Math.max(400,Math.ceil(total/100)*100)-total)+' 秒':'');
        for(const row of rows){
          const item=[...box.querySelectorAll('.shot-editor-row')].find(el=>el.dataset.uid===row.uid);if(!item)continue;
          item.querySelector('.shot-time-range').textContent=fmt(row.start_cs)+'–'+fmt(row.end_cs)+' 秒';
          const slider=item.querySelector('input[type="range"]'),number=item.querySelector('input[type="number"]');
          slider.max=String(Math.max(1500,row.duration_cs));slider.value=String(row.duration_cs);
          if(document.activeElement!==number)number.value=fmt(row.duration_cs);
        }
      }
      const duration=content.querySelector('.review-duration'),source=state.documents.find(d=>d.uid===state.sourceUid);
      if(duration&&source){duration.readOnly=true;duration.value=fmt(source.shots.reduce((n,s)=>n+s.duration_cs,0));}
      const planned=state.documents.reduce((n,d)=>n+(d.removed?0:(d.touched||d.new?d.shots.reduce((t,s)=>t+s.duration_cs,0):d.saved_duration_cs||0)),0);
      host.querySelector('.shot-editor-total').textContent='整片规划 '+fmt(planned)+' 秒';
      updateAvailability();
    }
    function render(){
      const list=host.querySelector('.shot-editor-docs');list.replaceChildren();
      for(const doc of state.documents.filter(d=>d.uid===state.sourceUid||d.touched||d.new)){
        const box=document.createElement('div');box.className='shot-editor-document';box.dataset.uid=doc.uid;
        if(doc.removed){box.textContent=doc.title+'：已移除空段（可撤销）';list.appendChild(box);continue;}
        box.innerHTML='<strong class="shot-doc-title"></strong><div class="shot-doc-summary"><span class="shot-doc-total"></span><button type="button" data-action="align">补齐末 Shot</button></div><div class="shot-doc-rows"></div>';
        const rowList=box.querySelector('.shot-doc-rows');
        doc.shots.forEach((shot,i)=>{
          const row=document.createElement('div');row.className='shot-editor-row';row.dataset.uid=shot.uid;row.setAttribute('role','group');row.setAttribute('aria-label','Shot '+(i+1));
          row.innerHTML='<div class="shot-row-head"><strong></strong><span class="shot-time-range"></span></div><p class="shot-line"></p><div class="shot-duration-controls"><input type="range" min="1" max="1500" step="1"><label>时长（秒）<input type="number" min="0.01" step="0.01"></label></div><div class="shot-row-actions"><select class="shot-move-range" aria-label="移动范围"><option value="tail">此 Shot 及后续</option><option value="one">仅此 Shot</option></select><button type="button" data-action="move" title="移到下一 Part 开头；没有下一 Part 时自动新建">转移</button><button type="button" data-action="split" title="插入新 Part，原下一 Part 顺延">新建下一段</button><button type="button" class="shot-delete" data-action="delete">删除</button></div>';
          row.querySelector('strong').textContent='Shot '+(i+1);row.querySelector('.shot-line').textContent=shot.summary||shot.body.slice(0,100);
          const summary=row.querySelector('.shot-line');
          if(Array.isArray(shot.dialogues)){
            const dialogueBox=document.createElement('div');dialogueBox.className='shot-dialogues';summary.replaceWith(dialogueBox);
            const options=shot.speaker_options||[];
            function changed(){doc.touched=true;doc.dialogue_changed=true;mark();schedule();}
            if(!shot.dialogues.length){const empty=document.createElement('p');empty.className='shot-line';empty.textContent=shot.speech_issues?.length?'检测到台词解析异常，请重新读取当前稿；原文台词未删除':'无台词';dialogueBox.appendChild(empty);}
            shot.dialogues.forEach((line,j)=>{
              const group=document.createElement('div');group.className='shot-dialogue';
              const who=document.createElement('label');who.textContent='说话人';
              const select=document.createElement('select');select.setAttribute('aria-label','Shot '+(i+1)+' 第 '+(j+1)+' 句说话人');
              for(const option of options){const el=document.createElement('option');el.value=option.key;el.textContent=option.label;select.appendChild(el);}
              select.value=line.speaker_key;who.appendChild(select);
              select.onchange=()=>{if(state.stale||state.saving)return;remember();line.speaker_key=select.value;changed();};
              const how=document.createElement('label');how.textContent='发声方式';
              const scope=document.createElement('select');scope.setAttribute('aria-label','Shot '+(i+1)+' 第 '+(j+1)+' 句发声方式');
              for(const [value,label] of [['onscreen','画内对白'],['offscreen','画外对白'],['telephone','电话'],['narration','旁白']]){const el=document.createElement('option');el.value=value;el.textContent=label;scope.appendChild(el);}
              scope.value=line.scope;how.appendChild(scope);
              if(line.delivery_spans?.length>1){scope.disabled=true;const note=document.createElement('span');note.textContent='镜内分阶段发声：请在完整提示词中编辑阶段';how.appendChild(note);}
              scope.onchange=()=>{if(state.stale||state.saving)return;if(line.delivery_spans?.length>1){scope.value=line.scope;return;}remember();line.scope=scope.value;changed();};
              const label=document.createElement('label');label.className='shot-dialogue-text';label.textContent='台词';
              const text=document.createElement('textarea');text.rows=2;text.value=line.line;text.setAttribute('aria-label','Shot '+(i+1)+' 第 '+(j+1)+' 句台词');
              let rememberedLine=false;
              text.oninput=()=>{if(state.stale||state.saving)return;if(!rememberedLine){remember();rememberedLine=true;}line.line=text.value;changed();};
              text.onblur=()=>{rememberedLine=false;};label.appendChild(text);
              const remove=document.createElement('button');remove.type='button';remove.className='shot-delete';remove.textContent='删除台词';
              remove.onclick=()=>{if(state.stale||state.saving)return;remember();shot.dialogues.splice(shot.dialogues.indexOf(line),1);changed();render();};
              group.append(who,how,label,remove);dialogueBox.appendChild(group);
            });
            const add=document.createElement('button');add.type='button';add.className='shot-add-dialogue';add.textContent='+ 添加台词';add.disabled=!options.length;add.dataset.unavailable=String(!options.length);
            add.onclick=()=>{if(state.stale||state.saving||!options.length)return;remember();shot.dialogues.push({uid:'dialogue_'+crypto.randomUUID(),speaker_key:options[0].key,scope:'onscreen',line:''});changed();render();};
            dialogueBox.appendChild(add);
          }
          row.querySelector('input[type="range"]').setAttribute('aria-label','Shot '+(i+1)+' 时长');
          let remembered=false;
          for(const input of row.querySelectorAll('input')){
            input.disabled=!!A.archiveReadOnly;
            let dragging=false;
            if(input.type==='range'){
              input.title='拖动时接近本段整数秒会自动吸附；可用方向键或右侧数字精调';
              input.addEventListener('pointerdown',()=>{dragging=true;});
              for(const event of ['pointerup','pointercancel','blur','keydown'])input.addEventListener(event,()=>{dragging=false;});
            }
            input.addEventListener('input',()=>{
              if(state.stale||state.saving)return;
              let duration=input.type==='range'?Number(input.value):Math.round(Number(input.value)*100);
              if(!Number.isFinite(duration)||!Number.isInteger(duration)||duration<=0)return;
              if(input.type==='range'&&dragging)duration=snapTotalDuration(doc,shot,duration,Number(input.max));
              if(!remembered){remember();remembered=true;}
              shot.duration_cs=duration;doc.touched=true;mark();updateTimes();schedule();
            });
            input.addEventListener('change',()=>{remembered=false;schedule();});
          }
          row.querySelector('[data-action="delete"]').onclick=()=>{
            if(state.stale||state.saving)return;remember();doc.shots.splice(doc.shots.indexOf(shot),1);doc.touched=true;doc.structure_changed=true;mark();render();schedule();
          };
          const transfer=destination=>{
            if(state.stale||state.saving)return;
            const before=clone(state.documents);
            try{const target=move(state.documents,doc.uid,shot.uid,row.querySelector('.shot-move-range').value==='tail',destination);state.history.push(before);state.future=[];mark();render();schedule();info('Shot 已移入“'+target.title+'”开头，两段已从 0 秒重新排号；点击顶部保存一起提交。'+(!doc.shots.length?'当前 Part 已空，请删除此空段或撤销。':''));}
            catch(error){state.documents=before;info(error.message,true);}
          };
          row.querySelector('[data-action="move"]').onclick=()=>transfer('next');
          row.querySelector('[data-action="split"]').onclick=()=>transfer('new');
          rowList.appendChild(row);
        });
        if(!doc.shots.length){const remove=document.createElement('button');remove.type='button';remove.textContent='删除此空段';remove.onclick=()=>{remember();doc.removed=true;doc.touched=true;mark();render();schedule();};rowList.appendChild(remove);}
        box.querySelector('[data-action="align"]').onclick=()=>{if(state.stale||state.saving)return;remember();align(doc);doc.touched=true;mark();render();schedule();};
        list.appendChild(box);
      }
      host.querySelectorAll('button,select,textarea').forEach(el=>{if(A.archiveReadOnly)el.disabled=true;});
      updateTimes();
    }
    async function read(restore=true){
      state.sequence++;clearTimeout(previewTimer);
      state.reading=true;host.querySelector('[data-action="read"]').disabled=true;
      updateTimes();
      info('正在从当前文字读取镜头…');
      const submitted=editor.value;
      const submittedManifest=JSON.stringify(manifest());
      const submittedSignature=(A.h3ManifestSignature||JSON.stringify)(JSON.parse(submittedManifest));
      try{
        const result=await api('preview',{segment_index:Number(ov.dataset.segmentIndex),editor_prompt:submitted,asset_manifest:JSON.parse(submittedManifest),
          source_excerpt:ov.querySelector('#aicf-prompt-source-excerpt')?.value||''});
        if(!valid()||editor.value!==submitted)return false;
        if((A.h3ManifestSignature||JSON.stringify)(manifest())!==submittedSignature){
          info('读取期间参考素材已变化，已保留当前编辑，请重新读取。',true);return false;
        }
        Object.assign(state,{runtime:result.runtime,fingerprint:result.fingerprint,documents:result.documents,history:[],future:[],dirty:false,stale:false,total:result.total_duration_sec});
        state.sourceUid=state.documents.find(d=>d.index===Number(ov.dataset.segmentIndex)).uid;
        state.dirty=!!state.documents.find(d=>d.uid===state.sourceUid)?.editor_changed;
        state.key='aicf-shot-editor:'+state.runtime+':'+state.sourceUid;
        state.manifestBase=submittedSignature;state.lastPrompt=submitted;
        let saved;try{saved=JSON.parse(localStorage.getItem(state.key)||'null');}catch(_){}
        if(restore&&saved&&saved.fingerprint===state.fingerprint&&hasDraftChanges(saved.documents,state.documents)){state.documents=saved.documents;state.dirty=true;info('已恢复未保存的 Shot 草稿');}
        else info(state.dirty?'已从当前文字重建 Shot 草稿，点击顶部“保存”同步 Beat。':saved?'后台版本已变化，旧草稿保留；当前显示最新稿。':'可修改说话人和台词；顶部保存会同步 Beat。时长可精确到 0.01 秒。');
        if(!state.dirty)persist();
        render();if(state.dirty)schedule();return true;
      }catch(error){if(valid())info(error.message,true);return false;}
      finally{state.reading=false;if(valid()){host.querySelector('[data-action="read"]').disabled=!!(A.archiveReadOnly||ov._promptTaskBusy);updateTimes();}}
    }
    state.stop=()=>{state.stopped=true;state.sequence++;clearTimeout(previewTimer);persist();editor.removeEventListener('input',onEditor);};
    function onEditor(){
      if(state.own)return;
      if(editor.value===state.lastPrompt){
        if(state.stale){state.stale=false;updateTimes();info('文字已恢复为当前 Shot 草稿，可以继续调整。');if(state.dirty)schedule();}
        return;
      }
      state.stale=true;state.sequence++;clearTimeout(previewTimer);updateTimes();
      info('提示词文字已修改，Shot 控件暂时停用。请点击“读取当前稿”后继续调整；如需保留右侧未保存的修改，请选择“恢复 Shot 草稿”。');
    }
    editor.addEventListener('input',onEditor);
    state.read=read;
    state.flushPreview=async()=>{
      if(state.dirty&&!state.stale){clearTimeout(previewTimer);if(!await preview())throw Error('Shot 草稿尚未同步到文字，请读取当前稿后重试');
       if(state.previewErrors?.length)throw Error(state.previewErrors.join('；'));}
    };
    state.refreshControls=updateTimes;
    const restoreDraft=document.createElement('button');restoreDraft.type='button';restoreDraft.textContent='恢复 Shot 草稿';
    restoreDraft.hidden=true;host.querySelector('.shot-editor-head').appendChild(restoreDraft);
    editor.addEventListener('input',()=>{restoreDraft.hidden=!(state.stale&&state.dirty);});
    restoreDraft.onclick=()=>{
      if(state.saving)return;
      const source=state.documents.find(d=>d.uid===state.sourceUid);
      if(!source?.prompt)return;
      try{localStorage.setItem(state.key+':manual-text',editor.value);}catch(_){}
      syncPrompt(source);state.stale=false;restoreDraft.hidden=true;render();schedule();
      info('已恢复 Shot 草稿，手动文字已另存于本地；请点击顶部保存提交全部分段。');
    };
    state.save=async()=>{
      if(!valid())throw new Error('Shot 编辑会话已失效，请先复制备份当前文字，再重新打开编辑器。');
      if(state.reading)throw new Error('正在读取当前稿，请稍候再保存。');
      if(!state.dirty)return false;
      if(state.saving)throw new Error('Shot 草稿正在保存，请稍候。');
      if(state.stale)throw new Error('当前文字稿与 Shot 草稿不一致，请先读取当前稿');
      if((A.h3ManifestSignature||JSON.stringify)(manifest())!==state.manifestBase)throw new Error('参考配置已变化，请先读取当前稿后再调整 Shot；原草稿仍保留');
      if([...ov.querySelectorAll('.h3-asset-input')].some(input=>input.files?.length))throw new Error('请先保存新上传的参考素材，再读取当前稿调整 Shot');
      state.saving=true;state.sequence++;clearTimeout(previewTimer);
      const previousReadOnly=editor.readOnly;editor.readOnly=true;
      const controlStates=[...host.querySelectorAll('input,textarea,select,button')].map(el=>[el,el.disabled]);
      controlStates.forEach(([el])=>{el.disabled=true;});
      try{
        const source=state.documents.find(d=>d.uid===state.sourceUid);
        if(source)source.source_excerpt=ov.querySelector('#aicf-prompt-source-excerpt')?.value??source.source_excerpt;
        const result=await api('preview',payload());
        if(!valid())throw new Error('编辑会话已切换，保存已取消');
        if(!result.valid)throw new Error(result.errors.join('；'));
        const saved=await api('commit',{runtime:result.runtime,fingerprint:result.fingerprint,documents:result.documents});
        if(!valid())return true;
        state.dirty=false;persist();
        const beats=document.querySelector('#beats_editor textarea');
        if(beats&&saved.beats_display!=null){beats.value=saved.beats_display;beats.dispatchEvent(new Event('input',{bubbles:true}));}
        const panel=document.querySelector('.output-column .right-panel');
        if(panel&&saved.groups_html){const template=document.createElement('template');template.innerHTML=saved.groups_html;const next=template.content.querySelector('.right-panel');if(next)panel.replaceWith(next);}
        if(A.scheduleSegmentDirectorSync)A.scheduleSegmentDirectorSync();
        info('Shot、参考配置和 Beat 已统一保存；受影响段待重新生成。');
        if(state.keepOpen){
          const current=saved.documents.find(d=>d.uid===state.sourceUid&&!d.removed);
          if(!current||Number(current.index)!==Number(ov.dataset.segmentIndex))throw new Error('内容已保存，但当前分段已删除或编号变化，请重新打开目标分段后提交。');
          syncPrompt(current);
          const excerpt=ov.querySelector('#aicf-prompt-source-excerpt');if(excerpt)excerpt.dataset.savedValue=excerpt.value;
          const card=[...document.querySelectorAll('.group-card')].find(el=>el.dataset.segmentUid===state.sourceUid);
          if(card)ov._sourceButton=card.querySelector('.h3-prompt-open,button')||card;
          const referenceList=ov.querySelector('.h3-asset-list');
          const savedReferences=card?.querySelector('.h3-card-config-source');
          if(referenceList&&savedReferences){
            referenceList.dataset.manifest=savedReferences.dataset.manifest;
            referenceList.dataset.baseRevision=savedReferences.dataset.baseRevision;
          }
          if(!await read(false))throw new Error('内容已保存，但编辑器刷新失败，请重新打开后提交。');
          if(saved.editor_snapshots?.[state.sourceUid]&&A.syncH3EditorBaseline)A.syncH3EditorBaseline(ov,saved.editor_snapshots[state.sourceUid]);
          else if(A.refreshH3EditorBaseline)await A.refreshH3EditorBaseline(ov);
        }else A.closePromptModal();
        return true;
      }finally{state.saving=false;editor.readOnly=previousReadOnly;controlStates.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});if(state.dirty)persist();if(valid())render();}
    };
    state.saveForTask=async()=>{state.keepOpen=true;try{return await state.save();}finally{state.keepOpen=false;}};
    host.querySelector('[data-action="read"]').onclick=()=>{
      if(state.dirty){state.history.push(clone(state.documents));persist();}
      if(state.key&&state.dirty){try{localStorage.setItem(state.key+':previous',JSON.stringify({fingerprint:state.fingerprint,documents:state.documents}));}catch(_){}}
      read(false);
    };
    host.querySelector('[data-action="undo"]').onclick=()=>{if(!state.history.length||state.saving)return;state.future.push(clone(state.documents));state.documents=state.history.pop();mark();render();schedule();};
    host.querySelector('[data-action="redo"]').onclick=()=>{if(!state.future.length||state.saving)return;state.history.push(clone(state.documents));state.documents=state.future.pop();state.dirty=true;persist();render();schedule();};
    state.exportDocuments=()=>clone(state.documents);
    state.installContentDraft=function(draft,document){
      const current=state.documents.find(d=>d.uid===state.sourceUid);
      if(!current||!document){state.stale=true;updateAvailability();return;}
      state.sequence++;clearTimeout(previewTimer);
      const old=current.shots;
      document=clone(document);
      for(let i=0;i<document.shots.length;i++){
        if(old[i])document.shots[i].uid=old[i].uid;
        document.shots[i].reference_manifest=clone(draft.asset_manifest);
      }
      Object.assign(current,document,{uid:state.sourceUid,config:{...current.config,asset_manifest:clone(draft.asset_manifest)},prompt:draft.prompt});
      state.own=true;
      try {editor.value=draft.prompt;state.lastPrompt=draft.prompt;state.manifestBase=(A.h3ManifestSignature||JSON.stringify)(draft.asset_manifest);state.dirty=false;state.stale=false;}
      finally{state.own=false;}
      render();info('全文、参考与 Shot 已同步；点击顶部保存。');
    };
    read();
  };
})();
'''

SHOT_EDITOR_CSS = r'''
/* Scoped to the Shot editor so surrounding prompt tools keep their own hierarchy. */
.aicf-review-pane .h3-shot-editor{
  margin:16px 0;padding:16px;border:1px solid var(--border-strong);border-radius:12px;
  background:var(--surface-muted);color:var(--text-body);
  font:400 14px/1.6 var(--theme-font-ui);container-type:inline-size;
}
.h3-shot-editor *{box-sizing:border-box;min-width:0}
.h3-shot-editor .shot-editor-head,.h3-shot-editor .shot-editor-toolbar,
.h3-shot-editor .shot-doc-summary,.h3-shot-editor .shot-row-head{
  display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;
}
.h3-shot-editor .shot-editor-head>strong{font-size:16px;font-weight:700;color:var(--text-title)}
.h3-shot-editor .shot-editor-info{line-height:1.6;margin:10px 0;color:var(--text-secondary);overflow-wrap:anywhere;font-size:13px}
.h3-shot-editor .shot-editor-info.is-error{color:var(--danger-text)}
.h3-shot-editor .shot-editor-toolbar{justify-content:flex-start;padding-bottom:14px;border-bottom:1px solid var(--border)}
.h3-shot-editor .shot-editor-total{margin-left:auto;color:var(--text-secondary);font-size:13px}
.h3-shot-editor .shot-editor-document{margin-top:18px}
.h3-shot-editor .shot-doc-title{display:block;color:var(--text-title);font-size:15px;font-weight:700;overflow-wrap:anywhere}
.h3-shot-editor .shot-doc-summary{margin:8px 0 14px}
.h3-shot-editor .shot-doc-total{font-weight:600;color:var(--text-secondary)}
.h3-shot-editor .shot-doc-rows{display:grid;gap:20px}
.h3-shot-editor .shot-editor-row{
  overflow:hidden;padding:0 14px 14px;border:1px solid var(--border-strong);border-radius:12px;
  background:var(--surface);box-shadow:var(--theme-shadow);
}
.h3-shot-editor .shot-editor-row:focus-within{border-color:var(--theme-accent);box-shadow:var(--theme-active-ring)}
.h3-shot-editor .shot-row-head{
  margin:0 -14px 14px;padding:12px 14px;background:var(--accent-soft);
  border-bottom:1px solid var(--theme-accent-border);border-left:4px solid var(--theme-accent);
}
.h3-shot-editor .shot-row-head strong{font-size:16px;line-height:1.5;font-weight:700;color:var(--text-title)}
.h3-shot-editor .shot-time-range{
  padding:3px 8px;border:1px solid var(--theme-accent-border);border-radius:6px;
  background:var(--surface);color:var(--accent-hover);font-size:12px;font-weight:600;white-space:nowrap;
}
.h3-shot-editor .shot-editor-total,.h3-shot-editor .shot-time-range,.h3-shot-editor .shot-doc-total{font-variant-numeric:tabular-nums}
.h3-shot-editor .shot-line{margin:8px 0;line-height:1.7;color:var(--text-secondary);overflow-wrap:anywhere;white-space:pre-wrap}
.h3-shot-editor .shot-dialogues{display:grid;gap:12px;margin:0 0 16px}
.h3-shot-editor .shot-dialogue{
  display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;
  padding:12px;background:var(--surface-subtle);border:1px solid var(--border);border-radius:8px;
}
.aicf-review-pane .h3-shot-editor label{
  display:flex;flex-direction:column;gap:6px;margin:0;min-width:0;
  font:500 12px/1.5 var(--theme-font-ui);color:var(--text-secondary);
}
.aicf-review-pane .h3-shot-editor select,
.aicf-review-pane .h3-shot-editor textarea,
.aicf-review-pane .h3-shot-editor input[type=number]{
  width:100%;min-height:40px;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;
  background:var(--surface);color:var(--text-title) !important;font:400 14px/1.6 var(--theme-font-ui) !important;
}
.h3-shot-editor .shot-dialogue-text{grid-column:1/-1}
.aicf-review-pane .h3-shot-editor textarea{resize:vertical;min-height:80px;line-height:1.75}
.h3-shot-editor .shot-dialogue>.shot-delete{justify-self:end;grid-column:1/-1}
.h3-shot-editor .shot-duration-controls{
  display:grid;grid-template-columns:minmax(0,1fr) 92px;gap:14px;align-items:center;
  padding:14px 0;border-top:1px solid var(--border);
}
.h3-shot-editor .shot-duration-controls input[type=range]{width:100%;min-height:40px;accent-color:var(--theme-accent);cursor:pointer}
.aicf-review-pane .h3-shot-editor input[type=number]{font-weight:600;font-variant-numeric:tabular-nums}
.h3-shot-editor .shot-row-actions{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding-top:12px;border-top:1px solid var(--border)}
.aicf-review-pane .h3-shot-editor button{
  min-height:40px;padding:7px 12px;border:1px solid var(--border-strong);border-radius:6px;
  background:var(--surface);color:var(--text-body);font:500 13px/1.5 var(--theme-font-ui);
  cursor:pointer;white-space:normal;transition:background-color 150ms,border-color 150ms;
}
.aicf-review-pane .h3-shot-editor button:hover:not(:disabled){background:var(--surface-muted);border-color:var(--theme-accent)}
.aicf-review-pane .h3-shot-editor .shot-add-dialogue{border-style:dashed !important;color:var(--accent-hover) !important;background:var(--accent-soft) !important}
.aicf-review-pane .h3-shot-editor .shot-delete{color:var(--danger-text) !important;border-color:var(--theme-danger-border) !important;background:var(--surface) !important}
.aicf-review-pane .h3-shot-editor .shot-delete:hover:not(:disabled){background:var(--danger-soft) !important;border-color:var(--danger-text) !important}
.aicf-review-pane .h3-shot-editor :disabled{opacity:.5;cursor:not-allowed}
.h3-shot-editor :focus-visible{outline:2px solid var(--theme-accent);outline-offset:3px}
@container (max-width:320px){
  .h3-shot-editor .shot-dialogue{grid-template-columns:minmax(0,1fr)}
  .h3-shot-editor .shot-row-actions{grid-template-columns:minmax(0,1fr)}
}
@media (prefers-reduced-motion:reduce){.aicf-review-pane .h3-shot-editor button{transition:none}}
'''
