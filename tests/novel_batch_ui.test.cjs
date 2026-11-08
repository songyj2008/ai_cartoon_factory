const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../ui/text_jobs_ui.py'), 'utf8');

function fixture({dirtyText = false, dirtySegment = false, project = 'project', dirtyStory = false, submittedBeats = 'saved'} = {}) {
  const clicks = [];
  const fields = {
    project_name: {value: project}, project_episode: {value: 'episode'},
    beats_editor: {value: dirtyText ? 'user edit' : 'saved'},
    story_editor: {value: dirtyStory ? 'user story' : 'saved story'},
  };
  const context = {
    field: id => fields[id], editorBaselines: new Map([['beats_editor', 'saved'], ['story_editor', 'saved story']]),
    api: async () => ({result: {editors: {beats_editor: submittedBeats}, published_beats_text: 'generated'}}),
    setTimeout() {}, resetEditorBaselines() {},
    document: {
      getElementById: () => ({getBoundingClientRect: () => ({width: 900})}),
      querySelector: selector => selector === '#aicf-prompt-modal.open' ? null : {click: () => clicks.push(selector)},
      querySelectorAll: () => [{value: dirtySegment ? 'user edit' : 'saved', defaultValue: 'saved'}],
    },
  };
  const dirtyChecks = source.slice(source.indexOf('  function hasUnsavedEditors(){'), source.indexOf('  setTimeout(resetEditorBaselines,2500);'));
  const refresh = source.slice(source.indexOf('  async function maybeRefreshTask(task){'), source.indexOf('  async function api('));
  vm.createContext(context);
  vm.runInContext(dirtyChecks + refresh, context);
  return {clicks, refresh: kind => context.maybeRefreshTask({kind, state: 'applied', project: 'project', episode: 'episode'})};
}

test('completed segments refresh without overwriting dirty Beats text', async () => {
  const f = fixture({dirtyText: true});
  assert.equal(await f.refresh('novel_segment'), true);
  assert.match(f.clicks[0], /operation_segment_refresh_btn/);
});
test('completed segment refresh preserves unsaved segment edits', async () => {
  const f = fixture({dirtySegment: true});
  assert.equal(await f.refresh('novel_segment'), false);
  assert.equal(f.clicks.length, 0);
});
test('completed Beats never overwrite unsaved editor text', async () => {
  const f = fixture({dirtyText: true});
  assert.equal(await f.refresh('novel_beats'), false);
  assert.equal(f.clicks.length, 0);
});
test('result for another project cannot refresh the current project', async () => {
  const f = fixture({project: 'other'});
  assert.equal(await f.refresh('novel_segment'), false);
  assert.equal(f.clicks.length, 0);
});
test('completed Beats refresh only their own editor despite unrelated story edits', async () => {
  const f = fixture({dirtyStory: true});
  assert.equal(await f.refresh('novel_beats'), true);
  assert.match(f.clicks[0], /operation_beats_refresh_btn/);
});
test('Beats text already present at submission can be replaced by the requested new Beats', async () => {
  const f = fixture({dirtyText: true, submittedBeats: 'user edit'});
  assert.equal(await f.refresh('novel_beats'), true);
});
