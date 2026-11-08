"""Exercise the shared-scene calibration form through its real editor script."""
import subprocess

from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS


def test_shared_initial_calibration_preserves_all_roles_and_distinguishes_live_states(tmp_path):
    script = tmp_path / 'shared_calibration.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');
const clone=value=>JSON.parse(JSON.stringify(value));
global.setTimeout=callback=>setImmediate(callback);
function element(tag='div'){
 const item={tag,children:[],dataset:{},value:'',textContent:'',className:'',hidden:false,disabled:false,listeners:{},
  appendChild(child){this.children.push(child);child.parentElement=this;return child;},
  append(...children){children.forEach(child=>this.appendChild(child));},
  replaceChildren(...children){this.children=[];this.append(...children);},
  before(){},remove(){},setAttribute(){},focus(){this.focused=true;},
  addEventListener(event,listener){this.listeners[event]=listener;},removeEventListener(){},
  emit(event){this.listeners[event]?.({target:this});},
  querySelector(selector){return this.children.find(child=>child.className===selector.slice(1))||null;}};
 return item;
}
let activePanel;
global.document={createTextNode:text=>({textContent:text}),createElement(tag){
 const item=element(tag);
 if(tag==='section'){
  const fields={};
  item.querySelector=selector=>fields[selector]||(fields[selector]=element());
  item.querySelectorAll=selector=>selector.startsWith('.h3-scene-role')?
   item.querySelector('.h3-scene-roles').children.map(row=>row.querySelector('.h3-scene-role'))
    .filter(box=>!selector.includes(':checked')||box.checked):[];
  activePanel=item;
 }
 return item;
}};
const storage=new Map();global.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
const A={mountShotEditor(){},installH3Composite(){},refreshH3Composite(){},
 segmentCardFromButton:button=>({dataset:{segmentUid:button.uid,runtime:'test'}}),
 readH3Manifest:ov=>JSON.parse(ov.querySelector('.h3-asset-list').dataset.manifest),h3PromptDuration:()=>4};
global.window={AICF:A};
const role=(position,posture,present=true)=>({position:{value:position},posture:{value:posture},present:{value:present}});
const scenes={room:{name:'scene_011',display_name:'办公室',config_revision:'v1',initial_state:{
 a:role('桌后','坐着前倾'),b:role('甲的对面','站着'),c:role('访客侧','站着')}},
 other:{name:'走廊',config_revision:'hall-v1',initial_state:{x:role('门边','站着')}}};
