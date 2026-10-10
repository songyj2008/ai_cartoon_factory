(() => {
  "use strict";
  if (globalThis.__aicfPromptBridge) return;
  globalThis.__aicfPromptBridge = true;
  const P = globalThis.PromptBridge;
  const site = P.provider(location.href);
  if (!site) return;
  const pagePolicy=globalThis.TextProviderPolicy?.fromName(site);
  let task = null, timer = null, writing = false, lastText = "", stableSince = 0, operating = false, sending = false;
  const RESULT_RECHECK_MS = 300;
  let lastEnvelope = "", envelopeSince = 0;
  let replyObserver = null, replyDebounce = null, replySettled = null, continueCheck = null;
  const taskStorageKey = value => value?.bridge ? `bridgeTask:${value.id}` : 'task';
  const taskDeadline = value => Number(value?.deadline_at) > 0 ? Number(value.deadline_at) * 1000 :
    Date.parse(value?.created_at || value?.sent_at) + 900000;
  async function readStoredTask(value) {
    const key = taskStorageKey(value);
    return (await chrome.storage.local.get(key))[key];
  }
  async function saveStoredTask(value) {
    if(value?.bridge)throw new Error('后台任务只能由扩展后台保存');
    await chrome.storage.local.set({[taskStorageKey(value)]:value});
  }
  function stopReplyObserver() {
    replyObserver?.disconnect(); replyObserver = null;
    clearTimeout(replyDebounce); clearTimeout(replySettled);
    clearTimeout(continueCheck); continueCheck = null;
    clearTimeout(timer); timer = null;
  }
  function observeReply() {
    if (!timer && task) {
      const remaining = Math.max(0, taskDeadline(task) - Date.now());
      timer = setTimeout(() => check().catch(fail), remaining);
    }
    if (replyObserver || typeof MutationObserver === 'undefined' || !document.body) return;
    replyObserver = new MutationObserver(() => {
      clearTimeout(replyDebounce);
      replyDebounce = setTimeout(() => check().catch(fail), 150);
      if (!replySettled) replySettled = setTimeout(() => {replySettled=null;return check().catch(fail);}, 4300);
    });
    replyObserver.observe(document.body, {subtree:true,childList:true,characterData:true});
  }
  const visible = el => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
  const all = selector => {
    try { return [...document.querySelectorAll(selector)].filter(visible); }
    catch { throw new Error("网页适配设置中的 CSS 选择器无效。"); }
  };
  function clickableInViewport(el) {
    const rect = el.getBoundingClientRect();
    const left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
    const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
    if (right <= left || bottom <= top) return false;
    // A layout box can exist even when the sidebar is offscreen or covered.
    return [0.25, 0.5, 0.75].some(fraction => {
      const hit = document.elementFromPoint(left + (right - left) * fraction, (top + bottom) / 2);
      return hit && (hit === el || el.contains(hit));
    });
  }
  function editor(settings) {
    settings = settings || {};
    const editable = el => (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement || el.isContentEditable) && !el.disabled && !el.readOnly;
    const selectors = settings.editor ? [settings.editor] : site === 'ChatGPT' ? [
      '#prompt-textarea',
      '[contenteditable="true"][role="textbox"],[contenteditable="true"][data-lexical-editor="true"],[contenteditable="true"].ProseMirror',
      'form [contenteditable="true"],form textarea[placeholder]'
    ] : site === '豆包' ? [
      'textarea[placeholder]', 'textarea', '[contenteditable="true"][role="textbox"],[contenteditable="true"]'
    ] : ['textarea[placeholder]'];
    let nodes = [];
    for (const selector of selectors) {
      nodes = all(selector).filter(editable);
      if (nodes.length) break;
    }
    if (nodes.length !== 1) throw new Error(`无法唯一识别可编辑输入框（找到 ${nodes.length} 个）。自动提交暂缓，等待网页输入框就绪。`);
    const el = nodes[0];
    if (!(el instanceof HTMLTextAreaElement) && !(el instanceof HTMLInputElement) && !el.isContentEditable) throw new Error("目标不是可编辑输入框。");
    return el;
  }
  const inputText = el => typeof el.value === "string" ? el.value : el.innerText;
  const equivalent = (a, b) => String(a).replace(/\s+/g, ' ').trim() === String(b).replace(/\s+/g, ' ').trim();
  function conversationIdentity(value) {
    if(pagePolicy)return pagePolicy.conversationId(value);
    try {
      const url = new URL(value);
      const patterns = site === '豆包' ? [/^\/chat\/([^/?#]+)/] :
        site === 'DeepSeek' ? [/^\/a\/chat\/s\/([^/?#]+)/] : [/(?:^|\/)c\/([^/?#]+)/];
      for (const pattern of patterns) { const match=url.pathname.match(pattern); if (match) return match[1]; }
    } catch {}
    return '';
  }
  const provisionalDoubaoId = id => site === '豆包' && /^local_[a-zA-Z0-9_-]+$/.test(id || '');
  function pendingNewDeepSeekRoute(currentTask) {
    if (site !== 'DeepSeek' || !currentTask.force_new_conversation || !currentTask.sent_at ||
        !currentTask.dispatch_attempted_at || currentTask.conversation_url ||
        !conversationIdentity(location.href)) return false;
    try { return new URL(currentTask.dispatch_url).pathname === '/'; }
    catch { return false; }
  }
  function boundConversationMatches(currentTask) {
    const expectedUrl=currentTask.conversation_url || currentTask.dispatch_url;
    if (!expectedUrl) return true; // Legacy task, before URL binding was stored.
    // A new DeepSeek chat can navigate away from / while AICF_SEND is still
    // awaiting a response, leaving no stored conversation URL. Observe this
    // route without binding it; only the exact reply envelope may complete it.
    if (pendingNewDeepSeekRoute(currentTask)) return true;
    const expectedId=currentTask.conversation_id || conversationIdentity(expectedUrl);
    if (expectedId) return conversationIdentity(location.href) === expectedId;
    return location.href === expectedUrl;
  }
  function adoptActiveConversation(currentTask) {
    const expectedUrl=currentTask.conversation_url || currentTask.dispatch_url;
    const currentId=conversationIdentity(location.href);
    if (!expectedUrl || !currentId) return false;
    const expectedId=currentTask.conversation_id || conversationIdentity(expectedUrl);
    if (provisionalDoubaoId(expectedId) && !provisionalDoubaoId(currentId) &&
        !conversationIdentity(currentTask.dispatch_url || '') && currentTask.sent_at) {
      // Doubao first navigates to /chat/local_... and later replaces it with
      // the server-issued conversation ID. The submitted request can be
      // collapsed or virtualized by then; the task-specific reply marker is
      // the stable evidence that this is the same conversation.
      return pageHasRequest(currentTask.id);
    }
    // DeepSeek can assign a second conversation route after submission. Only
    // follow it when both the task marker and its submitted request are still
    // rendered in this conversation. A marker alone may have been copied into
    // another chat, so it cannot justify changing the binding.
    if (site === 'DeepSeek' && currentTask.force_new_conversation && currentTask.sent_at &&
        pageHasSubmittedRequest(currentTask) && pageHasDistinctReplyEnvelope(currentTask)) return true;
    if (site === 'ChatGPT' && currentTask.sent_at && pageHasRequest(currentTask.id) &&
        pageHasSubmittedRequest(currentTask)) return true;
    // Doubao may still expose a previous route when its new-chat composer is
    // cleared, then assign the real /chat/<id> after submission. Reconcile
    // that route only for a task dispatched from the new-chat page. Existing
    // conversation IDs remain strict unless this job explicitly started fresh.
    if (conversationIdentity(expectedUrl) &&
        (site !== '豆包' || (conversationIdentity(currentTask.dispatch_url || '') && !currentTask.force_new_conversation))) return false;
    if (currentTask.conversation_id === currentId) return false;
    // Once an ID was bound, an answer marker alone cannot distinguish route
    // settling from a later navigation. Require the submitted request too.
    if (currentTask.conversation_id) return pageHasRequest(currentTask.id) && pageHasSubmittedRequest(currentTask);
    return pageHasRequest(currentTask.id) || pageHasSubmittedRequest(currentTask);
  }
  let renameAttempt = {key:'', count:0, after:0};
  function doubaoConversationRow(id) {
    const links = all('a[href], [data-conversation-id], [data-chat-id]').filter(el => {
      const href = el.getAttribute('href');
      return (href && conversationIdentity(new URL(href, location.href).href) === id) ||
        el.getAttribute('data-conversation-id') === id || el.getAttribute('data-chat-id') === id;
    });
    if (links.length !== 1) return null;
    let row = links[0];
    for (let depth=0; row && depth<5; depth++, row=row.parentElement) {
      const conversations=[...row.querySelectorAll('a[href], [data-conversation-id], [data-chat-id]')]
        .filter(el => {
          const href=el.getAttribute('href');
          return (href && conversationIdentity(new URL(href,location.href).href)) ||
            el.getAttribute('data-conversation-id') || el.getAttribute('data-chat-id');
        });
      if (conversations.some(el => el !== links[0])) return null;
      if (row.querySelector('button[aria-haspopup="menu"], [role="button"][aria-haspopup="menu"], button[aria-label*="更多"], button[title*="更多"], [data-testid*="more" i]')) return row;
    }
    return null;
  }
  function doubaoHeaderMenu() {
    // Doubao's recent-chat rows are not always links. The top-right menu is
    // scoped to the open conversation whose URL was already verified above.
    const controls=all('button,[role="button"]').filter(el => {
      const box=el.getBoundingClientRect();
      return box.width>0 && box.width<=64 && box.height>0 && box.height<=64 &&
        box.left>innerWidth*0.7 && box.right<=innerWidth && box.top>=0 && box.bottom<100;
    });
    const labelled=controls.filter(el => /^(更多|更多操作|more|\.\.\.|···)$/i.test([
      el.getAttribute('aria-label'),el.getAttribute('title'),el.innerText].filter(Boolean).join(' ').trim()));
    if (labelled.length===1) return labelled[0];
    if (labelled.length>1 || controls.length>3 || !controls.length) return null;
    controls.sort((a,b)=>b.getBoundingClientRect().right-a.getBoundingClientRect().right);
    if (controls.length>1 && controls[0].getBoundingClientRect().right-controls[1].getBoundingClientRect().right<20) return null;
    return controls[0];
  }
  function doubaoHeaderHasTitle(title) {
    return all('h1,h2,[role="heading"],span,div').some(el => {
      if ((el.innerText || '').trim()!==title) return false;
      const box=el.getBoundingClientRect();
      return box.top>=0 && box.bottom<100 && box.left>innerWidth*0.2 && box.right<innerWidth*0.9;
    });
  }
  async function renameDoubaoConversation() {
    if (site !== '豆包' || !task || !['waiting','sending'].includes(task.status)) return;
    const id=task.conversation_id || conversationIdentity(task.conversation_url || '');
    if (!id || provisionalDoubaoId(id) || conversationIdentity(location.href) !== id || task.conversation_renamed_id === id) return;
    const key=`${task.id}:${id}`, now=Date.now();
    if (renameAttempt.key !== key) renameAttempt={key,count:0,after:0};
    if (renameAttempt.count>=10 || now<renameAttempt.after) return;
    renameAttempt.count++; renameAttempt.after=now+4000;
    const targetTitle=String(task.conversation_title || task.id).trim();
    if (!targetTitle) return;
    const row=doubaoConversationRow(id);
    if (String(row?.innerText || '').includes(targetTitle) || doubaoHeaderHasTitle(targetTitle)) {
      await persist({conversation_renamed_id:id}); return;
    }
    const menu=row ? [...row.querySelectorAll('button,[role="button"],[data-testid]')].filter(el => visible(el) &&
      (el.getAttribute('aria-haspopup')==='menu' || /更多|more/i.test([
        el.getAttribute('aria-label'),el.getAttribute('title'),el.getAttribute('data-testid')].filter(Boolean).join(' ')))) : [];
    const control=menu.length===1 ? menu[0] : !row ? doubaoHeaderMenu() : null;
    if (!control) return;
    control.click();
    await new Promise(resolve=>setTimeout(resolve,180));
    const labels=all('[role="menuitem"],button,span,div').filter(el => (el.innerText || '').trim()==='重命名');
    const leaves=labels.filter(el=>!labels.some(child=>child!==el && el.contains(child)));
    const actions=[...new Set(leaves.map(el=>el.closest('[role="menuitem"],button') || el))];
    if (actions.length !== 1) return;
    actions[0].click();
    await new Promise(resolve=>setTimeout(resolve,180));
    const fields=all('input,textarea,[contenteditable="true"]').filter(el =>
      el !== document.querySelector('textarea[placeholder]') &&
      ((row && row.contains(el)) || el.closest('[role="dialog"],[role="menu"]') ||
        (!row && el.getBoundingClientRect().top<140 && el.getBoundingClientRect().left>innerWidth*0.2)) &&
      !el.disabled && !el.readOnly);
    if (fields.length !== 1) return;
    writeEditor(fields[0],targetTitle);
    const save=all('button,[role="button"]').filter(el => /^(保存|确定|确认)$/.test((el.innerText || '').trim()) &&
      ((row && row.contains(el)) || el.closest('[role="dialog"],[role="menu"]') ||
        (!row && el.getBoundingClientRect().top<140)));
    if (save.length===1) save[0].click();
    else fields[0].dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}));
    await new Promise(resolve=>setTimeout(resolve,200));
    if (String(row?.innerText || '').includes(targetTitle) || doubaoHeaderHasTitle(targetTitle)) {
      await persist({conversation_renamed_id:id});
    }
  }
  function pageHasRequest(id) {
    // Rich-text editors are part of body.innerText. Their draft must never be
    // evidence that a message was submitted, even when its line breaks differ.
    const body = document.body?.cloneNode(true);
    if (!body) return false;
    body.querySelectorAll('textarea,input,[contenteditable],script,style').forEach(el => el.remove());
    const text = body.textContent || '';
    const {start, end} = P.markers(id);
    return text.includes(start) || text.includes(end);
  }
  function pageHasDistinctReplyEnvelope(currentTask) {
    const body = document.body?.cloneNode(true);
    if (!body) return false;
    body.querySelectorAll('textarea,input,[contenteditable],script,style').forEach(el => el.remove());
    const page = body.textContent || '';
    const request = String(currentTask.request || '').replace(/\s+/g, ' ').trim();
    const {start,end} = P.markers(currentTask.id);
    let offset = 0;
    while (true) {
      const from = page.indexOf(start, offset);
      if (from < 0) return false;
      const to = page.indexOf(end, from + start.length);
      if (to < 0) return false;
      const envelope = page.slice(from, to + end.length).replace(/\s+/g, ' ').trim();
      if (!request.includes(envelope)) return true;
      offset = to + end.length;
    }
  }
  function pageHasSubmittedRequest(currentTask) {
    const body = document.body?.cloneNode(true);
    if (!body) return false;
    body.querySelectorAll('textarea,input,[contenteditable],script,style').forEach(el => el.remove());
    const normalize=value=>String(value||'').replace(/\s+/g,' ').trim();
    const page=normalize(body.textContent), request=normalize(currentTask.request);
    // Doubao collapses long submitted prompts in the visible transcript. Its
    // opening text remains rendered while the rest may be omitted.
    return page.includes(request) || (request.length>=100 && page.includes(request.slice(0,120)));
  }
  function isGenerating() {
    const explicit = all('button[data-testid="stop-button"],button[aria-label*="Stop"],button[aria-label*="停止"],[role="button"][aria-label*="停止"]');
    return explicit.length > 0 || (site === '豆包' && all('button,[role="button"]').some(el => /^(停止生成|停止)$/i.test((el.getAttribute('aria-label') || el.innerText || '').trim())));
  }
  function sendButton(settings) {
    // Stable Doubao hook copied from the live page DOM: this is the blue
    // submit arrow, not the unrelated model/action controls in the composer.
    if (site === '豆包') {
      const doubaoButtons=all('button[data-testid="chat_input_send_button"]');
      if (doubaoButtons.length===1) return doubaoButtons[0];
      if (doubaoButtons.length>1) throw new Error('豆包页面找到多个发送箭头，已停止以免误点。');
    }
    if (settings.send) {
      const matches = all(settings.send);
      if (matches.length !== 1) throw new Error("发送按钮选择器必须匹配唯一可见元素。");
      return matches[0];
    }
    const matches = all('button,[role="button"]').filter(el => {
      const labels = [el.getAttribute("aria-label"), el.getAttribute("title"), el.innerText].filter(Boolean);
      return labels.some(label => /^(send|send message|send prompt|发送|发送消息|发送提示|发送提示词|提交)(?:\s*[（(].*[）)])?$/i.test(label.trim())) || el.getAttribute("data-testid") === "send-button";
    });
    if (!matches.length && site === '豆包') {
      // Doubao has used an unlabeled arrow-only submit control. Prefer stable
      // semantic hooks, then its send/submit class names; never select an
      // arbitrary toolbar icon.
      const specific = all('[data-testid*="send" i],[data-testid*="submit" i],button[class*="send" i],[role="button"][class*="send" i],button[aria-label*="发送"],[role="button"][aria-label*="发送"],button[title*="发送"],[role="button"][title*="发送"]')
        .filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true');
      if (specific.length === 1) return specific[0];
      if (specific.length > 1) throw new Error('豆包页面找到多个发送控件，已停止以免误点。请更新网页或检查发送按钮选择器。');
      // Scope a geometric fallback to the composer row around the actual
      // editor. On Doubao the send arrow is the enabled control at the lower
      // right of the composer; unrelated navigation and toolbar controls are
      // outside this narrow region.
      const input = editor(settings), rect = input.getBoundingClientRect();
      const nearby = all('button,[role="button"]').filter(el => {
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
        const box = el.getBoundingClientRect();
        const x = box.left + box.width / 2, y = box.top + box.height / 2;
        return x >= rect.right - 150 && x <= rect.right + 60 &&
          y >= rect.top - 12 && y <= rect.bottom + 125 && box.width > 0 && box.height > 0;
      });
      if (nearby.length === 1) return nearby[0];
      if (nearby.length > 1) {
        nearby.sort((a,b) => {
          const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
          return (br.right-ar.right) || (br.bottom-ar.bottom);
        });
        const first=nearby[0].getBoundingClientRect(), second=nearby[1].getBoundingClientRect();
        if (Math.abs(first.right-second.right)>24 || Math.abs(first.bottom-second.bottom)>24) return nearby[0];
      }
    }
    // DeepSeek may expose its icon-only send control without an accessible label.
    // Its composer also supports Enter; never guess among unrelated icon buttons.
    if (!matches.length && ["DeepSeek", "ChatGPT", "豆包"].includes(site)) return null;
    if (matches.length !== 1) throw new Error("未识别到唯一发送按钮。请求已填好，请手动发送，再点“读取本次回复”；也可配置发送按钮选择器。");
    return matches[0];
  }
  async function clickSendButton(button) {
    if (site !== '豆包' || button.getAttribute('data-testid') !== 'chat_input_send_button') {
      button.click(); return true;
    }
    const rect=button.getBoundingClientRect();
    const x=rect.left+rect.width/2, y=rect.top+rect.height/2;
    if (!clickableInViewport(button)) throw new Error('豆包发送箭头当前不可见或被遮挡，未发送。');
    const dispatch=activateIfNeeded=>chrome.runtime.sendMessage({type:'AICF_TRUSTED_DOUBAO_SEND',taskId:task?.id,
      buttonTestId:'chat_input_send_button',x,y,viewportWidth:innerWidth,viewportHeight:innerHeight,
      bindingEpoch:task?.binding_epoch,documentId:task?.document_id,activateIfNeeded});
    const response=await dispatch(false);
    if (response?.useDomClick) { button.click(); return true; }
    if (response?.dispatched) {
      const acceptanceId=task.json_repair?.envelopeId || task.id;
      const accepted=async()=>!inputText(editor(task.settings)).trim() || pageHasRequest(acceptanceId) || isGenerating();
      for (let i=0;i<10;i++) {
        await new Promise(resolve=>setTimeout(resolve,200));
        if (await accepted()) return true;
      }
      await persist({status:'waiting',message:'豆包后台发送未被页面确认：请求仍在输入框，尚无本任务消息。已暂停重复点击；可以手动发送，扩展继续读取本次回复。'});
      return false;
    }
    if (!response?.ok) {
      const message=response?.error || '豆包发送按钮点击未确认。';
      await persist({status:'error',message});
      throw new Error(message);
    }
    return true;
  }
  async function persist(next) {
    if (!task?.id) { stopReplyObserver(); return null; }
    const activeId = task.id;
    const savedTask = await readStoredTask(task);
    if (!task || task.id !== activeId || (savedTask && (savedTask.id !== activeId || ['cancelled','error','timeout','complete'].includes(savedTask.status)))) {
      stopReplyObserver(); return savedTask;
    }
    if(task.bridge) {
      const response=await chrome.runtime.sendMessage({type:'AICF_TASK_PATCH',taskId:activeId,
        bindingEpoch:task.binding_epoch,operationId:task.page_operation?.id,
        requestEvidence:pageHasRequest(activeId) || pageHasSubmittedRequest(task),
        patch:{...next,updated_at:new Date().toISOString()}});
      if(!response?.ok)throw new Error(response?.error || '后台任务保存失败，网页操作已暂停');
      task=response.task;
    } else {
      task = { ...task, ...next, updated_at: new Date().toISOString() };
      await saveStoredTask(task);
    }
    if (['prepared','sending','waiting'].includes(task.status)) observeReply();
    else stopReplyObserver();
    if (next.status === 'complete') {
      // Notify immediately after durable storage; do not await the worker,
      // which may itself be awaiting this content-script response.
      if (task.bridge && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({type:'AICF_RESULT_READY',taskId:task.id}).catch(() => {});
      }
      const key = `conversation:${task.tab_id}`;
      const saved = await chrome.storage.local.get(key);
      const record = saved[key];
      if (record?.taskId === task.id && !record.completed) await chrome.storage.local.set({[key]: {
        ...record, completed: true, chars: record.chars + (next.raw || '').length, url: location.href
      }});
    }
    return task;
  }
  async function prepareConversation(next) {
    const key = `conversation:${next.tab_id}`;
    const saved = await chrome.storage.local.get(['conversationPolicy', key]);
    const previous = saved[key];
    const plan = P.conversationPlan(saved.conversationPolicy, next, previous, location.href);
    if (previous?.taskId === next.id) return;
    if (plan.fresh && (next.force_new_conversation || !equivalent(inputText(editor(next.settings)), next.request))) {
      if (inputText(editor(next.settings)).trim()) throw new Error('开启新对话前发现输入框已有草稿，请先处理草稿；未切换或发送。');
      const replies = () => all(site === 'ChatGPT' ? '[data-message-author-role]' :
        site === '豆包' ? '[data-testid*="message"],[class*="message-content"]' : '.ds-markdown');
      const emptyHome = () => site === '豆包'
        ? !replies().length && !inputText(editor(next.settings)).trim()
        : new URL(location.href).pathname === '/' && !replies().length;
      if (!emptyHome() || site === '豆包') {
        // DeepSeek renders this control as nested div/span elements in some
        // versions, without a button role. Click the exact visible text leaf;
        // its click bubbles to the owning control. Collapse ancestor duplicates.
        const labelled = all('a,button,[role="button"],div,span').filter(el =>
          [el.getAttribute('aria-label'), el.getAttribute('title'), el.innerText].some(label =>
            label && /^(开启新对话|新对话|新建对话|新聊天|new chat)(?:\s*\([^)]*\)|\s+(?:ctrl|shift|alt|command|⌘)\b.*)?$/i.test(label.trim())));
        let matches;
        if (site === 'ChatGPT') {
          // Text and icon descendants belong to one clickable control. Never
          // treat plain conversation text as a new-chat navigation entry.
          matches = [...new Set(labelled.map(el => el.closest('a[href],button,[role="button"]')).filter(el =>
            el && visible(el) && clickableInViewport(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
            !el.closest('main,[role="main"],[data-message-author-role],[role="dialog"]')))];
          const navigation = matches.filter(el => el.closest('nav,aside,[role="navigation"]'));
          if (navigation.length) matches = navigation;
          const homeLinks = matches.filter(el => {
            const href = el.getAttribute('href');
            if (!href) return false;
            const url = new URL(href, location.href);
            return url.origin === new URL(location.href).origin && url.pathname === '/';
          });
          if (homeLinks.length) matches = homeLinks;
          // Multiple genuine links to exactly the same new-chat destination
          // are equivalent. Prefer the upper-left navigation entry the user sees.
          if (matches.length > 1 && matches.every(el => el.getAttribute('href')) &&
              new Set(matches.map(el => new URL(el.getAttribute('href'), location.href).href)).size === 1 &&
              homeLinks.length === matches.length) {
            matches.sort((a, b) => {
              const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
              return ar.left - br.left || ar.top - br.top;
            });
            matches = matches.slice(0, 1);
          }
        } else if (site === '豆包') {
          matches = [...new Set(labelled.map(el => el.closest('a[href],button,[role="button"],[tabindex="0"]') || el))]
            .filter(el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
          // Doubao can expose the same command in its sidebar row, toolbar
          // button, and nested accessible wrappers. Prefer the visible text
          // action in navigation over icon/tooltip duplicates elsewhere.
          const startsNewChatLabel=el=>/^(新对话|新建对话|新聊天|new chat)(?:\s|$)/i.test((el.innerText||'').replace(/\s+/g,' ').trim());
          const navigation=matches.filter(el=>el.closest('nav,aside,[role="navigation"]'));
          // Doubao's current sidebar may not use semantic nav/aside tags.
          // Click the smallest exact text node in the left navigation area;
          // its click bubbles to the owning React control and avoids counting
          // toolbar icons or duplicated accessibility wrappers as candidates.
          const textLabels=labelled.filter(startsNewChatLabel);
          const textLeaves=textLabels.filter(el=>!textLabels.some(child=>child!==el&&el.contains(child)));
          const sidebarText=textLeaves.filter(el=>{
            if(el.closest('nav,aside,[role="navigation"]'))return true;
            const rect=el.getBoundingClientRect?.();
            return rect&&rect.left<=innerWidth*0.35&&rect.top<innerHeight*0.75;
          });
          if(sidebarText.length){
            const positioned=sidebarText.map(el=>({el,rect:el.getBoundingClientRect?.()})).filter(item=>item.rect);
            const firstTop=positioned.length?Math.min(...positioned.map(item=>item.rect.top)):null;
            const firstRow=firstTop===null?[]:positioned.filter(item=>item.rect.top<=firstTop+24);
            if(firstRow.length===1)matches=[firstRow[0].el];
            else if(sidebarText.length===1)matches=sidebarText;
          }
          if(matches.length!==1&&navigation.filter(startsNewChatLabel).length===1) matches=navigation.filter(startsNewChatLabel);
          else if(matches.length!==1&&navigation.length===1) matches=navigation;
          else {
            // Collapse ancestor/descendant wrappers first, then use the
            // semantically labelled sidebar action if it is the unique one.
            matches=matches.filter(el=>!matches.some(child=>child!==el&&el.contains(child)));
            const textActions=matches.filter(startsNewChatLabel);
            if(textActions.length===1) matches=textActions;
          }
        } else {
          matches = labelled.filter(el => !labelled.some(child => child !== el && el.contains(child)));
        }
        if (matches.length !== 1) throw new Error(`[扩展 v0.3.49 豆包新对话识别] 可点击入口为 ${matches.length} 个，未切换或发送。请手动点击左上角“新聊天”后重试。`);
        matches[0].click();
        for (let i = 0; i < 40; i++) {
          await new Promise(resolve => setTimeout(resolve, 200));
          try { if (emptyHome() && !inputText(editor(next.settings)).trim()) break; } catch {}
        }
        if (!emptyHome() || inputText(editor(next.settings)).trim()) throw new Error('未确认新对话已打开，已停止提交。请手动打开新对话后重试。');
      }
    }
    await chrome.storage.local.set({[key]: {taskId: next.id, key: plan.key, url: location.href,
      rounds: (plan.fresh ? 0 : previous?.rounds || 0) + 1,
      chars: (plan.fresh ? 0 : previous?.chars || 0) + next.request.length}});
  }
  async function fill(next) {
    const saved = await readStoredTask(next);
    const records=await chrome.storage.local.get(null);
    if(Object.entries(records).some(([key,value])=>key.startsWith('bridgeTask:') &&
        value?.tab_id===next.tab_id && value.id!==next.id && !value.bridge?.reported))
      throw new Error('此标签页已被其他后台任务占用，不能同时发送。');
    const manual = (await chrome.storage.local.get('task')).task;
    if (next.bridge && manual && manual.id !== next.id && manual.tab_id === next.tab_id &&
        ['prepared','sending','waiting'].includes(manual.status))
      throw new Error('此标签页有手工任务，后台任务等待该标签页空闲。');
    if (!next.bridge) {
      const all = await chrome.storage.local.get(null);
      if (Object.entries(all).some(([key,value]) => key.startsWith('bridgeTask:') &&
          value?.tab_id === next.tab_id && !value.bridge?.reported && value.id !== next.id))
        throw new Error('此标签页有后台任务，请等待完成后再提交手工任务。');
    }
    if (saved && saved.id !== next.id && ["prepared", "sending", "waiting"].includes(saved.status)) throw new Error("扩展已有未结束的任务，请先结束等待。");
    if (task && ["waiting", "sending"].includes(task.status)) throw new Error("此页面已有任务在等待，请先结束等待。");
    if (isGenerating()) throw new Error("网页正在生成回复，请等待结束后再提交。");
    if (saved?.bridge && !saved.bridge.reported && saved.id !== next.id) throw new Error('上次结果尚未回传，请等待回传完成后再开启任务。');
    await prepareConversation(next);
    const el = editor(next.settings);
    const previous = inputText(el).trim();
    if (previous && !equivalent(previous, next.request)) throw new Error("网页输入框已有草稿，未覆盖。请先手动处理网页里的草稿。");
    if (!equivalent(previous, next.request)) writeEditor(el, next.request);
    if (!equivalent(inputText(el), next.request)) throw new Error("填入内容校验失败，未发送。请复制请求后手动粘贴。");
    clearTimeout(timer); timer = null;
    task = next; lastText = ""; stableSince = 0; lastEnvelope = ""; envelopeSince = 0;
    return persist({ status: "prepared", message: "请求已填入。可在网页检查后手动发送，或点击提交。" });
  }
  function writeEditor(el, text) {
      el.focus();
      if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
        const prototype = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(el, text);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges(); selection.addRange(range);
        if (!document.execCommand("insertText", false, text)) throw new Error("网页拒绝自动输入，请使用“复制请求”手动粘贴。");
      }
  }
  async function repairJson(raw, error) {
    if (task.json_repair) return persist({status:'error', raw,
      message:'自动修复一次后 JSON 仍无效：' + error.message});
    if (isGenerating()) return task;
    if (Date.now() >= taskDeadline(task))
      return persist({status:'timeout', raw, message:'任务已超时，未发送 JSON 修复请求。'});
    const el = editor(task.settings);
    if (inputText(el).trim()) return persist({status:'error', raw,
      message:'回复 JSON 语法错误，但输入框已有草稿，未覆盖。请处理草稿后重新提交任务。'});
    const envelopeId = task.id + '-repair1';
    const {start, end} = P.markers(envelopeId);
    const request = P.buildJsonRepairRequest(raw, task.id, error.message);
    const activeId = task.id;
    // Persist before any input: re-injection and worker polls cannot resend repair.
    await persist({status:'waiting', raw, json_repair:{envelopeId, request, original_raw:raw},
      message:'回复 JSON 语法错误，正在同一对话请求修复一次。'});
    const beforeInput = await readStoredTask(task);
    if (beforeInput?.id !== activeId || beforeInput.status !== 'waiting') return beforeInput;
    if (!task || task.id !== activeId || task.status !== 'waiting') return task;
    writeEditor(el, request);
    if (!equivalent(inputText(el), request)) throw new Error('JSON 修复请求填入校验失败，未发送。');
    await new Promise(resolve => setTimeout(resolve, 200));
    const saved = await readStoredTask(task);
    if (saved?.id !== activeId || saved.status !== 'waiting') return saved;
    const button = sendButton(task.settings);
    if (button && (button.disabled || button.getAttribute('aria-disabled') === 'true'))
      return persist({message:'JSON 修复请求已填入，发送按钮未就绪，请在网页手动发送。'});
    if (site === '豆包' && !button) throw new Error('未找到豆包蓝色发送箭头，未用 Enter 代替发送。');
    if (button) {
      const clicked=await clickSendButton(button);
      if (!clicked) return task;
    }
    else {
      el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}));
      el.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
    }
    lastText = ''; stableSince = 0;
    return persist({message:'已尝试提交 JSON 修复请求，等待完整修复结果；不会重复发送。'});
  }
  async function send(next) {
    if(sending)return readStoredTask(next);
    sending=true;
    try { return await sendRequest(next); }
    finally {
      sending=false;
      if(task && ['prepared','sending','waiting'].includes(task.status)) {
        clearTimeout(replyDebounce);
        replyDebounce=setTimeout(()=>check().catch(fail),150);
      }
    }
  }
  async function sendRequest(next) {
    // Same ID is never clicked a second time, even after an uncertain response.
    const saved = await readStoredTask(next);
    if (saved?.id === next.id && saved.sent_at) throw new Error("这个任务已尝试发送，不会重复点击。请查看网页并读取回复。");
    if (!task || task.id !== next.id || task.status !== "prepared" || !equivalent(inputText(editor(next.settings)), next.request)) await fill(next);
    const el = editor(task.settings);
    if (!equivalent(inputText(el), task.request)) throw new Error("网页输入内容已变化，未发送。请结束当前任务后重新填入。");
    if (isGenerating()) throw new Error("网页正在生成回复，请稍后操作。");
    // Give the editor's reactive state time to enable the submit control.
    await new Promise(resolve => setTimeout(resolve, 200));
    let button = sendButton(task.settings);
    for (let i = 0; i < 10 && button && (button.disabled || button.getAttribute("aria-disabled") === "true"); i++) {
      await new Promise(resolve => setTimeout(resolve, 200));
      button = sendButton(task.settings);
    }
    if (button && (button.disabled || button.getAttribute("aria-disabled") === "true")) throw new Error("发送按钮尚不可用，自动恢复将在后续检查时重试。");
    await persist({ status: "sending", sent_at: new Date().toISOString(),
      dispatch_attempted_at: Date.now(), dispatch_url: location.href,
      dispatch_count: (task.dispatch_count || 0) + 1, message: "正在提交到网页…" });
    if (site === '豆包' && !button) throw new Error('未找到豆包蓝色发送箭头，未用 Enter 代替发送。');
    if (button) {
      const clicked=await clickSendButton(button);
      if (!clicked) return task;
    }
    else {
      el.focus();
      el.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true}));
      el.dispatchEvent(new KeyboardEvent("keyup", {key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true}));
    }
    if (site === 'DeepSeek') {
      // In a background tab Chrome can throttle ten 200 ms timers for several
      // minutes. DeepSeek may also replace the composer while navigating from
      // / to the new chat, leaving the old `el` with its unchanged draft.
      // Check the live page once and let worker polls handle uncertain sends.
      const moved = location.href !== task.dispatch_url && Boolean(conversationIdentity(location.href));
      let currentInput = null;
      if (!moved) {
        try { currentInput = inputText(editor(task.settings)).trim(); } catch {}
      }
      if (moved || currentInput === '' || pageHasRequest(task.id) || isGenerating()) {
        await persist({status:'waiting',
          ...(moved ? {conversation_url:location.href,conversation_id:conversationIdentity(location.href)} : {}),
          message:'已尝试提交到 DeepSeek，正在读取本次回复；不会重复发送。'});
        observeReply();
      } else {
        await persist({status:'prepared',message:'发送状态尚未确认，正在自动检查网页；不会立即重复发送。'});
      }
      return task;
    }
    for (let attempt = 0; attempt < 10 && inputText(el).trim(); attempt++) await new Promise(resolve => setTimeout(resolve, 200));
    if (inputText(el).trim()) {
      return persist({status: "prepared", message: "网页尚未接受提交，正在自动检查并恢复。"});
    }
    for (let attempt=0;attempt<10&&site==='豆包'&&!conversationIdentity(location.href);attempt++) await new Promise(resolve=>setTimeout(resolve,200));
    await persist({ status: "waiting", conversation_url:location.href,
      conversation_id:conversationIdentity(location.href),
      message: "网页输入框已清空，已绑定当前对话，正在等待本次回复；不会自动重复发送。" });
    observeReply();
    return task;
  }
  async function resume(next) {
    const saved = await readStoredTask(next);
    if (saved?.id !== next.id || ['complete','cancelled','error','timeout'].includes(saved.status)) return saved;
    if (!task || task.id !== saved.id) {task = saved; lastText = ''; stableSince = 0;}
    if (!boundConversationMatches(task)) {
      if (adoptActiveConversation(task)) await persist({conversation_url:location.href,conversation_id:conversationIdentity(location.href)});
      else return persist({status:'waiting',message:'当前标签页已切换到其他对话，任务仍绑定原对话；请回到任务对应对话后自动继续读取。'});
    }
    const alreadyWatching = Boolean(replyObserver);
    observeReply();
    if (task.json_repair) return check();
    // The worker polls this task every second. Keep the observer for fast
    // updates, but also inspect the rendered page on each worker poll: hidden
    // tabs can finish rendering without delivering a useful mutation event.
    if (alreadyWatching && task.status === 'waiting') return check();
    const {start} = P.markers(task.id);
    const input = inputText(editor(task.settings)).trim();
    // Inspect before deciding to send: recovery never creates another task ID.
    if (pageHasRequest(task.id)) {
      if (task.status !== 'waiting') await persist({status:'waiting', message:'已找到本次请求，正在自动读取回复。'});
      observeReply();
      return check();
    }
    if (isGenerating()) return task;
    if (input && !equivalent(input, task.request)) {
      return persist({status:'prepared', message:'输入框有其他草稿，自动提交已暂停；不会覆盖草稿。'});
    }
    if (task.dispatch_attempted_at) {
      // An unchanged exact draft on the same page is a negative acknowledgement.
      // An empty editor after a click is ambiguous and must not cause another send.
      if (!input || location.href !== task.dispatch_url) {
        // New conversations change URL, and long messages may be collapsed or
        // virtualized while a reply streams. Neither proves submission failed.
        // Observe until the overall deadline; never dispatch a second request.
        if (task.status !== 'waiting') await persist({status:'waiting',message:'已尝试提交，继续检查网页回复；不会重复发送。'});
        observeReply();
        return check();
      }
      if (Date.now() - task.dispatch_attempted_at < 10000) return task;
      if (task.dispatch_count >= 3) return persist({status:'error', message:'网页连续三次未接受提交，自动恢复失败；请检查网页登录、额度或页面适配。'});
    } else if (task.sent_at && !input && new URL(location.href).pathname !== '/') {
      // Old releases assigned sent_at during READ, even when nothing was sent.
      // Migrate these only on a blank new-chat page, not an unrelated old chat.
      return persist({status:'error', message:'旧版任务发送状态不明确，当前也不是新对话页，已停止重复提交。'});
    }
    if(task.bridge) {
      // A claimed project request with any dispatch evidence is never sent
      // again after a worker or page restart.
      if(task.dispatch_attempted_at || task.sent_at)
        return persist({status:'waiting',message:'发送状态不明确，继续只读检查；不会重复提交。'});
      return persist({status:'prepared',message:'请求尚未提交，等待后台重新安排发送。'});
    }
    const retry = {...task, sent_at: undefined, status:'prepared'};
    clearTimeout(timer); timer = null; task = null;
    await saveStoredTask(retry);
    await fill(retry);
    return send(task);
  }
  async function fail(error) {
    if(task && /CONVERSATION_BINDING_PENDING|会话地址缺少本任务发送证据/.test(error.message || '')) {
      // SPA navigation can precede rendering of the submitted message. Keep
      // the persisted send record and wait for its markers, without resending.
      await persist({message:'会话地址尚未确认，继续只读检查本次回复；不会重复发送。'});
      clearTimeout(replySettled);
      replySettled=setTimeout(()=>check().catch(fail),4100);
      return;
    }
    clearTimeout(timer); timer = null;
    if (task) await persist({ status: "error", message: error.message || String(error) });
  }
  let continuationScan = {};
  function deepSeekContinueButton(candidates, start, end) {
    continuationScan = {reason:'当前回复附近未找到按钮', exactMatches:0};
    if (site !== 'DeepSeek') return null;
    const reply = candidates.findLast(el => (el.innerText || '').includes(start));
    if (!reply || (reply.innerText || '').includes(end)) return null;
    const older = candidates.slice(0, candidates.indexOf(reply));
    // Search only the latest reply's nearby controls, never the whole page:
    // old answers and quoted button labels must not initiate another request.
    let root = reply.parentElement;
    for (let depth = 0; root && root !== document.body && depth < 8; depth++, root = root.parentElement) {
      if (older.some(other => root.contains(other))) { continuationScan.reason = '搜索范围包含旧回复，停止扩大范围'; break; }
      const labels = [...root.querySelectorAll('button,[role="button"],div,span')].filter(el =>
        !candidates.some(other => other.contains(el)) && (el.innerText || el.textContent || '').trim() === '继续生成');
      continuationScan = {reason:'按钮不可见或被禁用', exactMatches:labels.length,
        visibleMatches:labels.filter(visible).length,
        disabledMatches:labels.filter(el => el.disabled || el.getAttribute('aria-disabled') === 'true' || el.closest?.('[disabled],[aria-disabled="true"]')).length};
      const matches = [...root.querySelectorAll('button,[role="button"],div,span')].filter(el =>
        !candidates.some(other => other.contains(el)) && visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        !el.closest?.('[disabled],[aria-disabled="true"]') &&
        (el.innerText || el.textContent || '').trim() === '继续生成');
      // DeepSeek renders the label inside .ds-button__content. Activating that
      // span did not resume generation in the live page, so prefer the actual
      // button/role=button owner and use the label only for non-semantic markup.
      const labelsDeepest = matches.filter(el => !matches.some(other => other !== el && el.contains(other)));
      const controls = [...new Set(labelsDeepest.map(el => {
        const owner = el.closest?.('button,[role="button"]');
        return owner && (owner.innerText || owner.textContent || '').trim() === '继续生成' ? owner : el;
      }))];
      if (controls.length === 1) { continuationScan.reason = '找到唯一可用按钮'; return controls[0]; }
      if (controls.length > 1) { continuationScan.reason = '存在多个候选按钮，无法确定目标'; return null; }
      if (!labels.length) continuationScan.reason = '当前回复附近未找到按钮';
    }
    return null;
  }
  async function continuationLog(event, details = {}) {
    const entry = {at:new Date().toISOString(), event, ...details};
    await persist({continuation_diagnostics:[...(task.continuation_diagnostics || []), entry].slice(-200)});
  }
  function buttonDetails(button) {
    if (!button) return null;
    const r = button.getBoundingClientRect?.();
    return {tag:button.tagName || '', role:button.getAttribute('role'),
      className:String(button.className || '').slice(0, 300),
      disabled:Boolean(button.disabled),
      rect:r ? {x:r.left,y:r.top,width:r.width,height:r.height} : null};
  }
  function replyFingerprint(text) {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return `${text.length}:${hash >>> 0}`;
  }
  async function clickContinue(button) {
    const control = button.closest?.('button,[role="button"]') || button;
    control.scrollIntoView?.({block:'center', inline:'nearest', behavior:'instant'});
    const rect = control.getBoundingClientRect();
    const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    // The center may hit the nested label span, but activation must target the
    // owning control. Never click an overlay or an unrelated neighboring item.
    if (!hit || !(hit === control || control.contains(hit))) {
      throw new Error('继续生成按钮中心被遮挡或不在可点击区域');
    }
    const response = await chrome.runtime.sendMessage({
      type:'AICF_TRUSTED_CONTINUE_CLICK', taskId:task?.id, x, y,
      viewportWidth:innerWidth, viewportHeight:innerHeight
    });
    if (!response?.ok) throw new Error(response?.error || 'Chrome 未能发送真实鼠标点击');
    return {method:response.method || 'debugger-input', target:buttonDetails(control), hit:buttonDetails(hit)};
  }
  async function check() {
    if (!task) { stopReplyObserver(); return null; }
    if (['complete','cancelled','error','timeout'].includes(task.status)) { stopReplyObserver(); return task; }
    if (writing || sending) return task;
    writing = true;
    try {
      const current = await readStoredTask(task);
      if (current?.id !== task.id || current?.status === "cancelled") {
        clearTimeout(timer); timer = null; return current;
      }
      const deadline=taskDeadline(task);
      if (Number.isFinite(deadline) && Date.now()>=deadline) {
        clearTimeout(timer); timer=null;
        return await persist({status:'timeout',message:'任务等待已达 900 秒，已停止自动读取。请检查登录、额度和网页回复；不会重复发送。'});
      }
      if (!boundConversationMatches(task)) {
        if (adoptActiveConversation(task)) await persist({conversation_url:location.href,conversation_id:conversationIdentity(location.href)});
        else return await persist({status:'waiting',message:'当前页面不是任务绑定的对话，已暂停读取；返回原对话后自动继续。'});
      }
      // Naming is cosmetic. A changed Doubao menu must never fail a generation task.
      await renameDoubaoConversation().catch(() => {});
      const selector = task.settings.reply || (site === "ChatGPT" ? '[data-message-author-role="assistant"]' :
        site === '豆包' ? '[data-testid*="message"],[class*="message-content"]' : '.ds-markdown');
      const { start, end } = P.markers(task.json_repair?.envelopeId || task.id);
      const selectors = [selector];
      if (site === 'ChatGPT') selectors.push('.markdown,.prose,[data-message-author-role="assistant"],[data-testid="conversation-turn"]');
      const candidates = [...new Set(selectors.flatMap(all))].filter(el => !el.closest('[data-message-author-role="user"],textarea,[contenteditable="true"]'));
      const normalize = value => value.replace(/\s+/g, " ").trim();
      // Some sites render user messages with the same markdown class. Never
      // mistake our own JSON output example for the model's completed answer.
      const requestText = normalize(task.request + '\n' + (task.json_repair?.request || ''));
      let text = candidates.map(el => el.innerText || '').reverse().find(value => {
        if (!value.includes(start) || normalize(value).includes(requestText)) return false;
        const from = value.indexOf(start), to = value.indexOf(end, from + start.length);
        // The user's request also contains a complete example envelope. Never
        // mistake that example for a reply when a fallback matches both roles.
        return to < 0 || !requestText.includes(normalize(value.slice(from, to + end.length)));
      }) || "";
      // A single assistant answer can contain several markdown blocks. Only
      // join sibling blocks within the start block's own container, never
      // concatenate arbitrary messages from the conversation.
      if (site === 'DeepSeek' && text.includes(start) && !text.includes(end)) {
        const first = candidates.findLast(el => (el.innerText || '') === text);
        const siblings = first?.parentElement ? candidates.filter(el => el.parentElement === first.parentElement) : [];
        const index = siblings.indexOf(first);
        if (index >= 0 && siblings.length > index + 1) {
          text = siblings.slice(index).map(el => el.innerText || '').join('');
        }
      }
      // Read protocol envelopes independently of site-specific reply classes.
      // Remove editable/user regions; also reject the exact example from the
      // submitted request on pages without semantic message roles.
      if (!text.includes(end)) {
        const body = document.body?.cloneNode(true);
        if (body) {
          body.querySelectorAll('script,style,textarea,input,[contenteditable],[data-message-author-role="user"]').forEach(el => el.remove());
          const pageText = body.textContent || '';
          const envelopes = [];
          let offset = 0, from;
          while ((from = pageText.indexOf(start, offset)) !== -1) {
            const to = pageText.indexOf(end, from + start.length);
            if (to < 0) break;
            const envelope = pageText.slice(from, to + end.length);
            if (!requestText.includes(normalize(envelope))) envelopes.push(envelope);
            offset = to + end.length;
          }
          if (envelopes.length) text = envelopes.at(-1);
        }
      }
      if (text !== lastText) {
        lastText = text; stableSince = Date.now();
      }
      // Compare only the task envelope; surrounding UI text is not output.
      const envelope = P.resultEnvelope(text, task.json_repair?.envelopeId || task.id);
      if (envelope !== lastEnvelope) {
        lastEnvelope = envelope; envelopeSince = Date.now();
        if (envelope) {
          clearTimeout(replySettled);
          replySettled = setTimeout(() => {replySettled=null;return check().catch(fail);}, RESULT_RECHECK_MS + 20);
        }
      }
      if (envelope && Date.now() - envelopeSince >= RESULT_RECHECK_MS) {
        let result;
        try { result = P.parseResult(envelope, task.id, task.kind || "revision", task.json_repair?.envelopeId || task.id); }
        catch (error) {
          if (error.code === 'INVALID_JSON_SYNTAX') return await repairJson(text, error);
          clearTimeout(timer); timer = null; return persist({ status: "error", message: error.message, raw: text });
        }
        clearTimeout(timer); timer = null;
        return persist({ status: "complete", message: "已取回本次修正。请检查修改说明后再应用。", result, raw: text });
      }
      if (task.continue_pending && text.length > task.continue_pending.chars) {
        await continuationLog('reply_grew', {before:task.continue_pending.chars, after:text.length});
        await persist({continue_pending:null});
      }
      if (text.includes(start) && !text.includes(end)) {
        const button = deepSeekContinueButton(candidates, start, end);
        const fingerprint = replyFingerprint(text);
        const same = task.continued_reply === fingerprint;
        const attempts = same ? (task.continue_attempts || 1) : 0;
        const diagnosticState = `${Boolean(button)}:${candidates.length}:${same}:${attempts}:${JSON.stringify(continuationScan)}`;
        if (task.continue_scan_state !== diagnosticState) {
          await continuationLog('button_scan', {replyCount:candidates.length, matched:Boolean(button),
            ...continuationScan, target:buttonDetails(button)});
          await persist({continue_scan_state:diagnosticState});
        }
        if (task.continue_pending && Date.now() - task.continue_pending.at >= 8000) {
          await continuationLog('no_reply_growth', {chars:text.length, buttonVisible:Boolean(button), generating:isGenerating()});
          await persist({continue_pending:null});
        }
        if (button && Date.now() - stableSince >= 4000 && Date.now() - (task.continued_at || 0) >= 8000) {
          if (attempts >= 3) {
            const message = 'DeepSeek“继续生成”已尝试点击 3 次，按钮仍在且回复未增长。请手动点击；扩展继续等待完整结果，不会重新发送原请求。';
            if (task.message !== message) await persist({message});
            return task;
          }
          // Persist before dispatch; retries require unchanged text AND a
          // still-visible button, and never reset the overall task deadline.
          await persist({continued_reply: fingerprint, continued_at: Date.now(), continue_attempts:attempts + 1,
            continue_events:[...(task.continue_events || []), {at:new Date().toISOString(), attempt:attempts + 1, chars:text.length}].slice(-12),
            message: `已尝试点击 DeepSeek“继续生成”（${attempts + 1}/3），正在核对回复是否增长。`});
          const saved = await readStoredTask(task);
          if (saved?.id !== task.id || ['complete','cancelled','error','timeout'].includes(saved.status)) return saved;
          // Storage awaits can let React replace the button. Never dispatch
          // to the stale detached element captured before those awaits.
          const freshCandidates = all(selector).filter(el => !el.closest('[data-message-author-role="user"],textarea,[contenteditable="true"]'));
          const freshButton = deepSeekContinueButton(freshCandidates, start, end);
          if (freshButton) {
            try {
              const dispatch = await clickContinue(freshButton);
              await continuationLog('click_dispatched', {attempt:attempts + 1, ...dispatch});
              await persist({continue_pending:{at:Date.now(), chars:text.length}});
            } catch (error) {
              await continuationLog('click_failed', {error:String(error.message || error).slice(0,500)});
            }
          } else {
            await continuationLog('click_skipped', {reason:'点击前重新定位时按钮已消失或不再满足条件'});
          }
          clearTimeout(continueCheck);
          continueCheck = setTimeout(() => check().catch(fail), 8100);
          return task;
        }
      }
      const progress = `[读取 v0.4.24] 回复区域 ${candidates.length} 个；` +
        (text.includes(end) ? '已找到完整任务标记，等待内容稳定。' : text.includes(start) ? '已找到开始标记，等待结束标记。' : '未找到本次回复标记，请确认绑定的对话。');
      if (task.message !== progress) await persist({message: progress});
      return task;
    } finally { writing = false; }
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || !message?.type?.startsWith("AICF_")) return;
    (async () => {
      if(message.type==='AICF_SLOT_STATUS') {
        if (operating || isGenerating()) return {idle:false};
        try { return {idle:!inputText(editor(message.task?.settings)).trim()}; }
        catch { return {idle:false}; }
      }
      if(message.type==='AICF_OPERATION_STATUS') {
        const saved=await readStoredTask(message.task);
        return {id:saved?.id,status:saved?.status,busy:operating || sending,page_operation:saved?.page_operation,
          sent_at:saved?.sent_at,dispatch_attempted_at:saved?.dispatch_attempted_at};
      }
      if (message.type === 'AICF_RESUME') {
        if (operating) return readStoredTask(message.task);
        operating = true;
        try {return await resume(message.task);} finally {operating = false;}
      }
      if (message.type === "AICF_FILL") return fill(message.task);
      if (message.type === "AICF_SEND") {
        if (operating) return readStoredTask(message.task);
        operating=true;
        try { return await send(message.task); }
        finally { operating=false; }
      }
      if (message.type === "AICF_READ") {
        if (operating) return readStoredTask(message.task);
        const saved = await readStoredTask(message.task);
        if (saved?.id === message.task.id && ["complete", "cancelled"].includes(saved.status)) return saved;
        if (!task || task.id !== message.task.id) { task = message.task; lastText = ""; stableSince = 0; lastEnvelope = ""; envelopeSince = 0; }
        if (!boundConversationMatches(task)) {
          if (adoptActiveConversation(task)) await persist({conversation_url:location.href,conversation_id:conversationIdentity(location.href)});
          else return persist({status:'waiting',message:'当前页面不是任务绑定的对话，已暂停读取；返回原对话后自动继续。'});
        }
        if (!boundConversationMatches(task)) return persist({status:'waiting',message:'当前页面不是任务绑定的对话，已暂停读取；返回原对话后自动继续。'});
        if (task.json_repair || pageHasDistinctReplyEnvelope(task)) {
          // A finished answer does not depend on the composer being mounted.
          observeReply(); return check();
        }
        const currentInput = inputText(editor(task.settings)).trim().replace(/\r/g, "");
        if (equivalent(currentInput, task.request) && !pageHasRequest(task.id)) {
          clearTimeout(timer); timer = null;
          if(task.bridge && task.page_operation?.kind==='send')
            return persist({status:'waiting',message:site==='豆包' ? '豆包尚未确认发送：请求仍在输入框；可手动点击发送箭头，扩展继续读取，不会重复提交。' : '发送结果尚未确认，保留输入并只读等待；不会重复提交。'});
          if (task.status === "prepared" && task.message?.includes("尚未发送")) return task;
          return persist({status: "prepared", message: "请求仍在网页输入框中，尚未发送。请点击网页右下角发送箭头后再读取回复。"});
        }
        const {start} = P.markers(task.id);
        if (!pageHasRequest(task.id)) {
          if (pendingNewDeepSeekRoute(task)) {
            if (task.status !== 'waiting') await persist({status:'waiting',message:'DeepSeek 新对话已打开，继续只读等待本次任务编号；不会重复发送。'});
            observeReply();
            return check();
          }
          // Long chats virtualize older user messages. A sent request remains
          // read-only and may still yield its unique result envelope.
          if (task.sent_at || task.dispatch_attempted_at) {
            observeReply();
            return check();
          }
          clearTimeout(timer); timer = null;
          return persist({status:'prepared',message:'当前对话未找到本次任务编号；尚未确认发送。'});
        }
        // Manual read also resumes observation after a reload or expired wait.
        if (task.status !== "waiting") await persist({ status: "waiting", sent_at: task.sent_at || new Date().toISOString(), message: "正在读取本次回复；未重新发送。" });
        observeReply();
        return check();
      }
      if (message.type === "AICF_CANCEL") {
        if(task && task.id!==message.task?.id)return null;
        stopReplyObserver(); clearTimeout(timer); timer = null; task = null; return null;
      }
      throw new Error("未知操作");
    })().then(value => respond({ ok: true, task: value })).catch(error => respond({ ok: false, error: error.message }));
    return true;
  });
})();
