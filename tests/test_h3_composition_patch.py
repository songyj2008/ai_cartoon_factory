from spatial_test_support import planner_reply, model_reply
import copy
import pytest
from generation.pipelines.minimax_h3.continuity.composition import merge_composition_patch,validate_spatial_edit,plan_composition_patch
from generation.pipelines.minimax_h3.continuity.ledger import validate_record
from generation.pipelines.minimax_h3.continuity.reducer import reduce_plan
from generation.pipelines.minimax_h3.prompt_retimer import validate_timing_edit


def complete_coverage(patch, roles=('a',)):
    patch.update(conflicts=[], unresolved=[])
    for row in patch['shots']:
        row['role_coverage'] = [{'role_id': role,
            'visibility': 'visible' if role in row['visible_roles'] else 'out_of_frame',
            'reason': '主体位于取景内' if role in row['visible_roles'] else '摄影机朝主体所在侧，另一座位位于机后'} for role in roles]
    return patch


def example():
    base={'segment_uid':'seg','shot_uids':['s1'],'asset_manifest':[{'asset_id':'actor','type':'image','role':'甲','metadata':{'asset_kind':'character','role_id':'a'}}],
          'prompt':'【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：<Subject 1>；人物调度：桌前站着；镜头构图：全景；面无表情，纸张落地；声音：无对白，纸张轻响\n\n【非叙事性音乐】\n低沉音乐'}
    patch={'shots':[{'shot_uid':'s1','visible_roles':['a'],'edits':[{'source_quote':'全景','replacement':'近景，背景虚化'}],'world_events':[]}]}
    return base,complete_coverage(patch)


def test_spatial_patch_preserves_performance_sound_music_and_time():
    base,patch=example();before=copy.deepcopy(base)
    text=merge_composition_patch(base,patch)
    assert '镜头构图：近景，背景虚化' in text
    assert '面无表情，纸张落地；声音：无对白，纸张轻响' in text
    assert text.endswith('低沉音乐') and '0–4 秒' in text
    validate_spatial_edit(base,dict(base,prompt=text,composition_patch=patch))
    assert base==before


@pytest.mark.parametrize('field',['action','audio','dialogue','start_sec','non_diegetic_music'])
def test_patch_cannot_write_other_fields(field):
    base,patch=example();patch['shots'][0][field]='越权'
    with pytest.raises(ValueError,match='字段'):merge_composition_patch(base,patch)


def test_saved_candidate_cannot_change_expression():
    base,_=example()
    with pytest.raises(ValueError,match='受保护'):validate_spatial_edit(base,dict(base,prompt=base['prompt'].replace('面无表情','笑着')))


def test_legacy_mixed_camera_requests_composition_once(monkeypatch):
    base,patch=example();base['prompt']=base['prompt'].replace('镜头构图：','')
    calls=[]
    def model(system,user,**kw):calls.append(system);return patch,0
    monkeypatch.setattr('services.llm.call_llm',model_reply(model))
    result=plan_composition_patch(base,{'input_state':{},'tracked_role_ids':['a']})
    assert len(calls)==1 and '本次只确认状态' not in calls[0]
    from generation.pipelines.minimax_h3.rules import h3_visual_prompt_rules
    assert calls[0].count(h3_visual_prompt_rules()) == 1
    assert '每段 H3 总时长必须' not in calls[0]
    assert '近景，背景虚化' in merge_composition_patch(base,result)


def test_empty_record_rejected_but_per_shot_empty_events_valid():
    with pytest.raises(ValueError,match='完整Shot'):validate_record({'shots':[]})
    _,patch=example();validate_record(patch)


def test_posture_changes_then_persists_out_of_frame():
    plan={'shots':[{'shot_uid':'s1','world_events':[]},
                   {'shot_uid':'s2','world_events':[{'role_id':'a','facts':{'posture':{'value':'站着'}}}]},
                   {'shot_uid':'s3','world_events':[]}]}
    world={'a':{'posture':{'value':'坐着'},'present':{'value':True}}}
    result=reduce_plan(world,plan,'seg',['a'])
    assert result['a']['posture']['value']=='站着' and result['a']['present']['value'] is True
    assert world['a']['posture']['value']=='坐着'


