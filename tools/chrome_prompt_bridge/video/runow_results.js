"use strict";

if (!globalThis.__AICF_RUNOW_RESULTS__) {
  globalThis.__AICF_RUNOW_RESULTS__ = true;
  const contract = globalThis.RunowContract;
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const actionPause = () => sleep(20 + Math.floor(Math.random() * 131));

  async function waitFor(read, description) {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const value = read();
      if (value) return value;
      await sleep(150);
    }
    throw new Error('RunningHub 任务列表未就绪：' + description);
  }

  function taskRows() {
    const headers = [...document.querySelectorAll('thead th')]
      .map(node => contract.normalize(node.textContent));
    const columns = ['任务ID', '发起时间', '任务名称', '状态'].map(name => headers.indexOf(name));
    if (columns.includes(-1)) return [];
    return [...document.querySelectorAll('tr[data-row-key]')].map(row => {
      const cells = [...row.querySelectorAll('td')];
      const [id, createdAt, name, status] = columns.map(index => contract.normalize(cells[index]?.textContent));
      return {id, createdAt, name, status, element: row};
    });
  }

  function responseFor(taskId) {
    const detail = document.querySelector('.ant-drawer-body');
    if (!detail) return null;
    for (const pre of detail.querySelectorAll('pre')) {
      let response;
      try { response = JSON.parse(pre.textContent); } catch { continue; }
      if (!response || !response.status) continue;
      const result = contract.taskResult(response, taskId);
      if (result) return result;
    }
    return null;
  }

  async function readTaskResult(channel, taskId) {
    if (typeof taskId !== 'string' || !taskId.trim()) throw new Error('缺少提交回执中的远端任务 ID，请核对原任务');
    if (location.origin !== contract.originForChannel(channel || 'runninghub_web') || contract.pagePath(location.pathname) !== '/call-api/bill-task') {
      throw new Error('RunningHub 结果查询页已变化');
    }
    const search = await waitFor(() => document.querySelector('input[placeholder="输入任务 ID 或任务名称"]'), '任务搜索框');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(search, taskId);
    search.dispatchEvent(new Event('input', {bubbles: true}));
    await actionPause();
    const searchButton = await waitFor(() => [...document.querySelectorAll('button')]
      .find(button => !button.disabled && contract.normalize(button.textContent).replace(/\s/g, '') === '搜索'), '搜索按钮');
    searchButton.click();
    await actionPause();
    await sleep(350);
    await waitFor(() => !document.querySelector('.ant-spin-spinning,.ant-table[aria-busy="true"]'), '任务列表刷新');

    const latest = contract.matchingResultTask(taskRows(), taskId);
    if (!latest) return {ok: true, ready: false};
    const receipt = {taskId: latest.id, source: 'runninghub_task_list'};
    if (/失败|取消/.test(latest.status)) {
      return {ok: true, ready: false, failed: true, receipt, error: 'RunningHub 任务 ' + taskId + ' ' + latest.status};
    }
    if (!/成功|完成/.test(latest.status)) return {ok: true, ready: false, receipt};

    document.querySelector('.ant-drawer-close')?.click();
    await actionPause();
    latest.element.click();
    await actionPause();
    return waitFor(() => responseFor(taskId), '任务 ' + taskId + ' 的响应信息');
  }

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (message?.type !== 'AICF_RUNOW_RESULT_STATUS') return;
    readTaskResult(message.channel, message.taskId).then(respond).catch(error => respond({ok: false, error: error.message}));
    return true;
  });
}
