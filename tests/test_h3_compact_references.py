import copy
import re

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.editor_draft import validate_candidate
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
from generation.pipelines.minimax_h3.prompt_reconciler import H3PromptReconciler
from generation.pipelines.minimax_h3.prompt_retimer import _time_slots


def config():
    return H3GenerationConfig.from_mapping({'asset_manifest':[
        {'id':name,'type':'image','path':name+'.png','role':label,
         'metadata':{'asset_kind':'character','identity_id':name,'role_id':'person_'+name,
                     'subject_description':label+'，项目负责人。'}}
        for name,label in [('a','甲方'),('b','乙方')]]+[
        {'id':'room','type':'image','path':'room.png','role':'会议室',
         'metadata':{'asset_kind':'background'}},
        {'id':'vb','type':'audio','path':'b.wav','role':'不应重复的长用途说明',
         'metadata':{'voice_only':True,'identity_id':'b','role_id':'person_b'}},
        {'id':'va','type':'audio','path':'a.wav',
         'metadata':{'voice_only':True,'identity_id':'a','role_id':'person_a'}},
    ]})


def compiled(conf):
    segment={'shots':[
        {'start_sec':0,'end_sec':3,'visible_roles':['person_a','person_b'],
         'subject':'乙方','action':'乙方站着向甲方说明',
         'dialogue':{'speaker':'person_b','line':'甲方，请看编号3的文件。'}},
        {'start_sec':3,'end_sec':6,'visible_roles':['person_a'],
         'subject':'甲方','action':'甲方坐在桌后回应',
         'dialogue':{'speaker':'person_a','line':'我看过了。'}},
    ]}
    return H3PromptCompiler().compile(segment,conf,H3AssetBinder().bind(conf),duration_sec=6).text


def detail(text):
    return text.split('【详细镜头描述】',1)[1].split('【整体声音环境】',1)[0]


def test_retention_uses_same_camera_subject_and_scene_as_editor():
    from generation.pipelines.minimax_h3.shot_editor import inspect_prompt
    conf=config();assets=conf.to_dict()['asset_manifest']
    assets[2]['metadata']['scene_id']='room'
    conf=H3GenerationConfig.from_mapping({'asset_manifest':assets})
    text=('【详细镜头描述】\n'
          '[Shot 1]（0–2 秒）：场景使用 <Subject 3>；画面人物：<Subject 1>、<Subject 2>；两人对峙。\n'
          '[Shot 2] At 00:02.000（2–6 秒）：<Subject 1 > 正面近景，机位稳定。；'
          '<Subject 1>说：<d>[Chinese]原文。</d>；声音：<Subject 1 > 画内对白。')
    binding=H3AssetBinder().bind(conf)
    rows=H3PromptCompiler()._retention_lines_from_prompt(text,binding)
    assert '<Subject 1>（出现于 Shot 1、Shot 2）' in rows[0]
    assert '<Subject 2>（出现于 Shot 1）' in rows[1]
    assert '<Subject 3>（出现于 Shot 1、Shot 2）' in rows[2]
    assert inspect_prompt(text,'test',assets)['document']['shots'][1]['visible_roles']==['person_a']


def test_project_names_replace_library_names_on_compile_and_save(monkeypatch):
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_compiler.load_project_bible', lambda: {
        'core_characters': [
            {'id': 'person_a', 'display_name': '项目甲', 'asset_identity_id': 'a'},
            {'id': 'person_b', 'display_name': '项目乙', 'asset_identity_id': 'b'},
        ]})
    conf = config()
    prompt = compiled(conf)
    assert '<Subject 1>：项目甲；可见内容来自 <Picture 1>。' in prompt
    assert '<Subject 2>：项目乙；可见内容来自 <Picture 2>。' in prompt
    assert '项目负责人' not in prompt
    prompt = H3PromptReconciler().reconcile(prompt, conf, conf)
    old = prompt.replace('<Subject 1>：项目甲；', '<Subject 1>：旧名字；')
    saved = H3PromptReconciler().reconcile(old, conf, conf)
    assert saved == prompt
    assert H3PromptReconciler().reconcile(saved, conf, conf) == saved
    assert detail(saved) == detail(old)
    assert '<d>[Chinese]甲方，请看编号3的文件。</d>' in saved
    assert '<Audio 1> → <Subject 2>（S1）' in saved


