"""Web LLM configuration and video revision workspace UI."""

SETTINGS_HTML = '''
<section class="web-llm-settings web-llm-box">
  <h3>网页 LLM / Chrome 扩展</h3>
    <p>配置全局默认服务；各功能默认跟随全局，也可单独选择其他网页服务或 API。网页文字服务支持 DeepSeek、ChatGPT 和豆包。</p>
  <fieldset class="web-llm-provider-picker"><legend>全局默认服务</legend>
    <div class="web-llm-default-options web-llm-provider-options" aria-label="全局默认 LLM 服务"></div>
    <p class="web-llm-route-summary">加载配置…</p>
  </fieldset>
  <button type="button" onclick="window.AICF.saveWebLLMDefault(this)">保存全局默认</button>
  <div class="web-llm-entry-settings"></div>
  <details><summary>并行任务与标签页池</summary>
    <p>按需创建后台标签页并复用空闲页。同一标签页只执行一个任务；调低上限后，已开始的任务继续完成。</p>
    <label>网页任务总上限<input type="number" min="1" max="12" value="6" data-pool-limit="total"></label>
    <div class="web-llm-concurrency-providers"></div>
    <button type="button" onclick="window.AICF.saveWebLLMPool(this)">保存并发设置</button>
  </details>
  <details><summary>连接 Chrome 扩展</summary>
    <p>扩展代码更新后，请在 Chrome 扩展管理页重新加载。在扩展侧边栏填写本机地址和配对码，为每个服务绑定专用对话并启用连接。</p>
    <label>本机后台地址<input class="web-llm-address" readonly></label>
    <label>扩展配对码<input class="web-llm-token" type="password" readonly></label>
    <button type="button" onclick="window.AICF.copyWebLLMToken(this)">复制配对码</button>
  </details>
  <button type="button" onclick="window.AICF.refreshWebLLMSettings()">刷新连接状态</button>
  <span class="web-llm-connection" role="status"></span>
  <span class="web-llm-settings-status" role="status" aria-live="polite"></span>
</section>
'''


def route_controls(entries):
    labels = {"story": "剧情", "beats": "Beats / 小说拆分", "prompt": "分段提示词", "feedback": "问题分析", "other": "素材补全 / 其他调用"}
    return '<div class="web-llm-route-row">' + ''.join(
        f'<fieldset class="web-llm-provider-picker"><legend>{labels[entry]} LLM</legend><div class="web-llm-route-options web-llm-provider-options" data-entry="{entry}" aria-label="{labels[entry]} LLM 服务"></div><p class="web-llm-route-summary">加载配置…</p></fieldset>'
        for entry in entries
    ) + '<span class="web-llm-route-status" role="status"></span></div>'


