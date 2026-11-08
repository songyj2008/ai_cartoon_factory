import subprocess

from ui.scripts import APP_JS


def test_segment_voice_survives_library_refresh_and_automatic_binding(tmp_path):
    from services.character_voice import attach_character_voices, refresh_character_voices

    directory = tmp_path / 'characters' / 'identities' / 'alice'
    directory.mkdir(parents=True)
    (directory / 'default.wav').write_bytes(b'library sample')
    (directory / 'asset_meta.json').write_text(
        '{"identity":{"voice_reference":{"file":"default.wav","duration_sec":3}}}',
        encoding='utf-8',
    )
    manifest = [
        {'id': 'pic', 'type': 'image', 'label': 'Alice', 'metadata': {'identity_id': 'alice'}},
        {'id': 'custom', 'type': 'audio', 'path': 'segment.wav', 'source': 'segment_upload',
         'metadata': {'identity_id': 'alice', 'binding_source': 'segment_character_voice', 'voice_only': True}},
    ]
    refreshed = refresh_character_voices(manifest, tmp_path)
    result = attach_character_voices(refreshed, {'dialogue_units': [{'speaker': 'Alice', 'line': 'Hello'}]}, tmp_path)
    audio = [item for item in result if item['type'] == 'audio']
    assert len(audio) == 1
    assert audio[0]['path'] == 'segment.wav'
    assert audio[0]['metadata']['identity_id'] == 'alice'


def test_character_audio_replacement_preserves_images_and_other_voices(tmp_path):
    start = APP_JS.index('    window.AICF.h3BindCharacterAudio = function')
    end = APP_JS.index('    function _h3CharacterVoiceControls', start)
    script = tmp_path / 'binding.cjs'
    script.write_text('''
const assert = require('node:assert/strict');
global.window = {AICF:{}};
''' + APP_JS[start:end] + '''
const images = [
 {id:'a',type:'image',path:'a.png',label:'Alice',metadata:{identity_id:'alice',voice_disabled:true}},
 {id:'b',type:'image',path:'b.png',metadata:{identity_id:'bob'}}];
const old = {id:'old',type:'audio',path:'default.wav',metadata:{identity_id:'alice',binding_source:'character_voice'}};
const bob = {id:'bob',type:'audio',path:'bob.wav',metadata:{identity_id:'bob'}};
const input = [...images,old,bob];
const result = window.AICF.h3BindCharacterAudio(input,'a',{id:'new',type:'audio',path:'custom.wav'});
assert.equal(input[0].metadata.voice_disabled,true);
assert.equal(result[0].metadata.voice_disabled,false);
assert.deepEqual(result.filter(x=>x.type==='image').map(x=>x.path),['a.png','b.png']);
assert.equal(result.filter(x=>x.type==='audio').length,2);
assert.equal(result.at(-1).metadata.identity_id,'alice');
assert.equal(result.at(-1).metadata.binding_source,'segment_character_voice');
assert.equal(result.find(x=>x.id==='bob').path,'bob.wav');
assert.equal(result.some(x=>x.id==='old'),false);
const moved = window.AICF.h3BindCharacterAudio(input,'a',bob);
assert.equal(moved.filter(x=>x.type==='audio').length,1);
assert.equal(moved.at(-1).metadata.identity_id,'alice');
assert.throws(()=>window.AICF.h3BindCharacterAudio(input,'missing',bob),/身份/);
const full = [...images,...['x','y','z'].map(id=>({id,type:'audio',metadata:{identity_id:id}}))];
assert.throws(()=>window.AICF.h3BindCharacterAudio(full,'a',{id:'new',type:'audio'}),/最多/);
''', encoding='utf-8')
    subprocess.run(['node', str(script)], check=True, capture_output=True)


def test_asset_rows_show_bound_audio_inside_character_and_no_duplicate_images(tmp_path):
    start = APP_JS.index('    window.AICF.renderH3AssetRows = function')
    end = APP_JS.index('    function _h3ImageUsageLabel', start)
    script = tmp_path / 'rows.cjs'
    script.write_text('''
const assert = require('node:assert/strict');
function el() {return {children:[],style:{},dataset:{},appendChild(x){this.children.push(x)},setAttribute(){},addEventListener(){},add(x){this.children.push(x)},replaceChildren(){this.children=[];}};}
global.window={AICF:{}};
global.document={createElement:el}; global.Option=function(label,value){return {label,value};};
const manifest=[{id:'pic',type:'image',label:'Alice',metadata:{identity_id:'alice'}},
 {id:'scene',type:'image',label:'Office'},
 {id:'voice',type:'audio',label:'Alice voice',path:'voice.wav',metadata:{identity_id:'alice',binding_source:'character_voice'}},
 {id:'narration',type:'audio',label:'Narrator',metadata:{voice_usage:'narration'}}];
const list=el(), host=el(); host.dataset.voiceIdentity='alice';
const source={dataset:{segmentIndex:'2'},querySelector(){return list}};
const root={dataset:{},querySelector(){return source},querySelectorAll(selector){return selector==='[data-voice-identity]'?[host]:[]}};
function _h3Manifest(){return manifest;}
''' + APP_JS[start:end] + '''
window.AICF.renderH3AssetRows(root);
assert.equal(host.children.length,1);
assert.equal(host.children[0].className,'h3-bound-audio');
assert.equal(host.children[0].children[0].textContent,'audio · Alice voice');
assert.equal(list.children.length,1);
assert.equal(list.children[0].children[0].children[0].textContent,'audio · Narrator');
''', encoding='utf-8')
    subprocess.run(['node', str(script)], check=True, capture_output=True)


def test_rebuilt_character_controls_show_bound_audio_without_changing_manifest(tmp_path):
    start = APP_JS.index('    function _h3CharacterVoiceControls')
    end = APP_JS.index('    window.AICF.renderH3SelectedPreview', start)
    script = tmp_path / 'selected-audio.cjs'
    script.write_text("""
const assert=require('node:assert/strict');
global.window={AICF:{archiveReadOnly:false}};
function el(tag){return {tag,children:[],dataset:{},value:'',setAttribute(){},
 appendChild(child){this.children.push(child)},add(option){this.children.push(option)}};}
global.document={createElement:el};
global.Option=function(text,value){return {text,value}};
""" + APP_JS[start:end] + """
const manifest=[{id:'alice',type:'image',metadata:{identity_id:'alice'}},
 {id:'bob',type:'image',metadata:{identity_id:'bob'}},
 {id:'voice',type:'audio',label:'Alice voice',metadata:{identity_id:'alice',voice_only:true}}];
const original=JSON.stringify(manifest);
for(let round=0;round<2;round++){
 const alice=el('div'),bob=el('div');
 _h3CharacterVoiceControls({},manifest[0],alice,manifest);
 _h3CharacterVoiceControls({},manifest[1],bob,manifest);
 assert.equal(alice.children[0].children.find(x=>x.tag==='select').value,'2');
 assert.equal(bob.children[0].children.find(x=>x.tag==='select').value,'');
 assert.equal(JSON.stringify(manifest),original);
}
""",encoding='utf-8')
    subprocess.run(['node',str(script)],check=True,capture_output=True)