def test_explicit_project_role_wins_shared_identity_and_identity_fallback_is_unique(monkeypatch):
    monkeypatch.setattr('generation.pipelines.minimax_h3.prompt_compiler.load_project_bible', lambda: {
        'core_characters': [
            {'id': 'person_a', 'display_name': '项目甲', 'asset_identity_id': 'a'},
            {'id': 'other_role', 'display_name': '同素材另一角色', 'asset_identity_id': 'a'},
            {'id': 'person_b', 'display_name': '项目乙', 'asset_identity_id': 'b'},
        ]})
    assets = config().to_dict()['asset_manifest']
    # A known role wins even if an old asset carries a conflicting identity.
    assets[0]['metadata']['identity_id'] = 'b'
    assets[1]['metadata'].pop('role_id')
    conf = H3GenerationConfig.from_mapping({'asset_manifest': assets})
    entries = H3PromptCompiler()._subject_reference_entries(H3AssetBinder().bind(conf))
    assert [entry['name'] for entry in entries[:2]] == ['项目甲', '项目乙']
    assets[0]['metadata'].pop('role_id')
    assets[0]['metadata']['identity_id'] = 'a'
    conf = H3GenerationConfig.from_mapping({'asset_manifest': assets})
    entries = H3PromptCompiler()._subject_reference_entries(H3AssetBinder().bind(conf))
    assert entries[0]['name'] == '甲方'  # Shared identity cannot select a project role.


def test_generated_saved_and_submitted_references_stay_compact():
    conf=config();bindings=H3AssetBinder().bind(conf);prompt=compiled(conf)
    assert '<Subject 1>：甲方；可见内容来自 <Picture 1>。' in prompt
    assert '<Subject 2>：乙方；可见内容来自 <Picture 2>。' in prompt
    assert '项目负责人' not in prompt
    assert '不应重复的长用途说明' not in prompt
    assert prompt.count('【人物音色绑定】')==1
    assert prompt.count('人物保持参考外观')==1
    assert prompt.count('不播放参考录音')==1
    definitions=prompt.split('【人物音色绑定】',1)[1].split('【详细镜头描述】',1)[0]
    assert '<Audio 1> → <Subject 2>（S1）' in definitions
    assert '<Audio 2> → <Subject 1>（S2）' in definitions
    base={'prompt':prompt,'asset_manifest':conf.to_dict()['asset_manifest'],'duration_sec':6}
    for _ in range(3):
        rebuilt=H3PromptReconciler().reconcile(prompt,conf,conf)
        validate_candidate(base,{**base,'prompt':rebuilt})
        assert H3PromptReconciler().reconcile(rebuilt,conf,conf)==rebuilt
        override=H3GenerationConfig.from_mapping({**conf.to_dict(),'prompt_override':rebuilt})
        submitted=H3PromptCompiler().compile({},override,bindings,duration_sec=6).text
        assert submitted==rebuilt and detail(submitted)==detail(rebuilt)
        prompt=submitted


def test_old_managed_text_is_compacted_without_changing_speech_or_timing():
    conf=config();bindings=H3AssetBinder().bind(conf);short=compiled(conf)
    long=short.replace('人物保持参考外观',('重复的人物一致性说明。'*40)+'人物保持参考外观')
    long+='\n\n【人物音色绑定】\n重复音色规则。\n【人物音色绑定结束】'
    manifest=conf.to_dict()['asset_manifest']
    base={'prompt':long,'asset_manifest':manifest,'duration_sec':6}
    compact=H3PromptCompiler.compact_reference_sections(long,bindings)
    assert compact==short and len(compact)<len(long)-300
    assert detail(compact)==detail(long)
    validate_candidate(base,{**base,'prompt':compact})
    assert [s['original'] for s in _time_slots(compact)]==[s['original'] for s in _time_slots(long)]


