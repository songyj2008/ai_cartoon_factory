"use strict";
const P = globalThis.PromptBridge;
const $ = id => document.getElementById(id);
const extensionVersion = globalThis.chrome?.runtime?.getManifest?.().version;
if (extensionVersion) $("extension-version").textContent = `v${extensionVersion} · 项目后台桥接版 · 不使用 Codex 或 API 密钥`;
const draftKeys = ["prompt", "issue", "expected", "context"];
let task = null, tabs = [], settings = {}, previewId = crypto.randomUUID(), busy = false;
const hasExtension = Boolean(globalThis.chrome?.storage?.local && globalThis.chrome?.tabs);
const draft = () => Object.fromEntries(draftKeys.map(key => [key, $(key).value]));
const activeTask = () => task && ["prepared", "sending", "waiting"].includes(task.status);
function status(message, error = false) { $("status").textContent = message; $("status").classList.toggle("error", error); }
function backendStatus(message, error = false) {
  $("backend-status").textContent = message;
  $("backend-status").classList.toggle("error", error);
}
let backendBusy = false;
function runBackend(fn) {
  return async () => {
    if (backendBusy) return;
    backendBusy = true;
    const buttons = ["connect-backend", "disconnect-backend", "poll-backend"];
    buttons.forEach(id => { $(id).disabled = true; });
    try { await fn(); }
    catch (error) { backendStatus(error.message || String(error), true); }
    finally { backendBusy = false; buttons.forEach(id => { $(id).disabled = false; }); }
  };
}
async function pollBackend() {
  let timeout;
  try {
    const response = await Promise.race([
      chrome.runtime.sendMessage({ type: "AICF_BACKEND_POLL" }),
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("扩展后台未响应。请在 chrome://extensions 重新加载本扩展，再关闭并重开侧边栏。")), 15000); })
    ]);
    if (response?.accepted) {
      const saved=(await chrome.storage.local.get('bridgeStatus')).bridgeStatus;
      if(saved)backendStatus(saved.message,saved.error);
    } else if (response?.status) backendStatus(response.status.message, response.status.error);
  } catch (error) {
    if (/receiving end|establish connection|context invalidated/i.test(error.message || "")) {
      throw new Error("扩展后台脚本未连接。请在 chrome://extensions 重新加载本扩展，再关闭并重开侧边栏。");
    }
    throw error;
  } finally { clearTimeout(timeout); }
}
function controls() {
  const locked = busy || Boolean(activeTask());
  draftKeys.forEach(key => { $(key).disabled = locked; });
  $("target").disabled = locked || !hasExtension;
  const pendingCallback = Boolean(task?.bridge && !task.bridge.reported);
  $("fill").disabled = busy || Boolean(activeTask()) || pendingCallback || !hasExtension;
  $("send").disabled = busy || Boolean(activeTask() && task.status !== "prepared") || Boolean(pendingCallback && task.status !== "prepared") || !hasExtension;
  $("read").disabled = busy || !task || task.status === "cancelled" || task.status === "complete" || !hasExtension;
  $("cancel").hidden = !activeTask();
  $("cancel").disabled = busy;
  $("refresh").disabled = locked || !hasExtension;
  $("save-selectors").disabled = locked || !hasExtension;
}
function preview() {
  try { $("request").value = activeTask() ? task.request : P.buildRequest(draft(), previewId); }
  catch { $("request").value = "填写原提示词与问题描述后，在这里查看完整请求。"; }
}
function renderTask(next) {
  const invalid = Boolean(next && (typeof next.id !== 'string' || !next.id.trim()));
  task = invalid ? null : next || null;
  $("result").hidden = !task?.result;
  $("empty").hidden = Boolean(task?.result);
  $("raw-box").hidden = !task?.raw;
  $("raw").value = task?.raw || "";
  $("revised").value = task?.result?.revised_prompt || (task?.result?.data ? (typeof task.result.data === "string" ? task.result.data : JSON.stringify(task.result.data, null, 2)) : "");
  $("original").textContent = task?.draft?.prompt || "";
  $("changes").replaceChildren();
  for (const change of task?.result?.changes || []) {
    const li = document.createElement("li"); li.textContent = change; $("changes").append(li);
  }
  const taskStates = { prepared: "已准备", sending: "正在发送", waiting: "等待网页回复", complete: "已完成", error: "执行失败", timeout: "等待超时", cancelled: "已取消" };
  const progressByState = { prepared: 8, sending: 24, waiting: 62, complete: 100 };
  const taskState = task ? (taskStates[task.status] || task.status || "处理中") : "等待提交";
  const taskProgress = task ? (progressByState[task.status] ?? (task.status === "error" || task.status === "timeout" || task.status === "cancelled" ? 100 : 0)) : 0;
  $("extension-task-state").textContent = taskState;
  $("extension-task-percent").textContent = task ? `${taskProgress}%` : "—";
  $("extension-task-progress").value = taskProgress;
  $("extension-task-progress").classList.toggle("is-failed", Boolean(task && ["error", "timeout"].includes(task.status)));
  $("task-info").textContent = task ? `${task.provider} · ${new Date(task.created_at).toLocaleString()} · ${task.id.slice(0, 8)}` : "提交后，结果会显示在这里。";
  if (task) status(task.message, ["error", "timeout"].includes(task.status));
  else if (invalid) status('缓存任务缺少编号，已停止恢复该任务；后台连接设置不受影响。', true);
  controls(); preview();
}
function loadSelectors() {
  const tab = tabs.find(item => item.id === Number($("target").value));
  const config = settings[P.provider(tab?.url)] || {};
  for (const key of ["editor", "send", "reply"]) $(`${key}-selector`).value = config[key] || "";
}
async function refresh() {
  const previous = activeTask() ? String(task.tab_id) : $("target").value;
  tabs = (await chrome.tabs.query({ url: ["https://chatgpt.com/*", "https://chat.deepseek.com/*", "https://*.doubao.com/*"] })).filter(tab => P.provider(tab.url));
  $("target").replaceChildren();
  const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = tabs.length ? "请选择一个对话标签页" : "未找到对话，请先打开并登录"; $("target").append(placeholder);
  for (const tab of tabs) {
    const option = document.createElement("option"); option.value = String(tab.id); option.textContent = `${P.provider(tab.url)} · ${tab.title || "对话"} · #${tab.id}`; $("target").append(option);
  }
  if (tabs.some(tab => String(tab.id) === previous)) $("target").value = previous;
  else if (tabs.length === 1) $("target").value = String(tabs[0].id);
  loadSelectors();
  for (const id of ['deepseek','chatgpt','doubao']) {
    const select = $("backend-" + id);
    select.replaceChildren(new Option("自动管理标签页池（数量见下方状态）", ""));
    select.disabled = true;
  }
}
async function sendMessage(type, selectedTask) {
  const tab = await chrome.tabs.get(selectedTask.tab_id);
  if (P.provider(tab.url) !== selectedTask.provider) throw new Error("目标标签页已关闭或切换网站，请重新选择。");
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["protocol.js", "text_provider_policy.js", "content.js"] });
  const response = await chrome.tabs.sendMessage(tab.id, { type, task: selectedTask });
  if (!response?.ok) throw new Error(response?.error || "网页未响应，请查看目标标签页。");
  if (response.task) renderTask(response.task);
  return response;
}
async function archiveCurrent() {
  if (!task) return;
  const saved = await chrome.storage.local.get("history");
  const history = [task, ...(saved.history || []).filter(item => item.id !== task.id)].slice(0, 10);
  await chrome.storage.local.set({ history });
}
async function assertManualTabFree(tabId) {
  const saved = await chrome.storage.local.get(null);
  if (Object.entries(saved).some(([key,value]) => key.startsWith('bridgeTask:') &&
      value?.tab_id === tabId && value.bridge && !value.bridge.reported))
    throw new Error('所选标签页正在执行项目后台任务，请等待完成或选择其他标签页。');
}
async function submit(type) {
  let next = task;
  if (!activeTask()) {
    const tab = tabs.find(item => item.id === Number($("target").value));
    if (!tab) throw new Error("请先选择 ChatGPT、DeepSeek 或豆包标签页。");
    const id = previewId;
    const data = draft();
    const request = P.buildRequest(data, id);
    await chrome.storage.local.set({ draft: data });
    await archiveCurrent();
    next = { id, tab_id: tab.id, provider: P.provider(tab.url), url: tab.url,
      draft: data, request, settings: settings[P.provider(tab.url)] || {}, created_at: new Date().toISOString() };
  }
  await assertManualTabFree(next.tab_id);
  await sendMessage(type, next);
  previewId = crypto.randomUUID();
}
function run(fn) {
  return async () => {
    if (busy) return;
    busy = true; controls();
    try { await fn(); }
    catch (error) { status(error.message || String(error), true); }
    finally { busy = false; controls(); }
  };
}
$("fill").addEventListener("click", run(() => submit("AICF_FILL")));
$("send").addEventListener("click", run(() => submit("AICF_SEND")));
$("read").addEventListener("click", run(() => sendMessage("AICF_READ", task)));
$("refresh").addEventListener("click", run(refresh));
$("target").addEventListener("change", loadSelectors);
$("cancel").addEventListener("click", run(async () => {
  const cancelled = { ...task, status: "cancelled", message: "已结束本次等待。网页里的生成不会因此停止；再次提交前请自行处理网页输入框。" };
  await chrome.storage.local.set({ task: cancelled });
  try { await sendMessage("AICF_CANCEL", cancelled); } catch { /* Tab may already be closed. */ }
  renderTask(cancelled); previewId = crypto.randomUUID(); preview();
}));
$("save-selectors").addEventListener("click", run(async () => {
  const tab = tabs.find(item => item.id === Number($("target").value));
  if (!tab) throw new Error("先选择要配置的网站标签页。");
  const config = {};
  for (const key of ["editor", "send", "reply"]) {
    config[key] = $(`${key}-selector`).value.trim();
    if (config[key]) { try { document.querySelector(config[key]); } catch { throw new Error(`${key} 选择器语法无效。`); } }
  }
  settings[P.provider(tab.url)] = config;
  await chrome.storage.local.set({ settings }); status("当前网站的适配设置已保存，下次新任务生效。");
}));
$("copy-request").addEventListener("click", run(async () => {
  const text = activeTask() ? task.request : P.buildRequest(draft(), previewId);
  await navigator.clipboard.writeText(text); status("请求已复制。要自动读取回复，请先用“仅填入网页”创建对应任务。");
}));
$("copy-result").addEventListener("click", run(async () => {
  await navigator.clipboard.writeText($("revised").value); status("修正版已复制，可以粘贴回项目编辑器。");
}));
$("export").addEventListener("click", run(async () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(task, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = `prompt-revision-${task.id}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}));
let saveTimer;
for (const key of draftKeys) $(key).addEventListener("input", () => {
  preview(); clearTimeout(saveTimer);
  if (hasExtension) saveTimer = setTimeout(() => chrome.storage.local.set({ draft: draft() }).catch(error => status(`草稿保存失败：${error.message}`, true)), 300);
});
(async () => {
  controls();
  if (!hasExtension) { $("target").options[0].textContent = "预览模式 · 加载扩展后选择对话"; status("界面预览模式。请在 Chrome 扩展侧边栏中使用提交功能。"); return; }
  const saved = await chrome.storage.local.get(["draft", "task", "settings"]);
  settings = saved.settings || {};
  const restoredDraft = saved.task && ["prepared", "sending", "waiting"].includes(saved.task.status) ? saved.task.draft : saved.draft;
  for (const key of draftKeys) $(key).value = restoredDraft?.[key] || "";
  renderTask(saved.task); await refresh();
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.task) renderTask(changes.task.newValue);
    if (area === "local" && changes.bridgeStatus) backendStatus(changes.bridgeStatus.newValue?.message || "", changes.bridgeStatus.newValue?.error);
  });
  const backend = (await chrome.storage.local.get("backend")).backend || {};
  $("backend-url").value = backend.url || "http://127.0.0.1:7860";
  $("backend-token").value = backend.token || "";
  const previousStatus = (await chrome.storage.local.get("bridgeStatus")).bridgeStatus;
  if (backend.enabled) backendStatus(previousStatus?.message || "配置已启用，正在检查后台连接…", previousStatus?.error);
  else backendStatus("尚未启用：填写配对码后，点击“保存并启用连接”。");
  setInterval(() => {
    if (!backendBusy) pollBackend().catch(error => backendStatus(error.message, true));
  }, 1000);
})().catch(error => { backendStatus(`扩展初始化失败：${error.message}。请重新加载扩展并重新打开侧边栏。`, true); status(error.message, true); });

$("connect-backend").addEventListener("click", runBackend(async () => {
  backendStatus("正在保存配置…");
  const url = new URL($("backend-url").value.trim());
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.pathname !== "/" || url.search || url.hash || url.username || url.password) throw new Error("请输入本机 HTTP 地址，例如 http://127.0.0.1:7860。");
  const token = $("backend-token").value.trim();
  if (!token) throw new Error("请从项目设置复制配对码。");
  if (task?.bridge && !task.bridge.reported && task.bridge.url !== url.origin) throw new Error("请先结束旧后台任务并完成回传，再更换后台地址。");
  await chrome.storage.local.set({ backend: { enabled: true, url: url.origin, token,
    tabs: { deepseek: Number($("backend-deepseek").value), chatgpt: Number($("backend-chatgpt").value) } },
    bridgeStatus: {message: "配置已保存，正在连接本机后台…", at: Date.now()} });
  backendStatus("配置已保存，正在连接本机后台…");
  await pollBackend();
}));
$("disconnect-backend").addEventListener("click", runBackend(async () => {
  const saved = await chrome.storage.local.get("backend");
  await chrome.storage.local.set({ backend: { ...saved.backend, enabled: false } });
  backendStatus("已暂停接收和回传；网页上的生成不会停止，恢复连接后继续同步。");
}));
$("poll-backend").addEventListener("click", runBackend(async () => {
  backendStatus("正在刷新标签页和检查连接…");
  await refresh();
  const saved = await chrome.storage.local.get("backend");
  if (!saved.backend?.enabled) {backendStatus("后台连接尚未启用。请先点击“保存并启用连接”；刷新不会自动保存配对码。");return;}
  await pollBackend();
}));

const conversationEntries = ['story', 'beats', 'prompt', 'feedback', 'revision', 'other'];
async function loadConversationPolicy() {
  if (!hasExtension) return;
  const {conversationPolicy: policy = {}} = await chrome.storage.local.get('conversationPolicy');
  $('conversation-mode').value = policy.mode || 'new';
  $('conversation-rounds').value = policy.maxRounds || 5;
  $('conversation-chars').value = policy.maxChars || 30000;
  for (const entry of conversationEntries) $('conversation-' + entry).value = policy.overrides?.[entry] || 'inherit';
}
$('save-conversation').addEventListener('click', async () => {
  const button = $('save-conversation'); button.disabled = true;
  try {
    if (!hasExtension) throw new Error('请在 Chrome 扩展侧边栏中保存策略。');
    const maxRounds = Number($('conversation-rounds').value), maxChars = Number($('conversation-chars').value);
    if (!Number.isInteger(maxRounds) || maxRounds < 1 || maxRounds > 50) throw new Error('任务轮数须为 1–50 的整数。');
    if (!Number.isInteger(maxChars) || maxChars < 1000 || maxChars > 500000) throw new Error('累计字符须为 1000–500000 的整数。');
    const overrides = Object.fromEntries(conversationEntries.map(entry => [entry, $('conversation-' + entry).value]));
    await chrome.storage.local.set({conversationPolicy: {mode: $('conversation-mode').value, maxRounds, maxChars, overrides}});
    $('conversation-status').textContent = '已保存，从下一条新任务生效；当前任务保持原对话。';
  } catch(error) { $('conversation-status').textContent = error.message; }
  finally { button.disabled = false; }
});
loadConversationPolicy().catch(error => { $('conversation-status').textContent = error.message; });
