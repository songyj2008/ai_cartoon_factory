"""Execute the production JS with a small DOM fixture for pagination races."""
import shutil
import subprocess

import pytest

from ui.scripts import APP_JS


@pytest.mark.parametrize("force_save", [True, False])
def test_h3_pagination_and_save_state_in_javascript(force_save):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-state regression")
    manifest = APP_JS.split("    function _h3Manifest(root)", 1)[1].split(
        "    function _scheduleH3ConfigAutosave", 1
    )[0]
    save = APP_JS.split("    window.AICF.saveH3GenerationConfig = async function", 1)[1].split(
        "    window.AICF.regenerateH3Segment", 1
    )[0]
    program = r"""
const assert = require('node:assert/strict');
const window = {AICF: {}, clearTimeout() {}};
const currentImages = [{id: 'jian', type: 'image', order: 1}, {id: 'craft', type: 'image', order: 2}, {id: 'wu', type: 'image', order: 3}, {id: 'street', type: 'image', order: 4}, {id: 'uploaded', type: 'image', order: 1}];
const oldImages = [{id: 'wu'}, {id: 'jian'}, {id: 'cabin'}, {id: 'sky'}];
const source = {dataset: {segmentIndex: '7', manifest: JSON.stringify(currentImages)}};
const target = {dataset: {segmentIndex: '11', manifest: JSON.stringify(oldImages)}};
const regenerate = {removeAttribute() {}};
const promptSource = {textContent: 'old prompt'};
const cardPrompt = {value: 'old prompt'};
let card = {querySelector(selector) {
    if (selector === '.h3-card-config-source' || selector === '.h3-asset-list') return source;
    if (selector === '.h3-prompt-override') return cardPrompt;
    if (selector === '.prompt-modal-source') return promptSource;
    return null;
}};
const panel = {querySelector(selector) {return selector === '.h3-asset-list' ? target : regenerate;}};
""" + "function _h3Manifest(root)" + manifest + r"""
window.AICF.hydrateH3ModalConfig(panel, card, '7');
assert.deepEqual(JSON.parse(target.dataset.manifest), currentImages);
assert.equal(target.dataset.segmentIndex, '7');
let regenerated;
window.AICF.regenerateH3Segment = index => {regenerated = index;};
regenerate.onclick();
assert.equal(regenerated, '7');
source.dataset.manifest = '[]';
window.AICF.hydrateH3ModalConfig(panel, card, '7');
assert.deepEqual(JSON.parse(target.dataset.manifest), []);
assert.throws(() => window.AICF.hydrateH3ModalConfig(panel, card, '11'));
source.dataset.manifest = JSON.stringify(currentImages);
window.AICF.hydrateH3ModalConfig(panel, card, '7');
const field = {value: 'submitted prompt'};
const root = {
    dataset: {segmentIndex: '7', h3Session: '1'},
    classList: {contains: () => true},
    querySelector(selector) {return selector === '.h3-asset-list' ? target : field;},
    querySelectorAll() {return [];}
};
function _segmentCardForIndex() {return card;}
function _h3Status() {}
window.AICF.updateSegmentCardState = () => {};
window.AICF.refreshH3AssetCount = () => {};
let release;
const requests = [];
function _requestJson(url, options) {
    requests.push(JSON.parse(options.body));
    return new Promise(resolve => {release = resolve;});
}
""" + "window.AICF.saveH3GenerationConfig = async function" + save + r"""
(async () => {
    const saving = window.AICF.saveH3GenerationConfig('7', null, root, true);
    assert.ok(root._h3SavePending);
    const recycledSource = {dataset: {segmentIndex: '11', manifest: JSON.stringify(oldImages)}};
    // The retained card has now become page 11; resolve the current page anew.
    const retainedCard = card;
    card = {querySelector: retainedCard.querySelector};
    retainedCard.querySelector = () => recycledSource;
    release({generation_config: {asset_manifest: currentImages}, canonical_prompt: 'canonical prompt'});
    await saving;
    assert.equal(promptSource.textContent, 'canonical prompt');
    assert.equal(field.value, 'canonical prompt');
    assert.equal(cardPrompt.value, 'canonical prompt');
    assert.deepEqual(JSON.parse(source.dataset.manifest), currentImages);
    assert.deepEqual(JSON.parse(recycledSource.dataset.manifest), oldImages);
    assert.equal(root._h3SavePending, null);
    assert.equal(requests[0].segment_index, 7);
    assert.equal(requests[0].force_save, true);
    assert.deepEqual(requests[0].asset_manifest, currentImages.map((item, index) => ({...item, order: index + 1})));
    assert.equal(requests[0].asset_manifest[4].id, 'uploaded');
    assert.equal(requests[0].asset_manifest[4].order, 5);
    // A queued save cannot read a newly opened session after its await.
    let unlock;
    root._h3SavePending = new Promise(resolve => {unlock = resolve;});
    const queued = window.AICF.saveH3GenerationConfig('7', null, root);
    root.dataset.h3Session = '2';
    root.dataset.segmentIndex = '11';
    root._h3SavePending = null;
    unlock();
    await assert.rejects(queued, /编辑会话已切换/);
    assert.equal(requests.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    if not force_save:
        program = program.replace("null, root, true);", "null, root, false);")
        program = program.replace("requests[0].force_save, true", "requests[0].force_save, false")
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
