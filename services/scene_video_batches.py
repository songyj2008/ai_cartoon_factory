"""Durable scene preparation; video submission consumes the saved final draft."""
import copy
import time
import uuid
from pathlib import Path

from services import context, text_jobs as tasks
from generation.pipelines.minimax_h3.editor_draft import snapshot, fingerprint
from generation.pipelines.minimax_h3.continuity.display import scene_display_contexts as _display_scenes, project_role_names

PHASES = {'initial': '建立场景初态', 'optimize': '优化提示词', 'timing': '规划并保存时长',
          'state': '登记结束状态', 'submit': '提交视频'}

def _catalog_revision(data):
    # Queue progress and generated history timestamps do not change grouping inputs.
    return fingerprint({'scenes': data.get('h3_scene_continuity'), 'rows': [
        {'draft': snapshot(row), 'scene': row.get('scene_context_id'), 'mode': row.get('h3_state_mode')}
        for row in data.get('segments', [])]})


def catalog():
    from workflow.video_merge import load_video_jobs
    from generation.pipelines.minimax_h3.continuity.calibration import scene_members
    data = load_video_jobs()
    with tasks.database() as db:
        history = [tasks._decode(r) for r in db.execute('SELECT * FROM operations ORDER BY created DESC')]
    latest = next((t for t in history if t['kind']=='scene_video_batch' and Path(t['source']).resolve()==context.get_project_dir().resolve()), None)
    saved = {r['uid']:r for r in latest['args'][0]['rows']} if latest and latest['state']!='applied' else {}
    registered_names = project_role_names()
    roles = {str((a.get('metadata') or {}).get('role_id')): registered_names.get(str((a.get('metadata') or {}).get('role_id'))) or a.get('role') or a.get('label')
             for r in data.get('segments',[]) for a in (r.get('generation_config') or {}).get('asset_manifest',[])
             if (a.get('metadata') or {}).get('role_id')}
    return {'runtime': str(context.get_project_dir().resolve()),
            'batch_id': latest['id'] if latest else None, 'roles': roles,
            'revision': _catalog_revision(data),
            'scenes': _display_scenes(data),
            'members': scene_members(data),
            'rows': [{**{k: row.get(k) for k in ('segment_uid', 'segment_index', 'part_id', 'title',
                       'scene_context_id', 'h3_state_mode', 'status', 'active_render_id')},
                      'prompt': snapshot(row)['prompt'], 'duration_sec': snapshot(row)['duration_sec'],
                      'planned_mode': saved.get(row['segment_uid'],{}).get('mode'),
                      'selected': saved.get(row['segment_uid'],{}).get('selected', row.get('status')!='success')}
                     for row in data.get('segments', [])]}


def _stamp(data, uid):
    from generation.pipelines.minimax_h3.continuity.readiness import scene_state_guard
    row = next(s for s in data['segments'] if s['segment_uid'] == uid)
    guard = scene_state_guard(data, uid) if row.get('scene_context_id') else None
    return fingerprint({'draft': snapshot(row), 'guard': guard,
                        'framing': row.get('h3_scene_framing')})