def test_timing_edit_only_accepts_numeric_time_changes():
    base,_=example();before=base['prompt']
    validate_timing_edit(before,before.replace('4 秒','6 秒'))
    with pytest.raises(ValueError,match='非时间'):validate_timing_edit(before,before.replace('面无表情','笑着'))


def test_state_event_must_quote_original_shot_not_new_composition():
    base,patch=example()
    patch['shots'][0]['world_events']=[{'role_id':'a','facts':{'posture':{'value':'坐着'}},'source_quote':'不存在的起身动作'}]
    with pytest.raises(ValueError,match='原稿依据'):merge_composition_patch(base,patch)


@pytest.mark.parametrize('edit_camera', [False, True])
def test_final_state_evidence_recovers_source_whitespace_without_mutating_model(edit_camera):
    from generation.pipelines.minimax_h3.continuity.composition import bind_final_state_quotes
    base, patch = example()
    base['prompt'] = base['prompt'].replace('桌前站着', '<Subject 1>站在桌前')
    patch['shots'][0]['world_events'] = [{'role_id': 'a',
        'source_quote': '<Subject 1 > 站在桌前；镜头构图：全景',
        'facts': {'posture': {'value': '站着'}}}]
    if not edit_camera:
        patch['shots'][0]['edits'] = []
    before = copy.deepcopy(patch)
    text = merge_composition_patch(base, patch)
    rows = bind_final_state_quotes(base, patch, text)
    quote = rows[0]['world_events'][0]['source_quote']
    assert quote in text and '<Subject 1>站在桌前' in quote
    assert ('近景，背景虚化' in quote) == edit_camera
    assert patch == before


def test_final_state_evidence_cannot_recover_changed_words():
    from generation.pipelines.minimax_h3.continuity.composition import bind_final_state_quotes
    base, patch = example()
    patch['shots'][0]['world_events'] = [{'role_id': 'a', 'source_quote': '桌前坐着',
                                         'facts': {'posture': {'value': '坐着'}}}]
    with pytest.raises(ValueError, match='无法对应最终稿'):
        bind_final_state_quotes(base, patch, base['prompt'])


def test_plan_patch_uses_one_model_call_and_compact_contract(monkeypatch):
    base,patch=example();calls=[]
    def model(system,user,**kwargs):
        import json
        calls.append(json.loads(user));return patch,0
    monkeypatch.setattr('services.llm.call_llm',model_reply(model))
    assert plan_composition_patch(base,{'input_state':{},'tracked_role_ids':['a']})==patch
    assert len(calls)==1 and 'approved_prompt_experiences' not in calls[0]


def test_whole_segment_context_keeps_existing_patch_contract_and_protected_actions(monkeypatch):
    import json
    base, patch = example()
    base['shot_uids'] = ['s1', 's2']
    base['prompt'] = '【任务概述】\n从门边观察桌前，人物位置不变。\n\n' + base['prompt']
    base['prompt'] = base['prompt'].replace('0–4 秒', '0–2 秒').replace(
        '\n\n【非叙事性音乐】',
        '\n[Shot 2]（2–4 秒）：画面人物：<Subject 1>；镜头构图：近景；甲拾起纸张；声音：本镜无对白。\n\n【非叙事性音乐】')
    patch['shots'].append({'shot_uid': 's2', 'visible_roles': ['a'], 'edits': [], 'world_events': []})
    complete_coverage(patch)
    state = {'a': {'position': {'value': '桌前'}, 'posture': {'value': '站着'}, 'present': {'value': True}}}
    before = copy.deepcopy((base, state))
    calls = []
    def model(system, user, **kwargs):
        payload = json.loads(user)
        calls.append(payload)
        assert payload['editor_prompt'] == base['prompt']
        assert [shot['shot_uid'] for shot in payload['shots']] == ['s1', 's2']
        assert payload['input_state'] == state
        assert payload['spatial_constraints'] == '门在桌子左侧'
        assert payload['output_contract']['required_top_level_fields'] == ['shots', 'conflicts', 'unresolved']
        return copy.deepcopy(patch), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    result = plan_composition_patch(base, {'input_state': state, 'tracked_role_ids': ['a'], 'spatial_constraints': '门在桌子左侧'})
    assert result == patch and len(calls) == 1
    text = merge_composition_patch(base, result)
    assert '面无表情，纸张落地' in text and '甲拾起纸张' in text
    assert '0–2 秒' in text and '2–4 秒' in text
    assert (base, state) == before


