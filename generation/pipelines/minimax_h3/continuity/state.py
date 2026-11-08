"""Structural scene facts with source evidence; never infer posture from action words."""
import copy

SCHEMA_VERSION = 1
CONTRACT_VERSION = 2
FACTS = {'present', 'position', 'posture', 'facing', 'gaze', 'holding'}
SCENE_FACTS = {'present', 'position', 'posture'}


def scene_world(world):
    """Only durable spatial facts belong in the scene ledger."""
    return {role:{key:copy.deepcopy(fact) for key,fact in facts.items() if key in SCENE_FACTS}
            for role,facts in world.items()}

def model_world(world):
    """Project values for the model without changing local evidence."""
    return {role:{field:{key:copy.deepcopy(fact[key]) for key in ('value','relative_to_role_id') if key in fact}
                  for field,fact in facts.items() if field in SCENE_FACTS}
            for role,facts in world.items()}


def binding_hash(manifest):
    from .checkpoints import digest
    return digest([{**{k:a.get(k) for k in ('asset_id','id','path','order','role','label')},
                    'metadata':{k:(a.get('metadata') or {}).get(k) for k in
                                ('asset_kind','role_id','identity_id','scene_id')}}
                   for a in manifest if a.get('type')=='image'])


def character_keys(manifest):
    return {str((a.get('metadata') or {}).get('role_id') or
                (a.get('metadata') or {}).get('identity_id') or a.get('asset_id') or a.get('id'))
            for a in manifest if a.get('type') == 'image'
            and (a.get('metadata') or {}).get('asset_kind') == 'character'}


def validate_state(value, source, manifest):
    """Validate evidence existence and identity, not natural-language entailment."""
    if not isinstance(value, dict):
        raise ValueError('承接状态必须为对象')
    value=copy.deepcopy(value)
    if not value:
        return {'schema_version': SCHEMA_VERSION, 'characters': []}
    if type(value.get('schema_version')) is not int or value['schema_version'] != SCHEMA_VERSION or set(value) - {'schema_version', 'scene_id', 'characters'}:
        raise ValueError('承接状态结构无效，请重新生成候选')
    if value.get('scene_id') is not None and not isinstance(value['scene_id'], str):
        raise ValueError('承接场景身份无效')
    characters = value.get('characters')
    if not isinstance(characters, list):
        raise ValueError('承接状态缺少人物列表')
    allowed = character_keys(manifest)
    source_text = str((source.get('snapshot') or {}).get('prompt') or '')
    seen = set()
    for person in characters:
        if not isinstance(person, dict) or set(person) != {'role_id', 'facts'}:
            raise ValueError('承接人物须按稳定身份提供facts，不使用Subject作为身份')
        role = person['role_id']
        if not isinstance(role, str) or role not in allowed or role in seen:
            raise ValueError('承接人物身份无效或重复')
        seen.add(role)
        facts = person['facts']
        if not isinstance(facts, dict) or set(facts) - FACTS:
            raise ValueError('承接人物状态字段无效')
        for name, fact in facts.items():
            if not isinstance(fact, dict) or not {'value','source_quote'}<=set(fact) or set(fact)-{'value','source_quote','relative_to_role_id'}:
                raise ValueError('来源状态格式错误：' + role + '.' + name +
                                 ' 须为含value和source_quote的对象，不接受裸值；请重新生成承接候选')
            if fact.get('relative_to_role_id') is not None and fact['relative_to_role_id'] not in allowed:
                raise ValueError('相对位置关联人物身份无效')
            val, quote = fact['value'], fact['source_quote']
            from .evidence import source_quote
            recovered=source_quote(source_text,quote)
            if recovered is not None:fact['source_quote']=quote=recovered
            if not isinstance(quote, str):
                raise ValueError('承接事实依据必须为来源原文')
            if val is None:
                if quote:
                    raise ValueError('未知承接状态不得附带已确认依据')
                continue
            valid_value = type(val) is bool if name == 'present' else isinstance(val, str) and bool(val.strip())
            if not valid_value or not quote.strip() or quote not in source_text:
                raise ValueError('承接事实缺少有效来源原文依据：' + name)
    return copy.deepcopy(value)