scenes.room.initial_state.b.position.relative_to_role_id='a';
const assignments={part1:'room',part2:'room'};
let status={mode:'extract',ready:true,message:'已保存'},requests=[];
function snapshot(uid){
 const key=assignments[uid],scene=scenes[key],initial=scene.initial_state;
 return {draft:{prompt:'原稿',duration_sec:4,source_excerpt:'剧情',asset_manifest:[],scene_context_id:key},
  context_id:key,base_revision:'saved-content',scene_contexts:clone(scenes),
  scene_members:Object.fromEntries(Object.keys(scenes).map(key=>[key,Object.entries(assignments)
   .filter(([uid,scene])=>scene===key).map(([uid])=>({segment_uid:uid,part_id:uid}))])),
  scene_initial_state:clone(initial),scene_initial_revision:scene.config_revision,
  scene_member_parts:key==='room'?[{segment_uid:'part1',part_id:'Part 1'},{segment_uid:'part2',part_id:'Part 2'}]:[{segment_uid:uid,part_id:uid}],
  scene_roles:key==='room'?[{id:'a',name:'甲'},{id:'b',name:'乙'},{id:'c',name:'丙'}]:[{id:'x',name:'丁'}],
  state_status:clone(status),state_input_context:{input_state:{a:role('桌后','侧坐')}},
  state_context:{input_state:{a:role('桌后','侧坐')}},
  scene_record:{record_kind:status.mode==='inherit'?'inherit':'extracted',end_state:{a:role('桌前','站起')}}};
}
global.fetch=async(url,options)=>{
 const action=url.split('/').at(-1),payload=JSON.parse(options.body);requests.push({action,payload});
 if(action==='snapshot')return {ok:true,json:async()=>({ok:true,...snapshot(payload.segment_uid)})};
 if(action==='scene-state')return {ok:true,json:async()=>({ok:true,state_task:null})};
 if(action==='state-result')return {ok:true,json:async()=>({ok:true,state:'applied'})};
 if(action==='delete-scene'){
  assert.ok(!Object.values(assignments).includes(payload.context_id));
  assert.equal(payload.scene_initial_revision,scenes[payload.context_id].config_revision);
  delete scenes[payload.context_id];
  return {ok:true,json:async()=>({ok:true,deleted_context_id:payload.context_id})};
 }
 if(action==='scene-config'){
  const scene=scenes[payload.context_id];
  if(payload.initial_state&&payload.scene_initial_revision!==scene.config_revision)
   return {ok:false,json:async()=>({ok:false,code:'SCENE_INITIAL_CONFLICT',message:'场景初态已被其他页面修改，请重新读取后校准'})};
  if(payload.initial_state){scene.initial_state=clone(payload.initial_state);scene.config_revision='saved-v2';
   status={mode:'unset',ready:false,message:'场景初态已变化',detail:'场景初态已变化，请先重新提取 Part 1 的状态'};}
  assignments[payload.segment_uid]=payload.context_id;
  return {ok:true,json:async()=>({ok:true,context_id:payload.context_id})};
 }
 throw Error('Unexpected request '+action);
};
''' + EDITOR_DRAFT_JS + r'''
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function mount(uid){
 const nodes={},ov={dataset:{h3Session:uid},_sourceButton:{uid},
  _shotEditor:{documents:[{uid,shots:[{}]}],sourceUid:uid},
  querySelector(selector){if(selector==='.h3-continuity-card')return null;
   if(selector.startsWith('.h3-state')||selector.startsWith('.h3-continuity')||selector.startsWith('.h3-scene'))return activePanel.querySelector(selector);
   return nodes[selector]||(nodes[selector]=element());},
  querySelectorAll(selector){return selector.startsWith('.h3-scene-role')?activePanel.querySelectorAll(selector):[];}};
 ov.querySelector('.aicf-prompt-modal-body').value='原稿';ov.querySelector('#aicf-prompt-source-excerpt').value='剧情';
 ov.querySelector('.h3-asset-list').dataset.manifest='[]';
 A.mountShotEditor(ov,'');await tick();return ov;
}
function controls(id){const box=activePanel.querySelectorAll('.h3-scene-role').find(box=>box.value===id);assert.ok(box,'missing '+id);
 return {box,position:box.parentElement.querySelector('.state-position'),posture:box.parentElement.querySelector('.state-posture'),present:box.parentElement.querySelector('.state-present')};}