def start(plan):
    from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock
    from generation.pipelines.minimax_h3.contracts import H3_REF2VA_MODEL_ID
    from generation.job_schema import resolved_model_id
    context.assert_current_runtime_writable('保存场景分组并生成视频')
    plan = copy.deepcopy(plan)
    if type(plan.get('timing_api', False)) is not bool:
        raise ValueError('规划时长 API 选项必须为布尔值')
    from services.web_llm_bridge import resolve_page_providers
    resolve_page_providers(plan.get('providers') if plan.get('providers') is not None else ['inherit'])
    from generation.channels import get_channel
    if not get_channel(plan.get('backend', 'runninghub')).uses_workflow:
        raise ValueError('场景批量生成当前需要选择工作流视频渠道')
    with tasks._LOCK, video_jobs_lock():
        data = load_video_jobs()
        if _catalog_revision(data) != plan.get('revision'):
            raise ValueError('分段或场景已变化，请刷新分组页后再提交')
        rows = plan.get('rows')
        live = {s['segment_uid']: s for s in data.get('segments', [])}
        if not isinstance(rows, list) or len(rows) != len(live) or {r.get('uid') for r in rows} != set(live):
            raise ValueError('请列出全部分段且每段仅出现一次')
        if not any(r.get('selected') is True for r in rows):
            raise ValueError('请勾选至少一段视频')
        with tasks.database() as db:
            active = [tasks._decode(r) for r in db.execute(
                "SELECT * FROM operations WHERE state IN ('preparing','queued','running','waiting_children','needs_attention')")]
        if any(t['kind'] == 'scene_video_batch' and Path(t['source']).resolve() == context.get_project_dir().resolve() for t in active):
            raise ValueError('当前项目已有场景批次，请先查看或继续原批次')
        contexts = data.setdefault('h3_scene_continuity', {'schema_version': 3, 'contexts': {}})['contexts']
        created = {}
        for item in rows:
            row = live[item['uid']]
            if not item.get('selected'):
                # Unchecked rows are read-only continuity inputs, never work items.
                item['scene'] = row.get('scene_context_id') or ''
                item['mode'] = 'inherit' if row.get('h3_state_mode') == 'inherit' else 'extract'
                continue
            if item.get('mode') not in ('inherit', 'extract') or not isinstance(item.get('scene'), str):
                raise ValueError('请选择每段的场景和结束状态处理方式')
            key = item['scene']
            if key.startswith('new:'):
                if key not in created:
                    name = str((plan.get('new_scenes') or {}).get(key) or '').strip()
                    if not name: raise ValueError('新场景名称不能为空')
                    created[key] = 'scene_' + uuid.uuid4().hex
                    contexts[created[key]] = {'name': name, 'initial_pending': True,
                                              'initial_state': {}, 'config_revision': uuid.uuid4().hex}
                key = created[key]
            if key and key not in contexts: raise ValueError('所选场景不存在')
            if resolved_model_id(row, data) != H3_REF2VA_MODEL_ID:
                raise ValueError('场景批量准备目前支持 H3 分段，请先统一分段模型')
            from workflow.runninghub_queue import has_active_submission
            if has_active_submission(row) and (item.get('selected') or row.get('scene_context_id') != (key or None)):
                raise ValueError('所选分段或待换组分段正在生成视频，请完成后重试')
            if row.get('scene_context_id') != (key or None):
                row.pop('h3_continuity', None)
                row['h3_state_mode'] = 'unset'
            row['scene_context_id'] = key or None
            row['h3_continuity_source'] = 'draft'
            item['scene'] = key
        # Empty initial states get an explicit unknown roster, not guessed facts.
        from generation.pipelines.minimax_h3.continuity.state import character_keys
        selected_scenes = {item['scene'] for item in rows if item.get('selected') and item['scene']}
        for key in selected_scenes:
            scene = contexts[key]
            if not scene.get('initial_state'):
                roles = set().union(*(character_keys((r.get('generation_config') or {}).get('asset_manifest', []))
                                      for r in live.values() if r.get('scene_context_id') == key))
                scene['initial_state'] = {role: {f: {'value': None} for f in ('position', 'posture', 'present')} for role in roles}
                scene['initial_pending'] = True
        save_video_jobs(data)
        data = load_video_jobs()
        order = {s['segment_uid']: i for i, s in enumerate(data['segments'])}
        rows.sort(key=lambda r: order[r['uid']])
        plan['baseline'] = {s['segment_uid']: fingerprint(snapshot(s)) for s in data['segments']}
        task = tasks.submit('scene_video_batch', [plan], start=False, hold=True,
                            page_providers=plan.get('providers'))
        tasks.update(task['id'], 'waiting_children', scene_batch=True, stages={}, attempts={},
                     progress_rows={}, phase='准备连续场景', message='分组已保存，后台自动准备并提交视频')
    tasks.ensure_worker(); tasks._WAKE.set()
    return status(task['id'])


