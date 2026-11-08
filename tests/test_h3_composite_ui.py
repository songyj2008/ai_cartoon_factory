import shutil
import subprocess

import pytest

from ui.h3_composite_ui import COMPOSITE_JS


FIXTURE = r'''
const assert=require('node:assert/strict');
const trace=[];
function node(value='') {return {value,textContent:'',disabled:false,inert:false,
 setAttribute(){},classList:{toggle(){}},querySelector(){return inherit;}};}
const inherit=node(),picker=node('extract'),scene=node('room'),button=node(),line=node(),
 editor=node('draft'),excerpt=node('context'),content=node();
const fields={'.h3-composite-mode':picker,'.h3-state-select':scene,'.h3-composite-run':button,
 '.h3-composite-status':line,'.h3-composite-hint':node(),'.aicf-prompt-modal-body':editor,
 '#aicf-prompt-source-excerpt':excerpt,'.aicf-prompt-modal-content':content};
const ov={dataset:{h3Session:'1',segmentIndex:'2'},_editorUid:'two',_editorRuntime:'test',
 classList:{contains:()=>true},querySelector:s=>fields[s]||null,querySelectorAll:()=>[]};
let base='saved',state='extract',states=['applied'],failAt='',registered=false,closed=0;
const actions={
 snapshot:async()=>({base_revision:base,context_id:scene.value,scene_has_predecessor:true,
  state_context:{input_revision:'upstream'},draft:{prompt:editor.value},
  state_status:{ready:true,mode:state}}),
 optimize:async()=>{trace.push('optimize');if(failAt==='optimize')throw Error('optimization failed');editor.value='optimized';},
 register:async mode=>{trace.push(mode);if(failAt==='state')throw Error('state failed');registered=true;state=mode;}
};
const A={promptRuntimeRevision:1,readH3Manifest:()=>[],
 closePromptModal:()=>{assert.equal(content.inert,false);assert.equal(ov._h3CompositeRun,undefined);closed++;},
 readPromptReplanOptions:()=>({duration_sec:8,providers:['chatgpt']}),
 savePromptBeforeTask:async()=>{trace.push('save');base='saved-current';},
 runSavedPromptAction:async(root,submit)=>{await A.savePromptBeforeTask(root);return submit();},
 savePromptSourceExcerpt:async(replan,options)=>{
  assert.equal(replan,true);assert.deepEqual(options.providers,['chatgpt']);
  trace.push('retime');if(failAt==='retime-submit')throw Error('retime submit failed');
  ov._sourceReplanPending={id:'timing'};return {operation_id:'timing'};
 },
 regenerateH3Segment:async(index,b,options)=>{
  options.guard();assert.equal(options.prepared,true);assert.equal(options.throwOnFailure,true);
  assert.equal(index,2);trace.push('video');if(failAt==='video')throw Error('video failed');return {ok:true};
 }};
global.window={AICF:A};
global.setTimeout=callback=>setImmediate(callback);
global.fetch=async()=>{
 trace.push('poll');const state=states.length>1?states.shift():states[0];
 return {ok:true,json:async()=>({ok:true,result:{state,message:'timing failed'}})};
};
ov._syncSourceReplan=async()=>{trace.push('sync');delete ov._sourceReplanPending;if(states[0]==='applied')base='retimed';};
function progressPanel(){
 const summary=node(),rows=[];
 const list={replaceChildren:()=>{rows.length=0;},appendChild:row=>rows.push(row)};
 fields['.h3-composite-progress']={querySelector:s=>s==='ol'?list:summary};
 global.document={createElement:()=>({...node(),dataset:{}})};
 return {summary,rows};
}
function manualStepButtons(){
 const selectors=['.h3-continuity-start','.aicf-prompt-modal-save','.aicf-prompt-modal-replan',
  '.h3-state-inherit','.h3-state-extract','.h3-config-actions button'];
 for(const selector of selectors)fields[selector]={...node(),dataset:{},title:'原按钮提示',onclick:()=>trace.push('manual:'+selector)};
 ov.querySelectorAll=selector=>selector==='button[data-composite-failed]'?
  selectors.map(s=>fields[s]).filter(b=>b.dataset.compositeFailed):[];
 return selectors;
}
'''