@pytest.mark.parametrize('invalid_kind', ['facts_array', 'unexpected_initial_state', 'invalid_json', 'protected_edit'])
def test_invalid_model_output_is_retried_with_original_input_and_error(monkeypatch, invalid_kind):
    import json
    base, valid = example()
    original = copy.deepcopy(base)
    invalid = copy.deepcopy(valid)
    if invalid_kind == 'facts_array':
        invalid['shots'][0]['world_events'] = [{'role_id': 'a', 'source_quote': '桌前站着',
                                               'facts': [{'posture': {'value': '站着'}}]}]
    elif invalid_kind == 'unexpected_initial_state':
        invalid['initial_state'] = {}
    elif invalid_kind == 'protected_edit':
        invalid['shots'][0]['edits'] = [{'source_quote': '声音：无对白，纸张轻响', 'replacement': '静音'}]
    else:
        invalid = {'_parse_error': 'invalid JSON', '_raw_text': '{'}
    calls = []
    def model(system, user, **kwargs):
        calls.append((system, json.loads(user)))
        return copy.deepcopy(invalid if len(calls) == 1 else valid), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    assert plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']}) == valid
    assert len(calls) == 2 and base == original
    first, second = calls[0][1], calls[1][1]
    assert second['shots'] == first['shots']
    assert second['validation_feedback']['previous_response']['patch'] == invalid
    assert second['validation_feedback']['error']
    assert first['output_contract']['required_top_level_fields'] == ['shots', 'conflicts', 'unresolved']
    assert 'facts 是一个 JSON 对象' in calls[0][0]


def test_invalid_model_output_stops_after_one_retry(monkeypatch):
    base, patch = example()
    patch['initial_state'] = {}
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return patch, 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    with pytest.raises(ValueError, match='已自动重试一次.*initializing=false'):
        plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']})
    assert len(calls) == 2


def test_initializing_model_must_supply_complete_initial_state(monkeypatch):
    import json
    base, patch = example()
    calls = []
    valid = dict(copy.deepcopy(patch), initial_state={'a': {
        'position': {'value': None}, 'posture': {'value': '站着'}, 'present': {'value': True}}})
    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        return patch if len(calls) == 1 else valid, 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    assert plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a'], 'initializing': True}) == valid
    assert calls[0]['output_contract']['required_top_level_fields'] == ['shots', 'conflicts', 'unresolved', 'initial_state']
    assert 'initializing=true' in calls[1]['validation_feedback']['error']


def test_changed_state_record_is_not_replayed():
    from test_h3_scene_state_machine import jobs
    from generation.pipelines.minimax_h3.continuity.ledger import state_before
    data=jobs();data['segments'][0]['h3_continuity']['state_pending']=True
    with pytest.raises(ValueError,match='待确认'):state_before(data,'3')


def test_visible_role_uses_picture_subject_even_with_uploaded_voice():
    base,patch=example()
    base['asset_manifest'].append({'asset_id':'voice','type':'audio','role':'甲音色',
                                  'metadata':{'role_id':'a','voice_only':True,'binding_source':'segment_character_voice'}})
    text=merge_composition_patch(base,patch)
    assert '画面人物：<Subject 1>' in text and '画面人物：<Audio' not in text


@pytest.mark.parametrize('quote', ['无对白', '纸张轻响', '声音：无对白，纸张轻响'])
def test_explicit_sound_is_not_spatially_editable(quote):
    base,patch=example();patch['shots'][0]['edits']=[{'source_quote':quote,'replacement':'安静'}]
    with pytest.raises(ValueError,match='保护区'):merge_composition_patch(base,patch)


