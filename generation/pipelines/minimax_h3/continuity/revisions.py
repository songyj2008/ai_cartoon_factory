"""Nonrecursive content dependencies, shared by every persistence path."""
import copy
from .checkpoints import digest

def revision(segment):
    from ..editor_draft import snapshot
    from .state import binding_hash
    draft=snapshot(segment)
    return digest({'prompt':draft['prompt'],'images':binding_hash(draft['asset_manifest']),
                   'uid':segment.get('segment_uid')})

def validate_dependencies(jobs, dependencies):
    rows={s['segment_uid']:s for s in jobs.get('segments',[])}
    order=[s['segment_uid'] for s in jobs.get('segments',[])]
    indices=[]
    for dep in dependencies:
        uid=dep['segment_uid']
        if uid not in rows or revision(rows[uid])!=dep['revision']: raise ValueError('场次来源已变化，请重新记录或承接')
        indices.append(order.index(uid))
    if indices!=sorted(indices) or any(b!=a+1 for a,b in zip(indices,indices[1:])):
        raise ValueError('场次来源顺序已变化，请重新承接')

def reconcile_ledger_revisions(jobs):
    contexts=(jobs.get('h3_scene_continuity') or {}).get('contexts',{})
    for row in jobs.get('segments',[]):
        record=row.get('h3_continuity')
        if not record: continue
        if record.get('record_kind')=='inherit':
            record.update(text_pending=False,state_pending=False)
            continue
        if record.get('schema_version')==3:
            # This is provenance information, not a state invalidation signal.
            # Confirmed state changes only through events/calibration.
            record['text_pending']=record.get('source_revision')!=revision(row)
            continue
        try:
            if record['content_revision']!=revision(row): raise ValueError('正文或空间参考已变化')
            if contexts.get(record['context_id'])!=record['config']: raise ValueError('场次名单已变化')
            validate_dependencies(jobs,record['dependencies'])
            deps=record['dependencies']
            if deps:
                order=[s['segment_uid'] for s in jobs['segments']]
                if order.index(row['segment_uid'])!=order.index(deps[-1]['segment_uid'])+1: raise ValueError('前驱已变化')
        except (ValueError,KeyError,TypeError) as exc:
            record.update(status='needs_analysis',reason=str(exc))
    return jobs

def accept_candidate_state(jobs, patch):
    if 'record' not in patch or patch['record'].get('schema_version')!=3:
        raise ValueError('旧承接候选已停用')
    from .ledger import state_before
    context=state_before(jobs,patch['segment_uid'])
    if not context or context['context_id']!=patch['context_id'] or context['input_revision']!=patch['input_revision']:
        raise ValueError('场景状态或成员关系已变化，请重新优化')
    row=next(s for s in jobs['segments'] if s['segment_uid']==patch['segment_uid'])
    if patch['record']['source_revision']!=revision(row):raise ValueError('候选与当前正文不匹配')
    record=copy.deepcopy(patch['record'])
    if record.get('record_kind')=='inherit':
        from .ledger import validate_record
        from .inheritance import inherited_record
        validate_record(record,row)
        if row.get('h3_state_mode')!='inherit':raise ValueError('本段状态方式已变化，请重新优化')
        if row.get('h3_scene_framing'):
            from .framing import validate_framing_state
            validate_framing_state(row, context, end_state=context['input_state'])
        row['h3_continuity']=inherited_record(row,context)
        return
    if context.get('initializing'):
        initial=patch.get('initial_state')
        if not isinstance(initial,dict) or set(initial)!=set(context['tracked_role_ids']):raise ValueError('缺少自动初始化状态')
        from .reducer import reduce_plan
        initial=reduce_plan({r:{} for r in initial},{'shots':[{'shot_uid':'initial','world_events':[{'role_id':r,'facts':f} for r,f in initial.items()]}]},'initial',list(initial))
        scene=jobs['h3_scene_continuity']['contexts'][context['context_id']]
        scene.update(initial_state=initial,initial_pending=False)
        record['input_revision']=state_before(jobs,patch['segment_uid'])['input_revision']
    from .ledger import validate_record,record_from_plan
    opening=patch.get('initial_state',context['input_state'])
    from .reducer import reduce_plan
    reduce_plan(opening,record,patch['segment_uid'],context['tracked_role_ids'])
    details={key:copy.deepcopy(record[key]) for key in ('opening_requirements','state_trace_version','state_trace_source','record_kind') if key in record}
    record=record_from_plan(record,record['source_revision'],record['input_revision'],
        opening_state=opening,segment_uid=patch['segment_uid'],previous_segment_uid=context['previous_segment_uid'])
    record.update(details,context_id=context['context_id'],previous_source=copy.deepcopy(context.get('previous_source')))
    from ..editor_draft import snapshot
    record['source_prompt']=snapshot(row)['prompt']
    record['text_pending']=False
    record['state_pending']=False
    validate_record(record,row)
    row['h3_continuity']=record
    # A saved candidate owns its end state, superseding an earlier no-change declaration.
    row['h3_state_mode']='extract'


def snapshot_for_render(segment):
    record=segment.get('h3_continuity') or {}
    if record.get('schema_version')==3:return copy.deepcopy(record)
    if record.get('status')=='current' and record.get('content_revision')==revision(segment): return copy.deepcopy(record)
    return {}
