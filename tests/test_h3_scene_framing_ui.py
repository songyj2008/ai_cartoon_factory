"""The framing preview distinguishes scene presence, whole-shot visibility and provenance."""
import subprocess

from ui.h3_editor_draft_ui import EDITOR_DRAFT_JS


def test_framing_preview_replays_presence_and_renders_model_text_as_text(tmp_path):
    script = tmp_path / 'framing_preview.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');
function element(tag='div'){return {tag,children:[],textContent:'',className:'',
 setAttribute(){},appendChild(child){this.children.push(child);return child;},replaceChildren(...children){this.children=children;}};}
global.document={createElement:element};
global.window={AICF:{mountShotEditor(){}}};
''' + EDITOR_DRAFT_JS + r'''
const A=window.AICF,host=element(),prompt={value:'已保存提示词'};
const ov={_editorSaved:{draft:{prompt:'已保存提示词'},scene_roles:[{id:'a',name:'甲'},{id:'b',name:'乙'}]},
 querySelector(selector){return selector==='.h3-framing-results'?host:prompt;}};
const record={input_state:{a:{present:{value:true}},b:{present:{value:true}}},shots:[
 {shot_uid:'s1',role_coverage:[{role_id:'a',visibility:'visible',reason:'主体'},
  {role_id:'b',visibility:'out_of_frame',reason:'位于a后方画外'}],world_events:[]},
 {shot_uid:'s2',role_coverage:[{role_id:'a',visibility:'partial',reason:'前景肩部'},
  {role_id:'b',visibility:'visible',reason:'<img src=x onerror=alert(1)>'}],
  world_events:[{role_id:'b',facts:{present:{value:false}}}]},
 {shot_uid:'s3',role_coverage:[{role_id:'a',visibility:'visible',reason:'主体'},
  {role_id:'b',visibility:'not_present',reason:'已经离场'}],world_events:[]}]};
const original=JSON.stringify(record);
assert.deepEqual(A.h3FramingRows(record).map(row=>[row.before,row.after,row.visible]),[[2,2,1],[2,1,2],[1,1,1]]);
A.renderH3SceneFraming(ov,record,'saved',{ready:true,message:'当前保存稿已核对'});
assert.equal(JSON.stringify(record),original);
assert.ok(host.children[0].textContent.includes('已保存稿'));
assert.ok(host.children[1].children[0].textContent.includes('整镜曾入画 1 人（不是同帧人数）'));
assert.equal(host.children[1].children[1].children[3].textContent,'位于甲后方画外');
assert.equal(host.children[2].children[1].children[3].textContent,'<img src=x onerror=alert(1)>');
assert.equal(host.children[2].children[1].children[3].children.length,0);
prompt.value='人工改稿';A.refreshH3FramingDirty(ov);
assert.ok(host.children[0].textContent.includes('需重新核对'));
prompt.value='已保存提示词';A.refreshH3FramingDirty(ov);
assert.ok(host.children[0].textContent.includes('当前保存稿已核对'));
ov._editorCandidate={draft:{prompt:'候选提示词'}};
A.renderH3SceneFraming(ov,record,'candidate');assert.ok(host.children[0].textContent.startsWith('未应用候选'));
A.renderH3SceneFraming(ov,null,'saved');assert.ok(host.children[0].textContent.includes('尚无全员取景核对记录'));
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
