import pytest

from generation.pipelines.minimax_h3.shot_editor import parse_prompt, render_prompt, timeline, duration_errors


PROMPT = '''【主体定义】
<Subject 1>：测试角色；来自 <Picture 1>。

【任务概述】
[reference generation + audio reference] 6 秒。原剧情。

【参考保留关系】
<Subject 1>：fully_preserved。

【详细镜头描述】
多 Shot；保持连续。
[Shot 1]（0.000–2.100 秒）：<Subject 1> (S1)说：<d>[Chinese]金额从3亿做到30亿。</d>；动作在 00:01.000 完成。
[Shot 2] At 00:02.100（2.100–3.500 秒）：<Subject 1> (S1)说：<d>[Chinese]第二句话。</d>；
[Shot 3] At 00:03.500（3.500–6.000 秒）：<Subject 1> (S1)说：<d>[Chinese]最后一句。</d>；

【整体声音环境】
安静。
'''


def test_ripple_and_protected_dialogue_and_event_time():
    doc = parse_prompt(PROMPT, 'segment_one')
    doc['shots'][0]['duration_cs'] = 280
    rows = timeline(doc)
    assert [(s['start_cs'], s['end_cs']) for s in rows] == [(0, 280), (280, 420), (420, 670)]
    text = render_prompt(doc)
    assert '（2.80–4.20 秒）' in text and 'At 00:04.20' in text
    assert '6.70 秒' in text and '00:01.33' in text
    assert '<d>[Chinese]金额从3亿做到30亿。</d>' in text
    assert '补齐 0.30' in duration_errors(doc)[0]


def test_delete_removes_dialogue_and_rebuilds_summary():
    doc = parse_prompt(PROMPT, 'one')
    doc['shots'].pop(1)
    doc['structure_changed'] = True
    text = render_prompt(doc)
    assert '第二句话' not in text and '[Shot 3]' not in text
    assert '[Shot 2] At 00:02.10（2.10–4.60 秒）' in text
    assert '原剧情' not in text


def test_bad_ranges_and_subcentisecond_collapse_are_not_repaired():
    with pytest.raises(ValueError, match='连续'):
        parse_prompt(PROMPT.replace('（2.100–3.500', '（2.200–3.500'), 'one')
    doc = parse_prompt(PROMPT, 'one')
    doc['shots'][0]['duration_cs'] = 0
    assert duration_errors(doc)


def test_no_accumulation_error():
    doc = parse_prompt(PROMPT, 'one')
    for shot in doc['shots']:
        shot['duration_cs'] = 110
    assert timeline(doc)[-1]['end_cs'] == 330


@pytest.mark.parametrize('field',[
    '画面人物：<Subject 1>、<Subject 2>；',
    '画面人物: 陈默、王振国；',
    '入画人物：陈默、王振国；',
    'visible_roles: ["a", "b"]；',
    'visible_roles: ["陈默", "王振国"]；',
])
def test_read_current_prompt_resolves_explicit_cast_in_supported_forms(field):
    manifest=[{'asset_id':key,'type':'image','path':key+'.png','role':name,
               'metadata':{'asset_kind':'character','role_id':key,'identity_id':key}}
              for key,name in [('a','陈默'),('b','王振国')]]
    text='【详细镜头描述】\n[Shot 1]（0–4 秒）：'+field+'中景，两人保持原站位。'
    doc=parse_prompt(text,'test',manifest)
    assert doc['shots'][0]['visible_roles']==['a','b']
    assert field in render_prompt(doc)


@pytest.mark.parametrize('field',['画面人物：无；','visible_roles: []；'])
def test_read_current_prompt_recognizes_explicit_empty_shot(field):
    doc=parse_prompt('【详细镜头描述】\n[Shot 1]（0–4 秒）：'+field+'办公室桌面特写。','test')
    assert doc['shots'][0]['visible_roles']==[]


def test_read_current_prompt_does_not_treat_mentions_or_dialogue_as_cast():
    text='【详细镜头描述】\n[Shot 1]（0–4 秒）：陈默在画外说：<d>[Chinese]画面人物：王振国；他还在吗？</d>'
    doc=parse_prompt(text,'test')
    assert 'visible_roles' not in doc['shots'][0]


def test_read_current_prompt_preserves_named_cast_without_a_reference():
    from generation.pipelines.minimax_h3.shot_editor import dialogue_units

    document = parse_prompt('【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：阿尔·阿明；中景。；'
                            '阿尔·阿明 (S1)说：<d>[Chinese]我到了。</d>；'
                            'Jean-Luc Roy (S2)在画外通过电话说：<d>[Chinese]收到。</d>', 'test')
    first = document['shots'][0]
    assert first['visible_roles'] == ['阿尔·阿明']
    assert [(unit['speaker'], unit['scope']) for unit in dialogue_units(first)] == [
        ('阿尔·阿明', 'onscreen'), ('Jean-Luc Roy', 'telephone')]


@pytest.mark.parametrize('description',[
    '<Subject 1 > 正面近景，机位稳定。',
    '声音：< Subject 1 > 画内对白，其他人物无对白。',
])
def test_explicit_camera_subject_reads_cast_and_unique_bound_scene(description):
    manifest=[{'asset_id':'a','type':'image','path':'a.png','role':'陈默',
               'metadata':{'asset_kind':'character','role_id':'a','identity_id':'a'}},
              {'asset_id':'bg','type':'image','path':'bg.png','role':'办公室',
               'metadata':{'asset_kind':'background','scene_id':'office'}}]
    text='【详细镜头描述】\n[Shot 1]（0–4 秒）：'+description
    doc=parse_prompt(text,'test',manifest)
    assert doc['shots'][0]['visible_roles']==['a']
    assert doc['shots'][0]['scene_id']=='office'
    assert description in render_prompt(doc)


@pytest.mark.parametrize('description',[
    '看向<Subject 1>方向；另一个人在画外。',
    '不拍<Subject 1>正面近景；镜头对准文件。',
    '<Subject 1>在画外说：<d>[Chinese]正面近景。</d>',
])
def test_camera_cast_extraction_does_not_use_mentions_negations_or_spoken_words(description):
    doc=parse_prompt('【详细镜头描述】\n[Shot 1]（0–4 秒）：'+description,'test')
    assert 'visible_roles' not in doc['shots'][0]


def test_multiple_backgrounds_require_explicit_scene_selection():
    manifest=[{'asset_id':str(i),'type':'image','path':str(i)+'.png',
               'metadata':{'asset_kind':'background','scene_id':str(i)}} for i in range(2)]
    doc=parse_prompt('【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；桌面特写。','test',manifest)
    assert 'scene_id' not in doc['shots'][0]