def test_reordering_and_removing_voice_refresh_definition_and_speaker_ids():
    old=config();prompt=compiled(old);assets=old.to_dict()['asset_manifest']
    new=H3GenerationConfig.from_mapping({'asset_manifest':[assets[1],assets[0],*assets[2:]]})
    reordered=H3PromptReconciler().reconcile(prompt,old,new)
    assert '<Audio 1> → <Subject 1>（S1）' in reordered
    assert '<Audio 2> → <Subject 2>（S2）' in reordered
    assert '<d>[Chinese]甲方，请看编号3的文件。</d>' in reordered
    # Removing an unused reference voice must not leave its definition behind.
    quiet='【详细镜头描述】\n[Shot 1]（0–6 秒）：画面人物：<Subject 1>；本镜无对白。'
    no_voices=H3GenerationConfig.from_mapping({'asset_manifest':assets[:3]})
    removed=H3PromptReconciler().reconcile(quiet,old,no_voices)
    assert '<Audio' not in removed and '不播放参考录音' not in removed


def test_frame_anchor_audio_reuse_and_video_soundtrack_keep_their_roles():
    assets=config().to_dict()['asset_manifest'][:2]+[
        {'id':'frame','type':'image','path':'frame.png','role':'首帧构图',
         'metadata':{'picture_role':'first_frame','shot_indices':[1]}},
        {'id':'motion','type':'video','path':'motion.mp4','role':'镜头运动参考',
         'use_video_audio':True,'metadata':{'audio_role':'室内环境声'}},
        {'id':'music','type':'audio','path':'music.mp3','role':'复用配乐',
         'metadata':{'retention_marker':'fully_copy'}},
        config().to_dict()['asset_manifest'][-2],
    ]
    conf=H3GenerationConfig.from_mapping({'asset_manifest':assets})
    prompt=compiled(conf)
    updated=H3PromptCompiler.compact_reference_sections(prompt,H3AssetBinder().bind(conf))
    assert '<Picture 3>（用于 Shot 1）：fully_preserved - 作为首帧锚点使用' in updated
    assert '<Audio 1>：reference - 室内环境声' in updated
    assert '<Audio 2>：fully_copy - 复用配乐' in updated
    assert '<Audio 3> → <Subject 2>（S1）' in updated
    assert H3PromptCompiler.compact_reference_sections(updated,H3AssetBinder().bind(conf))==updated


def test_character_appearance_metadata_is_not_injected_into_prompt():
    data=config().to_dict();before=copy.deepcopy(data)
    data['asset_manifest'][0]['metadata']['appearance_summary']='灰色短发、深色外套'
    conf=H3GenerationConfig.from_mapping(data);prompt=compiled(conf)
    assert '灰色短发、深色外套' not in prompt
    assert conf.asset_manifest[0].metadata['appearance_summary']=='灰色短发、深色外套'
    assert conf.asset_manifest[0].metadata['subject_description']==before['asset_manifest'][0]['metadata']['subject_description']
    assert len(re.findall(r'^【主体定义】',prompt,re.M))==1


def test_identity_summary_and_appearance_stay_in_metadata():
    data=config().to_dict()
    data['asset_manifest'][0]['metadata'].update({
        'identity_summary':'甲方，制造企业负责人',
        'subject_description':'不需要重复的完整人物背景。',
        'appearance_summary':'灰色短发、深色外套',
    })
    prompt=compiled(H3GenerationConfig.from_mapping(data))
    assert '<Subject 1>：甲方；可见内容来自 <Picture 1>。' in prompt
    assert '制造企业负责人' not in prompt
    assert '灰色短发、深色外套' not in prompt
    assert '不需要重复的完整人物背景' not in prompt


def test_legacy_identity_uses_only_opening_sentence_and_scene_avoids_boilerplate():
    data=config().to_dict()
    data['asset_manifest'][0]['metadata']['subject_description']='甲方，制造企业老板。现有素材仅明确身份，未提供外貌信息。'
    data['asset_manifest'][2]['metadata']['subject_description']='会议室场景背景，为企业管理者办公使用的室内空间。'
    prompt=compiled(H3GenerationConfig.from_mapping(data))
    assert '制造企业老板。' not in prompt
    assert '现有素材' not in prompt and '未提供外貌' not in prompt
    assert '为企业管理者' not in prompt
    assert '保持空间、陈设与光线' in prompt


