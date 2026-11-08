"""Snapshot based content edits, sharing the existing two-file transaction."""
import copy
import hashlib
import json
from .contracts import H3GenerationConfig
from .prompt_reconciler import H3PromptReconciler
from .shot_editor import inspect_prompt, dialogue_units
from .speech_contract import speech_signature


class EditorConflictError(ValueError):
    def __init__(self, message, code, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def reference_revision(segment):
    cfg = segment.get('generation_config') or {}
    return fingerprint({'manifest': H3GenerationConfig.from_mapping(cfg).to_dict()['asset_manifest'],
                        'prompt': cfg.get('prompt_override') or segment.get('compiled_prompt_snapshot')
                        or (segment.get('job') or {}).get('final_prompt') or (segment.get('job') or {}).get('prompt') or ''})


def snapshot(segment):
    cfg=segment.get('generation_config') or {}
    return {'scene_context_id':segment.get('scene_context_id'),'shot_uids':copy.deepcopy((segment.get('h3_shot_editor') or {}).get('shot_uids',[])), 'segment_uid':segment.get('segment_uid'), 'reference_revision':reference_revision(segment), 'prompt':cfg.get('prompt_override') or segment.get('compiled_prompt_snapshot') or (segment.get('job') or {}).get('final_prompt') or '',
            'asset_manifest':copy.deepcopy(cfg.get('asset_manifest') or []),
            'source_excerpt':segment.get('source_excerpt') or '',
            'duration_sec':cfg.get('duration_sec') or (segment.get('job') or {}).get('duration_sec'),
            'editor_constraints':copy.deepcopy(segment.get('h3_editor_constraints') or {}),
            'scene_framing':copy.deepcopy(segment.get('h3_scene_framing'))}


def saved_baseline(jobs, segment):
    """Return the content and scene versions belonging to one saved snapshot."""
    from .continuity.readiness import read_ready_context
    from .continuity.inheritance import state_status
    draft = snapshot(segment)
    result = {'draft': draft, 'base_revision': fingerprint(draft), 'state_context': None,
              'state_status': state_status(jobs, segment['segment_uid'])}
    from .optimization_history import status
    result['optimization_status'] = status(segment)
    try:
        result['state_context'] = read_ready_context(jobs, segment['segment_uid'], include_target=True, target_source='draft')
    except (ValueError, KeyError, TypeError) as exc:
        result['state_message'] = str(exc)
    # The incoming world is still readable when this segment has no saved end
    # state, or its own record has expired. Never present its old input as live.
    try:
        result['state_input_context'] = read_ready_context(jobs, segment['segment_uid'], target_source='draft')
    except (ValueError, KeyError, TypeError) as exc:
        result['state_input_context'] = None
        result['state_input_message'] = str(exc)
    # Framing and world-state readiness are separate contracts. A saved
    # no-change declaration does not prove that every person was considered.
    if segment.get('scene_context_id'):
        result['framing_status'] = {'ready': False, 'message': '尚未核对全员取景，请根据人物状态优化提示词'}
        if segment.get('h3_scene_framing') and result['state_input_context'] is None:
            result['framing_status']['message'] = result.get('state_input_message') or '场景输入状态尚未就绪'
        elif segment.get('h3_scene_framing'):
            try:
                from .continuity.framing import validate_framing_record
                record = validate_framing_record(segment, result['state_input_context'])
                result['framing_status'] = {'ready': True, 'message': '逐镜人物与构图已核对',
                                            'shots': copy.deepcopy(record.get('shots') or [])}
            except (ValueError, KeyError, TypeError) as exc:
                result['framing_status'] = {'ready': False, 'message': str(exc)}
    return result


def _save_scene_framing(jobs, segment, draft, *, previous_framing=None):
    """Accept only a record bound to the content and incoming world being saved."""
    if not draft.get('scene_framing'):
        return
    if (draft['scene_framing'] == segment.get('h3_scene_framing')
            or draft['scene_framing'] == previous_framing):
        # Ordinary edit operations carry the saved snapshot through unchanged.
        # Preserve that historical record; its source signature makes it stale
        # naturally if the user changed camera, speech or reference content.
        return
    from .continuity.framing import validate_framing_record
    from .continuity.readiness import read_ready_context
    candidate = copy.deepcopy(segment)
    candidate['h3_scene_framing'] = copy.deepcopy(draft['scene_framing'])
    context = read_ready_context(jobs, segment['segment_uid'], target_source='draft')
    validate_framing_record(candidate, context)
    segment['h3_scene_framing'] = candidate['h3_scene_framing']


def locate(jobs, uid):
    matches=[s for s in jobs.get('segments',[]) if s.get('segment_uid')==uid]
    if len(matches)!=1: raise ValueError('目标分段已变化，请重新打开编辑器')
    return matches[0]


def validate_candidate(base, candidate, *, allow_background_visibility=False):
    if candidate.get("composition_patch"):
        from .continuity.composition import validate_spatial_edit
        validate_spatial_edit(base,candidate)
    from .prompt_retimer import _validate_timeline
    _validate_timeline(candidate['prompt'], float(base['duration_sec']))
    docs=[]
    for draft in (base,candidate):
        inspected=inspect_prompt(draft['prompt'],'editor',draft['asset_manifest'])
        if not inspected['verified']: raise ValueError('当前分镜结构无法可靠解析：'+'；'.join(inspected['issues']))
        docs.append(inspected['document'])
    from .asset_binder import H3AssetBinder
    from .prompt_compiler import H3PromptCompiler
    import re
    bindings=H3AssetBinder().bind({'asset_manifest':candidate['asset_manifest']})
    allowed={tag.casefold() for tag,_,_ in bindings.prompt_references}
    allowed.update(e['subject_tag'].casefold() for e in H3PromptCompiler()._subject_reference_entries(bindings))
    from .dialogue_text import transform_outside_dialogue
    unknown=[]
    transform_outside_dialogue(candidate['prompt'],lambda t: unknown.extend(tag for tag in re.findall(r'<(?:Subject|Picture|Audio|Video)\s+\d+>',t,re.I) if tag.casefold() not in allowed) or t)
    if unknown: raise ValueError('参考绑定不存在：'+'、'.join(unknown))
    a,b=docs
    if len(a['shots'])!=len(b['shots']): raise ValueError('此操作不能增删 Shot')
    for shot_index,(old,new) in enumerate(zip(a['shots'],b['shots']),1):
        if (old['original_start_cs'],old['original_end_cs'])!=(new['original_start_cs'],new['original_end_cs']): raise ValueError('此操作不能修改时间轴')
        before=[speech_signature(u) for u in dialogue_units(old)]
        after=[speech_signature(u) for u in dialogue_units(new)]
        if allow_background_visibility:
            for sequence in (before,after):
                for unit in sequence: unit.pop('visible_roles',None)
        if before != after:
            prefix=f'此操作不能改变对白、说话人或声音阶段：Shot {shot_index}'
            if len(before)!=len(after):
                raise ValueError(f'{prefix} 的对白数量由 {len(before)} 变为 {len(after)}')
            labels={'speaker':'说话人','scope':'画内外发声方式','line':'对白原文',
                    'continues_previous':'跨镜续说','delivery_spans':'声音阶段',
                    'source_type':'声音渠道','voice_asset_id':'音色绑定',
                    'listeners':'听者状态','visible_roles':'可见人物'}
            for line_index,(left,right) in enumerate(zip(before,after),1):
                for key,label in labels.items():
                    if (key in left)!=(key in right) or left.get(key)!=right.get(key):
                        values=[json.dumps(row[key],ensure_ascii=False) if key in row else '未设置' for row in (left,right)]
                        raise ValueError(f'{prefix} 第 {line_index} 句的{label}发生变化（原稿：{values[0]}；当前稿：{values[1]}）')
    return b


def preview(payload):
    from workflow.video_merge import load_video_jobs
    segment=locate(load_video_jobs(),payload['segment_uid'])
    base=copy.deepcopy(payload['draft'])
    candidate=copy.deepcopy(base)
    if payload['operation']!='remove_reference': raise ValueError('未知编辑操作')
    selected=next((a for a in base['asset_manifest'] if a.get('asset_id')==payload.get('asset_id')),None)
    if selected is None: raise ValueError('参考素材已变化')
    from .reference_edits import remove_character_voice
    cleaned,manifest,is_voice=remove_character_voice(base['prompt'],base['asset_manifest'],selected)
    candidate['asset_manifest']=[a for a in manifest if a.get('asset_id')!=payload['asset_id']]
    candidate['prompt']=H3PromptReconciler().reconcile(cleaned,H3GenerationConfig.from_mapping(base),H3GenerationConfig.from_mapping(candidate))
    if is_voice and not any(a.get('type') in ('audio','video') for a in candidate['asset_manifest']):
        from .dialogue_text import transform_outside_dialogue
        candidate['prompt']=transform_outside_dialogue(candidate['prompt'],lambda text:text.replace('[reference generation + audio reference]','[reference generation]'))
    # Removing an optional voice deliberately drops voice_asset_id. The reference
    # rebind above is deterministic; all subsequent edits retain speech validation.
    operation_base=copy.deepcopy(candidate) if is_voice else base
    document=validate_candidate(operation_base,candidate)
    return {'draft':candidate,'document':document,'base_revision':payload.get('base_revision') or fingerprint(snapshot(segment)),
            'draft_hash':fingerprint(base),'operation_base':operation_base,'changes':[
                '移除本段参考音色并禁用自动带入；保留人物、对白和时间轴' if is_voice else '移除参考、清理托管章节并同步编号；对白与时间轴保持']}


def commit(payload):
    from workflow.video_merge import load_video_jobs,video_jobs_lock
    from services.context import get_project_dir,assert_current_runtime_writable
    from .segment_content_sync import sync_segment_content,write_content_pair
    from .shot_editor import restore_dialogue_ids
    from generation.deferred_regeneration import defer_regeneration_for_active_submission
    assert_current_runtime_writable('保存完整编辑稿')
    with video_jobs_lock():
        jobs=load_video_jobs(); segment=locate(jobs,payload['segment_uid'])
        current_revision = fingerprint(snapshot(segment))
        if current_revision != payload['base_revision']:
            raise EditorConflictError('后台内容已变化，草稿保留，请重新读取后合并', 'CONTENT_CONFLICT',
                                      {'expected_revision': payload['base_revision'], 'current_revision': current_revision})
        from .asset_binder import H3AssetBinder
        from .prompt_compiler import H3PromptCompiler
        draft=copy.deepcopy(payload['draft'])
        manual_edit = payload.get('manual_edit') is True
        if manual_edit:
            if draft.get('scene_context_id') != segment.get('scene_context_id'):
                raise ValueError('请通过场景归属控件修改场景，再保存正文')
            # A human-edited screenplay owns new content, not the old model's
            # patch permissions, plan or inferred ending state.
            for key in ('scene_patch', 'state_guard', 'composition_patch', 'state_only',
                        'state_preparation', 'operation_contract', 'operation_result',
                        'optimization_attempt_id', 'plan', 'scene_framing'):
                draft.pop(key, None)
            constraints = copy.deepcopy(draft.get('editor_constraints') or {})
            constraints.pop('opening_continuity', None)
            draft['editor_constraints'] = constraints
        if draft.get('scene_patch') and (not draft.get('state_guard') or draft.get('state_preparation')):
            raise ValueError('此候选来自旧的承接流程，请重试当前操作；已有稿件和视频已保留')
        if draft.get('scene_patch'):
            from .continuity.readiness import validate_scene_guard
            validate_scene_guard(jobs,draft['state_guard'])
        if draft.get('scene_patch') and not (draft.get('composition_patch') or draft.get('state_only')):
            raise ValueError('旧全文重写候选已停用，请重新生成构图候选')
        submitted_prompt=draft['prompt']
        from .continuity.source import validate_source_revision
        from .continuity.state import finalize_constraints
        validate_source_revision(jobs,payload['segment_uid'],draft.get('editor_constraints'))
        operation_base=copy.deepcopy(draft if manual_edit else payload.get('operation_base') or snapshot(segment))
        if draft['asset_manifest']!=operation_base['asset_manifest'] and not draft.get('composition_patch'):
            old_config=H3GenerationConfig.from_mapping(operation_base)
            new_config=H3GenerationConfig.from_mapping(draft)
            draft['prompt']=H3PromptReconciler().reconcile(draft['prompt'],old_config,new_config)
            operation_base['prompt']=H3PromptReconciler().reconcile(operation_base['prompt'],old_config,new_config)
            operation_base['asset_manifest']=copy.deepcopy(draft['asset_manifest'])
        if not (draft.get('composition_patch') or draft.get('state_only')):
            draft['prompt']=H3PromptCompiler.compact_reference_sections(draft['prompt'],H3AssetBinder().bind(
                H3GenerationConfig.from_mapping(draft)))
        document=validate_candidate(draft if manual_edit else operation_base,draft,allow_background_visibility=bool(draft.get('composition_patch') or draft.get('plan')))
        root=get_project_dir(); beats=json.loads((root/'beats.json').read_text(encoding='utf-8-sig'))
        matches=[b for b in beats['beats'] if b.get('beat_uid')==segment.get('beat_uid')]
        if len(matches)!=1: raise ValueError('无法唯一定位 Beat')
        current=snapshot(segment)
        if draft.get('state_only') and (submitted_prompt!=current['prompt'] or draft['asset_manifest']!=current['asset_manifest'] or draft.get('source_excerpt','')!=current['source_excerpt'] or draft['duration_sec']!=current['duration_sec']):
            changed = [label for key, label in (('prompt', '正文'), ('asset_manifest', '素材'),
                       ('source_excerpt', '上下文'), ('duration_sec', '时长'))
                       if draft.get(key, '' if key == 'source_excerpt' else None) != current[key]]
            raise ValueError('只确认状态不能修改正文、素材、上下文或时长；检测到变化：'
                             + '、'.join(changed) + '。请先保存当前内容，再重新优化')
        if (draft.get('state_only') and submitted_prompt==current['prompt']
            and draft['asset_manifest']==current['asset_manifest']
            and draft.get('source_excerpt','')==current['source_excerpt']):
            from .continuity.revisions import accept_candidate_state
            accept_candidate_state(jobs,draft['scene_patch'])
            _save_scene_framing(jobs, segment, draft, previous_framing=current.get('scene_framing'))
            from .continuity.ledger import state_before
            confirmed_context=state_before(jobs,segment['segment_uid'])
            from .optimization_history import mark_saved, status as optimization_status
            mark_saved(segment,draft)
            write_content_pair(root,beats=beats,jobs=jobs)
            from .shot_edit_service import _editing_fingerprint
            return {'optimization_status':optimization_status(segment),'editing_fingerprint':_editing_fingerprint(beats,jobs),'draft':snapshot(segment),
                'document':document,'base_revision':fingerprint(snapshot(segment)),
                'state_context':confirmed_context,'scene_initial_state':jobs['h3_scene_continuity']['contexts'][segment['scene_context_id']]['initial_state'],
                'operation_result':draft.get('operation_result'),
                'scene_record':segment['h3_continuity'],'scene_record_message':'场景人物记录已更新，正文与视频状态保持不变。'}
        saved=segment.get('h3_shot_editor') or {}
        spatial=draft.get('composition_patch')
        shot_uids=([shot['shot_uid'] for shot in spatial['shots']] if spatial else saved.get('shot_uids') or [])
        for shot,uid in zip(document['shots'],shot_uids):
            shot['uid']=uid
            ids=(saved.get('dialogue_uids') or {}).get(uid,[])
            if len(ids)==len(shot['dialogues']): restore_dialogue_ids(shot,ids)
        cfg=copy.deepcopy(segment.get('generation_config') or {})
        if draft['asset_manifest'] != cfg.get('asset_manifest', []):
            cfg['reference_selection_source'] = 'manual'
        cfg.update(prompt_override=draft['prompt'],asset_manifest=draft['asset_manifest'])
        if manual_edit:
            cfg['duration_sec'] = draft['duration_sec']
        from datetime import datetime,timezone
        cfg['prompt_version_info']={'version':int((cfg.get('prompt_version_info') or {}).get('version') or 0)+1,'source':'manual_edit' if manual_edit else 'editor_operation','provider':'人工编辑' if manual_edit else '编辑稿操作','updated_at':datetime.now(timezone.utc).isoformat()}
        from .continuity.revisions import revision
        proposed=(draft.get('scene_patch') or {}).get('record',{})
        plan_matches=proposed.get('source_revision')==revision({'segment_uid':segment['segment_uid'],'generation_config':cfg})
        beat,updated,diagnostics=sync_segment_content(matches[0],segment,prompt=draft['prompt'],generation_config=cfg,
            plan=draft.get('plan') if plan_matches else None,source_excerpt=draft.get('source_excerpt',''),inspection={'verified':True,'issues':[],'document':document})
        updated['h3_editor_constraints']=finalize_constraints(draft.get('editor_constraints'),submitted_prompt,draft['prompt'],draft['asset_manifest'])
        if manual_edit and updated.get('scene_context_id'):
            updated['h3_state_mode'] = 'unset'
            if updated.get('h3_continuity'):
                updated['h3_continuity']['state_pending'] = True
        beat['h3_editor_constraints']=copy.deepcopy(updated['h3_editor_constraints'])
        updated.setdefault('job',{}).update(prompt=draft['prompt'],final_prompt=draft['prompt'],director_prompt=draft['prompt'],asset_manifest=draft['asset_manifest'])
        if not defer_regeneration_for_active_submission(updated,reason='prompt changed'):
            updated.update(status='needs_regenerate',submitted=False)
            updated.pop('workflow_path',None); updated.pop('workflow',None)
        matches[0].clear(); matches[0].update(beat)
        segment.clear(); segment.update(updated)
        if draft.get('scene_patch'):
            from .continuity.revisions import accept_candidate_state
            accept_candidate_state(jobs,draft['scene_patch'])
        _save_scene_framing(jobs, segment, draft, previous_framing=current.get('scene_framing'))
        jobs.pop('final_video_path',None)
        from .continuity.ledger import state_before
        if manual_edit:
            saved_context=saved_baseline(jobs,segment)['state_input_context']
        else:
            saved_context=state_before(jobs,segment['segment_uid'])
        from .optimization_history import mark_saved, status as optimization_status
        mark_saved(segment,draft)
        write_content_pair(root,beats=beats,jobs=jobs)
        from .shot_edit_service import _editing_fingerprint
        from .continuity.ledger import state_before
        return {'manual_edit':manual_edit,'optimization_status':optimization_status(segment),'operation_result':draft.get('operation_result'),'scene_initial_state':((jobs.get('h3_scene_continuity') or {}).get('contexts',{}).get(segment.get('scene_context_id')) or {}).get('initial_state'),'state_context':saved_context,'editing_fingerprint':_editing_fingerprint(beats,jobs),'draft':snapshot(segment),'document':document,'base_revision':fingerprint(snapshot(segment))}
