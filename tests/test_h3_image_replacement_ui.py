import subprocess

from ui.scripts import APP_JS


def test_replacement_js_syntax_and_slot_identity(tmp_path):
    script = tmp_path / "replacement.cjs"
    start = APP_JS.index('window.AICF.h3ReplacementAsset = function')
    end = APP_JS.index('window.AICF.beginH3ImageReplacement', start)
    script.write_text('const assert = require("node:assert/strict");\n'
                      'global.window = {AICF: {}};\n' + APP_JS[start:end] + '''
const old = {id: 'slot_a', type: 'image', path: 'old.png', role: 'Alice', order: 2,
 metadata: {identity_id: 'alice', role_id: 'character_1', subject_description: 'old'}};
const fresh = {id: 'upload_b', type: 'image', path: 'new.png', role: '',
 metadata: {width: 640, subject_description: 'new'}};
const replacement = window.AICF.h3ReplacementAsset(old, fresh);
assert.equal(replacement.id, 'slot_a');
assert.equal(replacement.asset_id, 'slot_a');
assert.equal(replacement.order, 2);
assert.equal(replacement.role, 'Alice');
assert.equal(replacement.metadata.identity_id, 'alice');
assert.equal(replacement.metadata.subject_description, 'new');
assert.equal(replacement.path, 'new.png');
assert.equal(old.path, 'old.png');
assert.equal(fresh.id, 'upload_b');
''', encoding="utf-8")
    subprocess.run(["node", str(script)], check=True, capture_output=True)
    script.write_text(APP_JS, encoding="utf-8")
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)


def test_confirm_cancel_and_failed_save_preserve_original(tmp_path):
    start = APP_JS.index('function _h3EditorRoot(root)')
    end = APP_JS.index('window.AICF.renderH3SelectedPreview = function', start)
    script = tmp_path / 'interaction.cjs'
    script.write_text('''
const assert = require('node:assert/strict');
global.window = {AICF: {}};
function element() { return {children: [], appendChild(x) {this.children.push(x);}, setAttribute() {}, scrollIntoView() {}}; }
global.document = {createElement: element};
global.FormData = class {constructor(){this.values={};} append(k,v){this.values[k]=v;}};
let uploaded;
async function _requestJson(url, options) {
 uploaded = {url, form:options.body};
 return {assets:[{id:'png',type:'image',path:'uploaded.png',label:'吴天2.png'}]};
}
const original = [{id:'slot', type:'image', path:'old.png', role:'Alice', order:1}];
const source = {dataset:{manifest:JSON.stringify(original)}, scrollIntoView() {}};
const root = {dataset:{h3Session:'one',segmentIndex:'7'}, querySelector() {return source;}};
const nestedPanel = {dataset:{},closest(selector){return selector === '.aicf-prompt-modal' ? root : null;}};
function _h3Manifest() {return JSON.parse(source.dataset.manifest);}
function _h3ProjectManifestIndex(manifest, id, path) {return manifest.findIndex(x => x.path === path);}
function _h3Status() {}
window.AICF.refreshH3AssetCount = () => {};
window.AICF.renderH3SelectedPreview = () => {};
window.AICF.assetFileUrl = x => x;
''' + APP_JS[start:end] + '''
(async () => {
 function start() {
  window.AICF.beginH3ImageReplacement(nestedPanel,'slot');
  root._h3Replacement.candidate = {id:'new',type:'image',path:'new.png'};
  const container = element();
  _renderH3Replacement(root,container);
  const children = container.children[0].children;
  return {file:children.find(x=>x.type==='file'),confirm: children.find(x=>x.textContent==='确认替换'),cancel:children.find(x=>x.textContent==='取消替换')};
 }
 let buttons = start();
 buttons.cancel.onclick();
 assert.equal(root._h3Replacement,null);
 assert.deepEqual(_h3Manifest(),original);
 buttons = start();
 const png = {name:'吴天2.png',type:'image/png'};
 buttons.file.files = [png];
 window.AICF.renderH3SelectedPreview = () => {buttons.file.files = [];};
 await buttons.file.onchange();
 assert.equal(uploaded.url,'/api/segment/7/assets');
 assert.equal(uploaded.form.values.files,png);
 assert.equal(root._h3Replacement.candidate.path,'uploaded.png');
 assert.deepEqual(_h3Manifest(),original);
 window.AICF.renderH3SelectedPreview = () => {};
 buttons = start();
 window.AICF.saveH3GenerationConfig = async () => {throw Error('save failed');};
 await buttons.confirm.onclick();
 assert.deepEqual(_h3Manifest(),original);
 assert.equal(root._h3Replacement.busy,false);
 buttons = start();
 window.AICF.saveH3GenerationConfig = async () => ({ok:true});
 await buttons.confirm.onclick();
 assert.equal(_h3Manifest()[0].path,'new.png');
 assert.equal(_h3Manifest()[0].id,'slot');
 assert.equal(root._h3Replacement,null);
})().catch(e=>{console.error(e);process.exit(1);});
''', encoding='utf-8')
    subprocess.run(['node', str(script)], check=True, capture_output=True)
