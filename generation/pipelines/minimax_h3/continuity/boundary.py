"""Target-only edits, with one opening fact selection for Shot 1."""
import copy
from .mapping import project_facts,reference_map
from ..shot_editor import parse_prompt,render_prompt

def target_context(extraction,base,prepared):
    return {'target':base,'same_scene_target':prepared,
            'source_end':{'scene_id':extraction['state'].get('scene_id'),
                          'facts':project_facts(extraction,prepared['asset_manifest']),
                          'events':[{k:v for k,v in e.items() if k!='source_quote'} for e in extraction['events']]},
            'target_references':reference_map(prepared['asset_manifest'])}

def apply_plan(result,request):
    if not isinstance(result,dict):raise ValueError('承接回复必须为对象')
    if result.get('conflicts'):raise ValueError('承接需处理：'+'；'.join(result['conflicts']))
    relation=result.get('relation')
    if relation not in {'immediate','time_jump','scene_change','unclear'}:raise ValueError('承接回复缺少有效时空关系')
    if result.get('shot1_start_ref')!='opening':raise ValueError('Shot 1须引用唯一开场事实')
    ids=result.get('opening_fact_ids')
    if not isinstance(ids,list) or any(not isinstance(i,str) or i not in request['source_end']['facts'] for i in ids):raise ValueError('开场事实引用无效')
    edits=result.get('edits')
    if not isinstance(edits,list):raise ValueError('承接修改须为定向编辑列表')
    if relation!='immediate':
        if ids or edits:raise ValueError('非即时连续不得强套来源站位或改写当前稿')
        return request['target']['prompt']
    document=parse_prompt(request['same_scene_target']['prompt'],'boundary',request['same_scene_target']['asset_manifest'])
    seen=set()
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'shot_index','before','after','fact_ids'}:raise ValueError('承接修改字段无效')
        index=edit['shot_index']; before=edit['before']; after=edit['after']
        if (type(index)is not int or not 0<=index<=len(document['shots']) or index in seen
            or not isinstance(before,str) or not isinstance(after,str) or (not before and index!=0)
            or not isinstance(edit['fact_ids'],list) or not edit['fact_ids'] or any(not isinstance(f,str) or f not in ids for f in edit['fact_ids'])):raise ValueError('承接修改范围或事实无效')
        seen.add(index)
        body=document['preamble'] if index==0 else document['shots'][index-1]['body']
        if (before and body.count(before)!=1) or (not before and body):raise ValueError('承接修改底稿不唯一，请重新生成候选')
        if '<d>' in before or '</d>' in before or '<d>' in after or '</d>' in after:raise ValueError('承接不能改写对白标签')
        body=body.replace(before,after,1)
        if index==0:document['preamble']=body
        else:document['shots'][index-1]['body']=body
    return render_prompt(document) if edits else request['same_scene_target']['prompt']
