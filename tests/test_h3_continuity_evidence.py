from generation.pipelines.minimax_h3.continuity.evidence import source_quote
from generation.pipelines.minimax_h3.continuity.reducer import reduce_segment

def test_subject_tag_and_chinese_whitespace_recovers_exact_original():
    original='建立空间关系：<Subject 1>与<Subject 2>隔桌相对。'
    returned='建立空间关系：<Subject 1> 与 < Subject 2 > 隔桌相对。'
    assert source_quote(original,returned)==original

def test_no_semantic_or_punctuation_repair_or_stitched_sections():
    assert source_quote('甲坐下。','甲站起。') is None
    assert source_quote('<Subject 1>坐下。','<Subject 2>坐下。') is None
    assert source_quote('甲坐下。中间发生事件。乙离开。','甲坐下。乙离开。') is None
    assert source_quote('甲坐下。','甲坐下，') is None

def test_ambiguous_normalized_match_is_rejected():
    assert source_quote('甲 乙。甲\n乙。','甲乙。') is None

def test_world_origin_stores_actual_source_span_not_model_formatting():
    text='甲在桌前。';quote='甲 在 桌前。'
    source={'segment_uid':'one','prompt_hash':'version','snapshot':{'prompt':text}}
    trace={'shots':[{'shot_uid':'shot','world_events':[{'role_id':'a','facts':{
        'position':{'value':'桌前','source_quote':quote}}}]}]}
    world=reduce_segment({'a':{}},trace,source,['a'])
    fact=world['a']['position']
    assert fact['source_quote']==text and fact['origin']['source_quote']==text
    assert trace['shots'][0]['world_events'][0]['facts']['position']['source_quote']==quote