def planning_constraints(constraints, prompt='', manifest=None, jobs=None, target_uid=None):
    """Only versioned state bound to this draft can constrain replanning."""
    from ..editor_draft import fingerprint
    result = copy.deepcopy(constraints or {})
    opening = result.get('opening_continuity')
    if not isinstance(opening, dict):
        return result
    if (not opening.get('enabled') or opening.get('schema_version') != CONTRACT_VERSION
            or opening.get('target_prompt_hash') != fingerprint(prompt)):
        result.pop('opening_continuity', None)
        return result
    if manifest is not None and opening.get('target_binding_hash') != binding_hash(manifest):
        result.pop('opening_continuity',None)
        return result
    if (opening.get('source') or {}).get('prompt_hash'):
        from .source import validate_source_revision
        if jobs is None:
            import json
            from services.context import get_project_dir
            try:jobs=json.loads((get_project_dir()/'video_jobs.json').read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):
                result.pop('opening_continuity',None);return result
        if target_uid is None:
            matches=[s.get('segment_uid') for s in jobs.get('segments',[]) if
                     (s.get('h3_editor_constraints') or {}).get('opening_continuity')==opening]
            target_uid=matches[0] if len(matches)==1 else None
        try:
            if not target_uid:raise ValueError('无法定位承接目标')
            target=next((s for s in jobs.get('segments',[]) if s.get('segment_uid')==target_uid),{})
            if (target.get('h3_continuity') or {}).get('status')=='needs_analysis': raise ValueError('场景状态待更新')
            validate_source_revision(jobs,target_uid,result)
        except ValueError:result.pop('opening_continuity',None)
    return result


def finalize_constraints(constraints, submitted_prompt, saved_prompt, manifest=None):
    """An edited candidate invalidates its old facts; never overwrite the user's edit."""
    from ..editor_draft import fingerprint
    result = copy.deepcopy(constraints or {})
    opening = result.get('opening_continuity')
    if isinstance(opening, dict) and opening.get('schema_version') == CONTRACT_VERSION:
        if opening.get('target_prompt_hash') != fingerprint(submitted_prompt):
            opening.update(enabled=False, status='target_changed')
        else:
            opening['target_prompt_hash'] = fingerprint(saved_prompt)
        if manifest is not None and opening.get('target_binding_hash') != binding_hash(manifest):
            opening.update(enabled=False,status='references_changed')
    return result


def initial_from_beat(beat, segment):
    """Use explicit Beat/shot facts; never infer poses from free text or images."""
    from ..contracts import H3_REF2VA_MODEL_ID
    manifest=(segment.get('generation_config') or {}).get('asset_manifest',[])
    registered=character_keys(manifest)
    plan=(beat.get('model_hints') or {}).get(H3_REF2VA_MODEL_ID) or segment.get('h3_prompt_plan') or {}
    shots=plan.get('shots') or []
    opening=shots[0] if shots else {}
    roles=set(beat.get('visible_roles') or [])
    visible=set(opening.get('visible_roles') or [])
    roles.update(visible)
    units=beat.get('dialogue_units') or []
    for unit in units:
        if isinstance(unit,dict) and unit.get('scope') in ('onscreen','offscreen','画内','画外','diegetic'):
            if unit.get('speaker'):roles.add(unit['speaker'])
    explicit=beat.get('initial_state') or plan.get('initial_state') or {}
    roles.update(explicit)
    world={role:{'position':{'value':None},'posture':{'value':None},
                 'present':{'value':True if role in visible else None}} for role in sorted(roles&registered)}
    for role,facts in model_world(explicit).items():
        if role in world:world[role].update(facts)
    return world
