const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const contract = require('../video/runow_contract.js');
const driver = fs.readFileSync(path.join(__dirname, '../runow_worker.js'), 'utf8');
const worker = fs.readFileSync(path.join(__dirname, '../video_worker.js'), 'utf8');

test('selects only the submitted ID while newer and older H3 tasks coexist', () => {
  const own = {id:'own', name:contract.resultTaskName, status:'运行中'};
  const rows = [{id:'newer',name:contract.resultTaskName,status:'成功'},own,
    {id:'older',name:contract.resultTaskName,status:'成功'}];
  assert.equal(contract.matchingResultTask(rows,'own'),own);
  assert.equal(contract.matchingResultTask(rows,'missing'),null);
  assert.throws(() => contract.matchingResultTask(rows,''), /缺少/);
});

test('parent results accept the associated child response and require successful video output', () => {
  const response = {taskId: 'child', parentTaskId: 'parent', status: 'RUNNING',
    results: [{url: 'https://example.com/video.mp4', outputType: 'mp4'}]};
  assert.equal(contract.taskResult(response, 'parent').ready, false);
  assert.equal(contract.taskResult(response, 'different'), null);
  response.status = 'SUCCESS';
  assert.equal(contract.taskResult(response, 'parent').url, response.results[0].url);
  response.results = [];
  assert.equal(contract.taskResult(response, 'parent').ready, false);
  response.status = 'FAILED';
  assert.equal(contract.taskResult(response, 'parent').failed, true);
});

test('Runow results recover through a dedicated list tab when the creation tab is gone', async () => {
  const events = [];
  const task = {receipt:{taskId:'parent',source:'runninghub_commit'}, id: 'local', tabId: 7, resultTabId: 8, submittedAt: Date.now() - 300000};
  const context = {URL, Date, videoTaskSave: async () => {}, ensureTabReady: async id => events.push(['ready', id]),
    chrome: {tabs: {
      get: async () => {throw new Error('closed');},
      create: async options => {events.push(['create', options]);return {id: 9};},
      sendMessage: async (id, message) => {events.push(['read', id, message.type]);return {ok: true, ready: true, receipt:{taskId:'parent'}, url: 'https://example.com/result.mp4'};}
    }, scripting: {executeScript: async options => events.push(['inject', options.files])}}};
  vm.runInNewContext(driver, context);
  const result = await context.VideoChannelDrivers.runninghub_web.status(task);
  assert.equal(result.ready, true);
  assert.equal(task.resultTabId, 9);
  assert.equal(events[0][1].url, 'https://www.runninghub.cn/call-api/bill-task');
  assert.equal(events.at(-1)[2], 'AICF_RUNOW_RESULT_STATUS');
  assert.equal(result.receipt.taskId, 'parent');
  assert.equal(result.receipt.source, 'runninghub_commit');
});

test('the normal resume entry uses Runow list status and downloads its video', async () => {
  const downloaded = [];
  const task = {channel: 'runninghub_web'};
  const context = {VideoChannelDrivers: {runninghub_web: {status: async () => ({ok: true, ready: true,
    url: 'https://example.com/result.mp4', receipt: {taskId: 'parent'}})}},
    videoPage: async () => {throw new Error('creation page must not be required for list lookup');},
    videoTaskSave: async () => {}, videoDownload: async (_task, result) => downloaded.push(result.url)};
  const start = worker.indexOf('async function videoResume(');
  const end = worker.indexOf('async function videoPumpFromStorage(', start);
  vm.runInNewContext(worker.slice(start, end), context);
  await context.videoResume({}, task);
  assert.deepEqual(downloaded, ['https://example.com/result.mp4']);
  assert.equal(task.receipt.taskId, 'parent');
});


test('Runow waits five minutes then refreshes every thirty seconds across worker restarts', async () => {
  let now = 1000000;
  const events = [];
  const task = {receipt:{taskId:'parent',source:'runninghub_commit'}, id: 'video', submittedAt: now};
  function loadWorker() {
    const context = {URL, Date: {now: () => now},
      videoTaskSave: async () => {}, ensureTabReady: async () => {},
      chrome: {tabs: {
        get: async id => ({id, url: 'https://www.runninghub.cn/call-api/bill-task'}),
        create: async () => {events.push('create');return {id: 9};},
        reload: async () => events.push('reload'),
        sendMessage: async () => {events.push('query');return {ok: true, ready: false};}
      }, scripting: {executeScript: async () => {}}}};
    vm.runInNewContext(driver, context);
    return context.VideoChannelDrivers.runninghub_web;
  }
  let channel = loadWorker();
  await channel.status(task);
  now += 299999;
  await channel.status(task);
  assert.deepEqual(events, []);
  now += 1;
  await channel.status(task);
  assert.deepEqual(events, ['create', 'query']);
  now += 29999;
  channel = loadWorker();
  await channel.status(task);
  assert.equal(events.length, 2);
  now += 1;
  await channel.status(task);
  assert.deepEqual(events, ['create', 'query', 'reload', 'query']);
  now += 30000;
  await channel.status(task);
  assert.deepEqual(events.slice(-2), ['reload', 'query']);
});

