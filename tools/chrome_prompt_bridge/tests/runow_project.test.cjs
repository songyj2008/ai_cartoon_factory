const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname, '../video/runow.js'), 'utf8');

function extract(start, end, context) {
  context.actionPause ||= async()=>{};
  context.C ||= require('../video/runow_contract.js');
  vm.runInNewContext(page.slice(page.indexOf(start), page.indexOf(end, page.indexOf(start))), context);
}

test('submission composer requires the project form with direct creation enabled', () => {
  let projectForm = null;
  const context = {visible: node => Boolean(node), document: {querySelector: selector => {
    assert.equal(selector, 'section[data-testid="quick-create-composer"][data-kind="video"] [data-direct-creation="true"]');
    return projectForm;
  }}};
  extract('  const composer=', '  function find(', context);
  assert.equal(vm.runInNewContext('composer()', context), null);
  projectForm = {};
  assert.equal(vm.runInNewContext('composer()', context), projectForm);
});

for (const targetModel of ['Minimax H3-RH增强版', 'Minimax H3-RH Enhanced'])
for (const initialView of ['canvas', 'cards'])
for (const toolbarDelay of initialView==='cards'?[0,2,99]:[0,2])
for (const menuMode of [true, false])
for (const rebuild of [false, true]) test('project workspace ' + initialView + ' selects ' + targetModel + '; toolbarDelay=' + toolbarDelay + '; menu=' + menuMode + '; rebuild=' + rebuild, async () => {
  const events = [];
  let opened = false;
  let kind = 'image';
  let modelName = 'Seedance 2.5';
  let menuOpen = false;
  let modelClicks = 0;
  const model = {getAttribute: () => modelName, classList: {contains: () => menuOpen}, click: () => {
    events.push('model-menu');
    modelClicks++;
    menuOpen = !rebuild || modelClicks > 1;
  }};
  const switchMode = {};
  const videoItem = {};
  // The menu joins the model title and its description without whitespace.
  const h3Option = {disabled: false, getAttribute: () => null,
    innerText: targetModel + '基于开源 MiniMax H3 的 RH增强版，支持多参考',
    querySelector: () => ({label: targetModel})};
  let view=initialView;
  let toolbarReads=0;
  const cardsButton={label:'卡片'};
  const workspace={getAttribute:()=>view,querySelectorAll:()=>[cardsButton]};
  const entry={matches:()=>false};
  const panel = {getAttribute: () => kind, querySelector: () => menuMode?switchMode:null,
    querySelectorAll:()=>[videoItem],matches:()=>true};
  const root = {querySelector: () => model};
  const location = {pathname: '/runow/home'};
  const context = {location, visible: node => Boolean(node), text: node => node?.label || '视频',
    composer: () => kind === 'video' ? root : null,
    waitFor: async (read, description) => {
      for (let attempt = 0; attempt < 5; attempt++) {
        const result = await read();
        if (result) return result;
      }
      throw new Error(description);
    },
    find:()=>view==='cards'?entry:null,
    document: {readyState: 'complete', querySelector:()=>++toolbarReads>toolbarDelay?workspace:null, querySelectorAll: selector =>
      selector === '[role="menuitem"]' ? [videoItem] : selector === 'button,[role="button"]' ? (menuOpen ? [h3Option] : []) : opened ? [panel] : []},
    clickText: async pattern => {
      if (pattern.test('新建项目')) {events.push('new-project');location.pathname = '/runow/123';}
      else throw new Error('canvas creation toolbar must not be clicked');

    },
    click: async node => {
      if(node===cardsButton){events.push('cards');view='cards';}
      if(node===entry){events.push('open-form');opened=true;}
      if (node === switchMode) events.push('mode-menu');
      if (node === videoItem) {events.push('video');kind = 'video';}
      if (node === model) model.click();
      if (node === h3Option) {events.push('select-H3');modelName = targetModel;}
    }};
  extract('  async function openProject(', '  async function openLibrary(', context);
  await context.openProject({});
  if(initialView==='canvas')assert.ok(toolbarReads>toolbarDelay);
  assert.deepEqual(events, ['new-project', ...(initialView==='canvas'?['cards']:[]), 'open-form', ...(menuMode?['mode-menu']:[]), 'video', 'model-menu',
    ...(rebuild ? ['model-menu'] : []), 'select-H3']);
});

test('native project resources use separate media groups and preserve per-type reference numbers', async () => {
  const media = [
    {type: 'image', name: 'actor.png', web_label: '@图片1'},
    {type: 'image', name: 'scene.png', web_label: '@图片2'},
    {type: 'video', name: 'motion.mp4', web_label: '@视频1'},
    {type: 'audio', name: 'voice.mp3', web_label: '@音频1'}
  ];
  const groups = [];
  const previews = media.map(item => ({
    querySelector: selector => selector === '.qcv2-ref-kind-mark' ? {label: item.web_label.slice(1)} :
      item.type==='audio'?null:{getAttribute: () => `预览 ${item.name} 的素材详情`}
  }));
  const context = {visible: () => true, text: node => node.label,
    composer: () => ({querySelectorAll: () => previews}),
    selectMediaGroup: async (_task, items, kind) => groups.push([kind, Array.from(items, item => item.name)]),
    waitFor: async read => {assert.equal(read(), true);}};
  extract('  async function selectMedia(task)', '  const settingsSummary=', context);
  await context.selectMedia({media});
  assert.deepEqual(groups, [['image', ['actor.png', 'scene.png']], ['video', ['motion.mp4']], ['audio', ['voice.mp3']]]);
});
