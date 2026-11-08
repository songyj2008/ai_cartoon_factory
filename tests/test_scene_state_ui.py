import subprocess

from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS
from ui.scripts import APP_JS


def test_state_progress_is_independent_and_ignores_old_editor_sessions(tmp_path):
    script = tmp_path / 'scene_state.cjs'
    script.write_text('''
const assert = require('node:assert/strict');
const A={mountShotEditor(){}};
global.window={AICF:A};
global.setTimeout=callback=>setImmediate(callback);
let calls=[],mode='success',hold,release;
const stages=[{state:'queued'},{state:'running',web_task:{provider:'doubao',status:'waiting'}},
 {state:'running',web_task:{provider:'doubao',status:'complete'}},{state:'applying'},{state:'applied'}];
const nodes={};
const root={_editorUid:'target',_editorRuntime:'test',dataset:{h3Session:'one'},
 querySelector(s){return nodes[s]||(nodes[s]={textContent:'',hidden:true,setAttribute(){}});},
 querySelectorAll(){return [];}};
global.fetch=async (url,options)=>{
 const action=url.split('/').at(-1);calls.push(action);
 if(action==='state-result'){
  if(mode==='hold'){hold();await new Promise(resolve=>release=resolve);}
  if(mode==='gone')return {ok:false,json:async()=>({ok:false,code:'OPERATION_NOT_FOUND',message:'task cleared'})};
  if(mode==='network')throw Error('connection lost');
  if(mode==='stages')return {ok:true,json:async()=>({ok:true,...stages.shift()})};
  if(['cancelled','needs_attention'].includes(mode))return {ok:true,json:async()=>({ok:true,state:mode,message:'stopped'})};
  return {ok:true,json:async()=>({ok:true,state:mode==='error'?'error':'applied',message:'source changed'})};
 }
 assert.equal(action,'snapshot');
 return {ok:true,json:async()=>({ok:true,base_revision:'saved',scene_contexts:{},
  state_context:{input_state:{actor:{posture:{value:'坐着'}}}}})};
};
''' + EDITOR_DRAFT_JS + '''
(async()=>{
 const first=A.monitorH3Scene(root,'state1');
 assert.equal(A.monitorH3Scene(root,'state1'),first);
 assert.equal(root.querySelector('.h3-continuity-start').disabled,true);
 await first;
 assert.deepEqual(calls,['state-result','snapshot']);
 assert.equal(root._editorSaved.base_revision,'saved');
 assert.equal(root.querySelector('.h3-continuity-start').disabled,false);
 assert.ok(root.querySelector('.h3-state-summary').textContent.includes('已保存'));
 mode='error';calls=[];
 await assert.rejects(A.monitorH3Scene(root,'failed'),/source changed/);
 assert.equal(root.querySelector('.h3-state-retry').hidden,false);
 assert.ok(root.querySelector('.h3-state-summary').textContent.includes('提取未完成'));
 assert.ok(root.querySelector('.h3-state-summary').textContent.includes('source changed'));
 await assert.rejects(A.monitorH3Scene(root,'failed'),/source changed/);
 assert.deepEqual(calls,['state-result']);
 for(const status of ['cancelled','needs_attention','gone','network']){
  mode=status;calls=[];
  await assert.rejects(A.monitorH3Scene(root,status));
  const text=root.querySelector('.h3-state-summary').textContent;
  assert.ok(!text.includes('正在提取'));
  if(status==='cancelled')assert.ok(text.includes('提取已取消'));
  if(status==='needs_attention')assert.ok(text.includes('提取需要处理'));
  if(status==='gone')assert.ok(text.includes('提取任务已不存在'));
  assert.equal(root._sceneStateBusy,false);
  assert.equal(root.querySelector('.h3-state-retry').hidden,false);
 }
 mode='stages';calls=[];
 const progress=[],summary=root.querySelector('.h3-state-summary');
 Object.defineProperty(summary,'textContent',{get(){return progress.at(-1)||'';},set(value){progress.push(value);}});
 await A.monitorH3Scene(root,'progress');
 assert.ok(progress.some(text=>text.includes('已排队')));
 assert.ok(progress.some(text=>text.includes('等待 豆包 回复')));
 assert.ok(progress.some(text=>text.includes('正在校验结果')));
 assert.ok(progress.some(text=>text.includes('正在保存本段状态')));
 assert.ok(progress.at(-1).includes('已保存'));
 mode='hold';calls=[];
 const entered=new Promise(resolve=>hold=resolve);
 const pending=A.monitorH3Scene(root,'old');await entered;
 root.dataset.h3Session='two';root._editorSaved={base_revision:'new editor'};summary.textContent='new session status';
 release();await pending;
 assert.deepEqual(calls,['state-result']);
 assert.equal(root._editorSaved.base_revision,'new editor');
 assert.equal(summary.textContent,'new session status');
})().catch(error=>{console.error(error);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_video_regenerate_saves_without_scheduling_state_analysis(tmp_path):
    start = APP_JS.index('window.AICF.regenerateH3Segment = async function')
    end = APP_JS.index('window.__AICF_READY__', start)
    script = tmp_path / 'video.cjs'
    script.write_text('''
const assert=require('node:assert/strict');
const root={classList:{contains:()=>false}},card={querySelector:()=>null};
const _segmentCardForIndex=()=>card,_h3ConfigRoot=()=>root;
let saves=0,submissions=0,clears=0;
const A={saveH3GenerationConfig:async(index,button,r,forceSave,refreshState)=>{
 assert.equal(index,5);assert.equal(r,root);assert.equal(refreshState,false);saves++;
},chooseVideoChannel:async()=>({id:'runninghub',uses_workflow:true}),
replaceSegmentCardFromResponse(){}};
global.window={AICF:A};
const _requestJson=async(url,options)=>{
 assert.ok(url==='/api/segment/validate'||url==='/api/segment/regenerate_video');
 if(url.endsWith('regenerate_video')){assert.equal(clears,0);submissions++;assert.equal(JSON.parse(options.body).channel_id,'runninghub');}
 return {};
};
const _h3Status=(root,message)=>{throw Error(message);};
''' + APP_JS[start:end] + '''
(async()=>{
 await A.regenerateH3Segment(5,{textContent:'重新生成视频'});
 assert.equal(saves,1);assert.equal(submissions,1);assert.equal(clears,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    script.write_text(APP_JS, encoding='utf-8')
    subprocess.run(['node', '--check', str(script)], check=True, capture_output=True)
