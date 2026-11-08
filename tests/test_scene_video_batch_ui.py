import subprocess
from ui.scene_video_batch_ui import SCENE_BATCH_JS


def test_all_rows_group_assignment_sources_and_submit_payload(tmp_path):
    script=tmp_path/'batch.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.className='';this._value=null;this.open=false;this.disabled=false;}
 appendChild(n){this.children.push(n);n.parent=this;return n;}
 replaceChildren(){this.children=[];}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
 setAttribute(k,v){if(k==='class')this.className=v;else this[k]=v;}
 set value(v){this._value=v;}get value(){return this._value??(this.tag==='select'?this.children[0]?.value??'':'');}
 set innerHTML(html){
  const stack=[this];for(const token of html.match(/<[^>]+>|[^<]+/g)||[]){
   if(token.startsWith('</')){stack.pop();continue;}if(!token.startsWith('<'))continue;
   const tag=token.match(/^<(\w+)/)?.[1];if(!tag)continue;const n=new Node(tag);
   for(const m of token.matchAll(/([\w-]+)="([^"]*)"/g))n.setAttribute(m[1],m[2]);
   stack.at(-1).appendChild(n);if(!['input','br','hr'].includes(tag))stack.push(n);
  }
 }
 all(){return this.children.flatMap(n=>[n,...n.all()]);}
 matches(s){return s.startsWith('.')?this.className.split(' ').includes(s.slice(1)):this.tag===s;}
 querySelectorAll(s){return this.all().filter(n=>s.split(',').some(part=>{const pieces=part.trim().split(' ');return n.matches(pieces.at(-1))&&(pieces.length===1||n.parent?.matches(pieces[0]));}));}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 showModal(){this.open=true;}close(){this.open=false;}focus(){}
}
const body=new Node('body');let intercept;global.document={body,createElement:t=>new Node(t),addEventListener:(e,f)=>{intercept=f;}};
let intervalCallback;global.setInterval=fn=>{intervalCallback=fn;};global.crypto={randomUUID:()=> 'new-key'};
const storage=new Map();global.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
let submitted,statusProgress={};const catalog={ok:true,runtime:'F:/project',revision:'r1',scenes:{room:{name:'scene_011',display_name:'办公室',initial_state:{}}},roles:{},
 rows:[1,2,3].map(i=>({segment_uid:String(i),part_id:'part_'+i,scene_context_id:'room',selected:true,status:'pending',duration_sec:4,
  prompt:i===3?'':'【详细镜头描述】\n[Shot 1] 人物起身后站在原椅旁；<Subject '+i+'>；<script>禁止执行</script>'}))};
