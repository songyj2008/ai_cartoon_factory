"""Replay authored facts only; framing changes do not move people."""
import copy
from .evidence import source_quote
from .state import FACTS

def initial_world(roster):
    return {role:{} for role in roster}

def reduce_segment(world, trace, source, roster):
    world=copy.deepcopy(world)
    text=source['snapshot']['prompt']
    for shot in trace['shots']:
        for event in shot['world_events']:
            role=event['role_id']
            if role not in roster: raise ValueError('世界事件人物不在跟踪名单：'+str(role))
            facts=event['facts']
            if not isinstance(facts,dict) or set(facts)-FACTS: raise ValueError('世界事件事实字段无效')
            for field,fact in facts.items():
                if not isinstance(fact,dict) or set(fact)-{'value','source_quote','relative_to_role_id'} or not {'value','source_quote'}<=set(fact):
                    raise ValueError(role+'.'+field+' 须提供value与source_quote对象')
                value= fact['value']; quote=fact['source_quote']
                previous=world.get(role,{}).get(field)
                # Reasserting an unchanged fact is not a state transition.
                # Keep its original evidence rather than asking the importer
                # to prove the same fact again in every subsequent shot.
                if (isinstance(previous,dict) and type(value) is type(previous.get('value'))
                    and value==previous.get('value')
                    and fact.get('relative_to_role_id')==previous.get('relative_to_role_id')):
                    continue
                recovered=source_quote(text,quote)
                if not isinstance(quote,str) or (value is not None and recovered is None):
                    raise ValueError(role+'.'+field+' 缺少来源原文依据')
                if value is not None and not (type(value)is bool if field=='present' else isinstance(value,str) and bool(value.strip())):
                    raise ValueError(role+'.'+field+' 类型无效')
                if isinstance(value,str):
                    import re
                    if re.search(r'<\s*(Subject|Picture|Audio)\s+\d+',value,re.I): raise ValueError('世界状态须使用人物身份，不使用素材编号')
                relative=fact.get('relative_to_role_id')
                if relative is not None and relative not in roster: raise ValueError('相对位置人物未登记')
                stored=copy.deepcopy(fact)
                quote=recovered if recovered is not None else quote
                stored['source_quote']=quote
                stored['origin']={'segment_uid':source['segment_uid'],'source_revision':source['prompt_hash'],
                                  'shot_uid':shot['shot_uid'],'source_quote':quote}
                world.setdefault(role,{})[field]=stored
    return world

def as_extraction(world, scene_id=None, events=None):
    return {'state':{'schema_version':1,'scene_id':scene_id,'characters':[
        {'role_id':role,'facts':{k:{a:b for a,b in fact.items() if a!='origin'} for k,fact in facts.items()}}
        for role,facts in world.items()]},'events':events or []}


def reduce_plan(world, plan, segment_uid, roster):
    from .checkpoints import digest
    from .state import scene_world, SCENE_FACTS
    result=copy.deepcopy(world)
    version=digest(plan)
    for shot in plan['shots']:
        for event in shot['world_events']:
            role=event.get('role_id')
            if role not in roster: raise ValueError('状态变化人物不在场次名单')
            facts=event.get('facts')
            if not isinstance(facts,dict) or set(facts)-SCENE_FACTS: raise ValueError('状态变化字段无效')
            for field,fact in facts.items():
                if not isinstance(fact,dict) or 'value' not in fact: raise ValueError('状态变化必须明确value')
                value=fact['value']
                if value is not None and not (type(value)is bool if field=='present' else isinstance(value,str)): raise ValueError('状态值类型无效')
                if fact.get('relative_to_role_id') is not None and fact['relative_to_role_id'] not in roster: raise ValueError('关联人物身份无效')
                prior = result.get(role, {}).get(field)
                if (isinstance(prior, dict) and type(value) is type(prior.get('value'))
                        and value == prior.get('value')
                        and fact.get('relative_to_role_id') == prior.get('relative_to_role_id')):
                    continue
                result.setdefault(role,{})[field]={**copy.deepcopy(fact),'origin':{'kind':'plan','segment_uid':segment_uid,'plan_revision':version,'shot_uid':shot['shot_uid']}}
    return scene_world(result)
