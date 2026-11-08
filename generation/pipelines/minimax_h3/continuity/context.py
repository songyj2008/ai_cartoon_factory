"""Explicit scene scopes; a shared background is never a continuity decision."""
import copy
from .state import character_keys
from .checkpoints import digest

def segment_context(segment):
    return segment.get('scene_context_id')

def freeze_scope(jobs, uid, settings):
    rows=jobs.get('segments',[])
    index=next(i for i,s in enumerate(rows) if s.get('segment_uid')==uid)
    context=str(settings.get('context_id') or '').strip()
    if not context: raise ValueError('请明确选择或填写连续场次，不按背景图自动合并')
    start=int(settings.get('start_index') or 1)-1
    if not 0<=start<=index: raise ValueError('场次起始段无效')
    saved=copy.deepcopy(((jobs.get('h3_scene_continuity') or {}).get('contexts') or {}).get(context))
    first=rows[start]
    roster=settings.get('tracked_role_ids')
    if roster is None:
        roster=(saved or {}).get('tracked_role_ids') or sorted(character_keys((first.get('generation_config') or {}).get('asset_manifest') or []))
    if not isinstance(roster,list) or not roster or any(not isinstance(r,str) or not r for r in roster):
        raise ValueError('场次需要明确的跟踪人物名单')
    roster=list(dict.fromkeys(roster))
    config={'context_id':context,'start_segment_uid':first['segment_uid'],'tracked_role_ids':roster}
    if settings.get('manual'): config['manual']=True
    config['roster_revision']=digest(config)
    chain=[]
    from .source import freeze_segment
    for row in rows[start:index]:
        old=segment_context(row)
        if old and old!=context and not settings.get('manual'): raise ValueError('来源链跨越不同连续场次，请调整场次起始段')
        chain.append(freeze_segment(row))
    return {'config':config,'base_config':saved,'chain':chain}

def resolve_shot_contexts(document, context_id, saved=None):
    for shot in document['shots']:
        key=(saved or {}).get(shot['uid']) or context_id
        if key: shot['scene_context_id']=key
    return document
