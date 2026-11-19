"use strict";

// Runs in the website's main world. Keep this workaround scoped to one owned task page.
function runowSubmissionPatch(command, task) {
  const key = '__AICF_RUNOW_SUBMISSION_PATCH__';
  let state = window[key];
  if (command === 'stop') {
    if (state?.taskId === task.id) state.restore();
    return {ok: true};
  }
  if (command === 'arm') {
    if (state?.taskId !== task.id) throw new Error('Runow 素材收集任务已失效');
    const missing = task.media.filter(item => !state.urls.has(item.id));
    if (missing.length) throw new Error('资源库原始地址尚未取得：' + missing.map(item => item.name).join('、'));
    state.armed = true;
    delete state.quotes.quote;
    return {ok: true, mediaCount: state.urls.size};
  }
  if (command === 'quote' || command === 'media-quote') {
    if (state?.taskId !== task.id || !state.armed) throw new Error('Runow 报价任务已失效');
    const quote = state.quotes[command];
    if (quote?.error) throw new Error(quote.error);
    return {ok:true, ready:Boolean(quote?.ready), fields:quote?.fields || []};
  }
  if (command === 'receipt') {
    if (state?.taskId !== task.id) throw new Error('Runow 尚未确认完整素材提交');
    if (state.receiptError) throw new Error(state.receiptError);
    return {ok: true, ready: Boolean(state.receipt), ...state.receipt};
  }
  if (command !== 'start') throw new Error('Runow 提交补丁操作无效');
  if (location.origin !== (task.origin || 'https://www.runninghub.cn') || !/^\/(?:zh-cn\/)?runow\/\d+\/?$/.test(location.pathname)) {
    throw new Error('Runow 提交补丁需要项目页面');
  }
  state?.restore();
  const originalFetch = window.fetch;
  const OriginalFile = window.File;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const requests = new WeakMap();
  const projectId = location.pathname.replace(/^\/zh-cn(?=\/)/,'').split('/')[2];
  const compact = value => String(value || '').replace(/\s/g, '');
  const prompt = task.promptParts.map(part => {
    if (typeof part.text === 'string') return part.text;
    return part.label.replace(/^@(图片|视频|音频)(\d+)$/, (_, kind, number) =>
      '@' + ({图片: 'Image', 视频: 'Video', 音频: 'Audio'})[kind] + ' ' + number);
  }).join('');
  const expectedPrompt = compact(prompt);
  const mediaGroups = Object.entries({image:'refImage', video:'refVideo', audio:'refAudio'})
    .map(([kind, prefix]) => ({prefix, items:task.media.filter(item => item.type === kind)}));
  state = {taskId: task.id, urls: new Map(), armed: false, commitFields: null, quotes:{}, quoteVersion:0};
  const sameOrigin = url => {
    try { return new URL(url, location.href).origin === location.origin; } catch { return false; }
  };

  function collect(value, depth = 0) {
    if (state.armed || !value || typeof value !== 'object' || depth > 12) return;
    const name = value.assetName || value.title || value.fileName || value.name;
    const sourceKind = String(value.assetType || value.mediaType || value.type || '').toUpperCase();
    const kind = ['VIDEO', 'VIDEOURL', 'VIDEO_URL', 'VIDEO ASSET'].includes(sourceKind) ? 'video' :
      ['AUDIO', 'AUDIOURL', 'AUDIO_URL', 'AUDIO ASSET'].includes(sourceKind) ? 'audio' :
      ['IMAGE', 'IMAGEURL', 'IMAGE_URL', 'IMAGE ASSET'].includes(sourceKind) ? 'image' : '';
    const item = task.media.find(media => media.name === name && media.type === kind);
    const originalUrl = value.assetUrl || value.materialUrl || value.url;
    if (item && typeof originalUrl === 'string' && /^https?:\/\//i.test(originalUrl)) {
      state.urls.set(item.id, originalUrl);
    }
    for (const child of Object.values(value)) collect(child, depth + 1);
  }

  // Runow normally downloads the library asset into a File and uploads it again.
  // Reuse its native cloud-reference cache for the File created from that exact asset.
  function installLibraryReferenceReuse(fingerprint, restoreReferences) {
    if (!OriginalFile || typeof fingerprint !== 'function' || typeof restoreReferences !== 'function') return;
    const originals = new WeakMap();
    state.observeLibraryDownload = (url, response) => {
      if (state.armed || !response.ok || ![...state.urls.values()].includes(url) ||
          (response.url && response.url !== url)) return;
      const readBlob = response.blob.bind(response);
      response.blob = async () => {
        const blob = await readBlob();
        if (blob.size) originals.set(blob, url);
        return blob;
      };
    };
    const ReuseFile = new Proxy(OriginalFile, {
      construct(target, args, newTarget) {
        const file = Reflect.construct(target, args, newTarget);
        const parts = args[0];
        const url = Array.isArray(parts) && parts.length === 1 ? originals.get(parts[0]) : undefined;
        if (!url || state.armed) return file;
        const item = task.media.find(media => media.name === file.name &&
          file.type.startsWith(media.type + '/') && state.urls.get(media.id) === url);
        if (item) restoreReferences([{fingerprint:fingerprint(file), url, mediaType:file.type}]);
        return file;
      }
    });
    window.File = ReuseFile;
    state.restoreFile = () => {if (window.File === ReuseFile) window.File = OriginalFile;};
  }

  async function enableLibraryReferenceReuse() {
    if (typeof document === 'undefined') return;
    const exportsByBuild = {
      '/runow/assets/index-BQbIze-b.js': ['ep', 'ek'],
      '/runow/assets/index-D1g3pehG.js': ['ep', 'ek'],
      '/runow/assets/index-BbuZvlU0.js': ['ep', 'ek'],
      '/runow/assets/index-BOSdfXE2.js': ['en', 'ei']
    };
    const entry = [...document.querySelectorAll('script[type="module"][src]')]
      .map(script => new URL(script.src, location.href))
      .find(url => url.origin === location.origin && Object.hasOwn(exportsByBuild, url.pathname));
    if (!entry) return;
    try {
      const api = await import(entry.href);
      if (window[key] !== state) return;
      const [fingerprint, restoreReferences] = exportsByBuild[entry.pathname];
      installLibraryReferenceReuse(api[fingerprint], api[restoreReferences]);
    } catch {
      // The website's native upload path remains available when its module changes.
    }
  }

  function mapRequest(url, body) {
    const unchanged = {body};
    if (!sameOrigin(url) || typeof body !== 'string') return unchanged;
    const path = new URL(url, location.href).pathname;
    if (!/\/task\/quick-creation\/(prepare|commit|fee-preview)$/.test(path)) return unchanged;
    let payload;
    try { payload = JSON.parse(body); } catch { return unchanged; }
    const request = payload?.createRequest || payload;
    const feePreview = path.endsWith('/fee-preview');
    const commit = path.endsWith('/commit');
    const correctProject = String(request?.projectId) === projectId || (feePreview && request?.projectId == null);
    if (!correctProject || request?.categoryId !== 'VIDEO' || !request.params) return unchanged;
    const actualPrompt = compact(request.params.prompt);
    const mediaQuote = feePreview && actualPrompt === '';
    if (mediaQuote) {
      // Runow can quote before the adapter finishes checking the selected chips.
      // The picker applies one full group per kind, even if Runow serializes only its first slot.
      if (!task.media.length || state.urls.size !== task.media.length ||
          mediaGroups.some(({prefix, items}) => items.length && !/^https?:\/\//i.test(request.params[prefix + '1']))) {
        return unchanged;
      }
    } else if (!state.armed || actualPrompt !== expectedPrompt) {
      return unchanged;
    }
    const params = {...request.params};
    const fields = [];
    for (const {prefix, items} of mediaGroups) {
      for (const field of Object.keys(params)) {
        if (new RegExp('^' + prefix + '\\d+$').test(field)) delete params[field];
      }
      items.forEach((item, index) => {
        const url = state.urls.get(item.id);
        if (!url) throw new Error('Runow 缺少已选素材原始地址：' + item.name);
        const field = prefix + (index + 1);
        params[field] = url;
        fields.push(field);
      });
    }
    request.params = params;
    let quote;
    if (feePreview) {
      quote = {type:mediaQuote ? 'media-quote' : 'quote', version:++state.quoteVersion};
      state.quotes[quote.type] = {ready:false, fields, version:quote.version};
    }
    if (commit) {
      state.commitFields = fields;
      state.clientRequestId = request.clientRequestId || '';
    }
    return {body:JSON.stringify(payload), commit, quote};
  }

  function acceptQuote(response, successful, {type, version}) {
    const quote = state.quotes[type];
    if (quote?.version !== version) return;
    if (!successful || !response || (response.code != null && ![0, '0'].includes(response.code))) {
      quote.ready = false;
      quote.error = 'RunningHub 报价请求失败：'+(response?.msg || response?.message || '网络或服务错误');
      return;
    }
    const data = response.data || response;
    const fee = data.feePreview || data;
    const priced = (fee.free === true && (fee.freeQuotaRemaining == null || fee.freeQuotaRemaining > 0)) ||
      ['requiredPointUnit','pointAmount','point_amount','requiredRhAmount','rhAmount',
        'requiredCashAmount','cashAmount','points'].some(key=>
        fee[key] != null && fee[key] !== '' && Number.isFinite(Number(fee[key])) && Number(fee[key]) >= 0);
    const error = fee.passed === false ? (fee.errorMessage || '当前账号或参数未通过报价校验') :
      priced ? '' : '报价响应缺少价格或免费额度';
    quote.ready = !error;
    quote.error = error ? 'RunningHub 报价失败：'+error : '';
  }

  function acceptCommit(response, successful) {
    if (!successful || (response.code != null && ![0, '0'].includes(response.code))) {
      state.receiptError = 'RunningHub 提交响应失败，请检查原任务页面';
      return;
    }
    const data = response.data || response;
    if (data.uncertain || data.submissionUnknown ||
        ['SUBMIT_UNKNOWN', 'SUBMITTING'].includes(data.submissionStatus || data.taskStatus)) {
      state.receiptError = 'RunningHub 尚未确认远端任务创建，请核对原任务';
      return;
    }
    const validId = value => (typeof value === 'string' && value.trim()) ||
      (typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? String(value) : '');
    const taskId = validId(data.taskId);
    if (!taskId) {
      state.receiptError = 'RunningHub 提交响应缺少可靠任务 ID，请核对原任务；禁止按最新任务取结果';
      return;
    }
    if (state.receipt && state.receipt.taskId !== taskId) {
      state.receiptError = 'RunningHub 本次提交返回多个任务 ID，请核对原任务';
      return;
    }
    state.receipt = {taskId, projectId, clientRequestId: state.clientRequestId, fields: state.commitFields,
      source: 'runninghub_commit', submittedAt: Date.now()};
  }

  async function patchedFetch(input, init) {
    const url = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
    let body = init?.body;
    if (body === undefined && typeof Request !== 'undefined' && input instanceof Request &&
        !['GET', 'HEAD'].includes(input.method)) body = await input.clone().text();
    const tracked = mapRequest(url, body);
    const options = body === undefined ? init : {...init, body:tracked.body};
    const response = await originalFetch.call(this, input, options);
    state.observeLibraryDownload?.(typeof input === 'string' || input instanceof URL
      ? new URL(url, location.href).href : url, response);
    if (tracked.commit || tracked.quote) {
      try {
        const data = await response.clone().json();
        if (tracked.commit) acceptCommit(data, response.ok);
        if (tracked.quote) acceptQuote(data, response.ok, tracked.quote);
      } catch {
        if (tracked.commit) state.receiptError = 'RunningHub 提交响应无法解析，请核对原任务';
        if (tracked.quote) acceptQuote(null, false, tracked.quote);
      }
    }
    if (sameOrigin(url) && /json/i.test(response.headers.get('content-type') || '')) {
      response.clone().json().then(collect).catch(() => {});
    }
    return response;
  }
  function patchedOpen(method, url, ...args) {
    requests.set(this, String(url));
    this.addEventListener('load', () => {
      if (!sameOrigin(url)) return;
      const tracked = requests.get(this);
      try {
        const data = this.responseType === 'json' ? this.response : JSON.parse(this.responseText);
        collect(data);
        if (tracked?.commit) acceptCommit(data, this.status >= 200 && this.status < 300);
        if (tracked?.quote) acceptQuote(data, this.status >= 200 && this.status < 300,
          tracked.quote);
      } catch {
        if (tracked?.commit) state.receiptError = 'RunningHub 提交响应无法解析，请核对原任务';
        if (tracked?.quote) acceptQuote(null, false, tracked.quote);
      }
    }, {once: true});
    return originalOpen.call(this, method, url, ...args);
  }
  function patchedSend(body) {
    const url = requests.get(this) || location.href;
    const tracked = mapRequest(url, body);
    requests.set(this, tracked);
    return originalSend.call(this, tracked.body);
  }
  state.restore = () => {
    clearTimeout(state.timer);
    state.restoreFile?.();
    if (window.fetch === patchedFetch) window.fetch = originalFetch;
    if (XMLHttpRequest.prototype.open === patchedOpen) XMLHttpRequest.prototype.open = originalOpen;
    if (XMLHttpRequest.prototype.send === patchedSend) XMLHttpRequest.prototype.send = originalSend;
    if (window[key] === state) delete window[key];
  };
  window[key] = state;
  window.fetch = patchedFetch;
  XMLHttpRequest.prototype.open = patchedOpen;
  XMLHttpRequest.prototype.send = patchedSend;
  state.timer = setTimeout(state.restore, 15 * 60 * 1000);
  return enableLibraryReferenceReuse().then(() => ({ok:true}));
}