def test_character_dossier_is_preserved_but_not_reproduced():
    data=config().to_dict()
    data['asset_manifest'][0]['metadata'].update({
        'subject_description':'甲方，制造企业老板。约50岁，方脸，身材壮实。',
        'image_description':'短黑发夹灰白，穿深色西装、白衬衫。左眉有疤。',
    })
    conf=H3GenerationConfig.from_mapping(data)
    prompt=compiled(conf)
    for trait in ['制造企业老板','约50岁','方脸','身材壮实','短黑发夹灰白','深色西装、白衬衫','左眉有疤']:
        assert trait not in prompt
    assert conf.asset_manifest[0].metadata==data['asset_manifest'][0]['metadata']
    assert H3PromptCompiler.compact_reference_sections(prompt,H3AssetBinder().bind(conf))==prompt


def test_offscreen_character_keeps_picture_without_inventing_presence():
    data=config().to_dict()
    data['asset_manifest'].append({'id':'listener','type':'image','path':'listener.png','role':'旁听者',
        'metadata':{'asset_kind':'character','identity_id':'listener','subject_description':'旁听者，项目助理。'}})
    conf=H3GenerationConfig.from_mapping(data)
    prompt=compiled(conf)
    assert '<Subject 4>：旁听者；可见内容来自 <Picture 4>。' in prompt
    assert '项目助理' not in prompt
    assert '本段未安排入画；在场状态与站位以镜头描述为准' in prompt
    assert '仅作身份参考' not in prompt and '本段不出镜' not in prompt
    assert H3PromptCompiler.compact_reference_sections(prompt,H3AssetBinder().bind(conf))==prompt


def test_internal_visible_role_lines_render_as_cast_without_changing_dialogue():
    text=('【详细镜头描述】\n[Shot 1]（0–6 秒）：\n'
          'visible_roles: [<Subject 1>, <Subject 2>]\n'
          '<Subject 1>说：<d>[Chinese]原文。\nvisible_roles: [<Subject 3>]</d>\n'
          '人物调度：<Subject 2>站在门口。')
    normalized=H3PromptCompiler.normalize_visible_role_lines(text)
    assert '画面人物：<Subject 1>、<Subject 2>。' in normalized
    assert '<d>[Chinese]原文。\nvisible_roles: [<Subject 3>]</d>' in normalized
    assert normalized.endswith('人物调度：<Subject 2>站在门口。')
    assert H3PromptCompiler.normalize_visible_role_lines(normalized)==normalized
    conf=config();manifest=conf.to_dict()['asset_manifest']
    base={'prompt':text,'asset_manifest':manifest,'duration_sec':6}
    candidate=H3PromptCompiler.compact_reference_sections(text,H3AssetBinder().bind(conf))
    validate_candidate(base,{**base,'prompt':candidate})


def test_visible_role_formatting_preserves_empty_cast_and_unrecognized_values():
    normalize=H3PromptCompiler.normalize_visible_role_lines
    assert normalize('visible_roles: []')=='画面人物：无。'
    assert normalize('visible_roles: ["<Subject 2>", "<Subject 1>"]')=='画面人物：<Subject 2>、<Subject 1>。'
    assert normalize('visible_roles: [unknown]')=='visible_roles: [unknown]'


def test_voice_section_without_end_marker_is_deduplicated():
    conf=config();bindings=H3AssetBinder().bind(conf)
    original=compiled(conf)
    duplicate='【人物音色绑定】\n<Audio 1> → <Subject 1>：旧绑定。\n\n'+original
    result=H3PromptCompiler.compact_reference_sections(duplicate,bindings)
    assert result.count('【人物音色绑定】')==1
    assert '旧绑定' not in result
    assert detail(result)==detail(original)
    assert result==H3PromptCompiler.compact_reference_sections(result,bindings)
