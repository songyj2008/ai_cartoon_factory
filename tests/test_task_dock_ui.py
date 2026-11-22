import shutil
import subprocess

import pytest

from ui.text_jobs_ui import PANEL_JS


def test_task_dock_drag_keeps_click_and_position_separate(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the task center JavaScript test")
    drag = PANEL_JS.split("  function readTaskDockPosition()", 1)[1].split("  function initializeTaskDock()", 1)[0]
    script = tmp_path / "task-dock-drag.cjs"
    script.write_text(r'''
const assert=require('node:assert/strict');
const document={documentElement:{clientWidth:1000,clientHeight:800}};
const dockMargin=12,panelGap=10,dragThreshold=6,positionKey='dock-position';
let dockPosition=null,opened=0;
const stored=new Map();
const localStorage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)};
const root=()=>({hidden:true}),setTaskPanelOpen=()=>opened++;
const styles=new Map(),classes=new Set(),handlers=new Map();
const dock={offsetWidth:48,offsetHeight:48,dataset:{},
  style:{setProperty:(key,value)=>styles.set(key,value),removeProperty:key=>styles.delete(key)},
  getBoundingClientRect:()=>({left:parseFloat(styles.get('--ops-drag-left')??(document.documentElement.clientWidth-48)),
    top:document.documentElement.clientHeight-48-parseFloat(styles.get('--ops-drag-bottom')||'200px')}),
  classList:{add:(...names)=>names.forEach(name=>classes.add(name)),remove:name=>classes.delete(name),
    toggle:(name,on)=>on?classes.add(name):classes.delete(name)}};
const trigger={addEventListener:(type,handler)=>handlers.set(type,handler),setPointerCapture(){}};
function fire(type,fields={}){
  let prevented=false;
  handlers.get(type)({type,pointerId:1,button:0,isPrimary:true,clientX:780,clientY:560,detail:1,
    preventDefault(){prevented=true;},...fields});
  return prevented;
}
''' + "function readTaskDockPosition()" + drag + r'''
bindTaskDockDrag(dock,trigger);
fire('pointerdown');
fire('pointermove',{clientY:555});
fire('pointerup');
assert.equal(fire('click'),false);
assert.equal(opened,1);
assert.equal(stored.has(positionKey),false);

fire('pointerdown');
fire('pointermove',{clientX:304,clientY:360});
assert.deepEqual(dock.getBoundingClientRect(),{left:476,top:352});
assert.equal(dock.dataset.dockEdge,'free');
assert.equal(dock.dataset.panelDirection,'down');
fire('pointerup');
assert.equal(fire('click'),true);
assert.equal(opened,1);
assert.equal(readTaskDockPosition().x,.5);

document.documentElement.clientWidth=600;
restoreTaskDock(dock);
assert.deepEqual(dock.getBoundingClientRect(),{left:276,top:352});
assert.ok(parseFloat(styles.get('--ops-panel-anchor'))>352);
assert.ok(parseFloat(styles.get('--ops-panel-left'))>=12);

fire('pointerdown');
fire('pointermove',{clientX:0,clientY:-30});
assert.equal(classes.has('is-dragging'),true);
assert.deepEqual(dock.getBoundingClientRect(),{left:0,top:12});
assert.equal(dock.dataset.panelDirection,'down');
assert.equal(dock.dataset.previewSide,'right');
fire('pointerup');
assert.equal(fire('click'),true);
assert.equal(opened,1);
assert.deepEqual(readTaskDockPosition(),{x:0,y:0});
assert.equal(classes.has('is-dragging'),false);

document.documentElement.clientHeight=420;
restoreTaskDock(dock);
assert.equal(dock.getBoundingClientRect().top,12);
assert.equal(fire('keydown',{altKey:true,key:'ArrowDown'}),true);
assert.equal(dock.getBoundingClientRect().top,36);
assert.ok(readTaskDockPosition().y>0);
assert.equal(fire('keydown',{altKey:true,key:'ArrowRight'}),true);
assert.equal(dock.getBoundingClientRect().left,24);

fire('pointerdown',{clientX:48,clientY:60});
fire('pointermove',{clientX:720,clientY:880});
assert.equal(dock.dataset.panelDirection,'up');
assert.equal(dock.getBoundingClientRect().left,552);
fire('pointercancel');
assert.deepEqual(dock.getBoundingClientRect(),{left:24,top:36});
fire('pointerdown');
fire('pointerup');
assert.equal(fire('click'),false);
assert.equal(opened,2);

fire('pointerdown',{pointerType:'touch'});
fire('pointerup',{pointerType:'touch'});
assert.equal(opened,3);
assert.equal(fire('click',{pointerType:'touch'}),true);
assert.equal(opened,3);
assert.equal(fire('click',{detail:0}),false);
assert.equal(opened,4);
stored.set(positionKey,'0.25');
assert.deepEqual(readTaskDockPosition(),{x:1,y:.25});
stored.set(positionKey,'{"x":3,"y":0}');
assert.equal(readTaskDockPosition(),null);
''', encoding="utf-8")
    subprocess.run([node, str(script)], check=True, timeout=15)


def test_task_dock_summary_and_progress(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the task center JavaScript test")
    summary = PANEL_JS.split("  const runningStates=", 1)[1].split("  function updateFilters(tasks)", 1)[0]
    script = tmp_path / "task-dock.cjs"
    script.write_text(r'''
const assert=require('node:assert/strict');
function element(){
  const nodes=new Map(),attributes=new Map(),classes=new Set();
  return {hidden:false,textContent:'',dataset:{},
    querySelector(selector){if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector);},
    setAttribute:(name,value)=>attributes.set(name,value),getAttribute:name=>attributes.get(name),
    classList:{contains:name=>classes.has(name),toggle(name,on){if(on)classes.add(name);else classes.delete(name);}}};
}
const panel=element(),dock=element(),root=()=>panel,taskDock=()=>dock;
const labels={queued:'排队中',running:'执行中'};
''' + "const runningStates=" + summary + r'''
const badge=dock.querySelector('.text-operations-badge'),pip=dock.querySelector('.text-operations-pip');
const title=panel.querySelector('.text-operations-current-title');
const phase=panel.querySelector('.text-operations-current-phase');
const bar=panel.querySelector('.text-operations-current-progress');
const trigger=dock.querySelector('#text-operations-launcher');
const video={state:'running',title:'网页视频',phase:'生成中',isVideo:true,progress:{percent:0}};
const queued={state:'queued',title:'等待提交',progress:{percent:0}};
updateSummary([video,queued,{state:'ready'},{state:'needs_attention'},{state:'waiting_children',batch_version:1}]);
assert.equal(badge.textContent,'2');
assert.equal(badge.hidden,false);
assert.equal(pip.hidden,false);
assert.equal(pip.classList.contains('has-error'),true);
assert.match(trigger.getAttribute('aria-label'),/1 执行 · 1 排队，1 待应用 · 1 需检查/);
assert.equal(title.textContent,'网页视频');
assert.equal(phase.textContent,'生成中');
assert.equal(bar.hidden,true);
updateSummary([queued]);
assert.equal(bar.hidden,true);
updateSummary([{state:'running',title:'编写分镜',progress:{percent:42,node:'规划中'}}]);
assert.equal(bar.hidden,false);
assert.equal(bar.value,42);
assert.equal(pip.hidden,true);
updateSummary([]);
assert.equal(badge.hidden,true);
assert.equal(bar.hidden,true);
assert.equal(panel.querySelector('.text-operations-current').hidden,true);
assert.match(trigger.getAttribute('aria-label'),/当前无运行任务/);
assert.deepEqual(['applied','needs_attention','ready','queued','running'].map(state=>taskPriority({state})),[4,3,3,2,0]);
''', encoding="utf-8")
    subprocess.run([node, str(script)], check=True, timeout=15)