def _row_timings(parent, progress, *, now=None):
    """Project durable child execution clocks onto each segment's progress."""
    now = time.time() if now is None else now
    progress = copy.deepcopy(progress or {})
    identifiers = list(dict.fromkeys([*(parent.get('stage_tasks') or []),
                                      *(parent.get('stages') or {}).values()]))
    children = {}
    with tasks.database() as db:
        for offset in range(0, len(identifiers), 200):
            part = identifiers[offset:offset + 200]
            records = db.execute('SELECT * FROM operations WHERE id IN (' + ','.join('?' for _ in part) + ')', part)
            children.update((row['id'], tasks._decode(row)) for row in records)
    for row in parent['args'][0]['rows']:
        uid = row['uid']
        if not row['selected']:
            progress[uid] = {'phase': '未勾选，已跳过', 'timing_state': 'skipped',
                             'started_at': None, 'finished_at': None, 'elapsed_seconds': None}
            continue
        state = progress.setdefault(uid, {'next': 0, 'phase': '等待准备'})
        owned = [child for child in children.values()
                 if child.get('batch_stage', '').rsplit(':', 1)[0] == uid]
        starts = [c['timings']['started_at'] for c in owned if (c.get('timings') or {}).get('started_at')]
        if state.get('started_at'):
            starts.append(state['started_at'])
        started = min(starts) if starts else None
        elapsed = 0
        for child in owned:
            clock = child.get('timings') or {}
            elapsed += float((clock.get('state_seconds') or {}).get('running', 0))
            if child['state'] == 'running':
                began = clock.get('state_started_at') or clock.get('last_started_at') or clock.get('started_at')
                if began is not None:
                    elapsed += max(0, now - began)
        final = children.get((parent.get('stages') or {}).get(uid + ':submit'))
        latest = max(owned, key=lambda c: c.get('created', 0), default=None)
        finished = None
        timing_state = 'waiting' if started is None else 'queued'
        if final and final['state'] == 'applied':
            timing_state = 'complete'
            finished = (final.get('timings') or {}).get('finished_at')
        elif latest:
            timing_state = latest['state']
            apply_failed = timing_state == 'ready' and bool(latest.get('apply_error'))
            if apply_failed:
                timing_state = 'error'
            if timing_state in ('error', 'cancelled', 'needs_attention'):
                clock = latest.get('timings') or {}
                finished = (latest.get('apply_attempted_at') if apply_failed else None) or clock.get('finished_at') or clock.get('state_started_at')
        elif state.get('finished_at'):
            finished, timing_state = state['finished_at'], state.get('timing_state', 'complete')
        state.update(started_at=started, finished_at=finished, timing_state=timing_state,
                     elapsed_seconds=round(max(float(state.get('elapsed_seconds') or 0), elapsed), 2)
                     if started is not None else None)
    return progress


def status(identifier):
    item = tasks.get(identifier)
    if item['kind'] != 'scene_video_batch' or Path(item['source']).resolve() != context.get_project_dir().resolve():
        raise ValueError('批次不属于当前项目')
    from workflow.video_merge import load_video_jobs
    return {**{k: item.get(k) for k in ('id', 'state', 'phase', 'message', 'progress_rows', 'failures')},
            'progress_rows': _row_timings(item, item.get('progress_rows')),
            'scenes': _display_scenes(load_video_jobs())}