WEB_LLM_JS = r"""
(() => {
  const A = window.AICF;
  if (A.webLLMInstalled) return;
  A.webLLMInstalled = true;
  let config = null, loading = null;
  const terminal = status => ['complete', 'error', 'cancelled', 'timeout'].includes(status);
  async function api(path, data) {
    const response = await fetch('/api/web-llm' + path, { method: data === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json'}, ...(data === undefined ? {} : {body: JSON.stringify(data)}) });
    const value = await response.json();
    if (!response.ok || !value.ok) throw new Error(value.message || '网页 LLM 请求失败');
    return value;
  }
  function option(select, value, label, disabled) {
    const item = document.createElement('option'); item.value = value; item.textContent = label; item.disabled = !!disabled; select.appendChild(item);
  }
  function options(select, current, inherit=true, includeApi=true) {
    select.replaceChildren();
    if (inherit) option(select, 'inherit', '跟随全局 / 功能默认');
    for (const provider of config.providers) option(select, provider.id, provider.label + (provider.available ? ' · 网页扩展' : ''), !provider.available);
    if (includeApi) option(select, 'api', 'DeepSeek · API');
    select.value = current || 'inherit';
  }
  function normalizedRoutes(value, inherit=true) {
    const routes=Array.isArray(value)?value:[value || (inherit?'inherit':'deepseek')];
    return [...new Set(routes.map(String))];
  }
  function pickerSummary(host) {
    const values=selectedRoutes(host),summary=host.parentElement?.querySelector('.web-llm-route-summary');
    if(!summary)return;
    if(!values.length)summary.textContent='未选择 LLM 服务';
    else if(values.includes('inherit'))summary.textContent='跟随全局默认服务';
    else {
      const names=values.map(value=>value==='api'?'DeepSeek API':config.providers.find(item=>item.id===value)?.label||value);
      summary.textContent=names.length===1?'固定使用 '+names[0]:'混合执行 · '+names.join('、');
    }
  }
  function renderRoutePicker(host,current,{inherit=true,includeApi=true}={}) {
    if(!host)return;
    const selected=normalizedRoutes(current,inherit);host.replaceChildren();
    const choices=[];
    if(inherit)choices.push({id:'inherit',label:'跟随全局',available:true});
    choices.push(...config.providers.map(provider=>({...provider,label:provider.label})));
    if(includeApi)choices.push({id:'api',label:'DeepSeek API',available:true});
    for(const provider of choices) {
      const label=document.createElement('label');label.className='web-llm-provider-chip';
      const input=document.createElement('input');input.type='checkbox';input.value=provider.id;
      input.checked=selected.includes(provider.id);input.disabled=provider.available===false;
      input.dataset.unavailable=provider.available===false?'1':'0';
      const text=document.createElement('span');text.textContent=provider.label;
      label.append(input,text);host.append(label);
    }
    pickerSummary(host);
  }
  function selectedRoutes(host) {
    return [...host.querySelectorAll('input:checked')].map(input=>input.value);
  }
  function normalizePickerChange(host,input) {
    if(input.value==='inherit'&&input.checked)for(const other of host.querySelectorAll('input'))if(other!==input)other.checked=false;
    if(input.value!=='inherit'&&input.checked){const inherit=host.querySelector('input[value="inherit"]');if(inherit)inherit.checked=false;}
    let selected=selectedRoutes(host);
    if(input.value==='api'&&input.checked)for(const other of host.querySelectorAll('input'))if(other!==input)other.checked=false;
    if(input.value!=='api'&&input.checked){const api=host.querySelector('input[value="api"]');if(api)api.checked=false;}
    selected=selectedRoutes(host);
    pickerSummary(host);return selected;
  }
  function displaySettings() {
    if (!config) return;
    document.querySelectorAll('.web-llm-settings').forEach(root => {
      renderRoutePicker(root.querySelector('.web-llm-default-options'),
        config.settings.default_providers||[config.settings.default_provider],{inherit:false,includeApi:false});
      root.querySelector('.web-llm-address').value = location.origin;
      root.querySelector('.web-llm-token').value = config.settings.token;
      const limitsHost=root.querySelector('.web-llm-concurrency-providers');
      const availableProviders=config.providers.filter(provider=>provider.available);
      const limitKeys=[...limitsHost.querySelectorAll('[data-pool-limit]')].map(input=>input.dataset.poolLimit);
      if(JSON.stringify(limitKeys)!==JSON.stringify(availableProviders.map(provider=>provider.id))) {
        limitsHost.replaceChildren();
        for(const provider of availableProviders) {
          const label=document.createElement('label');label.textContent=provider.label+' 并发上限';
          const input=document.createElement('input');input.type='number';input.min='1';input.max='4';input.value='2';input.dataset.poolLimit=provider.id;
          label.append(input);limitsHost.append(label);
        }
      }
      root.querySelectorAll('[data-pool-limit]').forEach(input => {
        if(document.activeElement!==input) input.value=config.settings.concurrency?.[input.dataset.poolLimit] ?? (input.dataset.poolLimit==='total'?6:2);
      });
      root.querySelector('.web-llm-connection').textContent = config.connection.connected ? '扩展在线 · 已绑定 ' + (config.connection.providers.join('、') || '尚无服务') : '尚未连接扩展，请在扩展中配对并启用';
      const entries = root.querySelector('.web-llm-entry-settings');
      if (!entries.children.length) for (const [key, title] of Object.entries(config.entries)) {
        const fieldset=document.createElement('fieldset');fieldset.className='web-llm-provider-picker';
        const legend=document.createElement('legend');legend.textContent=title;
        const host=document.createElement('div');host.className='web-llm-route-options web-llm-provider-options';host.dataset.entry=key;host.setAttribute('aria-label',title+' LLM 服务');
        const summary=document.createElement('p');summary.className='web-llm-route-summary';fieldset.append(legend,host,summary);entries.append(fieldset);
      }
    });
    document.querySelectorAll('.web-llm-route-options').forEach(host => {
      renderRoutePicker(host,config.settings.overrides[host.dataset.entry]||'inherit',
        {inherit:true,includeApi:host.dataset.entry!=='revision'});
      host.dataset.ready = '1';
    });
  }
  A.refreshWebLLMSettings = async function() {
    if (loading) return loading;
    loading = api('/settings').then(value => { config = value; displaySettings(); return value; }).catch(error => {
      document.querySelectorAll('.web-llm-settings-status,.web-llm-route-status').forEach(el => {el.textContent = error.message;}); throw error;
    }).finally(() => {loading = null;});
    return loading;
  };
  A.saveWebLLMDefault = async function(button) {
    const root = button.closest('.web-llm-settings'); button.disabled = true;
    try { await api('/settings', {default_providers:selectedRoutes(root.querySelector('.web-llm-default-options'))}); await A.refreshWebLLMSettings(); root.querySelector('.web-llm-settings-status').textContent = '默认服务已保存；选择一个时固定使用，选择多个时混合执行。'; }
    catch(error) { root.querySelector('.web-llm-settings-status').textContent = error.message; }
    finally {button.disabled = false;}
  };
  A.copyWebLLMToken = async function(button) {
    const root = button.closest('.web-llm-settings');
    try {await navigator.clipboard.writeText(root.querySelector('.web-llm-token').value); root.querySelector('.web-llm-settings-status').textContent = '配对码已复制';}
    catch {root.querySelector('.web-llm-settings-status').textContent = '剪贴板不可用，请手动选择配对码复制。';}
  };
  A.saveWebLLMPool = async function(button) {
    const root=button.closest('.web-llm-settings');button.disabled=true;
    try {
      const concurrency={};
      for(const input of root.querySelectorAll('[data-pool-limit]')) {
        if(!input.reportValidity())return;
        concurrency[input.dataset.poolLimit]=Number(input.value);
      }
      await api('/settings',{concurrency});await A.refreshWebLLMSettings();
      root.querySelector('.web-llm-settings-status').textContent='并发设置已保存，新任务按新上限分配；运行中的任务继续完成。';
    } catch(error) {root.querySelector('.web-llm-settings-status').textContent=error.message;}
    finally {button.disabled=false;}
  };
  document.addEventListener('change', async event => {
    const input = event.target;
    const defaultHost=input.closest('.web-llm-default-options');
    if(defaultHost&&input.matches('input[type="checkbox"]')){normalizePickerChange(defaultHost,input);return;}
    const host=input.closest('.web-llm-route-options');
    if(!host||!input.matches('input[type="checkbox"]'))return;
    const selected=normalizePickerChange(host,input);
    const root = host.closest('.web-llm-settings,.web-llm-route-row');
    const message = root.querySelector('.web-llm-settings-status,.web-llm-route-status');
    host.querySelectorAll('input').forEach(node=>node.disabled=true);
    try {await api('/settings', {overrides: {[host.dataset.entry]: selected}}); await A.refreshWebLLMSettings(); if(message) message.textContent = selected.length>1?'已保存，后续任务将混合使用所选服务':'此功能的服务选择已保存';}
    catch(error) {if(message) message.textContent = error.message;}
    finally {host.querySelectorAll('input').forEach(node=>node.disabled=node.dataset.unavailable==='1');}
  });
  const REVIEW_DRAFTS_KEY = 'aicf_web_llm_review_drafts_v1';
  function readDrafts() {
    try {
      const value = JSON.parse(localStorage.getItem(REVIEW_DRAFTS_KEY) || '{}');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    } catch { return {}; }
  }
  function writeDraft(key, value) {
    try {
      const drafts = readDrafts(); drafts[key] = {...value, savedAt: Date.now()};
      const entries = Object.entries(drafts).sort((a,b) => Number(b[1]?.savedAt || 0) - Number(a[1]?.savedAt || 0)).slice(0, 500);
      localStorage.setItem(REVIEW_DRAFTS_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch (error) { console.warn('反馈修正草稿保存失败', error); }
  }
  A.mountPromptRevision = async function(ov) {
    if (ov._webRevisionStop) ov._webRevisionStop();
    const session = String(ov.dataset.h3Session), index = Number(ov.dataset.segmentIndex);
    const runtimeRevision = String(A.promptRuntimeRevision || 0);
    let stopped = false, pollTimer, snapshot, task, busy = false, loadingSnapshot = false;
    let durationLoaded = false;
    let reviewDraftLoaded = false;
    let review = ov.querySelector('.aicf-review-pane');
    if (!review) {
      review = document.createElement('aside'); review.className = 'aicf-review-pane';
      ov.querySelector('.aicf-prompt-modal-content').appendChild(review);
    }
    const previous = review.querySelector('.aicf-review-content'); if(previous) previous.remove();
    const content = document.createElement('div'); content.className = 'aicf-review-content';
    content.innerHTML = '<div class="aicf-review-head"><strong>本段最新视频</strong><button type="button" data-action="refresh">刷新预览</button></div><video controls preload="metadata" playsinline hidden></video><p class="review-video-info">加载视频…</p><details class="review-original"><summary>生成视频时使用的提示词</summary><textarea class="review-original-text" rows="5" aria-label="视频生成时的原提示词" readonly></textarea></details><label class="review-source-choice"><input type="checkbox" class="review-use-editor"> 使用编辑器提示词</label><p class="review-source-hint">未勾选：使用视频生成时的提示词修正。</p><label>本段时长（秒）<input type="number" class="review-duration" min="1" step="1" aria-label="本段时长（秒）"></label><p class="review-duration-hint">保存或提交修正时同步对应 Beat；提交修正后按新时长重新安排镜头。</p><h3>反馈并修正</h3><fieldset class="web-llm-provider-picker review-provider-picker"><legend>本次使用的 LLM</legend><div class="review-provider-options web-llm-provider-options" aria-label="本次修正 LLM"></div><p class="web-llm-route-summary"></p></fieldset><label>视频出现的问题<textarea class="review-issue" rows="3" placeholder="描述问题，可标明发生时间" aria-label="视频问题描述"></textarea></label><button type="button" data-action="timestamp">插入当前播放时间</button><label>期望效果（选填）<textarea class="review-expected" rows="2" aria-label="期望效果"></textarea></label><div class="review-actions"><button type="button" class="review-submit" data-action="submit">提交给网页 LLM 修正</button><button type="button" data-action="cancel" hidden>取消等待</button></div><p class="review-state" role="status" aria-live="polite">准备就绪</p><div class="review-result" hidden><h3>修改说明</h3><ul class="review-changes"></ul><details><summary>查看提交前的编辑稿</summary><pre class="review-before"></pre></details><label>修正后的完整提示词</label><div class="review-revision-meta"></div><div class="review-compare-state" role="status"></div><textarea class="review-revised" rows="7" readonly aria-label="修正版提示词"></textarea><div class="review-actions"><button type="button" data-action="apply">应用到编辑器</button><button type="button" data-action="generate">应用并生成视频</button></div></div>';
    review.appendChild(content);
    const q = selector => content.querySelector(selector);
    const selectedDetails = document.createElement('details');
    selectedDetails.className = 'review-selected';
    selectedDetails.innerHTML = '<summary>本次将提交的提示词</summary><pre class="review-selected-prompt"></pre>';
    q('.review-source-hint').after(selectedDetails);
    const editor = ov.querySelector('.aicf-prompt-modal-body');
    function providerName(provider) {
      return config?.providers?.find(item=>item.id===provider)?.label || provider || '网页 LLM';
    }
    function taskProvenance(value) {
      return {source:'web_revision',version:Number(value?.metadata?.prompt_version || 0),updated_at:value?.updated_at,provider:value?.provider || '网页 LLM',task_id:value?.id || ''};
    }
    function renderRevisionComparison() {
      if(!task?.result)return;
      const provenance=taskProvenance(task);
      q('.review-revision-meta').textContent='最新修正版：'+A.formatPromptVersion({...provenance,provider:providerName(provenance.provider)},'网页 LLM');
      const savedHere=snapshot?.prompt_version_info?.source==='web_revision' && snapshot.prompt_version_info.task_id===task.id;
      const comparisonPrompt=task.result.revised_prompt;
      const matches=A.promptTextMatches(editor.value,comparisonPrompt);
      const state=q('.review-compare-state');
      state.textContent=savedHere
        ? (matches?'编辑器已载入后台保存的修正版':'H3 已保存此修正版；编辑器仍是旧内容，可点击“应用到编辑器”')
        : (matches?'编辑器与修正版一致':'编辑器尚未载入修正版');
      state.classList.toggle('is-match',matches);
      state.classList.toggle('is-different',!matches);
    }
    function referenceState() {
      if(String(ov.dataset.modelId)==='minimax_h3_local_ref2va') {
        const manifest=ov.querySelector('.aicf-h3-config-panel .h3-asset-list');
        const uploads=Array.from(ov.querySelectorAll('.aicf-h3-config-panel input[type="file"]')).map(input=>Array.from(input.files || []).map(file=>[file.name,file.size,file.lastModified]));
        return JSON.stringify([manifest?.dataset.manifest || '',uploads]);
      }
      return JSON.stringify(A.promptModalMaterialSelection(ov));
    }
    function revisionInputState(value, ignoreCurrentPrompt=false) {
      value=value || {};
      return JSON.stringify({
        runtime:value.runtime || '',
        render_id:value.render_id || '',
        video_url:value.video_url || '',
        original_prompt:value.original_prompt || '',
        ...(!ignoreCurrentPrompt?{current_prompt:value.current_prompt || ''}:{}),
        duration_sec:value.duration_sec,
        context:value.context || {}
      });
    }
    const valid = () => !stopped && ov.classList.contains('open') && String(ov.dataset.h3Session) === session && String(A.promptRuntimeRevision || 0) === runtimeRevision;
    ov._webRevisionStop = () => { stopped = true; clearTimeout(pollTimer); q('video').pause(); };
    function message(text, error=false) { if(valid()) {q('.review-state').textContent = text; q('.review-state').classList.toggle('is-error',error);} }
    function setControls() {
      const pending = task && !terminal(task.status);
      q('[data-action="submit"]').disabled = busy || !!pending || !snapshot || !!A.archiveReadOnly;
      q('[data-action="submit"]').textContent = A.archiveReadOnly ? '归档只读，无法提交修正' : '提交给网页 LLM 修正';
      q('[data-action="submit"]').title = A.archiveReadOnly ? '当前为已归档集数，请切换到当前工作区后提交修正。' : '';
      q('[data-action="cancel"]').hidden = !pending;
      for (const action of ['apply','generate']) q('[data-action="'+action+'"]').disabled = busy || !task?.result || !!A.archiveReadOnly;
    }
    function showTask(value) {
      task = value; setControls();
      q('.review-result').hidden = !task?.result;
      if (!task) { message(A.archiveReadOnly ? '当前是已归档集数，只能查看。请切换到当前工作区后提交修正。' : '准备就绪'); return; }
      const target=task.metadata?.snapshot || {};
      const location=[target.project_name,target.episode_name,'第 '+(task.metadata?.segment_index || index)+' 段'].filter(Boolean).join(' / ');
      const operationId=String(task.metadata?.operation_id || '');
      const tracking=operationId?'后台任务 '+operationId.slice(0,8)+' · '+location+'。可以关闭编辑器，继续处理其他分段或草稿集。':'';
      const operation=task.business_operation;
      const savedNote=operation?.state==='applied'?'修正版已保存到原分段，可载入编辑器或生成视频。':
        operation?.state==='ready'?'网页回复已到达，候选结果需要核对。':'';
      message([tracking,savedNote,task.message || ({queued:'等待扩展领取', claimed:'扩展已领取', waiting:'网页 LLM 处理中', cancelled:'已取消等待',timeout:'等待超时，请检查扩展'}[task.status] || task.status)].filter(Boolean).join(' '), ['error','timeout'].includes(task.status));
      if (task.result) {
        q('.review-changes').replaceChildren();
        for(const change of task.result.changes || []) {const li=document.createElement('li');li.textContent=change;q('.review-changes').appendChild(li);}
        q('.review-before').textContent = task.input.base_prompt;
        q('.review-revised').value = task.result.revised_prompt;
        renderRevisionComparison();
      }
    }
    async function loadSnapshot() {
      if(loadingSnapshot) return; loadingSnapshot=true;
      try {
        const result = await api('/segments/'+index); if(!valid()) return;
        const next = result.snapshot;
        if(snapshot && next.runtime !== snapshot.runtime) throw new Error('当前项目已切换，请重新打开编辑器');
        const changed = !snapshot || next.video_url !== snapshot.video_url;
        snapshot = next;
        if(String(ov.dataset.modelId)==='minimax_h3_local_ref2va') {
          A.renderH3PromptVersion(ov,{current:next.prompt_version_info || {},revision:next.latest_prompt_revision || {},matches:!!next.prompt_matches_revision});
        }
        if(!durationLoaded) {q('.review-duration').value=next.duration_sec || '';durationLoaded=true;}
        q('.review-duration').disabled=!!A.archiveReadOnly;
        if(changed) { const video=q('video'); video.pause(); video.hidden=!next.video_url; if(next.video_url) video.src=next.video_url; else video.removeAttribute('src'); }
        q('.review-video-info').textContent = next.video_url ? next.video_name + (next.video_created_at ? ' · '+next.video_created_at : '') : '本段还没有可预览的视频；新视频生成后可刷新。';
        const original=q('.review-original-text');
        if(next.snapshot_known) {original.value=next.original_prompt;original.readOnly=true;}
        else {original.readOnly=false;original.placeholder='历史生成快照缺失，请粘贴这个视频实际使用的提示词，不能用当前编辑稿冒充。';}
        if(!reviewDraftLoaded) {
          const key=[next.runtime,next.segment_uid || next.beat_uid || ('index-' + index)].join(':');
          const saved=readDrafts()[key];
          if(saved) {
            q('.review-use-editor').checked=!!saved.useEditor;
            if(saved.duration)q('.review-duration').value=saved.duration;
            q('.review-issue').value=saved.issue || '';
            q('.review-expected').value=saved.expected || '';
            if(saved.providers)renderRoutePicker(q('.review-provider-options'),saved.providers,{inherit:true,includeApi:false});
            else if(saved.provider)renderRoutePicker(q('.review-provider-options'),saved.provider,{inherit:true,includeApi:false});
            if(!next.snapshot_known)original.value=saved.original || '';
          } else if(result.task) {
            q('.review-use-editor').checked=result.task.metadata.prompt_source==='editor';
            q('.review-issue').value=result.task.input.video_issues || '';
            q('.review-expected').value=result.task.input.expected_result || '';
          }
          reviewDraftLoaded=true;
        }
        if(!task || result.task?.id === task.id) showTask(result.task);
        else renderRevisionComparison();
        setControls();
      } finally {loadingSnapshot=false;}
    }
    function sourceHint() {
      const useEditor = q('.review-use-editor').checked;
      q('.review-source-hint').textContent=useEditor
        ? '已勾选：直接使用左侧当前编辑稿修正，包含尚未保存的修改；本次将在网页 LLM 中开启新对话。'
        : '未勾选：使用视频生成时的提示词修正。';
      q('.review-selected-prompt').textContent = (useEditor ? String(editor.value || '') : String(q('.review-original-text').value || '')).trim();
    }
    q('.review-use-editor').addEventListener('change',sourceHint);
    editor.addEventListener('input',sourceHint);
    q('.review-original-text').addEventListener('input',sourceHint);
    ov._savePromptDuration = async function() {
      if(!valid() || !snapshot)throw new Error('时长信息尚未加载，请稍后保存');
      const duration=Number(q('.review-duration').value);
      if(!Number.isFinite(duration) || duration<=0 || !Number.isInteger(duration))throw new Error('本段时长请输入正整数秒');
      if(duration===Number(snapshot.duration_sec))return;
      const fresh=(await api('/segments/'+index)).snapshot;
      if(!valid() || fresh.runtime!==snapshot.runtime || fresh.segment_uid!==snapshot.segment_uid)throw new Error('编辑分段已切换，请重新打开编辑器');
      if(fresh.duration_sec!==snapshot.duration_sec)throw new Error('本段时长已在其他位置修改，请重新打开编辑器');
      const result=await api('/segments/'+index+'/duration', {
        runtime:fresh.runtime,fingerprint:fresh.fingerprint,duration_sec:duration
      });
      if(!valid())throw new Error('编辑分段已切换，请重新打开编辑器');
      snapshot=result.snapshot;
      q('.review-duration').value=snapshot.duration_sec;
      const card=A.segmentCardFromButton(ov._sourceButton);
      if(card)card.querySelectorAll('.metric-card').forEach(metric=>{
        if(metric.querySelector('span')?.textContent==='时长')metric.querySelector('b').textContent=snapshot.duration_sec+' 秒';
      });
      const beatsBox=document.querySelector('#beats_editor textarea');
      if(beatsBox) {
        const pattern=new RegExp('(【Beat '+index+'】[^\\n]*\\n时长[：:])[^\\n]*');
        const updated=beatsBox.value.replace(pattern,(_,prefix)=>prefix+snapshot.duration_sec+'秒');
        if(updated!==beatsBox.value){beatsBox.value=updated;beatsBox.dispatchEvent(new Event('input',{bubbles:true}));}
      }
      const panel=card?.closest('.right-panel');
      if(panel) {
        let total=0;
        panel.querySelectorAll('.metric-card').forEach(metric=>{
          if(metric.querySelector('span')?.textContent==='时长')total+=parseFloat(metric.querySelector('b').textContent)||0;
        });
        panel.querySelectorAll('.stats-pill').forEach(pill=>{pill.textContent=pill.textContent.replace(/当前页时长：\d+ 秒/,'当前页时长：'+total+' 秒');});
      }
      q('.review-duration-hint').textContent='时长已保存，并同步对应 Beat 和生成配置。';
    };
    q('video').addEventListener('error',()=>{if(valid())q('.review-video-info').textContent='视频加载失败，可刷新预览或检查原视频文件。';});
    async function poll() {
      if(!valid()) return;
      try {
        const awaitingBackgroundApply=task?.status==='complete' && !!task?.metadata?.operation_id &&
          !['applied','error','cancelled','needs_attention'].includes(task?.business_operation?.state);
        if(task && (!terminal(task.status) || awaitingBackgroundApply)) {
          const value=await api('/tasks/'+task.id);
          if(valid()) {
            showTask(value.task);
            if(value.task?.business_operation?.state==='applied' && snapshot?.prompt_version_info?.task_id!==value.task.id) await loadSnapshot();
          }
        }
      }
      catch(error) {message(error.message,true);}
      if(valid())pollTimer=setTimeout(poll,2500);
    }
    function draftKey() {
      if(!snapshot) return null;
      const stableId = snapshot.segment_uid || snapshot.beat_uid || ('index-' + index);
      return [snapshot.runtime, stableId].join(':');
    }
    function saveDraft() {
      const key=draftKey(); if(!key) return;
      writeDraft(key,{issue:q('.review-issue').value,expected:q('.review-expected').value,
        providers:selectedRoutes(q('.review-provider-options')),original:q('.review-original-text').value,
        useEditor:q('.review-use-editor').checked,duration:q('.review-duration').value});
    }
    content.addEventListener('input',saveDraft);
    content.addEventListener('change',saveDraft);
    content.addEventListener('click', async event => {
      const button=event.target.closest('button[data-action]'); if(!button || busy || !valid())return;
      const action=button.dataset.action;
      const originalLabel=button.textContent;
      if(action==='apply' || action==='generate')button.textContent='正在核对并应用…';
      busy=true;setControls();
      try {
        if(action==='refresh')await loadSnapshot();
        if(action==='timestamp') {q('.review-issue').value += ' ['+q('video').currentTime.toFixed(1)+' 秒] ';q('.review-issue').dispatchEvent(new Event('input',{bubbles:true}));q('.review-issue').focus();}
        if(action==='submit') {
          if(!snapshot)throw new Error('请先加载视频信息');
          await ov._savePromptDuration();
          await A.savePromptSourceExcerpt(false);
          await loadSnapshot();
          if(!valid())return;
          const useEditor=q('.review-use-editor').checked;
          if(useEditor && !String(editor.value || '').trim())throw new Error('编辑器提示词为空，无法提交。');
          const payload={runtime:snapshot.runtime,fingerprint:snapshot.fingerprint,providers:selectedRoutes(q('.review-provider-options')),issue:q('.review-issue').value,expected:q('.review-expected').value,edited_prompt:editor.value,use_editor_prompt:useEditor,original_prompt:useEditor?'':q('.review-original-text').value,editor_references:referenceState()};
          const value=await api('/segments/'+index+'/revisions',payload); if(valid()) {showTask(value.task);const target=value.task?.metadata?.snapshot||{};const location=[target.project_name,target.episode_name,'第 '+index+' 段'].filter(Boolean).join(' / ');message('已加入后台任务列表（'+String(value.operation_id||'').slice(0,8)+'），绑定到 '+location+'。可以关闭窗口继续处理其他分段或草稿集。');}
        }
        if(action==='cancel' && task) {const value=await api('/tasks/'+task.id+'/cancel',{});if(valid())showTask(value.task);}
        if(action==='apply' || action==='generate') {
          if(!task?.result)throw new Error('还没有可应用的修正版');
          if(A.archiveReadOnly)throw new Error('归档只读，不能应用修正');
          if(task.input.target_duration_sec!=null && Number(q('.review-duration').value)!==Number(task.input.target_duration_sec))throw new Error('时长已修改，请按新时长重新提交修正后再应用。');
          const fresh=await api('/segments/'+index); if(!valid())return;
          const currentInfo=fresh.snapshot.prompt_version_info || {};
          const revisionVersion=Number(task.metadata?.prompt_version || 0);
          const savedThisTask=currentInfo.source==='web_revision' && currentInfo.task_id===task.id &&
            Number(currentInfo.version || 0)===revisionVersion &&
            fresh.snapshot.latest_prompt_revision?.task_id===task.id &&
            A.promptTextMatches(fresh.snapshot.current_prompt,fresh.snapshot.latest_prompt_revision.prompt);
          if(!savedThisTask && revisionVersion && Number(currentInfo.version || 0)>=revisionVersion)
            throw new Error('H3 已有同版或更新的提示词，请刷新编辑器后核对，未覆盖现有内容。');
          const target=task.metadata.snapshot || {};
          if(fresh.snapshot.runtime!==target.runtime ||
             Number(fresh.snapshot.segment_index)!==Number(task.metadata.segment_index) ||
             (target.segment_uid && fresh.snapshot.segment_uid!==target.segment_uid) ||
             (target.beat_uid && fresh.snapshot.beat_uid!==target.beat_uid))
            throw new Error('项目或分段已切换，请重新打开对应分段后应用修正版。');
          // A saved revision is already authoritative for this segment. Saving
          // can normalize its asset manifest; loading that exact saved version
          // must not compare it against the pre-save configuration.
          if(!savedThisTask && revisionInputState(fresh.snapshot)!==revisionInputState(target))
            throw new Error('视频、参考配置或保存的提示词已变化；请对比修正版后手动合并，未覆盖编辑器。');
          if(task.metadata.editor_references && task.metadata.editor_references!==referenceState())throw new Error('等待期间参考素材已修改，请对比后手动合并，未覆盖编辑器。');
          const appliedPrompt=task.result.revised_prompt;
          editor.value=appliedPrompt;
          ov.dataset.promptRevisionPrompt=appliedPrompt;
          ov.dataset.promptProvenance=JSON.stringify(taskProvenance(task));
          editor.dispatchEvent(new Event('input',{bubbles:true}));
          editor.scrollTop=0;
          snapshot=fresh.snapshot;
          if(String(ov.dataset.modelId)==='minimax_h3_local_ref2va')
            A.renderH3PromptVersion(ov,{current:snapshot.prompt_version_info || {},revision:snapshot.latest_prompt_revision || {},matches:!!snapshot.prompt_matches_revision});
          renderRevisionComparison();
          const originalSaved=A.promptTextMatches(fresh.snapshot.current_prompt,appliedPrompt);
          message(originalSaved?'已载入 LLM 原始修正版到编辑器。':'已载入 LLM 原始修正版，尚未保存。可以继续编辑或点击保存。');
          if(action==='generate') {
            const version=session;
            await ov._savePromptDuration();
            const material=A.promptModalMaterialSelection(ov);
            if(!savedThisTask || !originalSaved) {
              if(String(ov.dataset.modelId)==='minimax_h3_local_ref2va') await A.saveH3GenerationConfig(index,null,ov);
              else await A.saveSegmentPrompt(index,null,editor.value,{...material,full_prompt:editor.value,build_workflow:false});
            }
            if(!valid() || version!==String(ov.dataset.h3Session))return;
            const channel=await A.chooseVideoChannel(index);
            if(!channel){message('修正版已保存，尚未提交视频生成。');return;}
            const regen={segment_index:index,channel_id:channel.id};
            if(String(ov.dataset.modelId)!=='minimax_h3_local_ref2va')Object.assign(regen,{...material,director_prompt:editor.value,full_prompt:editor.value});
            const generation=await fetch('/api/segment/regenerate_video',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(regen)});
            const value=await generation.json(); if(!generation.ok || !value.ok)throw new Error(value.message || '提交生成失败');
            if(valid()) {A.replaceSegmentCardFromResponse(index,value,A.segmentCardFromButton(ov._sourceButton),'.primary-mini');if(value.web_video_task_id)A.watchWebVideoTask(index,value.web_video_task_id);message('修正版已保存，视频生成任务已提交。完成后刷新预览。');}
          }
        }
      } catch(error) {message(error.message,true);}
      finally {button.textContent=originalLabel;busy=false;if(valid())setControls();}
    });
    setControls();
    if(ov._revisionEditorHandler)editor.removeEventListener('input',ov._revisionEditorHandler);
    ov._revisionEditorHandler=renderRevisionComparison;
    editor.addEventListener('input',ov._revisionEditorHandler);
    try {
      await A.refreshWebLLMSettings();if(!valid())return;
      renderRoutePicker(q('.review-provider-options'),'inherit',{inherit:true,includeApi:false});
      q('.review-provider-options').addEventListener('change',event=>{if(event.target.matches('input'))normalizePickerChange(q('.review-provider-options'),event.target);});
      await loadSnapshot();if(!valid())return;
      sourceHint();
      poll();
    } catch(error){message(error.message,true);}
  };
  let scheduled;
  function hydrate() {
    if(document.querySelector('.web-llm-settings:not([data-ready]),.web-llm-route-options:not([data-ready])')) {
      document.querySelectorAll('.web-llm-settings').forEach(el=>{el.dataset.ready='1';});
      if(config)displaySettings();else A.refreshWebLLMSettings().catch(()=>{});
    }
  }
  new MutationObserver(()=>{clearTimeout(scheduled);scheduled=setTimeout(hydrate,100);}).observe(document.body,{childList:true,subtree:true});
  hydrate();
})();
"""