(async()=>{
 const first=await mount('part1');
 assert.deepEqual(activePanel.querySelectorAll('.h3-scene-role').map(box=>box.value),['a','b','c']);
 assert.equal(controls('c').box.checked,true,'The future visible actor stays in the shared initial state');
 assert.equal(controls('a').posture.value,'坐着前倾');
 assert.ok(controls('a').posture.children.some(option=>option.value==='坐着前倾'));
 const scope=activePanel.querySelector('.h3-scene-scope').textContent;
 assert.ok(scope.includes('Part 1')&&scope.includes('Part 2'));
 assert.ok(scope.includes('办公室')&&!scope.includes('scene_011'));
 assert.equal(activePanel.querySelector('.h3-state-select').children.find(o=>o.value==='room').textContent,'办公室');
 assert.equal(activePanel.querySelector('.h3-state-name').value,'办公室');
 const preview=activePanel.querySelector('.h3-scene-preview').textContent;
 assert.ok(preview.includes('场景初态（同场景所有 Part 共用）'));
 assert.ok(preview.split('本段开场状态')[0].includes('坐着前倾'));
 assert.ok(preview.split('本段开场状态')[1].split('本段结束状态')[0].includes('侧坐'));
 assert.ok(preview.split('本段结束状态（已保存）')[1].includes('站起'));
 assert.ok(preview.includes('相对 甲'));
 await activePanel.querySelector('.h3-state-calibrate').onclick();
 const saved=requests.filter(row=>row.action==='scene-config').at(-1).payload;
 assert.deepEqual(Object.keys(saved.initial_state),['a','b','c']);
 assert.equal(saved.scene_initial_revision,'v1');
 assert.equal(saved.initial_state.a.posture.value,'坐着前倾');
 assert.equal(saved.initial_state.b.position.relative_to_role_id,'a');
 assert.equal(first._sceneCalibrationDirty,false);
 const expired=activePanel.querySelector('.h3-scene-preview').textContent;
 assert.ok(expired.includes('本段结束状态（尚未取得有效记录）'));
 assert.ok(expired.includes('历史结束状态（待重新确认，不代表当前有效状态）'));
 assert.ok(!expired.includes('本段结束状态（已保存）'));
 assert.ok(activePanel.querySelector('.h3-continuity-status').textContent.includes('重新提取 Part 1'));
 // Every Part hydrates the same shared initial state and its future-role row.
 const second=await mount('part2');assert.deepEqual(activePanel.querySelectorAll('.h3-scene-role').map(box=>box.value),['a','b','c']);
 assert.equal(controls('a').posture.value,'坐着前倾');
 // Background snapshots must not replace a dirty calibration or upgrade its old revision.
 controls('c').position.value='手动站位';activePanel.querySelector('.h3-scene-roles').emit('input');
 scenes.room.config_revision='concurrent-v3';scenes.room.initial_state.c.position.value='其他页面站位';
 await A.refreshH3EditorBaseline(second);
 assert.equal(controls('c').position.value,'手动站位');assert.equal(second._sceneCalibrationDirty,true);
 // Completing an independent extraction refreshes state data without resetting unsaved calibration.
 await A.monitorH3Scene(second,'finished-state-task');
 assert.equal(controls('c').position.value,'手动站位');assert.equal(second._sceneCalibrationDirty,true);
 await activePanel.querySelector('.h3-state-calibrate').onclick();
 const conflicted=requests.filter(row=>row.action==='scene-config').at(-1).payload;
 assert.equal(conflicted.scene_initial_revision,'saved-v2');
 assert.equal(controls('c').position.value,'手动站位');assert.equal(second._sceneCalibrationDirty,true);
 assert.equal(scenes.room.initial_state.c.position.value,'其他页面站位');
 assert.ok(activePanel.querySelector('.h3-continuity-status').textContent.includes('其他页面修改'));
 // Conflict recovery uses the real reload action and preserves ordinary content edits.
 second.querySelector('.aicf-prompt-modal-body').value='人工编辑正文';
 second.querySelector('#aicf-prompt-source-excerpt').value='手写剧情';
 const ordinaryManifest=JSON.stringify([{id:'local-reference'}]);second.querySelector('.h3-asset-list').dataset.manifest=ordinaryManifest;
 const readsBefore=requests.length;
 await activePanel.querySelector('.h3-state-reload').onclick();
 assert.deepEqual(requests.slice(readsBefore).map(row=>row.action),['snapshot']);
 assert.equal(controls('c').position.value,'其他页面站位');assert.equal(second._sceneCalibrationDirty,false);
 assert.equal(second._editorSaved.scene_initial_revision,'concurrent-v3');
 assert.equal(second.querySelector('.aicf-prompt-modal-body').value,'人工编辑正文');
 assert.equal(second.querySelector('#aicf-prompt-source-excerpt').value,'手写剧情');
 assert.equal(second.querySelector('.h3-asset-list').dataset.manifest,ordinaryManifest);
 assert.ok(activePanel.querySelector('.h3-continuity-status').textContent.includes('已读取最新共享初态'));
 await activePanel.querySelector('.h3-state-calibrate').onclick();
 assert.equal(requests.filter(row=>row.action==='scene-config').at(-1).payload.scene_initial_revision,'concurrent-v3');
 assert.equal(second.querySelector('.aicf-prompt-modal-body').value,'人工编辑正文');
 // Scene changes replace the complete role list, not just its values.
 activePanel.querySelector('.h3-state-select').value='other';activePanel.querySelector('.h3-state-select').onchange();await tick();await tick();
 assert.deepEqual(activePanel.querySelectorAll('.h3-scene-role').map(box=>box.value),['x']);
 assert.equal(controls('x').position.value,'门边');
 assert.ok(activePanel.querySelector('.h3-scene-scope').textContent.includes('走廊'));
 // Missing explicit presence blocks saving before a request can be sent.
 controls('x').present.value='';const before=requests.filter(row=>row.action==='scene-config').length;
 await activePanel.querySelector('.h3-state-calibrate').onclick();
 assert.equal(requests.filter(row=>row.action==='scene-config').length,before);
 assert.equal(controls('x').present.focused,true);
 assert.ok(activePanel.querySelector('.h3-continuity-status').textContent.includes('丁在场或不在场'));
 for(const control of [controls('x').present]){
  assert.equal(control.required,true);
  assert.deepEqual(control.children.filter(option=>!option.disabled).map(option=>option.value),['true','false']);
 }
 // An old editor's callback cannot replace another session's calibration.
 second.dataset.h3Session='reopened';second._syncSceneCalibration({...snapshot('part2'),context_id:'room'},true);
 assert.deepEqual(activePanel.querySelectorAll('.h3-scene-role').map(box=>box.value),['x']);
 // An inherited ending resolves from the current input, not its old stored ending snapshot.
 status={mode:'inherit',ready:true,message:'沿用上段已保存'};
 const inherited=await mount('part1');
 const inheritedText=activePanel.querySelector('.h3-scene-preview').textContent;
 assert.ok(inheritedText.includes('本段结束状态（已保存，沿用上段）'));
 const ending=inheritedText.split('本段结束状态（已保存，沿用上段）')[1];
 assert.ok(ending.includes('侧坐'));assert.ok(!ending.includes('站起'));
 // A commit-style partial response must retain shared metadata and display names.
 A.syncH3EditorBaseline(inherited,{base_revision:'new-content',draft:clone(inherited._editorSaved.draft),
  state_context:{input_state:{a:role('桌后','靠坐')}},state_status:clone(status)});
 const partial=activePanel.querySelector('.h3-scene-preview').textContent;
 assert.ok(partial.includes('场景初态（同场景所有 Part 共用）'));
 assert.ok(partial.includes('丙：'));assert.ok(!partial.includes('c：'));
 assert.ok(partial.includes('靠坐'));
 // A scene's first segment displays the initial state as its unchanged ending.
 A.syncH3EditorBaseline(inherited,{state_input_context:{previous_segment_uid:null,
  input_state:clone(scenes.room.initial_state)},state_status:clone(status)});
 const firstText=activePanel.querySelector('.h3-scene-preview').textContent;
 assert.ok(firstText.includes('本段开场状态（承接场景初态）'));
 assert.ok(firstText.includes('本段结束状态（已保存，沿用场景初态）'));
 assert.ok(!requests.some(row=>['continuity','extract-state','commit'].includes(row.action)));
 // Manage duplicate scene names without changing the part's assignment.
 scenes.unused={name:scenes.room.name,display_name:scenes.room.display_name,config_revision:'empty-v1',initial_state:{}};
 A.syncH3EditorBaseline(inherited,snapshot('part1'));
 const rows=activePanel.querySelector('.h3-scene-list').children;
 const used=rows.find(row=>row.children[0].textContent.includes('part1'));
 const unused=rows.find(row=>row.children[0].textContent.includes('未使用'));
 assert.equal(used.children[1].disabled,true);assert.equal(unused.children[1].disabled,false);
 assert.ok(activePanel.querySelector('.h3-state-select').children.find(o=>o.value==='unused').textContent.includes('未使用'));
 const promptBefore=inherited.querySelector('.aicf-prompt-modal-body').value;
 const initialBefore=clone(scenes.room.initial_state),assignmentBefore=clone(assignments);
 global.confirm=()=>false;
 await unused.children[1].onclick();
 assert.ok(scenes.unused);assert.ok(!requests.some(row=>row.action==='delete-scene'));
 global.confirm=()=>true;
 await unused.children[1].onclick();
 assert.equal(scenes.unused,undefined);
 assert.deepEqual(assignments,assignmentBefore);assert.deepEqual(scenes.room.initial_state,initialBefore);
 assert.equal(inherited.querySelector('.aicf-prompt-modal-body').value,promptBefore);
 assert.ok(!activePanel.querySelector('.h3-state-select').children.some(o=>o.value==='unused'));
 assert.ok(activePanel.querySelector('.h3-continuity-status').textContent.includes('已删除未使用的场景'));
 assert.equal(inherited._h3SceneConfigBusy,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
