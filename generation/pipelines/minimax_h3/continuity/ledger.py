"""Build missing scene records in the operation snapshot, never in live storage."""
import copy
from .reducer import initial_world,reduce_segment,as_extraction
from .trace import extract_segment_trace
from .checkpoints import digest
from .state import scene_world


def reuse_record(record,source,world,config,deps):
    """Reuse unchanged history, including safe automatic roster expansion."""
    if (record.get('schema_version')!=2 or record.get('status') not in ('current','needs_analysis')
        or record.get('content_revision')!=source['content_revision']
        or record.get('dependencies')!=deps): return None
    old=record.get('config') or {}
    old_roles=set(old.get('tracked_role_ids',[]));roles=set(config['tracked_role_ids'])
    if old!=config:
        from .state import character_keys
        if (old.get('manual') or config.get('manual')
            or old.get('context_id')!=config['context_id']
            or old.get('start_segment_uid')!=config['start_segment_uid']
            or not old_roles<=roles
            or (roles-old_roles)&character_keys(source['snapshot']['asset_manifest'])): return None
    prior={role:world.get(role,{}) for role in old_roles}
    if record.get('input_state_hash')!=digest(prior): return None
    result=copy.deepcopy(record)
    result.pop('reason',None)
    result.update(status='current',config=copy.deepcopy(config),input_state=copy.deepcopy(world),input_state_hash=digest(world),
                  end_state={**copy.deepcopy(world),**copy.deepcopy(record['end_state'])})
    return result


def source_world(scope,providers):
    config=scope['config']; world=copy.deepcopy(scope.get('baseline')) if scope.get('baseline') is not None else initial_world(config['tracked_role_ids']); records={};deps=[]
    # Refresh only derived checkpoints, never frozen prompts or selections.
    latest={}
    from services.text_jobs import current
    operation=current()
    if operation:
        import json
        from pathlib import Path
        try:
            saved=json.loads((Path(operation['source'])/'video_jobs.json').read_text(encoding='utf-8-sig'))
            latest={s['segment_uid']:s.get('h3_continuity') or {} for s in saved.get('segments',[])}
        except (OSError,ValueError,KeyError):pass
    for index,source in enumerate(scope['chain']):
        from services.text_jobs import phase,check_cancelled
        check_cancelled();phase(f'补齐场次记录 {index+1}/{len(scope["chain"])}')
        old=latest.get(source['segment_uid']) or source.get('record') or {}
        record=reuse_record(old,source,world,config,deps)
        if record is None: record=build_record(source,world,config,deps,providers)
        record['end_state']=scene_world(record['end_state'])
        records[source['segment_uid']]=record;world=record['end_state']
        deps=[*deps,{'segment_uid':source['segment_uid'],'revision':source['content_revision']}]
    return world,records,deps

def build_record(source,world,config,deps,providers):
    trace=extract_segment_trace(source,world,config,providers)
    end=scene_world(reduce_segment(world,trace,source,config['tracked_role_ids']))
    return {'schema_version':2,'status':'current','context_id':config['context_id'],'config':copy.deepcopy(config),
            'content_revision':source['content_revision'],'dependencies':copy.deepcopy(deps),
            'input_state_hash':digest(world),'input_state':copy.deepcopy(world),'trace':trace,'end_state':end}

def prepare_source(payload,providers):
    scope=payload['scene_scope'];world,records,deps=source_world(scope,providers)
    events=next(reversed(records.values()))['trace'].get('events',[]) if records else scope.get('events',[])
    return as_extraction(world,events=events),world,{'config':scope['config'],'base_config':scope['base_config'],
        'records':records,'dependencies':deps}