WEB_LLM_CSS = """
.web-llm-box{padding:16px;border:1px solid var(--border);border-radius:10px;background:var(--surface);margin:12px 0}
.web-llm-box>h3{color:var(--text-title);font-size:16px;font-weight:700;line-height:1.45}
.web-llm-box>p,.web-llm-connection{font-size:13px;color:var(--text-secondary);line-height:1.6}
.web-llm-entry-settings,.web-llm-route-row{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}
.web-llm-entry-settings>.web-llm-provider-picker,.web-llm-route-row>.web-llm-provider-picker{flex:1 1 300px}
.web-llm-box label{display:block;margin:8px 0}.web-llm-settings-status,.web-llm-route-status{display:block;font-size:12px;padding:4px;color:var(--text-secondary)}
.web-llm-box select,.web-llm-box input,.web-llm-route-row select,.aicf-review-pane select,.aicf-review-pane textarea{display:block;width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--border);border-radius:6px;padding:8px;background:var(--surface);color:var(--text-body);font:inherit}
.web-llm-provider-picker{min-width:0;margin:10px 0;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface-subtle)}
.web-llm-provider-picker legend{padding:0 4px;font-size:13px;font-weight:700;color:var(--text-body)}
.web-llm-provider-options{display:flex;flex-wrap:wrap;gap:8px}
.web-llm-box .web-llm-provider-chip,.web-llm-route-row .web-llm-provider-chip,.aicf-review-pane .web-llm-provider-chip{display:flex;align-items:center;gap:7px;min-height:40px;margin:0;padding:7px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text-body);font-size:13px;font-weight:600;cursor:pointer;user-select:none}
.web-llm-box .web-llm-provider-chip>span,.web-llm-route-row .web-llm-provider-chip>span,.aicf-review-pane .web-llm-provider-chip>span{color:var(--text-body)!important;-webkit-text-fill-color:var(--text-body)!important;opacity:1!important}
.web-llm-box .web-llm-provider-chip:has(input:checked),.web-llm-route-row .web-llm-provider-chip:has(input:checked),.aicf-review-pane .web-llm-provider-chip:has(input:checked){border-color:var(--theme-accent);background:color-mix(in srgb,var(--theme-accent) 10%,var(--surface))}
.web-llm-box .web-llm-provider-chip:has(input:disabled),.web-llm-route-row .web-llm-provider-chip:has(input:disabled),.aicf-review-pane .web-llm-provider-chip:has(input:disabled){opacity:1!important;color:var(--text-secondary);border-color:var(--border-strong);background:var(--surface-subtle);cursor:not-allowed}
.web-llm-box .web-llm-provider-chip:has(input:disabled)>span,.web-llm-route-row .web-llm-provider-chip:has(input:disabled)>span,.aicf-review-pane .web-llm-provider-chip:has(input:disabled)>span{color:var(--text-secondary)!important;-webkit-text-fill-color:var(--text-secondary)!important;opacity:1!important}
.web-llm-box .web-llm-provider-chip input,.web-llm-route-row .web-llm-provider-chip input,.aicf-review-pane .web-llm-provider-chip input{display:block;width:16px;height:16px;min-width:16px;margin:0;padding:0;accent-color:var(--theme-accent)}
.web-llm-route-summary{min-height:20px;margin:6px 0 0!important;color:var(--text-muted)}
.web-llm-concurrency-providers{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px 12px}
.web-llm-box select option,.web-llm-route-row select option,.aicf-review-pane select option{background-color:var(--surface);color:var(--text-body)}
.web-llm-box select option:disabled,.web-llm-route-row select option:disabled,.aicf-review-pane select option:disabled{color:var(--text-secondary);-webkit-text-fill-color:var(--text-secondary);opacity:1}
.web-llm-box button,.aicf-review-pane button{border:1px solid var(--border);border-radius:6px;padding:8px 10px;min-height:36px;background:var(--surface);color:var(--text-body);cursor:pointer;font-size:12px}
.aicf-review-pane button:disabled{opacity:.5;cursor:not-allowed}.aicf-review-pane .review-submit{background:var(--theme-accent);color:var(--theme-accent-foreground)}
.aicf-review-pane{overflow-y:auto;min-width:0;padding:14px;border-left:1px solid var(--border);background:var(--surface-subtle)}
.aicf-review-pane video{width:100%;max-height:280px;background:#111827;border-radius:8px;object-fit:contain;margin-top:10px}
.aicf-review-head,.review-actions{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.aicf-review-pane label{display:block;font-size:12px;font-weight:600;margin:12px 0 6px}.aicf-review-pane h3{font-size:14px;margin-top:16px}
.aicf-review-pane textarea{resize:vertical;font-size:13px;line-height:1.6}.aicf-review-pane summary{cursor:pointer;font-size:13px;padding:8px 0}
.review-source-hint,.review-duration-hint{font-size:12px;color:var(--text-muted);line-height:1.6}.review-selected{margin:8px 0;padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--surface)}.review-selected summary{cursor:pointer;font-size:12px;font-weight:650;color:var(--text-secondary)}.review-selected-prompt{max-height:220px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--text-body)}.aicf-review-pane .review-source-choice{display:flex;align-items:center;gap:8px;min-height:36px}.review-use-editor{width:16px;height:16px;flex:none}.review-duration{box-sizing:border-box;width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text-body)}
.review-video-info,.review-state{font-size:12px;line-height:1.6;overflow-wrap:anywhere}.review-state{padding:10px;background:var(--surface);border:1px solid var(--border);border-radius:6px}.review-state.is-error{color:var(--danger-text)}
.review-actions{margin-top:12px}.review-actions button{flex:1}.review-before{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.review-changes{font-size:13px;padding-left:20px}
.review-revision-meta{margin:6px 0 0;color:var(--text-secondary);font-size:12px;line-height:1.5;font-variant-numeric:tabular-nums}.review-compare-state{margin:6px 0 8px;padding:6px 8px;border:1px solid var(--warning-border);border-radius:6px;background:var(--warning-soft);color:var(--warning-text);font-size:12px;font-weight:650}.review-compare-state.is-match{border-color:var(--success-border);background:var(--success-soft);color:var(--success-text)}.review-revised{width:100%;box-sizing:border-box}
.aicf-review-pane [hidden]{display:none!important}.aicf-review-pane :focus-visible,.web-llm-box :focus-visible,.web-llm-route-row :focus-visible{outline:2px solid var(--theme-accent);outline-offset:2px}
.aicf-prompt-modal-content:has(.aicf-review-pane){grid-template-columns:clamp(240px,19vw,340px) minmax(0,1fr) clamp(320px,26vw,480px)}
.aicf-prompt-modal-content>.aicf-prompt-material-panel[hidden]{display:none!important}
@media(max-width:1100px) and (min-width:861px){.aicf-prompt-modal-content:has(.aicf-review-pane){grid-template-columns:220px minmax(0,1fr) 300px}.aicf-review-pane{padding:10px}.aicf-prompt-material-panel{padding:10px}}
@media(max-width:860px){.aicf-review-pane{overflow:visible;flex:0 0 auto;border-left:0;border-top:1px solid var(--border)}.aicf-review-pane video{max-height:240px}.aicf-prompt-editor-pane{order:0}.aicf-review-pane{order:1}.aicf-prompt-material-panel{order:2}.aicf-prompt-editor-head{flex-wrap:wrap}}
"""