def resume(identifier, modes=None):
    context.assert_current_runtime_writable('继续场景批次')
    item = tasks.get(identifier); status(identifier)
    if item['state'] != 'error': return status(identifier)
    args = copy.deepcopy(item['args'])
    if modes:
        by_uid={r['uid']:r for r in args[0]['rows']}
        if set(modes)!=set(by_uid) or any(v not in ('inherit','extract') for v in modes.values()):
            raise ValueError('状态处理选项无效，请刷新列表')
        for uid,mode in modes.items():
            if not by_uid[uid]['selected']:
                continue
            if mode!=by_uid[uid]['mode']:
                if (item.get('progress_rows',{}).get(uid) or {}).get('next',0)>3:
                    raise ValueError('已完成状态登记的分段不能在续跑时改模式，请建立新批次')
                by_uid[uid]['mode']=mode
    stages, attempts = dict(item.get('stages') or {}), dict(item.get('attempts') or {})
    for key, child_id in list(stages.items()):
        child = tasks.get(child_id)
        if child['state'] in ('error', 'cancelled') or (child['state'] == 'ready' and child.get('apply_error')):
            stages.pop(key); attempts[key] = attempts.get(key, 0) + 1
    tasks.update(identifier, 'waiting_children', args=args, stages=stages, attempts=attempts, failures={}, message='继续未完成步骤')
    tasks.ensure_worker(); tasks._WAKE.set()
    return status(identifier)


def _child(parent, row, stage, data):
    if not row['selected']:
        raise ValueError('未勾选分段不能创建批次步骤')
    uid = row['uid']; key = uid + ':' + stage
    if key in parent.get('stages', {}): return tasks.get(parent['stages'][key])
    current = next(s for s in data['segments'] if s['segment_uid'] == uid)
    attempt = (parent.get('attempts') or {}).get(key, 0)
    args = {'uid': uid, 'stage': stage, 'mode': row['mode'], 'stamp': None if stage == 'initial' else _stamp(data, uid),
            'backend': parent['args'][0].get('backend', 'runninghub')}
    kind = 'scene_video_step'
    if stage == 'timing':
        draft = snapshot(current)
        kind = 'source_replan'
        args = [current['segment_index'], draft['source_excerpt'],
                {'timing_only': True, 'duration_sec': draft['duration_sec'], 'editor_prompt': draft['prompt'],
                 'asset_manifest': draft['asset_manifest'], 'providers': parent['args'][0].get('providers'),
                 'timing_api': parent['args'][0].get('timing_api', False)}]
    else: args = [args]
    child = tasks.submit(kind, args, start=False, batch={'id': parent['id'], 'stage_key': key + ':' + str(attempt),
                                                       'stage': key, 'index': current['segment_index'], 'routes': parent['routes']})
    tasks.update(child['id'], title=(current.get('part_id') or uid)+' · '+PHASES[stage])
    tasks.update(parent['id'], stages={**parent.get('stages', {}), key: child['id']},
                 stage_tasks=list(dict.fromkeys([*parent.get('stage_tasks', []), child['id']])))
    return child


def reconcile_pending():
    with tasks.database() as db:
        parents = [tasks._decode(r) for r in db.execute("SELECT * FROM operations WHERE state='waiting_children'")]
    for parent in parents:
        if parent['kind'] != 'scene_video_batch': continue
        try:
            with tasks._LOCK, context.pin_runtime((Path(parent['source']), parent['project'], parent['episode'])):
                try: parent = tasks.get(parent['id'])
                except ValueError: continue
                if parent['state'] == 'waiting_children': _reconcile(parent)
        except Exception as exc:
            try: tasks.update(parent['id'], 'error', message=str(exc), phase='批次需要处理')
            except ValueError: pass