def test_dialogue_speaker_and_inner_time_are_protected():
    base,patch=example()
    base['prompt']=base['prompt'].replace('面无表情，纸张落地', '面无表情，纸张落地；在00:01.000时保持位置；<Subject 1> (S1)说：<d>[Chinese]请核对文件。</d>')
    for quote in ('请核对文件。','<Subject 1> (S1)说：','00:01.000'):
        patch['shots'][0]['edits']=[{'source_quote':quote,'replacement':'其他描述'}]
        with pytest.raises(ValueError,match='保护区'):merge_composition_patch(base,patch)


def test_exact_substring_edit_preserves_mixed_sentence_performance():
    base,patch=example();base['prompt']=base['prompt'].replace('人物调度：桌前站着；镜头构图：全景','面无表情地站在桌前，正面近景')
    patch['shots'][0]['edits']=[{'source_quote':'正面近景','replacement':'侧面近景，保持原站位，背景虚化'}]
    text=merge_composition_patch(base,patch)
    assert '面无表情地站在桌前，侧面近景，保持原站位，背景虚化' in text
    assert '正面近景' not in text
    validate_spatial_edit(base,dict(base,prompt=text,composition_patch=patch))


@pytest.mark.parametrize('edits',[
    [{'source_quote':'不存在的镜头','replacement':'近景'}],
    [{'source_quote':'全景','replacement':'近景'},{'source_quote':'全景','replacement':'特写'}],
    [{'insert_at':'shot_start','text':'近景'},{'insert_at':'shot_start','text':'特写'}],
    [{'source_quote':'全景','replacement':'声音：新增对白'}],
])
def test_invalid_spatial_edits_never_return_partially_modified_text(edits):
    base,patch=example();original=copy.deepcopy(base);patch['shots'][0]['edits']=edits
    with pytest.raises(ValueError,match='Shot 1'):merge_composition_patch(base,patch)
    assert base==original


def test_repeated_quote_requires_unambiguous_source():
    base,patch=example();base['prompt']=base['prompt'].replace('全景','全景，全景')
    with pytest.raises(ValueError,match='唯一匹配'):merge_composition_patch(base,patch)


def test_camera_quote_may_preserve_separator_before_protected_cast():
    base, patch = example()
    base['prompt'] = base['prompt'].replace('画面人物：', '越肩中近景镜头；画面人物：')
    patch['shots'][0]['edits'] = [{'source_quote': '越肩中近景镜头；',
                                  'replacement': '越肩中近景镜头，机位固定，浅景深；'}]
    result = merge_composition_patch(base, patch)
    assert '越肩中近景镜头，机位固定，浅景深；画面人物：<Subject 1>' in result
    validate_spatial_edit(base, dict(base, prompt=result, composition_patch=patch))


@pytest.mark.parametrize('replacement', ['越肩近景', '越肩近景，'])
def test_camera_patch_cannot_remove_or_change_cast_separator(replacement):
    base, patch = example()
    base['prompt'] = base['prompt'].replace('画面人物：', '越肩中近景镜头；画面人物：')
    patch['shots'][0]['edits'] = [{'source_quote': '越肩中近景镜头；', 'replacement': replacement}]
    with pytest.raises(ValueError, match='入画名单前的字段分隔符'):
        merge_composition_patch(base, patch)


def test_invalid_quote_feedback_identifies_actual_failed_reference():
    base, patch = example()
    patch['shots'][0]['edits'] = [{'source_quote': '<Subject 2>站在桌前', 'replacement': '桌前站着'}]
    with pytest.raises(ValueError, match='无效source_quote=.*Subject 2'):
        merge_composition_patch(base, patch)


def test_cast_label_and_value_remain_protected():
    base, patch = example()
    patch['shots'][0]['edits'] = [{'source_quote': '画面人物：<Subject 1>', 'replacement': '近景'}]
    with pytest.raises(ValueError, match='保护区.*画面人物'):
        merge_composition_patch(base, patch)


