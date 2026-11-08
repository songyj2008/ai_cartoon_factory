"""Source-only model extraction of per-shot world and framing events."""
import copy,json
from pathlib import Path
from . import checkpoints
from .reducer import reduce_segment
from .evidence import source_quote
from ..shot_editor import parse_prompt

def extract_segment_trace(source, world, config, providers):
    from services.llm import call_llm
    document=parse_prompt(source['snapshot']['prompt'],source['segment_uid'],source['snapshot']['asset_manifest'])
    saved=source.get('shot_uids') or []
    if len(saved)==len(document['shots']):
        for shot,uid in zip(document['shots'],saved): shot['uid']=uid
    if any('visible_roles' not in s for s in document['shots']): raise ValueError('场景记录需要每个Shot明确画面人物')
    system=(Path(__file__).resolve().parents[4]/'profiles'/'h3_scene_trace_rules.md').read_text(encoding='utf-8')
    from .mapping import normalize_script_entities
    normalized,aliases=normalize_script_entities(source['snapshot']['prompt'],source['snapshot']['asset_manifest'])
    from .mapping import model_references
    from .state import model_world
    request={'source':{'snapshot':{'prompt':normalized}},
             'roles':model_references(source['snapshot']['asset_manifest']),
             'input_world':model_world(world),'scene':{'tracked_role_ids':config['tracked_role_ids']},
             'shots':[{'shot_uid':s['uid'],'visible_roles':s['visible_roles']} for s in document['shots']]}
    key=checkpoints.digest({'rules':system,'input':request,
        'source_revision':source.get('content_revision') or source.get('prompt_hash'),
        'aliases':aliases,'identity_contract':3})
    # Per-operation cache; never trust a result belonging to a different input.
    path=checkpoints._path()
    if path: path=path.with_name('scene_trace_'+key+'.json')
    result=None
    if path and path.is_file():
        try: result=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError): pass
    if result is None:
        result,_=call_llm(system,json.dumps(request,ensure_ascii=False,sort_keys=True),max_tokens=6500,
            label='h3_prompt_scene_trace_'+key[:24],provider_override=providers)
    for attempt in range(2):
        try:
            result=_validate_trace(copy.deepcopy(result),document,source,world,config,aliases)
            break
        except ValueError as exc:
            if attempt: raise ValueError('旧稿场景记录自动修正后仍未通过：'+str(exc)) from exc
            from services.text_jobs import phase
            phase('旧稿场景记录引用校验未通过，正在自动修正一次')
            result,_=call_llm(system+'\n本次只修复无效的记录及来源引用。source_quote必须取当前来源中一段连续原文，不得拼接、缩写或改写；'
                '已有状态未改变时省略该事实，保留input_world中的原事实；不得猜测或把错误引用置空来保留确定值。返回完整JSON。',
                json.dumps({**request,'invalid_trace':result,'validation_error':str(exc)},ensure_ascii=False,sort_keys=True),
                max_tokens=6500,label='h3_prompt_scene_trace_'+key[:24]+'_repair1',provider_override=providers)
    if path:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    return result


def _validate_trace(result,document,source,world,config,aliases):
    if not isinstance(result,dict) or result.get('relation') not in ('immediate','initial'):
        raise ValueError('该段不是明确即时连续，请另建场次或明确初始状态')
    shots=result.get('shots')
    if not isinstance(shots,list) or len(shots)!=len(document['shots']): raise ValueError('场景记录Shot数量不匹配')
    reverse={role:tag for tag,role in aliases.items()}
    def original_quote(value):
        if not isinstance(value,str):return value
        for role in sorted(reverse,key=len,reverse=True): value=value.replace(role,reverse[role])
        return source_quote(source['snapshot']['prompt'],value) or value
    for shot,authored in zip(shots,document['shots']):
        if not isinstance(shot,dict) or shot.get('shot_uid')!=authored['uid']: raise ValueError('场景记录Shot身份不匹配')
        if not isinstance(shot.get('world_events'),list): raise ValueError('世界事件必须为列表')
        for event in shot['world_events']:
            if not isinstance(event,dict) or set(event)!={'role_id','facts'}: raise ValueError('世界事件结构无效')
        for field in ('framing_events','visibility_details'):
            if not isinstance(shot.get(field,[]),list): raise ValueError('画面记录必须为列表')
            for item in shot.get(field,[]):
                if isinstance(item,dict):
                    item['source_quote']=original_quote(item.get('source_quote'))
                if (not isinstance(item,dict) or set(item)!={'role_id','description','source_quote'}
                    or item['role_id'] not in config['tracked_role_ids']
                    or not isinstance(item['description'],str) or not isinstance(item['source_quote'],str)
                    or not item['source_quote'] or item['source_quote'] not in source['snapshot']['prompt']):
                    raise ValueError('画面变化缺少有效人物或原文依据')
        shot['visible_roles']=copy.deepcopy(authored['visible_roles'])
    for row in shots:
        for event in row['world_events']:
            for fact in event['facts'].values():
                if isinstance(fact,dict): fact['source_quote']=original_quote(fact.get('source_quote'))
    reduce_segment(world,result,source,config['tracked_role_ids'])
    for event in result.get('events',[]):
        if isinstance(event,dict):
            event['source_quote']=original_quote(event.get('source_quote'))
        if (not isinstance(event,dict) or set(event)!={'actor_role_ids','object','phase','source_quote'}
            or not isinstance(event['actor_role_ids'],list) or any(r not in config['tracked_role_ids'] for r in event['actor_role_ids'])
            or event['phase'] not in ('not_started','in_progress','completed','unknown')
            or not isinstance(event['object'],str) or not isinstance(event['source_quote'],str)
            or (event['phase']!='unknown' and (not event['source_quote'] or event['source_quote'] not in source['snapshot']['prompt']))):
            raise ValueError('动作阶段缺少有效来源或人物')
    return result
