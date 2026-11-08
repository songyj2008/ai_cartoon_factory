"""Exercise the actual candidate/apply/save messages without a browser or LLM."""
import subprocess

from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS


def test_composition_result_messages_follow_actual_operation(tmp_path):
    script = tmp_path / 'composition_messages.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');
const clone=x=>JSON.parse(JSON.stringify(x));
function element(){return {dataset:{},value:'',textContent:'',hidden:false,children:[],appendChild(x){this.children.push(x);},replaceChildren(...items){this.children=items;},before(){},remove(){}};}
const storage=new Map();
global.localStorage={setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)};
let panel;
global.document={createElement(tag){const e=element();if(tag==='section'){const children={};e.querySelector=s=>children[s]||(children[s]=element());e.querySelectorAll=()=>[];panel=e;}return e;}};
const A={mountShotEditor(){},installH3Composite(){},refreshH3Composite(){},readPageLLMProviders:()=>['chatgpt'],segmentCardFromButton(){return {dataset:{segmentUid:'s',runtime:'test'}};},
 h3PromptDuration:()=>4,readH3Manifest:ov=>JSON.parse(ov.querySelector('.h3-asset-list').dataset.manifest),
 renderH3SelectedPreview(){},refreshH3AssetCount(){}};
global.window={AICF:A};
let snapshot, candidate, commitResponse, rejectAction, inheritStatus, commitHook, rejectMessage, calls=[];
global.fetch=async (url,options)=>{
 const payload=JSON.parse(options.body);
 const action=url.split('/').at(-1);
 calls.push(action);if(action===rejectAction)throw Error(rejectMessage||'服务暂时不可用');
 let result;
 if(action==='snapshot')result=clone(snapshot);
 else if(action==='optimization-event')result={optimization_status:{known:true,last_attempt:{id:'attempt',stage:payload.stage,clicked_at:'2026-10-27T01:00:00Z',message:payload.message}}};
 else if(action==='continuity'||action==='preview'){if(action==='continuity')assert.deepEqual(payload.providers,['chatgpt']);result=clone(candidate);}
 else if(action==='extract-state'){assert.deepEqual(payload.providers,['chatgpt']);result={state_task:{id:'extract',state:'applied'}};}
 else if(action==='scene-state')result={state_task:null};
 else if(action==='inherit-state'){snapshot.state_status=inheritStatus||{mode:'inherit',ready:true,message:'沿用 Part 4 · 已保存'};result={state_status:snapshot.state_status};}
 else if(action==='commit'){
  if(!payload.manual_edit)assert.equal(payload.draft.prompt,'原稿');
  if(commitHook)await commitHook(payload);
  result=clone(commitResponse);
 }else throw Error('Unexpected API '+action);
 return {ok:true,json:async()=>({ok:true,...result})};
};
''' + EDITOR_DRAFT_JS + r'''
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(meta,oldStateOnly=false,stateMessage='',stateStatus){
 rejectAction=null;inheritStatus=null;commitHook=null;rejectMessage=null;calls=[];
 const draft={prompt:'原稿',source_excerpt:'剧情',duration_sec:4,asset_manifest:[],scene_context_id:'scene'};
 snapshot={draft,base_revision:'v1',context_id:'scene',scene_contexts:{},scene_roles:[],state_message:stateMessage,state_status:stateStatus};
 candidate={draft:{...clone(draft)},operation_base:clone(draft),document:{},changes:['旧版笼统构图完成说明']};
 if(meta)candidate.operation_result=meta;
 if(oldStateOnly)candidate.draft.state_only=true;
 commitResponse={draft:clone(draft),base_revision:'v2',document:{},scene_record_message:'人物记录已保存'};
 if(meta)commitResponse.draft.operation_result=meta;
 const nodes={};
 const ov={dataset:{h3Session:'session'},_sourceButton:{},
  _shotEditor:{documents:[{uid:'shot'}],sourceUid:'shot'},
  querySelector(s){if(s==='.h3-continuity-card')return null;if(s.startsWith('.h3-state')||s.startsWith('.h3-continuity')||s==='.h3-scene-preview')return panel.querySelector(s);return nodes[s]||(nodes[s]=element());},
  querySelectorAll(){return [];},closest(){return this;}};
 ov.querySelector('.aicf-prompt-modal-body').value='原稿';
 ov.querySelector('#aicf-prompt-source-excerpt').value='剧情';
 ov.querySelector('.h3-asset-list').dataset.manifest='[]';
 A.mountShotEditor(ov,'');await tick();
 return ov;
}
async function run(meta,expected,oldStateOnly=false){
 const ov=await setup(meta,oldStateOnly);
 await panel.querySelector('.h3-continuity-start').onclick();
 let message=panel.querySelector('.h3-continuity-status').textContent;
 assert.ok(message.includes(expected),message);assert.ok(message.includes('尚未应用或保存'),message);
 panel.querySelector('.h3-continuity-apply').onclick();
 message=panel.querySelector('.h3-continuity-status').textContent;
 assert.ok(message.includes(expected),message);assert.ok(message.includes('点击顶部保存'),message);
 assert.equal(ov.querySelector('.aicf-prompt-modal-body').value,'原稿');
 assert.ok(!calls.includes('commit'),'Applying a candidate must not save it');
 await A.saveH3EditorDraft(ov);
 message=panel.querySelector('.h3-continuity-status').textContent;
 assert.ok(message.includes(expected),message);assert.ok(message.includes('已保存'),message);
 assert.ok(message.includes('人物记录已保存'),message);
 assert.ok(!message.includes('尚未'),message);
}
(async()=>{
 const extracted=await setup(null);await panel.querySelector('.h3-state-extract').onclick();
 assert.ok(calls.includes('extract-state'));
 await run({kind:'composition',changed_shots:[1,3],summary:'已调整 Shot 1、3 的构图与入画人物'},'已调整 Shot 1、3');
 await run({kind:'state_only',changed_shots:[],summary:'仅更新人物状态，未修改构图'},'仅更新人物状态，未修改构图');
 await run({kind:'unchanged',changed_shots:[],summary:'现有构图符合场景状态，无需修改'},'无需修改');
 await run(null,'仅更新人物状态，未修改构图',true);
 // Saving an unchanged state-only candidate must not rederive its duration.
 const durationOnly=await setup({kind:'unchanged'},true);
 snapshot.draft.duration_sec=4.5;candidate.draft.duration_sec=4.5;
 await A.refreshH3EditorBaseline(durationOnly);
 await panel.querySelector('.h3-continuity-start').onclick();
 panel.querySelector('.h3-continuity-apply').onclick();
 commitHook=payload=>{assert.equal(payload.draft.duration_sec,4.5);assert.equal(payload.draft.state_only,true);};
 await A.saveH3EditorDraft(durationOnly,true);
 // A restored/older candidate can carry the operation summary only in its draft.
 const ov=await setup(null);
 candidate.draft.operation_result={kind:'unchanged',changed_shots:[]};
 await panel.querySelector('.h3-continuity-start').onclick();
 assert.ok(panel.querySelector('.h3-continuity-status').textContent.includes('当前构图无需修改'));
 // Reference removal must keep its own result message, without a composition claim.
 const refs=await setup(null);
 candidate.changes=['移除本段参考音色并禁用自动带入'];
 candidate.draft.operation_result={kind:'composition',changed_shots:[1],summary:'上一次构图操作'};
 A.editH3Reference(refs,'voice');await tick();
 const message=panel.querySelector('.h3-continuity-status').textContent;
 assert.ok(message.includes('移除本段参考音色'),message);
 assert.ok(message.includes('点击顶部保存'),message);
 assert.ok(!message.includes('构图'),message);
 await A.saveH3EditorDraft(refs);
 assert.ok(!panel.querySelector('.h3-continuity-status').textContent.includes('构图'));
 // Old missing/stale ledger messages explain the automatic path without requiring another optimization.
 for(const oldMessage of ['成员段人物状态尚未确认：Part 4','前驱人物状态已变化，请确认：Part 3',
     '人物状态待确认：Part 4','人物状态记录缺少完整Shot列表，请重新优化并保存']){
  const pending=await setup(null,false,oldMessage);
  assert.ok(panel.querySelector('.h3-scene-preview').textContent.includes('本段结束状态（尚未取得有效记录）'));
  assert.ok(panel.querySelector('.h3-scene-preview').textContent.includes(oldMessage));
  assert.deepEqual(calls,['snapshot','scene-state'],'Opening the editor checks the independent state task, never optimizes or saves a prompt');
  assert.equal(pending.querySelector('.aicf-prompt-modal-body').value,'原稿');
 }
 const conflict='Part 4 Shot 2 刘洋已离场，与 Part 5 Shot 1 原位接话冲突';
 await setup(null,false,conflict);
 assert.ok(panel.querySelector('.h3-scene-preview').textContent.includes(conflict));
 // A failed optimization releases its busy state and preserves the user's draft.
 const failed=await setup(null);rejectAction='continuity';
 await panel.querySelector('.h3-continuity-start').onclick();
 assert.equal(failed._editorBusy,false);assert.equal(failed._operationPending,false);
 assert.equal(panel.querySelector('.h3-continuity-start').disabled,false);
 assert.equal(panel.querySelector('.h3-continuity-status').textContent,'服务暂时不可用');
 assert.equal(failed.querySelector('.aicf-prompt-modal-body').value,'原稿');
 // A failed save keeps the applied candidate recoverable and permits retry.
 const inherited=await setup({kind:'unchanged'});
 await panel.querySelector('.h3-state-inherit').onclick();
 assert.ok(panel.querySelector('.h3-state-summary').textContent.includes('沿用 Part 4 · 已保存'));
 assert.deepEqual(calls,['snapshot','scene-state','snapshot','inherit-state','snapshot']);
 // Explain the blocking predecessor, and never claim an unsuccessful inheritance was saved.
 const blockedStatus={mode:'unset',ready:false,message:'受前序段 part_005 阻塞',
  detail:'前序段 part_005：提示词或参考图来源已变化。请先打开 part_005，沿用上段或提取本段状态；当前段状态操作尚未完成。',
  blocking_segment_uid:'previous',blocking_part_id:'part_005',is_target:false};
 const blocked=await setup(null,false,'旧的笼统错误',blockedStatus);
 assert.equal(panel.querySelector('.h3-state-summary').textContent,'当前状态：受前序段 part_005 阻塞');
 assert.ok(panel.querySelector('.h3-scene-preview').textContent.includes(blockedStatus.detail));
 rejectAction='inherit-state';await panel.querySelector('.h3-state-inherit').onclick();
 assert.equal(panel.querySelector('.h3-continuity-status').textContent,'沿用上段未完成：服务暂时不可用');
 assert.equal(panel.querySelector('.h3-state-inherit').disabled,false);
 assert.equal(blocked.querySelector('.aicf-prompt-modal-body').value,'原稿');
 rejectAction=null;inheritStatus=blockedStatus;
 await panel.querySelector('.h3-state-inherit').onclick();
 assert.equal(panel.querySelector('.h3-continuity-status').textContent,'沿用上段未完成：'+blockedStatus.detail);
 assert.ok(!panel.querySelector('.h3-continuity-status').textContent.includes('已保存'));
 const saveFailed=await setup({kind:'unchanged',changed_shots:[]});
 await panel.querySelector('.h3-continuity-start').onclick();
 panel.querySelector('.h3-continuity-apply').onclick();
 rejectAction='commit';await A.saveH3EditorDraft(saveFailed);
 assert.equal(saveFailed._editorBusy,false);
 assert.equal(panel.querySelector('.h3-continuity-start').disabled,false);
 assert.ok(saveFailed._editorDraft);
 assert.equal(saveFailed.querySelector('.aicf-prompt-modal-body').value,'原稿');
 await assert.rejects(A.saveH3EditorDraft(saveFailed,true),/服务暂时不可用/);
 assert.ok(saveFailed._editorDraft);assert.equal(saveFailed._editorBusy,false);
 rejectAction=null;await A.saveH3EditorDraft(saveFailed);
 assert.equal(saveFailed._editorBusy,false);assert.equal(saveFailed._editorDraft,undefined);
 assert.ok(panel.querySelector('.h3-continuity-status').textContent.includes('已保存'));
 // Editing after applying an optimization saves human content, not its old patch.
 const manual=await setup({kind:'composition',changed_shots:[1]});
 candidate.draft.scene_patch={record:{old:true}};candidate.draft.composition_patch={shots:[]};
 candidate.draft.optimization_attempt_id='attempt';
 await panel.querySelector('.h3-continuity-start').onclick();panel.querySelector('.h3-continuity-apply').onclick();
 manual.querySelector('.aicf-prompt-modal-body').value='人工修改后的新台词';
 commitResponse.draft.prompt='人工修改后的新台词';commitResponse.manual_edit=true;
 commitHook=payload=>{assert.equal(payload.manual_edit,true);assert.equal(payload.draft.prompt,'人工修改后的新台词');assert.equal(payload.base_revision,'v1');};
 await A.saveH3EditorDraft(manual,true);
 assert.equal(manual.querySelector('.aicf-prompt-modal-body').value,'人工修改后的新台词');
 assert.equal(manual._editorDraft,undefined);
 assert.ok(panel.querySelector('.h3-continuity-status').textContent.includes('人工编辑稿已保存'));
 assert.ok(panel.querySelector('.h3-continuity-status').textContent.includes('重新提取结束状态'));
 // Explicit text saving clears stale operation metadata and its recovery cache.
 const retained=await setup({kind:'unchanged'});
 await panel.querySelector('.h3-continuity-start').onclick();
 panel.querySelector('.h3-continuity-apply').onclick();
 assert.ok(storage.has('h3-state-v3-draft:test:s'));
 A.clearH3EditorDraft(retained);
 assert.equal(retained._editorDraft,undefined);assert.equal(retained._editorUndo,undefined);
 assert.equal(retained._editorCandidate,undefined);
 assert.equal(storage.has('h3-state-v3-draft:test:s'),false);
 assert.equal(panel.querySelector('.h3-continuity-apply').hidden,true);
 assert.equal(retained.querySelector('.aicf-prompt-modal-body').value,'原稿');
 // A successful commit consumes the optimization even if the user edits in flight.
 const concurrent=await setup({kind:'composition',changed_shots:[1]});
 candidate.draft.state_guard={source:'old'};candidate.draft.scene_patch={record:{}};
 await panel.querySelector('.h3-continuity-start').onclick();
 panel.querySelector('.h3-continuity-apply').onclick();
 commitHook=async()=>{concurrent.querySelector('.aicf-prompt-modal-body').value='保存期间的新文字';};
 await assert.rejects(A.saveH3EditorDraft(concurrent,true),/期间的新修改仍保留/);
 assert.equal(concurrent._editorDraft,undefined);assert.equal(concurrent._editorCandidate,undefined);
 assert.equal(concurrent._editorSaved.base_revision,'v2');
 assert.equal(concurrent.querySelector('.aicf-prompt-modal-body').value,'保存期间的新文字');
 assert.equal(concurrent._shotEditor.stale,true);
 assert.equal(storage.has('h3-state-v3-draft:test:s'),false);
 // Refresh a baseline after a known successful write without replacing visible edits.
 snapshot={...snapshot,base_revision:'v3',state_context:{input_revision:'new-state'},
  state_status:{ready:true,message:'新状态已保存'},draft:{...snapshot.draft,reference_revision:'refs-v3',source_excerpt:'已保存上下文'}};
 await A.refreshH3EditorBaseline(concurrent);
 assert.equal(concurrent._editorSaved.base_revision,'v3');
 assert.equal(concurrent._stateContext.input_revision,'new-state');
 assert.equal(concurrent.querySelector('.h3-asset-list').dataset.baseRevision,'refs-v3');
 assert.equal(concurrent.querySelector('#aicf-prompt-source-excerpt').dataset.savedValue,'已保存上下文');
 assert.equal(concurrent.querySelector('.aicf-prompt-modal-body').value,'保存期间的新文字');
 // An unexpected external write must not silently rebase a pending edit.
 snapshot.base_revision='external';
 await assert.rejects(A.refreshH3EditorBaseline(concurrent,true),/后台内容已变化/);
 assert.equal(concurrent._editorSaved.base_revision,'v3');
 // Late snapshots from another editor session must not contaminate the new one.
 const originalFetch=global.fetch;let finish;
 global.fetch=()=>new Promise(resolve=>finish=resolve);
 const refreshing=A.refreshH3EditorBaseline(concurrent);
 concurrent.dataset.h3Session='new-session';
 finish({ok:true,json:async()=>({ok:true,...snapshot})});await refreshing;
 assert.equal(concurrent._editorSaved.base_revision,'v3');global.fetch=originalFetch;
 // Background state updates cannot rebase an uncommitted optimization.
 const guarded=await setup({kind:'unchanged'});
 await panel.querySelector('.h3-continuity-start').onclick();
 panel.querySelector('.h3-continuity-apply').onclick();
 snapshot.base_revision='background';snapshot.state_context={input_revision:'new-input'};
 await A.refreshH3EditorBaseline(guarded);
 assert.equal(guarded._editorSaved.base_revision,'v1');assert.ok(guarded._editorDraft);
 assert.equal(guarded._stateContext.input_revision,'new-input');
 await assert.rejects(A.refreshH3EditorBaseline(guarded,true),/后台内容已变化/);
 // Permanent optimization history stays distinct from the scene state summary.
 const tracked=await setup(null);
 assert.ok(tracked.querySelector('.h3-optimization-history').textContent.includes('历史未知'));
 await panel.querySelector('.h3-continuity-start').onclick();
 assert.ok(tracked.querySelector('.h3-optimization-history').textContent.includes('候选已生成，尚未应用'));
 A.renderH3OptimizationHistory(tracked,{known:true,last_attempt:{stage:'saved',clicked_at:'2026-10-27T01:00:00Z'},
  last_saved:{saved_at:'2026-10-27T01:01:00Z'},current_matches_saved:true});
 assert.ok(tracked.querySelector('.h3-optimization-history').textContent.includes('已应用并保存'));
 tracked.querySelector('.aicf-prompt-modal-body').value='新文字';A.renderH3OptimizationHistory(tracked);
 assert.ok(tracked.querySelector('.h3-optimization-history').textContent.includes('未保存修改'));
 A.renderH3OptimizationHistory(tracked,{...tracked._optimizationStatus,current_matches_saved:false});
 assert.ok(tracked.querySelector('.h3-optimization-history').textContent.includes('历史优化记录仍保留'));
 const rejected=await setup(null);rejectAction='continuity';
 await panel.querySelector('.h3-continuity-start').onclick();
 assert.ok(rejected.querySelector('.h3-optimization-history').textContent.includes('本次未完成'));
 assert.ok(rejected.querySelector('.h3-optimization-history').textContent.includes('尚无应用并保存的记录'));
 // A stale optimization exposes an explicit content-only recovery action.
 const stale=await setup(null);
 await panel.querySelector('.h3-continuity-start').onclick();panel.querySelector('.h3-continuity-apply').onclick();
 rejectAction='commit';rejectMessage='优化所读取的人物状态或稿件已变化，请重新优化；当前稿已保留';
 await A.saveH3EditorDraft(stale);
 assert.equal(stale._staleOptimizationDraft,stale._editorDraft);
 assert.equal(stale.querySelector('.h3-save-content-only').hidden,false);
 assert.ok(panel.querySelector('.h3-continuity-status').textContent.includes('当前文字尚未保存'));
 A.clearH3EditorDraft(stale);assert.equal(stale._staleOptimizationDraft,undefined);
 assert.equal(stale.querySelector('.h3-save-content-only').hidden,true);
})().catch(e=>{console.error(e);process.exit(1);});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    script.write_text(EDITOR_DRAFT_JS, encoding='utf-8')
    subprocess.run(['node', '--check', str(script)], check=True, capture_output=True)