def test_model_selects_original_spans_without_retyping_or_crossing_cast(monkeypatch):
    import json
    base, _ = example()
    base['prompt'] = base['prompt'].replace('画面人物：', '中近景镜头；画面人物：')
    base['prompt'] = base['prompt'].replace('全景', '竖屏，<Subject 1>面部清晰，取景只覆盖桌后人物。')
    source = copy.deepcopy(base)
    def model(system, user, **kwargs):
        shot = json.loads(user)['shots'][0]
        spans = shot['editable_spans']
        assert all('画面人物' not in s['text'] and '声音：' not in s['text'] for s in spans)
        assert 'body' not in shot
        assert all(s['text'] in json.loads(user)['editor_prompt'] for s in spans)
        target = next(s for s in spans if s['text'].startswith('竖屏，'))
        return complete_coverage({'shots': [{'shot_uid': shot['shot_uid'], 'visible_roles': ['a'],
                          'edits': [{'span_id': target['span_id'], 'replacement': '竖屏，a面部清晰，固定近景'}],
                          'world_events': []}]}), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    patch = plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']})
    assert patch['shots'][0]['edits'][0]['source_quote'] == '竖屏，<Subject 1>面部清晰，取景只覆盖桌后人物。'
    result = merge_composition_patch(base, patch)
    assert '中近景镜头；画面人物：<Subject 1>；' in result
    assert '镜头构图：竖屏，<Subject 1>面部清晰，固定近景' in result
    assert '面无表情，纸张落地；声音：无对白，纸张轻响' in result
    assert base == source


def test_bad_quote_repair_uses_span_id_on_second_call(monkeypatch):
    import json
    base, _ = example()
    calls = []
    def model(system, user, **kwargs):
        payload = json.loads(user);calls.append(payload)
        shot = payload['shots'][0]
        span = next(s for s in shot['editable_spans'] if s['text'] == '全景')
        edit = ({'source_quote': '全景；拼接了不在原稿的内容', 'replacement': '近景'} if len(calls) == 1 else
                {'span_id': span['span_id'], 'replacement': '近景'})
        return complete_coverage({'shots': [{'shot_uid': shot['shot_uid'], 'visible_roles': ['a'], 'edits': [edit], 'world_events': []}]}), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    patch = plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']})
    assert patch['shots'][0]['edits'] == [{'source_quote': '全景', 'replacement': '近景'}]
    assert calls[0]['shots'] == calls[1]['shots']
    assert 'span_id' in calls[1]['validation_feedback']['instruction']


def test_unknown_span_is_rejected_without_applying_any_edits():
    from generation.pipelines.minimax_h3.continuity.composition import _shots, resolve_span_edits
    base, patch = example()
    patch['shots'][0]['edits'] = [{'span_id': 'unknown', 'replacement': '近景'}]
    before = copy.deepcopy((base, patch))
    with pytest.raises(ValueError, match='编号不存在'):
        resolve_span_edits(patch, _shots(base))
    assert (base, patch) == before


def test_editable_ids_never_address_protected_dialogue_or_audio():
    from generation.pipelines.minimax_h3.continuity.composition import _shots, editable_spans
    base, _ = example()
    base['prompt'] = base['prompt'].replace('声音：无对白，纸张轻响',
        '本句音色参考：<Audio 1>；<Subject 1>说：<d>[Chinese]一句台词</d>；声音：脚步声')
    spans = editable_spans(_shots(base)[0])
    assert spans
    for span in spans:
        assert not any(text in span['text'] for text in ('画面人物', '场景使用', '<d>', '台词', '音色', '声音', '脚步声'))


def test_legal_start_insertion_and_cast_addition_without_existing_fields():
    base,patch=example()
    base['prompt']=base['prompt'].replace('画面人物：<Subject 1>；人物调度：桌前站着；镜头构图：全景；','')
    patch['shots'][0]['edits']=[{'insert_at':'shot_start','text':'单人近景，背景虚化'}]
    text=merge_composition_patch(base,patch)
    assert text.count('镜头构图：')==1 and '画面人物：<Subject 1>' in text
    assert '面无表情，纸张落地；声音：无对白，纸张轻响' in text


def test_missing_edits_cannot_silently_fall_back_to_state_only():
    base,patch=example();patch['shots'][0].pop('edits')
    with pytest.raises(ValueError,match='字段'):merge_composition_patch(base,patch)


