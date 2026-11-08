import shutil
import subprocess
from types import SimpleNamespace

import pytest

from generation.service import GenerationService
from ui.group_panel import _prompt_revision_state
from ui.scripts import APP_JS


def test_production_javascript_compiles():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    result = subprocess.run(
        [node, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"],
        input=APP_JS, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("revision", ["saved text", "older revision", ""])
@pytest.mark.parametrize("saved", [True, False])
def test_persisted_status_is_independent_of_revision_comparison(saved, revision):
    segment = {"prompt_saved": saved, "generation_config": {
        "prompt_override": "saved text", "latest_prompt_revision": {"prompt": revision},
    }}
    label, css, _, versions = _prompt_revision_state(segment)
    assert label == ("已保存" if saved else "未保存")
    assert css == ("status-success" if saved else "status-pending")
    assert versions["matches"] == (revision == "saved text")


def test_manual_save_persists_saved_flag_without_replacing_revision(monkeypatch):
    revision = {"prompt": "older revision", "version": 1}
    payload = {"default_model_id": "minimax_h3_local_ref2va", "segments": [{
        "segment_index": 1, "model_id": "minimax_h3_local_ref2va", "prompt_saved": False,
        "generation_config": {"asset_manifest": [], "latest_prompt_revision": revision},
        "job": {"prompt": "old"},
    }]}
    persisted = []
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: payload)
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda value: persisted.append(value))
    service = GenerationService()
    monkeypatch.setattr(service, "_pipeline_for", lambda _: SimpleNamespace(
        reconcile_generation_config=lambda segment, old, new, text: new,
    ))
    result = service.update_segment_generation_config(1, prompt_override="manually saved text")
    segment = result["segments"][0]
    assert persisted and persisted[-1]["segments"][0]["prompt_saved"] is True
    assert segment["status"] == "needs_regenerate"
    assert segment["generation_config"]["latest_prompt_revision"] == revision


def test_browser_save_updates_badge_only_after_success():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    state_js = "window.AICF.segmentFromResponse = function" + APP_JS.split(
        "window.AICF.segmentFromResponse = function", 1
    )[1].split("window.AICF.replaceSegmentCardFromResponse", 1)[0]
    save_js = "window.AICF.saveSegmentPrompt = async function" + APP_JS.split(
        "window.AICF.saveSegmentPrompt = async function", 1
    )[1].split("window.AICF.collectSegmentPromptEdits", 1)[0]
    program = r"""
const assert = require('node:assert/strict');
const window = {AICF: {segmentEditorFromButton: () => null, segmentPromptDrafts: {2:'draft'}}};
const badge = {textContent:'提示词未保存', classList:{remove(){},add(){}}};
const status = {textContent:'',classList:{remove(){},add(){}}};
const card = {dataset:{},querySelector(selector){
    if(selector==='[data-prompt-save-status]')return badge;
    if(selector==='[data-segment-status]')return status;
    return null;
}};
const document = {querySelector:()=>card,getElementById:()=>null};
let resolve;
let reply;
const fetch = async ()=>new Promise(r=>{resolve=r;});
const setTimeout = ()=>{};
""" + state_js + save_js + r"""
(async()=>{
    for(const revision of ['saved text','older revision','']){
        badge.textContent='提示词未保存';
        const pending=window.AICF.saveSegmentPrompt(2,null,'saved text');
        assert.equal(badge.textContent,'提示词未保存');
        resolve({ok:true,json:async()=>({ok:true,video_jobs:{segments:[{
            segment_index:2,status:'needs_regenerate',prompt_saved:true,
            generation_config:{prompt_override:'saved text',latest_prompt_revision:{prompt:revision}}
        }]}})});
        await pending;
        assert.equal(badge.textContent,'提示词已保存');
        assert.equal(status.textContent,'需要重新生成');
        assert.equal(window.AICF.segmentPromptDrafts[2],undefined);
    }
    badge.textContent='提示词未保存';
    window.AICF.segmentPromptDrafts[2]='retry text';
    const failed=window.AICF.saveSegmentPrompt(2,null,'retry text');
    resolve({ok:false,json:async()=>({ok:false,message:'disk error'})});
    await assert.rejects(failed,/disk error/);
    assert.equal(badge.textContent,'提示词未保存');
    assert.equal(window.AICF.segmentPromptDrafts[2],'retry text');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
