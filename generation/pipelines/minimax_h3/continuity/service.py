"""One Shot plan owns framing, references, and the resulting world state."""
import copy
from ..editor_draft import fingerprint,validate_candidate
from .assets import _prepare_character_references


def build_opening_candidate(payload):
    from .ledger import state_before, record_from_plan
    if payload.get('operation_contract')!='scene_state_v3':
        raise ValueError('旧承接任务已停用，请使用场景状态机')
    base=copy.deepcopy(payload['draft'])
    # The current text may come from an earlier applied candidate. A new
    # optimization owns a fresh operation; its predecessor's flags cannot carry.
    for key in ('scene_patch','state_guard','composition_patch','state_only','state_preparation',
                'operation_contract','operation_result','optimization_attempt_id','plan'):
        base.pop(key,None)
    from .readiness import read_ready_context, scene_state_guard
    jobs=copy.deepcopy(payload['jobs'])
    # Optimization consumes the incoming world, not a prior analysis of itself.
    context=read_ready_context(jobs,payload['segment_uid'],target_source='draft')
    if context is None:raise ValueError('请先加入场景状态机')
    from .spatial_plan import predecessor_plan
    context['previous_spatial_plan'] = predecessor_plan(jobs, context)
    prepared=copy.deepcopy(base)
    # Offer registered references without replacing the current selections.
    for row in jobs['segments']:
        refs=[a for a in (row.get('generation_config') or {}).get('asset_manifest',[]) if (a.get('metadata') or {}).get('role_id') in context['tracked_role_ids']]
        prepared,_=_prepare_character_references(prepared,{'snapshot':{'asset_manifest':refs}})
    # A prior draft can omit an offscreen role while its selected render still
    # owns that identity. Recover only missing identities from this scene's
    # preceding selected sources; never replace the user's current references.
    from .state import character_keys
    missing=set(context['tracked_role_ids'])-character_keys(prepared['asset_manifest'])
    if missing:
        from .versions import source_segment
        target_index=next(i for i,row in enumerate(jobs['segments']) if row['segment_uid']==payload['segment_uid'])
        for row in reversed(jobs['segments'][:target_index]):
            if row.get('scene_context_id')!=context['context_id']:continue
            view=source_segment(row)
            refs=[a for a in (view.get('generation_config') or {}).get('asset_manifest',[])
                  if (a.get('metadata') or {}).get('role_id') in missing]
            prepared,_=_prepare_character_references(prepared,{'snapshot':{'asset_manifest':refs}})
            missing-=character_keys(prepared['asset_manifest'])
            if not missing:break
    from .composition import plan_composition_patch,merge_composition_patch,finalize_composition,validate_spatial_edit,validate_state_patch,_shots
    prepared['_framing_context'] = copy.deepcopy(context)
    prepared['shot_uids']=[s['uid'] for s in _shots(prepared)]
    spatial_result=plan_composition_patch(prepared,context)
    patch=spatial_result['patch']
    if not context.get('initializing') and isinstance(patch,dict) and 'initial_state' in patch:
        raise ValueError('已初始化的场景不能通过构图补丁重设初态')
    validate_state_patch(prepared,patch,context['tracked_role_ids'])
    spatial_text=merge_composition_patch(prepared,patch)
    text,manifest=finalize_composition(prepared,patch)
    from .constraints import prompt_with_constraints
    text=prompt_with_constraints(text,context.get('spatial_constraints',''))
    from .composition import bind_final_state_quotes
    plan={'shots':bind_final_state_quotes(prepared,patch,text,asset_manifest=manifest),'scene_relation':'immediate'}
    if 'initial_state' in patch:plan['initial_state']=patch['initial_state']
    candidate=copy.deepcopy(base)
    target = next(row for row in jobs['segments'] if row['segment_uid'] == payload['segment_uid'])
    candidate['prompt_baseline'] = copy.deepcopy(target.get('h3_prompt_baseline') or base.get('prompt_baseline') or {
        key: base.get(key) for key in ('segment_uid', 'prompt', 'asset_manifest', 'shot_uids')})
    if not candidate['prompt_baseline'].get('shot_uids'):
        candidate['prompt_baseline']['shot_uids'] = copy.deepcopy(prepared['shot_uids'])
    candidate.update(prompt=text,asset_manifest=manifest,editor_constraints={},shot_uids=prepared['shot_uids'])
    validate_state_patch(candidate, {'shots': plan['shots']}, context['tracked_role_ids'])
    candidate['composition_patch']=patch
    from ..editor_draft import snapshot
    persisted = snapshot(next(row for row in jobs['segments'] if row['segment_uid'] == payload['segment_uid']))
    if all(candidate.get(key) == persisted.get(key) for key in
           ('prompt', 'asset_manifest', 'source_excerpt', 'duration_sec', 'scene_context_id')):
        candidate['state_only'] = True
    operation_base=copy.deepcopy(prepared)
    candidate['state_guard']=scene_state_guard(jobs,payload['segment_uid'])
    validate_spatial_edit(operation_base,candidate)
    doc=validate_candidate(operation_base,candidate,allow_background_visibility=True)
    from .revisions import revision
    from .reducer import reduce_plan
    end=reduce_plan(plan.get('initial_state',context['input_state']),plan,payload['segment_uid'],context['tracked_role_ids'])
    fake={'segment_uid':payload['segment_uid'],'generation_config':{'prompt_override':text,'asset_manifest':manifest}}
    candidate['scene_patch']={'segment_uid':payload['segment_uid'],'context_id':context['context_id'],
        'input_revision':context['input_revision'],
        'record':record_from_plan(plan,revision(fake),context['input_revision'],opening_state=plan.get('initial_state',context['input_state']),segment_uid=payload['segment_uid'],previous_segment_uid=context['previous_segment_uid'])}
    candidate['scene_patch']['record']['source_prompt']=text
    candidate['scene_patch']['record']['record_kind']='optimized'
    from .framing import build_framing_record
    candidate['scene_framing'] = build_framing_record(prepared, context, patch, text, manifest)
    candidate['scene_framing'].update({key: copy.deepcopy(spatial_result[key]) for key in
                                      ('spatial_plan', 'spatial_review', 'spatial_sources')})
    if context.get('initializing'):
        if not isinstance(plan.get('initial_state'),dict) or set(plan['initial_state'])!=set(context['tracked_role_ids']):raise ValueError('本次规划缺少开场人物状态')
        candidate['scene_patch']['initial_state']=copy.deepcopy(plan['initial_state'])
    candidate['operation_contract']='scene_spatial_edits_v1'
    changed=[i for i,(before,after) in enumerate(zip(_shots(prepared),_shots(dict(prepared,prompt=spatial_text))),1) if before['body']!=after['body']]
    counts = '/'.join(str(len(row['visible_roles'])) for row in patch['shots'])
    summary = ('已逐镜核对场景' + str(len(context['tracked_role_ids'])) + '人、'
               + str(len(patch['shots'])) + '个Shot；各镜曾入画人数：' + counts
               + '。' + ('已调整 Shot ' + '、'.join(map(str, changed)) + ' 的构图与入画' if changed else '原构图保留'))
    if manifest!=base['asset_manifest'] or text!=spatial_text:summary+='；已同步本段参考与逐镜用途'
    outcome={'kind':'composition' if changed else 'unchanged','changed_shots':changed,'summary':summary}
    candidate['operation_result']=outcome
    candidate['state_guard']=scene_state_guard(jobs,payload['segment_uid'])
    if payload.get('optimization_attempt_id'):
        candidate['optimization_attempt_id']=payload['optimization_attempt_id']
    return {'scene_preview':{'input_state':end},'draft':candidate,'document':doc,'draft_hash':fingerprint(base),'base_revision':payload['base_revision'],
        'operation_base':operation_base,'operation_result':outcome,'changes':[summary]}