def _reconcile(parent):
    from workflow.video_merge import load_video_jobs
    plan = parent['args'][0]; groups = {}
    for row in plan['rows']:
        if row['selected']:
            groups.setdefault(row['scene'] or row['uid'], []).append(row)
    progress = copy.deepcopy(parent.get('progress_rows') or {}); failures = {}; pending = False
    for group, rows in groups.items():
        for row in rows:
            uid = row['uid']; data = load_video_jobs()
            state = progress.setdefault(uid, {'next': 0, 'phase': '等待准备'})
            stages = (['initial', 'optimize', 'timing', 'state'] if row['scene'] else ['timing']) + ['submit']
            if state['next'] >= len(stages): continue
            stage = stages[state['next']]
            if stage == 'initial':
                scene = data['h3_scene_continuity']['contexts'][row['scene']]
                if not scene.get('initial_pending') and isinstance(scene.get('initial_state'), dict):
                    state.update(next=state['next']+1); pending=True; break
                members = [s for s in data['segments'] if s.get('scene_context_id') == row['scene']]
                if members[0]['segment_uid'] != uid:
                    failures[group] = '场景初态尚未建立；未勾选的场景首段不会自动准备，请先校准初态'
                    state['phase'] = failures[group]
                    break
            key = uid + ':' + stage
            child_id = (parent.get('stages') or {}).get(key)
            if child_id:
                child = tasks.get(child_id)
                if child['state'] == 'applied':
                    # The child records a guarded post-write stamp; never bless external edits.
                    expected = (child.get('result') or {}).get('scene_batch_stamp')
                    if expected is None: raise ValueError('步骤缺少提交后的版本记录，不能跳过校验继续')
                    state.update(next=state['next']+1, stamp=expected, phase=PHASES[stage]+' 已完成')
                    pending=True
                    if stage == 'submit': continue
                    break
                if child['state'] in ('error','cancelled','needs_attention') or (child['state']=='ready' and child.get('apply_error')):
                    failures[group]=child.get('apply_error') or child.get('message') or '子任务失败'
                    state['phase']=PHASES[stage]+' 失败：'+failures[group]
                    if stage == 'submit': continue
                    break
                pending=True;state['phase']=PHASES[stage]+' 执行中'
                if stage == 'submit': continue
                break
            if stage != 'initial':
                try:
                    current_stamp = _stamp(data, uid)
                except ValueError as exc:
                    failures[group] = str(exc) + '；未勾选段不会自动准备'
                    state['phase'] = PHASES[stage] + ' 受阻：' + failures[group]
                    break
                if state.get('stamp') and state['stamp'] != current_stamp:
                    failures[group]='输入或前序状态已变化，请重新打开分组页建立新批次';state['phase']=failures[group];break
                if not state.get('stamp'):
                    current=next(s for s in data['segments'] if s['segment_uid']==uid)
                    if fingerprint(snapshot(current)) != plan['baseline'][uid]:
                        failures[group]='保存稿已变化，请重新分组提交';state['phase']=failures[group];break
            parent = tasks.get(parent['id'])
            _child(parent,row,stage,data);pending=True;state['phase']=PHASES[stage]+' 已排队'
            # A saved ending state is enough for the next scene member.
            # Video dispatch/completion is not a text preparation dependency.
            if stage == 'submit': continue
            break
    progress = _row_timings(tasks.get(parent['id']), progress)
    values={'progress_rows':progress,'failures':failures}
    if pending: tasks.update(parent['id'], **values)
    elif failures: tasks.update(parent['id'],'error',**values,phase='部分场景失败',message='其他场景已保留；处理失败项后可继续')
    else: tasks.update(parent['id'],'applied',**values,phase='视频已提交',message='全部准备完成，所选视频已提交；成片状态请查看视频列表')


