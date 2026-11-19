const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../video_worker.js'), 'utf8');

function worker(tasks = []) {
  const storage = {backend: {enabled: true, token: 'token'}, clientId: 'edge'};
  for (const task of tasks) storage['videoTask:' + task.id] = task;
  const context = {Date, crypto: {randomUUID: () => 'edge'},
    chrome: {storage: {local: {
      get: async () => storage,
      set: async patch => Object.assign(storage, patch)
    }}}};
  vm.createContext(context);
  vm.runInContext(source, context);
  context.videoCatalog = async () => [{id: 'doubao_web'}, {id: 'runninghub_web'}];
  return context;
}

test('a slow channel does not block ticks for other channels or start twice', async () => {
  const context = worker();
  const calls = [];
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  context.videoPumpChannel = async (_config, _client, channel) => {
    calls.push(channel);
    if (channel === 'doubao_web') await gate;
  };
  await context.videoPumpFromStorage();
  await new Promise(setImmediate);
  await context.videoPumpFromStorage();
  assert.equal(calls.filter(channel => channel === 'doubao_web').length, 1);
  assert.equal(calls.filter(channel => channel === 'runninghub_web').length, 2);
  release();
  await new Promise(setImmediate);
});

test('busy channel resumes only its own task while the other channel can claim', async () => {
  const context = worker([{id: 'a', channel: 'doubao_web', state: 'submitted'}]);
  const resumed = [], claimed = [];
  context.videoResume = async (_config, task) => resumed.push(task.id);
  context.videoApi = async (_config, route, body) => {
    if(route.endsWith('/status'))return {task: {state:'submitted'}};
    claimed.push(body);return {task: null};
  };
  await Promise.all([
    context.videoPumpChannel({}, 'edge', 'doubao_web'),
    context.videoPumpChannel({}, 'edge', 'runninghub_web')
  ]);
  assert.deepEqual(resumed, ['a']);
  assert.deepEqual(JSON.parse(JSON.stringify(claimed)), [{client_id: 'edge', channels: ['runninghub_web']}]);
});


test('a backend-cleared local task stops occupying its channel and permits the next claim', async () => {
  const task = {id: 'old', channel: 'runninghub_web', state: 'claimed'};
  const context = worker([task]);
  const requests = [];
  context.videoApi = async (_config, route) => {
    requests.push(route);
    return {task: route.endsWith('/status') ? {state:'uncertain',error:'reset'} : null};
  };
  context.videoResume = async () => {throw new Error('stale task resumed');};
  context.videoReport = async () => {throw new Error('revoked lease reused');};
  await context.videoPumpChannel({}, 'edge', 'runninghub_web');
  assert.equal(task.state, 'uncertain');
  await context.videoPumpChannel({}, 'edge', 'runninghub_web');
  assert.deepEqual(requests, ['/tasks/old/status', '/claim']);
});