global.fetch=async(url,options)=>{
 const payload=JSON.parse(options.body);let data;
 if(url.endsWith('/catalog'))data=catalog;
 else if(url.endsWith('/start')){submitted=payload;assert.equal(decodeURIComponent(options.headers['X-AICF-Runtime']),'F:/project');data={ok:true,id:'batch1'};}
 else if(url.endsWith('/status'))data={ok:true,id:'batch1',state:'waiting_children',phase:'准备',progress_rows:statusProgress,scenes:catalog.scenes};
 else throw Error(url);
 return {ok:true,json:async()=>data};
};
const A={chooseVideoChannel:async()=>({id:'runninghub',uses_workflow:true})};global.window={AICF:A};
''' + SCENE_BATCH_JS + r'''
(async()=>{
 await A.openSceneVideoBatch();const d=body.children[0],rows=d.querySelectorAll('tbody tr');
 assert.equal(rows.length,3);
 assert.equal(d.querySelector('.scene-batch-initials').querySelector('summary').textContent,'办公室 · 初态已建立');
 for(const select of [d.querySelector('.scene-batch-group'),...rows.map(r=>r.querySelector('.scene-batch-scene'))]){
  assert.equal(select.children.find(o=>o.value==='room').textContent,'办公室');
 }
 d.querySelector('.scene-batch-group').value='room';d.querySelector('.scene-batch-default').value='inherit';
 d.querySelector('.scene-batch-assign').onclick();
 assert.deepEqual(rows.map(r=>r.querySelector('.scene-batch-source').textContent),['场景初态','part_1','part_2']);
 assert.ok(rows.every(r=>r.querySelector('.scene-batch-mode').value==='inherit'));
 const valuesBefore=rows.map(r=>[r.querySelector('input').checked,r.querySelector('.scene-batch-scene').value,r.querySelector('.scene-batch-mode').value]);
 const picker=d.querySelector('.scene-batch-provider-options'),providers=picker.querySelectorAll('input');
 const chosen=()=>providers.filter(p=>p.checked).map(p=>p.value);
 const toggle=(value,checked=true)=>{const p=providers.find(p=>p.value===value);p.checked=checked;p.onchange();};
 assert.equal(picker.querySelectorAll('select').length,0);
 assert.deepEqual(providers.map(p=>p.value),['inherit','chatgpt','deepseek','doubao']);
 assert.deepEqual(chosen(),['inherit']);
 toggle('chatgpt');toggle('deepseek');toggle('doubao');
 assert.deepEqual(chosen(),['chatgpt','deepseek','doubao']);
 const timingApi=d.querySelector('.scene-batch-timing-api');timingApi.checked=true;assert.deepEqual(chosen(),['chatgpt','deepseek','doubao']);
 toggle('deepseek');assert.deepEqual(chosen(),['chatgpt','deepseek','doubao']);
 toggle('inherit');assert.deepEqual(chosen(),['inherit']);
 toggle('inherit',false);
 await d.querySelector('.scene-batch-start').onclick();
 assert.equal(submitted,undefined,'An empty provider selection must not submit');
 assert.ok(d.querySelector('.scene-batch-message').textContent.includes('请至少勾选一个'));
 assert.equal(d.querySelector('.scene-batch-start').disabled,false);
 toggle('chatgpt');toggle('deepseek');toggle('doubao');
 const promptView=rows[0].querySelector('.scene-batch-prompt-view');
 assert.equal(promptView.querySelector('summary').textContent,'浏览提示词');
 promptView.open=true;
 assert.equal(promptView.querySelector('pre').textContent,catalog.rows[0].prompt);
 assert.equal(promptView.querySelector('pre').children.length,0,'Prompt markup must stay literal text');
 assert.equal(promptView.querySelector('pre').tabindex,'0');
 assert.ok(rows[2].querySelector('pre').textContent.includes('尚未保存提示词'));
 assert.deepEqual(rows.map(r=>[r.querySelector('input').checked,r.querySelector('.scene-batch-scene').value,r.querySelector('.scene-batch-mode').value]),valuesBefore);
 assert.equal(submitted,undefined,'Browsing never starts a generation');
 rows[1].querySelector('input').checked=false;
 rows[1].querySelector('.scene-batch-scene').value='__pending__';
 rows[1].querySelector('input').onchange();
 assert.ok(d.querySelector('.scene-batch-summary').textContent.includes('仅处理勾选的 2 段'));
 assert.ok(d.querySelector('.scene-batch-summary').textContent.includes('未勾选段完全跳过'));
 assert.ok(!d.querySelector('.scene-batch-summary').textContent.includes('另需准备'));
 assert.equal(rows[1].querySelector('.scene-batch-progress').textContent,'未勾选，已跳过');
 assert.equal(rows[1].querySelector('.scene-batch-scene').disabled,true);
 assert.equal(rows[1].querySelector('.scene-batch-mode').disabled,true);
 assert.equal(rows[2].querySelector('.scene-batch-source').textContent,'part_2');
 statusProgress={'1':{phase:'提交视频 已完成',started_at:1704067200,finished_at:1704067265,elapsed_seconds:65,timing_state:'complete'},
                 '2':{phase:'未勾选，已跳过',timing_state:'skipped'},
                 '3':{phase:'优化提示词 执行中',started_at:1704067300,elapsed_seconds:3602,timing_state:'running'}};
 await d.querySelector('.scene-batch-start').onclick();
 assert.equal(rows[0].querySelector('.scene-batch-started').textContent,'开始：2024-01-01 08:00:00');
 assert.equal(rows[0].querySelector('.scene-batch-finished').textContent,'结束：2024-01-01 08:01:05');
 assert.equal(rows[0].querySelector('.scene-batch-elapsed').textContent,'累计执行：1 分 5 秒');
 assert.equal(rows[2].querySelector('.scene-batch-finished').textContent,'结束：处理中');
 assert.equal(rows[2].querySelector('.scene-batch-elapsed').textContent,'累计执行：1 小时 0 分 2 秒');
 assert.equal(rows[1].querySelector('.scene-batch-started').textContent,'开始：—');
 assert.equal(rows[1].querySelector('.scene-batch-elapsed').textContent,'累计执行：—');
 statusProgress['3'].elapsed_seconds=3607;
 intervalCallback();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(rows[2].querySelector('.scene-batch-elapsed').textContent,'累计执行：1 小时 0 分 7 秒');
 assert.equal(rows[0].querySelector('.scene-batch-elapsed').textContent,'累计执行：1 分 5 秒');
 assert.equal(submitted.rows.length,3);assert.equal(submitted.rows[1].selected,false);
 assert.equal(submitted.rows[1].scene,'room','Unchecked grouping edits must not be applied');
 assert.deepEqual(submitted.providers,['chatgpt','deepseek','doubao']);assert.equal(submitted.backend,'runninghub');
 assert.equal(submitted.timing_api,true);
 assert.equal(d.querySelector('.scene-batch-start').disabled,true);
 assert.equal(d.querySelector('.scene-batch-initials').querySelector('summary').textContent,'办公室 · 初态已建立');
 assert.equal(storage.get('scene-video-batch:F:/project'),'batch1');
 let prevented=false;intercept({target:{closest:()=>true},preventDefault:()=>{prevented=true;},stopImmediatePropagation(){}});
 assert.equal(prevented,true);
})().catch(e=>{console.error(e);process.exitCode=1;});
''',encoding='utf-8')
    result=subprocess.run(['node',str(script)],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