def test_noop_patch_preserves_exact_whitespace_and_reports_no_edits():
    base,patch=example();base['prompt']=base['prompt'].replace('画面人物：<Subject 1>','画面人物：  <Subject 1> ')
    patch['shots'][0]['edits']=[]
    assert merge_composition_patch(base,patch)==base['prompt']


@pytest.mark.parametrize('missing_anchor', [True, False])
def test_first_visible_anchor_examples_keep_framing_and_world_state(monkeypatch, missing_anchor):
    import json
    base, patch = example()
    base['prompt'] = base['prompt'].replace('全景', '单人近景')
    if missing_anchor:
        base['prompt'] = base['prompt'].replace('人物调度：桌前站着；', '')
        patch['shots'][0]['edits'] = [{'source_quote': '单人近景',
            'replacement': '单人近景，<Subject 1>站在门口立柱旁'}]
        position = '门口立柱旁'
    else:
        patch['shots'][0]['edits'] = []
        position = '桌前'
    world = {'a': {'present': {'value': True}, 'position': {'value': position},
                   'posture': {'value': '站着'}}}
    original = copy.deepcopy((base, patch, world))
    calls = []

    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        assert '人物在本段首次入镜时' in system
        assert '已有景别词不等于站位已交代' in system
        assert '已明确则不重复，未知不猜' in system
        assert '补写已有站位不产生world_events' in system
        return copy.deepcopy(patch), 0

    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    planned = plan_composition_patch(base, {'input_state': world, 'tracked_role_ids': ['a']})
    text = merge_composition_patch(base, planned)
    assert len(calls) == 1 and calls[0]['input_state'] == world
    assert text.count('单人近景') == 1
    assert '面无表情，纸张落地；声音：无对白，纸张轻响' in text
    assert ('<Subject 1>站在门口立柱旁' in text) is missing_anchor
    assert (text != base['prompt']) is missing_anchor
    assert planned['shots'][0]['visible_roles'] == ['a']
    assert reduce_plan(world, planned, 'seg', ['a']) == world
    assert (base, patch, world) == original


def test_present_offscreen_role_does_not_force_composition_change(monkeypatch):
    base,patch=example()
    base['asset_manifest'].append({'asset_id':'other','type':'image','role':'乙',
        'metadata':{'asset_kind':'character','role_id':'b'}})
    patch['shots'][0]['edits']=[]
    world={'a':{'present':{'value':True}},'b':{'present':{'value':True},'posture':{'value':'坐着'}}}
    complete_coverage(patch, ('a', 'b'))
    seen=[]
    def model(system,user,**kwargs):
        import json
        seen.append((system,json.loads(user)))
        return patch,0
    monkeypatch.setattr('services.llm.call_llm',model_reply(model))
    planned=plan_composition_patch(base,{'input_state':world,'tracked_role_ids':['a','b']})
    assert merge_composition_patch(base,planned)==base['prompt']
    assert planned['shots'][0]['visible_roles']==['a']
    assert reduce_plan(world,planned,'seg',['a','b'])['b']==world['b']
    assert len(seen)==1
    assert '在场不等于入画' in seen[0][0]
    assert '原稿普通构图可以调整' in seen[0][0]
    assert '移动镜头检查整个运动过程' in seen[0][0]
    assert '不能每镜重置为初态' in seen[0][0]
    assert '不能仅为登记人物状态扩大景别' in seen[0][0]


def test_segment_limits_cannot_be_selected_as_spatial_source():
    base,patch=example();base['prompt']=base['prompt'].replace('\n\n【非叙事性音乐】','\n限制：不新增台词\n\n【非叙事性音乐】')
    patch['shots'][0]['edits']=[{'source_quote':'限制：不新增台词','replacement':'近景'}]
    with pytest.raises(ValueError,match='本镜原文'):merge_composition_patch(base,patch)


@pytest.mark.parametrize('text', [
    '近景；画面人物：无', '近景；入画人物：<Subject 1>',
    '近景；场景使用 <Subject 1>', '近景，在00:03.000时切换为远景',
    '近景，3秒后切远景', '近景；说话人：其他人物', '近景；限制：新增要求',
])
@pytest.mark.parametrize('insert', [False, True])
def test_spatial_text_cannot_inject_owned_structures_or_timing(text,insert):
    base,patch=example()
    patch['shots'][0]['edits']=[{'insert_at':'shot_start','text':text} if insert
                               else {'source_quote':'全景','replacement':text}]
    with pytest.raises(ValueError,match='Shot 1.*不能注入'):merge_composition_patch(base,patch)


