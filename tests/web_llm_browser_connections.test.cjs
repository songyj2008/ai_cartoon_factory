const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../ui/web_llm_ui.py'), 'utf8');
const start = source.indexOf('  function displayBrowserConnections(');
const end = source.indexOf('  function displaySettings()', start);

function fixture() {
  const element = () => ({
    textContent: '', children: [],
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren() { this.children = []; },
    setAttribute() {},
  });
  const summary = element();
  const list = element();
  const changes = [];
  const root = {querySelector: selector => selector === '.web-llm-connection' ? summary : list};
  const context = {document: {createElement: element}, Intl, Date,
    A: {setPrimaryBrowser: id => changes.push(id)}};
  vm.runInNewContext(source.slice(start, end), context);
  return {summary, list, changes, render: value => context.displayBrowserConnections(root, value)};
}

function text(node) {
  return [node.textContent, ...node.children.map(text)].join(' ');
}

test('online browsers remain visible when the saved text primary is offline', () => {
  const f = fixture();
  f.render({settings: {primary_text_client_id: 'old'}, browsers: [
    {id: 'old', name: '旧 Edge', type: 'Edge', online: false, seen: 1},
    {id: 'edge', name: 'Edge 视频', type: 'Edge', online: true, seen: 1794385098,
      channels: ['doubao_web', 'runninghub_web']},
    {id: 'chrome', name: 'Chrome 工作', type: 'Chrome', online: true, seen: 1794385098},
  ]});
  assert.match(f.summary.textContent, /浏览器在线 2/);
  assert.doesNotMatch(f.summary.textContent, /旧 Edge/);
  assert.equal(f.list.children.length, 2);
  assert.match(text(f.list.children[0]), /Edge 视频 在线/);
  assert.match(text(f.list.children[0]), /豆包网页视频、RunningHub 网页视频/);
  assert.match(text(f.list.children[0]), /北京时间/);
  f.list.children[0].children[1].onclick();
  assert.deepEqual(f.changes, ['edge']);
});

test('offline browsers are hidden and show the offline empty state', () => {
  const f = fixture();
  f.render({settings: {}, browsers: [{id: 'edge', name: 'Edge', online: false}]});
  assert.match(f.summary.textContent, /暂无在线浏览器/);
  assert.equal(f.list.children.length, 0);
  f.render({settings: {}, browsers: []});
  assert.match(f.summary.textContent, /暂无在线浏览器/);
  assert.equal(f.list.children.length, 0);
});

test('an online primary has a role label and no redundant switch action', () => {
  const f = fixture();
  f.render({settings: {primary_text_client_id: 'edge'}, browsers: [
    {id: 'edge', name: '<script>test</script>', online: true},
  ]});
  assert.match(f.summary.textContent, /在线 1/);
  assert.match(text(f.list.children[0]), /文字主浏览器/);
  assert.equal(f.list.children[0].children.length, 1);
  assert.equal(f.list.children[0].children[0].children[0].children[0].textContent, '<script>test</script>');
});