test('failed Runow list reads also respect the thirty second interval', async () => {
  let now = 1000000;
  let reads = 0;
  const task = {receipt:{taskId:'parent',source:'runninghub_commit'}, id: 'video', submittedAt: now - 300000};
  const context = {URL, Date: {now: () => now},
    videoTaskSave: async () => {}, ensureTabReady: async () => {},
    chrome: {tabs: {
      create: async () => {reads++;throw new Error('page unavailable');}
    }}};
  vm.runInNewContext(driver, context);
  const channel = context.VideoChannelDrivers.runninghub_web;
  await assert.rejects(channel.status(task), /page unavailable/);
  now += 29999;
  await channel.status(task);
  assert.equal(reads, 1);
  now += 1;
  await assert.rejects(channel.status(task), /page unavailable/);
  assert.equal(reads, 2);
});


test('international Runow driver binds uploads and result queries to the international host', async () => {
  const opened = [];
  const messages = [];
  const context = {URL, Date, videoTaskSave: async () => {}, ensureTabReady: async () => {},
    chrome: {tabs: {
      create: async options => {opened.push(options.url); return {id: 18};},
      sendMessage: async (_id, message) => {messages.push(message); return {ok: true, ready: false};}
    }, scripting: {executeScript: async () => {}}}};
  vm.runInNewContext(driver, context);
  const international = context.VideoChannelDrivers.runninghub_international_web;
  const domestic = context.VideoChannelDrivers.runninghub_web;
  const channels = JSON.parse(fs.readFileSync(path.join(__dirname, '../video/channels.json'), 'utf8'));
  const entry = channels.find(channel => channel.id === 'runninghub_international_web');
  assert.equal(entry.url, 'https://www.runninghub.ai/runow/home?mode=video&model=grp_5947bf0b6892');
  assert.equal(international.matchesUrl(entry.url), true);
  assert.equal(international.matchesUrl('https://www.runninghub.ai/zh-cn/runow/123'), true);
  assert.equal(international.matchesUrl('https://www.runninghub.ai/runow/123'), true);
  assert.equal(international.matchesUrl('https://www.runninghub.cn/runow/123'), false);
  assert.equal(domestic.matchesUrl('https://www.runninghub.ai/runow/123'), false);
  assert.equal(international.matchesUrl('https://www.runninghub.ai.evil.com/runow/123'), false);
  await international.status({receipt:{taskId:'parent',source:'runninghub_commit'}, id: 'local', channel: 'runninghub_international_web', runowResultStartedAt: 1});
  assert.equal(opened[0], 'https://www.runninghub.ai/zh-cn/call-api/bill-task');
  assert.equal(messages[0].channel, 'runninghub_international_web');
  assert.equal(contract.originForChannel('runninghub_international_web'), 'https://www.runninghub.ai');
  assert.equal(contract.originForChannel('unknown'), undefined);
  assert.equal(contract.pagePath('/zh-cn/runow/123'), '/runow/123');
});

 test('historical tasks without a commit ID never search or download the latest result', async () => {
  const context = {URL, Date};
  vm.runInNewContext(driver, context);
  await assert.rejects(context.VideoChannelDrivers.runninghub_web.status({id:'old'}), /缺少/);
 });

test('a foreign result is rejected at the worker boundary', async () => {
  const context={URL,Date,videoTaskSave:async()=>{},ensureTabReady:async()=>{},
    chrome:{tabs:{create:async()=>({id:9}),sendMessage:async()=>({ok:true,ready:true,
      receipt:{taskId:'foreign'},url:'https://example.com/foreign.mp4'})},
      scripting:{executeScript:async()=>{}}}};
  vm.runInNewContext(driver,context);
  await assert.rejects(context.VideoChannelDrivers.runninghub_web.status({id:'local',submittedAt:1,
    receipt:{taskId:'own',source:'runninghub_commit'}}), /不一致/);
});