def state_before(jobs, segment_uid):
    rows=jobs['segments']; target=next(s for s in rows if s['segment_uid']==segment_uid)
    key=target.get('scene_context_id')
    if not key:return None
    scene=jobs.get('h3_scene_continuity',{}).get('contexts',{}).get(key)
    if not scene or 'initial_state' not in scene:raise ValueError('请先初始化场景人物状态')
    world=copy.deepcopy(scene['initial_state']); previous_uid=None; previous_source=None
    from .reducer import reduce_plan
    from .state import model_world,character_keys
    for row in rows[:rows.index(target)]:
        if row.get('scene_context_id')!=key:continue
        from .versions import source_segment, source_record
        view=source_segment(row)
        record=source_record(row,view) or {}
        if row.get('h3_state_mode') == 'unset':
            raise ValueError('成员段尚未选择状态方式：'+str(row.get('part_id') or row['segment_uid']))
        if record.get('schema_version')!=3:raise ValueError('成员段人物状态尚未确认：'+str(row.get('part_id') or row['segment_uid']))
        try:
            validate_record(record,view)
        except ValueError as exc:
            label=str(row.get('part_id') or row['segment_uid'])+' '+str(row.get('title') or '')
            raise ValueError(label+'：'+str(exc)) from exc
        if record.get('record_kind') == 'inherit':
            if record.get('context_id') != key or row.get('h3_state_mode') != 'inherit':
                raise ValueError('沿用状态归属或状态方式已变化')
            previous_uid=row['segment_uid'];previous_source=view.get('_continuity_source')
            continue
        upstream=digest(model_world(world))
        if 'previous_end_revision' in record and (record.get('previous_segment_uid')!=previous_uid or record['previous_end_revision']!=upstream):
            raise ValueError('前驱人物状态已变化，请确认：'+str(row.get('part_id') or row['segment_uid']))
        if 'end_state' in record:
            if record.get('end_revision')!=digest(model_world(record['end_state'])):raise ValueError('场景末态版本无效')
            world=copy.deepcopy(record['end_state'])
        else:
            roles=set(world)|{e['role_id'] for shot in record['shots'] for e in shot['world_events']}
            world=reduce_plan(world,{'shots':record['shots']},row['segment_uid'],roles)
        previous_uid=row['segment_uid']
        previous_source=view.get('_continuity_source')
    roles=set(world)|character_keys((target.get('generation_config') or {}).get('asset_manifest',[]))
    return {'context_id':key,'input_state':world,'tracked_role_ids':sorted(roles),
            'previous_segment_uid':previous_uid,'previous_end_revision':digest(model_world(world)),
            'previous_source':copy.deepcopy(previous_source),
            'initializing':bool(scene.get('initial_pending')) and previous_uid is None,
            'input_revision':digest({'context_id':key,'previous_segment_uid':previous_uid,'state':model_world(world),'pending':bool(scene.get('initial_pending'))})}



def record_from_plan(plan, source_revision, input_revision, *, opening_state=None, segment_uid=None, previous_segment_uid=None):
    validate_record({'shots':plan.get('shots')})
    record={'schema_version':3,'source_revision':source_revision,'input_revision':input_revision,
            'shots':[{k:copy.deepcopy(s[k]) for k in ('shot_uid','world_events')} for s in plan['shots']]}
    if opening_state is not None:
        from .reducer import reduce_plan
        from .state import model_world
        roles=set(opening_state)|{e['role_id'] for shot in plan['shots'] for e in shot['world_events']}
        end=reduce_plan(opening_state,plan,segment_uid,roles)
        record.update(input_state=copy.deepcopy(opening_state),end_state=end,end_revision=digest(model_world(end)),previous_segment_uid=previous_segment_uid,
                      previous_end_revision=digest(model_world(opening_state)))
    return record



def validate_record(record, segment=None):
    if record.get('record_kind') == 'inherit':
        if (record.get('schema_version') != 3 or record.get('shots') != []
                or record.get('user_declared_unchanged') is not True
                or record.get('state_pending')
                or not isinstance(record.get('input_state'), dict)
                or record.get('input_state') != record.get('end_state')):
            raise ValueError('沿用状态声明无效')
        from .state import model_world
        if record.get('end_revision') != digest(model_world(record['end_state'])):
            raise ValueError('沿用状态版本无效')
        return
    shots=record.get('shots')
    if not isinstance(shots,list) or not shots:raise ValueError('人物状态记录缺少完整Shot列表，将在当前操作中自动整理')
    ids=[s.get('shot_uid') for s in shots]
    if any(not uid for uid in ids) or len(set(ids))!=len(ids):raise ValueError('人物状态记录Shot身份缺失或重复')
    if any(not isinstance(s.get(k),list) for s in shots for k in ('world_events',)):raise ValueError('每镜须有状态变化列表')
    if segment is not None:
        if record.get('state_pending'):
            raise ValueError('人物状态待确认：'+str(segment.get('part_id') or segment.get('title') or segment['segment_uid']))
        from ..shot_editor import parse_prompt
        from ..editor_draft import snapshot
        d=snapshot(segment)
        doc=parse_prompt(d['prompt'],segment['segment_uid'],d['asset_manifest'])
        saved=d.get('shot_uids') or [s['uid'] for s in doc['shots']]
        if len(ids)!=len(doc['shots']) or ids!=saved:raise ValueError('人物状态记录与当前Shot数量或身份不一致')
