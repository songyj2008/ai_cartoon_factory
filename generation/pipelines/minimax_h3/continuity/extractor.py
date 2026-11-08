"""Extract source facts without seeing the target screenplay."""
import json,re
from .state import validate_state,character_keys
from .rules import load_source_rules
from . import checkpoints

def validate_extraction(result,source):
    if not isinstance(result,dict) or set(result)!={'state','events'}:raise ValueError('来源提取须返回state与events')
    manifest=(source.get('snapshot') or {}).get('asset_manifest') or []
    state=validate_state(result['state'],source,manifest)
    events=result['events']; allowed=character_keys(manifest)
    if not isinstance(events,list):raise ValueError('来源动作阶段无效')
    text=(source.get('snapshot') or {}).get('prompt','')
    for event in events:
        if not isinstance(event,dict) or set(event)!={'actor_role_ids','object','phase','source_quote'}:raise ValueError('来源动作字段无效')
        if (not isinstance(event['actor_role_ids'],list) or any(r not in allowed for r in event['actor_role_ids'])
            or event['phase'] not in {'not_started','in_progress','completed','unknown'}
            or not isinstance(event['object'],str) or not isinstance(event['source_quote'],str)
            or (event['phase']!='unknown' and (not event['source_quote'].strip() or event['source_quote'] not in text))):
            raise ValueError('来源动作缺少身份或有效依据')
    for person in state['characters']:
        for fact in person['facts'].values():
            if isinstance(fact['value'],str) and re.search(r'<\s*(?:Subject|Picture|Audio)\s+\d+',fact['value'],re.I):
                raise ValueError('来源状态值须使用人物身份描述，不携带来源编号')
    return {'state':state,'events':events}

def extract_source_end_state(source,providers):
    from services.llm import call_llm
    system=load_source_rules()
    frozen={'prompt':source.get('snapshot',{}).get('prompt',''),
            'asset_manifest':source.get('snapshot',{}).get('asset_manifest',[])}
    key=checkpoints.digest({'source':source,'rules':system,'version':2})
    cached=checkpoints.load(key)
    if cached is not None:
        try:return validate_extraction(cached,source)
        except (ValueError,TypeError,KeyError):pass
    result,_=call_llm(system,json.dumps(frozen,ensure_ascii=False,sort_keys=True),max_tokens=3500,
        label='h3_prompt_continuity_source_v2',provider_override=providers)
    result=validate_extraction(result,source)
    checkpoints.save(key,result)
    return result