@pytest.mark.parametrize('scope', ['', '在画外', '在画外通过电话', '以画外旁白'])
def test_visibility_preserves_existing_onscreen_speaking(scope):
    base,patch=example()
    base['prompt']=base['prompt'].replace('声音：无对白，纸张轻响',
        f'<Subject 1> (S1){scope}说：<d>[Chinese]请核对文件。</d>')
    patch['shots'][0]['visible_roles']=[]
    if not scope:
        with pytest.raises(ValueError,match='画内说话人'):merge_composition_patch(base,patch)
    else:
        assert '画面人物：无' in merge_composition_patch(base,patch)


def test_speaker_enters_frame_during_utterance_must_remain_in_cast():
    from generation.pipelines.minimax_h3.speech_contract import render_delivery
    base,patch=example()
    unit={'line':'请核对文件。','scope':'offscreen','delivery_spans':[
        {'range':[0,3],'visibility':'offscreen'}, {'range':[3,6],'visibility':'onscreen'}]}
    base['prompt']=base['prompt'].replace('声音：无对白，纸张轻响',render_delivery(unit,'<Subject 1>'))
    patch['shots'][0]['visible_roles']=[]
    with pytest.raises(ValueError,match='画内说话人'):merge_composition_patch(base,patch)


def test_missing_shots_is_rejected_before_model_submission(monkeypatch):
    base,_=example();base['prompt']='【详细镜头描述】\n没有分镜'
    monkeypatch.setattr('services.llm.call_llm',model_reply(lambda *a,**k:pytest.fail('invalid source must not call model')))
    with pytest.raises(ValueError,match='缺少完整Shot'):plan_composition_patch(base,{'input_state':{},'tracked_role_ids':['a']})


def test_retry_keeps_state_action_when_model_swallowed_it_with_camera_field(monkeypatch):
    import json
    from generation.pipelines.minimax_h3.continuity.composition import bind_final_state_quotes, finalize_composition
    base, patch = example()
    base['prompt'] = base['prompt'].replace('面无表情，纸张落地', '<Subject 1>推开门，抬手示意')
    patch['shots'][0]['world_events'] = [{'role_id': 'a', 'source_quote': '推开门，抬手示意',
                                        'facts': {'position': {'value': '门口'}}}]
    invalid = copy.deepcopy(patch)
    invalid['shots'][0]['edits'] = [{'source_quote': '全景；<Subject 1>推开门，抬手示意',
                                    'replacement': '侧面近景；<Subject 1>打开门后举手招呼'}]
    calls = []
    def model(system, user, **kwargs):
        calls.append(json.loads(user))
        return copy.deepcopy(invalid if len(calls) == 1 else patch), 0
    monkeypatch.setattr('services.llm.call_llm', model_reply(model))
    planned = plan_composition_patch(base, {'input_state': {}, 'tracked_role_ids': ['a']})
    assert len(calls) == 2
    assert '跨越构图字段边界' in calls[1]['validation_feedback']['error']
    assert '保留原稿动作' in calls[1]['validation_feedback']['error']
    text, manifest = finalize_composition(base, planned)
    rows = bind_final_state_quotes(base, planned, text, asset_manifest=manifest)
    assert '<Subject 1>推开门，抬手示意' in text
    assert rows[0]['world_events'][0]['source_quote'] == '推开门，抬手示意'


@pytest.mark.parametrize('quote', ['全景；面无表情，纸张落地',
                                    '人物调度：桌前站着；镜头构图：全景'])
def test_spatial_patch_cannot_cross_explicit_field_boundaries(quote):
    base, patch = example()
    patch['shots'][0]['edits'] = [{'source_quote': quote, 'replacement': '侧面近景'}]
    with pytest.raises(ValueError, match='跨越构图字段边界'):
        merge_composition_patch(base, patch)

from spatial_test_support import plan_patch as plan_composition_patch
