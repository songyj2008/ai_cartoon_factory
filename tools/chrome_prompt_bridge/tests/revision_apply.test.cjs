const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,'../../../ui/web_llm_ui.py'),'utf8');
const start = source.indexOf('          const target=task.metadata.snapshot || {};');
const end = source.indexOf('          const appliedPrompt=',start);
const validation = source.slice(start,end);
function check(savedThisTask, overrides={}, references='refs') {
  const original={runtime:'project',segment_index:2,segment_uid:'segment',beat_uid:'beat',context:{asset_manifest:['old']}};
  vm.runInNewContext(validation,{
    task:{metadata:{segment_index:2,snapshot:original,editor_references:'refs'}},
    fresh:{snapshot:{...original,context:{asset_manifest:['normalized']},...overrides}},
    savedThisTask,revisionInputState:JSON.stringify,referenceState:()=>references,
  });
}
test('saved revision loads despite normalization of the saved asset configuration',()=>{
  assert.doesNotThrow(()=>check(true));
});
test('unsaved revision still rejects changed configuration',()=>{
  assert.throws(()=>check(false),/参考配置/);
});
test('saved revision rejects a different project or segment and unsaved reference edits',()=>{
  for(const overrides of [{runtime:'other'},{segment_index:3},{segment_uid:'other'},{beat_uid:'other'}])
    assert.throws(()=>check(true,overrides),/项目或分段/);
  assert.throws(()=>check(true,{},'edited refs'),/参考素材已修改/);
});