def execute_step(item):
    from workflow.video_merge import load_video_jobs, video_jobs_lock
    from generation.pipelines.minimax_h3.continuity.readiness import read_ready_context
    args=item['args'][0];uid=args['uid'];stage=args['stage']
    with context.pin_runtime((Path(item['source']),item['project'],item['episode'])):
        context.assert_current_runtime_writable('准备场景视频')
        tasks.check_cancelled()
        with video_jobs_lock():
            data=load_video_jobs();row=next(s for s in data['segments'] if s['segment_uid']==uid)
            if stage!='initial' and _stamp(data,uid)!=args['stamp']:raise ValueError('执行前输入已变化')
        if stage=='initial' or (stage=='state' and args['mode']=='extract'):
            from services.scene_state_jobs import extraction_context,extraction_source_guard,build_scene_state_patch,commit_scene_state_patch
            ctx=extraction_context(data,uid,'draft')
            req={'segment_uid':uid,'context_id':row['scene_context_id'],'source_kind':'draft',
                 'source_key':extraction_source_guard(data,uid),'input_revision':ctx['input_revision'],'manual':True}
            result=build_scene_state_patch(req);tasks.check_cancelled();commit_scene_state_patch(item,result)
        elif stage=='optimize':
            from generation.pipelines.minimax_h3.continuity.service import build_opening_candidate
            from generation.pipelines.minimax_h3.editor_draft import commit
            draft=snapshot(row)
            candidate=build_opening_candidate({'operation_contract':'scene_state_v3','jobs':data,'segment_uid':uid,
                                                'draft':draft,'base_revision':fingerprint(draft)})
            with tasks._LOCK:
                tasks.check_cancelled()
                commit({'segment_uid':uid,**candidate})
        elif stage=='state':
            from generation.pipelines.minimax_h3.continuity.state import model_world
            from generation.pipelines.minimax_h3.continuity.inheritance import inherit_segment_state
            with tasks._LOCK, video_jobs_lock():
                tasks.check_cancelled()
                live=load_video_jobs();target=next(s for s in live['segments'] if s['segment_uid']==uid)
                ctx=read_ready_context(live,uid,target_source='draft');record=target.get('h3_continuity') or {}
                if record.get('record_kind')!='inherit' and model_world(record.get('end_state',{}))!=model_world(ctx['input_state']):
                    raise ValueError('本段结束状态与开场不同，不能登记无变化；请选择提取状态')
                inherit_segment_state(uid)
        elif stage=='submit':
            from generation.service import get_generation_service
            from generation.job_schema import resolved_model_id
            if row.get('scene_context_id'):
                read_ready_context(data,uid,include_target=True,target_source='draft')
                record=row.get('h3_continuity') or {}
                if record.get('record_status')=='ready_with_issues' and record.get('diagnostics'):
                    raise ValueError('关键开场状态仍有冲突：'+'；'.join(record['diagnostics']))
            tasks.check_cancelled()
            from workflow.video_merge import save_video_jobs
            from workflow.runninghub_queue import has_active_submission
            with video_jobs_lock():
                live=load_video_jobs();target=next(s for s in live['segments'] if s['segment_uid']==uid)
                ticket=target.get('scene_batch_submission') or {}
                if ticket.get('state')=='sending':
                    if ticket.get('stamp')!=args['stamp']:
                        raise tasks.NeedsAttentionError('旧提交记录与当前稿不同，请先核对原平台任务')
                    new_render=any(r.get('render_id') not in ticket.get('renders',[]) for r in target.get('renders',[]))
                    if has_active_submission(target) or new_render:
                        target['scene_batch_submission']={**ticket,'state':'accepted'};save_video_jobs(live)
                        return {'message':'已找到原视频提交，未重复提交','scene_batch_stamp':_stamp(live,uid)}
                    raise tasks.NeedsAttentionError('上次视频提交结果尚未确认，请先检查平台任务；不会自动重发')
                if ticket.get('task')==item['id'] and ticket.get('state')=='accepted':
                    return {'message':'视频已提交','scene_batch_stamp':_stamp(live,uid)}
                target['scene_batch_submission']={'task':item['id'],'state':'sending','stamp':args['stamp'],
                    'renders':[r.get('render_id') for r in target.get('renders',[])]}
                save_video_jobs(live)
            pipeline=get_generation_service()._pipeline_for(resolved_model_id(row,data))
            pipeline.submit_saved({row['segment_index']},wait=False,backend=args['backend'])
            with video_jobs_lock():
                live=load_video_jobs();target=next(s for s in live['segments'] if s['segment_uid']==uid)
                target['scene_batch_submission']={**target['scene_batch_submission'],'state':'accepted'};save_video_jobs(live)
        else:raise ValueError('未知批量步骤')
        latest=load_video_jobs()
        if stage=='state':
            record=next(s for s in latest['segments'] if s['segment_uid']==uid).get('h3_continuity') or {}
            if record.get('record_status')=='ready_with_issues' and record.get('diagnostics'):
                raise ValueError('关键开场状态仍有冲突：'+'；'.join(record['diagnostics']))
        return {'message':PHASES[stage]+' 已完成','scene_batch_stamp':_stamp(latest,uid)}
