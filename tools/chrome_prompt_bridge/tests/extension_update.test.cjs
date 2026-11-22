const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../extension_update.js'), 'utf8');

function fixture(saved = {}) {
  let reloads = 0;
  const chrome = {storage: {local: {
    get: async () => saved,
    set: async value => Object.assign(saved, value),
    remove: async key => delete saved[key],
  }}, runtime: {reload: () => reloads++}};
  function load() {
    const context = {crypto: require('node:crypto').webcrypto};
    vm.runInNewContext(source, context);
    return context.ExtensionUpdate;
  }
  return {saved, chrome, load, get reloads() { return reloads; }};
}

test('busy text and video tasks finish before reloading, then reload only once', async () => {
  const f = fixture({'bridgeTask:one': {status: 'waiting', bridge: {reported: false}}});
  const updater = f.load();
  const command = {id: 'one'};
  assert.equal(await updater.handle(f.chrome, command), true);
  assert.equal(f.reloads, 0);
  f.saved['bridgeTask:one'].bridge.reported = true;
  f.saved['videoTask:two'] = {state: 'submitted'};
  await updater.handle(f.chrome, command);
  assert.equal(f.reloads, 0);
  f.saved['videoTask:two'].state = 'complete';
  await updater.handle(f.chrome, command, true);
  assert.equal(f.reloads, 0);
  await updater.handle(f.chrome, command);
  await updater.handle(f.chrome, command);
  assert.equal(f.reloads, 1);
  assert.equal((await updater.receipt(f.chrome, 'old')).phase, 'reloading');
  assert.equal((await f.load().receipt(f.chrome, 'new')).phase, 'reloaded');
});

test('reload failure is retained for reporting and stops automatic reload loops', async () => {
  const f = fixture();
  const updater = f.load();
  f.chrome.runtime.reload = () => { throw new Error('permission required'); };
  await updater.handle(f.chrome, {id: 'one'});
  assert.equal(f.saved.extensionUpdate.phase, 'failed');
  assert.equal((await updater.receipt(f.chrome, '')).message, 'permission required');
  await updater.handle(f.chrome, {id: 'one'});
  await updater.handle(f.chrome, null);
  assert.equal(f.saved.extensionUpdate, undefined);
});