def run_node(tmp_path, code):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js required')
    script = tmp_path / 'composite.cjs'
    script.write_text(FIXTURE + COMPOSITE_JS + '\n' + code, encoding='utf-8')
    result = subprocess.run([executable, str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['extract', 'inherit', 'none'])
def test_combinations_preserve_order_and_force_requested_state_action(tmp_path, mode):
    run_node(tmp_path, "const mode=" + repr(mode) + r''';
(async()=>{
 if(mode==='none')scene.value='';else picker.value=mode;
 A.installH3Composite(ov,actions);
 assert.deepEqual(trace,[],'Selecting a mode must not execute any operation');
 assert.equal(picker.disabled,mode==='none');
 await A.runH3Composite(ov);
 const expected=mode==='none'?['save','save','retime','poll','sync','video']:
  ['optimize','save','save','retime','poll','sync',mode,'video'];
 assert.deepEqual(trace,expected);
 assert.equal(registered,mode!=='none');
 assert.equal(content.inert,false);assert.equal(ov._h3CompositeRun,undefined);
 assert.ok(line.textContent.includes('已提交'));
 assert.equal(closed,1,'Close only after the saved preparation and video submission succeed');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_waits_for_retime_publication_before_extracting(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 states=['running','ready','applying','applied'];
 A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.deepEqual(trace.slice(4),['poll','poll','poll','poll','sync','extract','video']);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


@pytest.mark.parametrize('stage', ['optimize', 'retime-submit', 'state'])
def test_failure_stops_before_video_and_unlocks(tmp_path, stage):
    run_node(tmp_path, "failAt=" + repr(stage) + r''';
(async()=>{
 A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.ok(!trace.includes('video'));assert.ok(line.textContent.includes('已停止'));
 assert.equal(content.inert,false);assert.equal(button.disabled,false);
 assert.equal(closed,0,'Keep the editor open on preparation failure');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_retime_error_never_registers_or_submits_video(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 states=['error'];A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.ok(!trace.includes('extract'));assert.ok(!trace.includes('video'));
 assert.ok(line.textContent.includes('timing failed'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


@pytest.mark.parametrize('switch_session', [False, True])
def test_editor_stays_open_until_submission_succeeds_and_never_closes_another_session(tmp_path, switch_session):
    run_node(tmp_path, 'const switchSession=' + str(switch_session).lower() + r''';
(async()=>{
 let release,entered;
 A.regenerateH3Segment=async()=>{trace.push('video');entered();await new Promise(resolve=>release=resolve);return {ok:true};};
 A.installH3Composite(ov,actions);
 const submitting=new Promise(resolve=>entered=resolve),pending=A.runH3Composite(ov);
 await submitting;
 assert.ok(trace.includes('sync'));assert.ok(trace.includes('extract'));
 assert.equal(closed,0);
 if(switchSession)ov.dataset.h3Session='other';
 release();await pending;
 assert.equal(closed,switchSession?0:1);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_video_submission_failure_keeps_editor_open(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 failAt='video';A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.equal(closed,0);assert.ok(line.textContent.includes('video failed'));
 assert.equal(content.inert,false);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_retry_continues_after_completed_steps(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 assert.equal(ov._h3CompositeCheckpoint.next,3);
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['extract','video']);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_failure_checklist_and_retry_identify_exact_start_step(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const panel=progressPanel();A.installH3Composite(ov,actions);
 A.refreshH3Composite(ov,await actions.snapshot());
 assert.equal(panel.rows.length,5);assert.ok(panel.summary.textContent.includes('第 1 步'));
 failAt='state';await A.runH3Composite(ov);
 assert.deepEqual(panel.rows.map(r=>r.dataset.state),['done','done','done','error','pending']);
 assert.ok(panel.summary.textContent.includes('已完成 3 步'));
 assert.ok(panel.summary.textContent.includes('第 4 步重试'));
 assert.equal(button.textContent,'重试一键准备并生成');
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['extract','video']);
 assert.ok(panel.summary.textContent.includes('本次从第 4 步继续'));
 assert.deepEqual(panel.rows.map(r=>r.dataset.state),Array(5).fill('done'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


@pytest.mark.parametrize('stage,selector', [
    ('optimize', '.h3-continuity-start'),
    ('save', '.aicf-prompt-modal-save'),
    ('retime-submit', '.aicf-prompt-modal-replan'),
    ('state', '.h3-state-extract'),
    ('video', '.h3-config-actions button'),
])
def test_failed_step_marks_its_existing_manual_button_without_replacing_action(tmp_path, stage, selector):
    run_node(tmp_path, 'const stage=' + repr(stage) + ',selector=' + repr(selector) + r''';
(async()=>{
 const panel=progressPanel(),selectors=manualStepButtons(),manual=fields[selector],handler=manual.onclick;
 if(stage==='save')A.savePromptBeforeTask=async()=>{throw Error('save failed');};else failAt=stage;
 A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.equal(manual.dataset.compositeFailed,'true');assert.equal(manual.disabled,false);
 assert.equal(selectors.filter(s=>fields[s].dataset.compositeFailed).length,1);
 assert.equal(manual.onclick,handler);manual.onclick();assert.equal(trace.at(-1),'manual:'+selector);
 assert.ok(panel.summary.textContent.includes('上次失败：第 '));
 assert.ok(panel.summary.textContent.includes('可单独点击标红按钮处理'));
 assert.ok(manual.title.includes('上次一键流程失败'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_failure_highlight_survives_reopening_and_clears_after_step_succeeds(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const panel=progressPanel();manualStepButtons();const extract=fields['.h3-state-extract'];
 A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 assert.equal(extract.dataset.compositeFailed,'true');
 delete ov._h3CompositeCheckpoint;ov.dataset.h3Session='reopened';A.installH3Composite(ov,actions);
 assert.equal(extract.dataset.compositeFailed,'true');assert.equal(panel.rows[3].dataset.state,'error');
 assert.ok(panel.summary.textContent.includes('第 4/5 步'));
 let release,entered;
 const register=actions.register;
 actions.register=async mode=>{entered();await new Promise(resolve=>release=resolve);await register(mode);};
 const started=new Promise(resolve=>entered=resolve);failAt='';const pending=A.runH3Composite(ov);await started;
 assert.equal(panel.rows[3].dataset.state,'running');assert.equal(extract.dataset.compositeFailed,'true');
 release();await pending;
 assert.equal(extract.dataset.compositeFailed,undefined);assert.equal(extract.title,'原按钮提示');
 assert.equal(ov._h3CompositeProgress.lastFailure,undefined);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_verified_checkpoint_survives_reopening_and_resumes_failed_step(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const panel=progressPanel();A.installH3Composite(ov,actions);
 failAt='state';await A.runH3Composite(ov);assert.equal(storage.size,1);
 delete ov._h3CompositeCheckpoint;ov.dataset.h3Session='reopened';
 A.installH3Composite(ov,actions);A.refreshH3Composite(ov,await actions.snapshot());
 assert.ok(panel.summary.textContent.includes('上次记录：共 5 步，已完成 3 步'));
 assert.ok(panel.summary.textContent.includes('未变化时从第 4 步继续'));
 assert.equal(button.textContent,'重试一键准备并生成');
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['extract','video']);
 assert.ok(panel.summary.textContent.includes('本次从第 4 步继续'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_inherit_mode_and_checkpoint_survive_reopening(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const panel=progressPanel();picker.value='inherit';A.installH3Composite(ov,actions);
 failAt='state';await A.runH3Composite(ov);
 picker.value='extract';ov.dataset.h3Session='new-session';A.installH3Composite(ov,actions);
 assert.equal(picker.value,'inherit');
 assert.deepEqual(panel.rows.map(r=>r.dataset.state),['done','done','done','error','pending']);
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['inherit','video']);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_target_state_failure_does_not_invalidate_unchanged_incoming_world(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let targetReady=true;
 const snapshot=actions.snapshot,register=actions.register;
 actions.snapshot=async()=>({...await snapshot(),state_input_context:{input_revision:'incoming'},
  state_context:targetReady?{input_revision:'incoming'}:null});
 actions.register=async mode=>{targetReady=false;await register(mode);};
 A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['extract','video']);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


@pytest.mark.parametrize('change', ['backend', 'upstream', 'prompt', 'excerpt', 'manifest', 'provider'])
def test_reopened_checkpoint_checks_live_inputs_before_skipping_steps(tmp_path, change):
    run_node(tmp_path, 'const change=' + repr(change) + r''';
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const panel=progressPanel();A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 ov.dataset.h3Session='new-session';A.installH3Composite(ov,actions);
 if(change==='backend')base='externally-saved';
 if(change==='upstream'){
  const snapshot=actions.snapshot;
  actions.snapshot=async()=>({...await snapshot(),state_input_context:{input_revision:'changed'}});
 }
 if(change==='prompt')editor.value='manual change';
 if(change==='excerpt')excerpt.value='new story context';
 if(change==='manifest')A.readH3Manifest=()=>[{id:'new-image'}];
 if(change==='provider'){
  A.readPromptReplanOptions=()=>({duration_sec:8,providers:['chatgpt','deepseek']});
  const retime=A.savePromptSourceExcerpt;
  A.savePromptSourceExcerpt=(replan,options)=>{
   assert.deepEqual(options.providers,['chatgpt','deepseek']);
   return retime(replan,{...options,providers:['chatgpt']});
  };
 }
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.equal(trace[0],'optimize');assert.ok(trace.includes('retime'));
 assert.ok(panel.summary.textContent.includes('输入或配置已变化'));
 const reason={backend:'后台保存稿',upstream:'前序人物状态',prompt:'提示词',
  excerpt:'剧情上下文',manifest:'参考素材',provider:'LLM 配置'}[change];
 assert.ok(panel.summary.textContent.includes(reason));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_numeric_duration_formatting_does_not_restart_but_actual_change_does(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 fields['.review-duration']=node('8');
 const panel=progressPanel();A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 fields['.review-duration'].value='8.0';trace.length=0;
 await A.runH3Composite(ov);assert.deepEqual(trace,['extract']);
 fields['.review-duration'].value='9';trace.length=0;failAt='';
 await A.runH3Composite(ov);assert.equal(trace[0],'optimize');
 assert.ok(panel.summary.textContent.includes('时长'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_old_display_only_history_is_not_treated_as_a_verified_checkpoint(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const panel=progressPanel();A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 const key=[...storage.keys()][0],record=JSON.parse(storage.get(key));delete record.checkpoint;
 storage.set(key,JSON.stringify(record));ov.dataset.h3Session='reopened';A.installH3Composite(ov,actions);
 assert.equal(ov._h3CompositeCheckpoint,undefined);
 assert.ok(panel.summary.textContent.includes('旧记录缺少可核验断点'));
 assert.ok(panel.summary.textContent.includes('可单独点击标红步骤'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_completed_checkpoint_is_not_resumed_on_reopening(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.equal(JSON.parse([...storage.values()][0]).checkpoint,undefined);
 ov.dataset.h3Session='reopened';A.installH3Composite(ov,actions);
 assert.equal(ov._h3CompositeCheckpoint,undefined);
 trace.length=0;await A.runH3Composite(ov);assert.equal(trace[0],'optimize');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_changed_input_displays_restart_reason_and_storage_failure_does_not_block(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 global.localStorage={getItem:()=>{throw Error('unavailable');},setItem:()=>{throw Error('quota');}};
 const panel=progressPanel();A.installH3Composite(ov,actions);
 failAt='state';await A.runH3Composite(ov);
 editor.value='edited';trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.equal(trace[0],'optimize');assert.equal(closed,1);
 assert.ok(panel.summary.textContent.includes('输入或配置已变化'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_retry_starts_new_retime_after_terminal_error(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 A.installH3Composite(ov,actions);states=['error'];await A.runH3Composite(ov);
 assert.ok(!trace.includes('video'));assert.equal(ov._sourceReplanPending,undefined);
 trace.length=0;states=['applied'];await A.runH3Composite(ov);
 assert.ok(trace.includes('retime'));assert.ok(!trace.includes('optimize'));
 assert.equal(trace.at(-1),'video');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_editing_after_failure_invalidates_resume_checkpoint(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 trace.length=0;failAt='';editor.value='manual edit';await A.runH3Composite(ov);
 assert.equal(trace[0],'optimize');assert.ok(trace.includes('retime'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_first_segment_can_inherit_initial_state(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 A.installH3Composite(ov,actions);
 const snapshot=actions.snapshot;
 actions.snapshot=async()=>({...await snapshot(),scene_has_predecessor:false});
 picker.value='inherit';await A.runH3Composite(ov);
 assert.deepEqual(trace,['optimize','save','save','retime','poll','sync','inherit','video']);
 assert.ok(line.textContent.includes('已提交'));
 A.refreshH3Composite(ov,await actions.snapshot());
 assert.equal(inherit.disabled,false);assert.equal(picker.value,'inherit');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_later_segment_with_missing_initial_state_identifies_scene_start(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 A.installH3Composite(ov,actions);
 const snapshot=actions.snapshot;
 actions.snapshot=async()=>({...await snapshot(),scene_initial_pending:true,
  scene_first_segment:{segment_uid:'first',part_id:'part_007',segment_index:7}});
 await A.runH3Composite(ov);assert.deepEqual(trace,[]);assert.ok(line.textContent.includes('part_007'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_first_segment_initializes_before_optimization_and_extracts_final_state_again(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let pending=true;
 const snapshot=actions.snapshot,register=actions.register;
 actions.snapshot=async()=>({...await snapshot(),scene_has_predecessor:false,scene_initial_pending:pending});
 actions.register=async mode=>{await register(mode);pending=false;};
 A.installH3Composite(ov,actions);A.refreshH3Composite(ov,await actions.snapshot());
 assert.ok(fields['.h3-composite-hint'].textContent.includes('首次处理先由 LLM 建立场景初态'));
 await A.runH3Composite(ov);
 assert.deepEqual(trace,['save','extract','optimize','save','save','retime','poll','sync','extract','video']);
 assert.equal(trace.filter(x=>x==='extract').length,2);
 assert.ok(line.textContent.includes('已提交'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_initialization_failure_stops_before_optimization_or_video(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const snapshot=actions.snapshot;
 actions.snapshot=async()=>({...await snapshot(),scene_has_predecessor:false,scene_initial_pending:true});
 A.installH3Composite(ov,actions);failAt='state';await A.runH3Composite(ov);
 assert.deepEqual(trace,['save','extract']);
 assert.ok(line.textContent.includes('建立场景初态'));assert.equal(content.inert,false);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_successful_extraction_must_publish_initial_state_before_optimization(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 const snapshot=actions.snapshot;
 actions.snapshot=async()=>({...await snapshot(),scene_has_predecessor:false,scene_initial_pending:true});
 A.installH3Composite(ov,actions);await A.runH3Composite(ov);
 assert.deepEqual(trace,['save','extract']);assert.ok(!trace.includes('optimize'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_retry_after_initialization_preserves_step_order_without_initializing_again(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let pending=true;
 const snapshot=actions.snapshot,register=actions.register;
 actions.snapshot=async()=>({...await snapshot(),scene_has_predecessor:false,scene_initial_pending:pending});
 actions.register=async mode=>{await register(mode);pending=false;};
 A.installH3Composite(ov,actions);failAt='optimize';await A.runH3Composite(ov);
 assert.deepEqual(trace,['save','extract','optimize']);assert.equal(ov._h3CompositeCheckpoint.next,1);
 trace.length=0;failAt='';await A.runH3Composite(ov);
 assert.deepEqual(trace,['optimize','save','save','retime','poll','sync','extract','video']);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_duplicate_click_and_session_switch_cannot_submit(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let release,entered;
 actions.optimize=async()=>{trace.push('optimize');entered();await new Promise(r=>release=r);};
 A.installH3Composite(ov,actions);
 const started=new Promise(r=>entered=r),pending=A.runH3Composite(ov);await started;
 await A.runH3Composite(ov);assert.deepEqual(trace,['optimize']);
 assert.equal(content.inert,true);
 ov.dataset.h3Session='other';release();await pending;
 assert.deepEqual(trace,['optimize']);assert.equal(content.inert,false);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_closing_releases_lock_without_waiting_for_model(tmp_path):
    run_node(tmp_path, r'''
(async()=>{
 let release,entered;
 actions.optimize=async()=>{entered();await new Promise(r=>release=r);};
 A.installH3Composite(ov,actions);
 const started=new Promise(r=>entered=r),pending=A.runH3Composite(ov);await started;
 assert.equal(content.inert,true);A.cancelH3Composite(ov);
 assert.equal(content.inert,false);assert.equal(ov._h3CompositeRun,undefined);
 release();await pending;assert.ok(!trace.includes('video'));
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_prepared_video_submission_reuses_handler_and_propagates_failure(tmp_path):
    from ui.scripts import APP_JS
    start = APP_JS.index('window.AICF.regenerateH3Segment = async function')
    end = APP_JS.index('window.__AICF_READY__', start)
    run_node(tmp_path, r'''
const _segmentCardForIndex=()=>null,_h3ConfigRoot=()=>ov,_h3Status=()=>{},_validationMessage=e=>e.join(';');
let submitted=0,saves=0,invalid=false,switchAfterChannel=false;
A.saveH3GenerationConfig=async()=>{saves++;};
A.chooseVideoChannel=async()=>{if(switchAfterChannel)A.promptRuntimeRevision++;return {id:'runninghub',uses_workflow:true};};
A.replaceSegmentCardFromResponse=()=>null;
const _requestJson=async(url,options)=>{
 assert.equal(options.headers['X-AICF-Runtime'],'test');
 if(url.endsWith('/validate'))return {errors:invalid?['invalid']:[]};
 assert.ok(url.endsWith('/regenerate_video'));submitted++;return {ok:true};
};
''' + APP_JS[start:end] + r'''
(async()=>{
 const options={prepared:true,throwOnFailure:true,runtime:'test',guard:()=>assert.equal(A.promptRuntimeRevision,1)};
 assert.deepEqual(await A.regenerateH3Segment(2,button,options),{ok:true});
 assert.equal(saves,0);assert.equal(submitted,1);
 invalid=true;await assert.rejects(A.regenerateH3Segment(2,button,options),/invalid/);
 assert.equal(submitted,1);assert.equal(A._h3VideoRegenPending['2'],undefined);
 invalid=false;switchAfterChannel=true;
 await assert.rejects(A.regenerateH3Segment(2,button,options));assert.equal(submitted,1);
})().catch(e=>{console.error(e);process.exitCode=1});
''')
