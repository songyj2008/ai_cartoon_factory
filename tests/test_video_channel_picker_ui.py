"""Card regeneration resolves a channel without requiring the fullscreen editor."""
import subprocess

from ui.scripts import APP_JS


def test_channel_resolution_without_editor_and_during_save(tmp_path):
    start = APP_JS.index('window.AICF.chooseVideoChannel = async function')
    end = APP_JS.index('window.AICF.watchWebVideoTask', start)
    script = tmp_path / 'channels.cjs'
    script.write_text('''
const assert = require('node:assert/strict');
global.window = {AICF: {}};
let modal = null, requests = 0;
global.document = {getElementById: () => modal};
const channels = [
 {id:'runninghub', available:true, uses_workflow:true},
 {id:'comfyui', available:true, uses_workflow:true},
 {id:'doubao_web', available:false, reason:'quota exhausted'}
];
let selected;
global.fetch = async () => {
 requests++;
 return {ok:true, json:async () => ({ok:true, channels, selected})};
};
''' + APP_JS[start:end] + '''
(async () => {
 assert.equal((await window.AICF.chooseVideoChannel(5)).id, 'runninghub');
 selected = 'comfyui';
 assert.equal((await window.AICF.chooseVideoChannel(5)).id, 'comfyui');
 let field = {_channels:channels, querySelector:() => null};
 modal = {dataset:{segmentIndex:'5'}, querySelector:() => field};
 selected = undefined;
 assert.equal((await window.AICF.chooseVideoChannel(5)).id, 'runninghub');
 const before = requests;
 let saved = false;
 field._savePending = Promise.resolve().then(() => {saved=true;});
 field.querySelector = () => {assert.equal(saved,true); return {value:'comfyui'};};
 assert.equal((await window.AICF.chooseVideoChannel(5)).id, 'comfyui');
 assert.equal(requests,before);
 field.querySelector = () => ({value:'doubao_web'});
 await assert.rejects(window.AICF.chooseVideoChannel(5), /quota exhausted/);
 field._savePending = Promise.reject(new Error('save failed'));
 await assert.rejects(window.AICF.chooseVideoChannel(5), /save failed/);
 modal = null;
 global.fetch = async () => ({ok:false,json:async () => ({ok:false,message:'network error'})});
 await assert.rejects(window.AICF.chooseVideoChannel(5), /network error/);
})().catch(error => {console.error(error);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    script.write_text(APP_JS, encoding='utf-8')
    subprocess.run(['node', '--check', str(script)], check=True, capture_output=True)
