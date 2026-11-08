"""Durable novel batches: publish Beats, then prepare and publish one segment at a time."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
from pathlib import Path
import uuid

from services import context


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def input_key(source, args, routes):
    from services.project_bible import project_bible_path
    from services.ui_run_config import load_ui_run_config
    from generation.registry import default_model_id
    from scripts.story_script.generate_beats import BEAT_GROUPING_REVISION
    config = load_ui_run_config()
    return _hash([1, BEAT_GROUPING_REVISION, str(source), args, config.get('default_video_model_id') or default_model_id(),
                  config.get('ui_submit_backend'), context.CONFIG,
                  _read(project_bible_path()), _read(context.ASSET_INDEX_PATH),
                  routes.get('default_providers') or [routes.get('default_provider')], routes.get('overrides')])


def generation_model():
    from services.ui_run_config import load_ui_run_config
    from generation.registry import default_model_id
    return load_ui_run_config().get('default_video_model_id') or default_model_id()


def _set(item, state=None, **values):
    from services import text_jobs as jobs
    if state and state != item['state'] or any(item.get(key) != value for key, value in values.items()):
        return jobs.update(item['id'], state, **values)
    return item


def _stage_input(source, index):
    beats_data = _read(source / 'beats.json', {})
    beats = beats_data.get('beats', [])
    beat = beats[index - 1]
    return _hash([beat, beats_data.get('scene_contexts')])


def validate_stage_target(item):
    if item.get('kind') == 'novel_segment':
        index = int(item['args'][0])
        if _stage_input(Path(item['source']), index) != item.get('batch_input'):
            raise ValueError(f'第 {index} 段依赖的 Beat 或原稿上下文已变化，保留候选，不覆盖新内容')


def stage_applied(child):
    """Advance the input identity with metadata published by the Beats stage itself."""
    if child.get('kind') != 'novel_beats':
        return
    from services import text_jobs as jobs
    parent = jobs.get(child['batch_id'])
    token = jobs.ACTIVE.set(None)
    try:
        with context.pin_runtime((Path(parent['source']), parent['project'], parent['episode'])):
            key = input_key(Path(parent['source']), parent['args'], parent['routes'])
        count = len(_read(Path(parent['source']) / 'beats.json', {}).get('beats') or [])
        _set(parent, batch_key=key, beats_task_applied=True, segment_total=count)
    finally:
        jobs.ACTIVE.reset(token)


def resume_existing(key):
    from services import text_jobs as jobs
    with jobs._LOCK:
        with jobs.database() as db:
            items = [jobs._decode(row) for row in db.execute('SELECT * FROM operations ORDER BY created DESC')]
        parent = next((item for item in items if item.get('batch_version') == 1 and item.get('batch_key') == key), None)
        if parent is None:
            return None
        if parent['state'] in ('waiting_children', 'needs_attention'):
            reconcile(parent['id'])
            parent = jobs.get(parent['id'])
        if parent['state'] in jobs.LIVE or parent['state'] in ('needs_attention', 'ready'):
            return {**parent, 'duplicate': True}
        source = Path(parent['source'])
        records = dict(parent.get('stage_records') or {})
        attempts = dict(parent.get('stage_attempts') or {})
        invalidate = False
        for stage, identifier in list(records.items()):
            try:
                child = jobs.get(identifier)
                valid = child['state'] == 'applied'
                if stage.startswith('segment:') and valid:
                    index = int(stage.split(':')[1])
                    # A repaired predecessor must not erase this successful segment.
                    saved_beats = _read(Path(child['work']) / 'beats.json', {}).get('beats', [])
                    current_beats = _read(source / 'beats.json', {}).get('beats', [])
                    valid = index <= len(saved_beats) and saved_beats[index-1] == current_beats[index-1]
                    segments = _read(source / 'video_jobs.json', {}).get('segments', [])
                    target = next((row for row in segments if row.get('beat_uid') == child.get('segment', {}).get('beat_uid')), None)
                    valid = (valid and target is not None and target.get('planning_state') != 'invalid'
                             and (child.get('result') or {}).get('planning_state') != 'invalid')
                if stage == 'beats':
                    valid = valid and bool(_read(source / 'beats.json', {}).get('beats'))
            except (ValueError, IndexError, KeyError, OSError):
                valid = False
            if not valid:
                invalidate = True
                records.pop(stage, None)
                attempts[stage] = int(attempts.get(stage, 0)) + 1
                try:
                    jobs.update(identifier, superseded=True)
                except ValueError:
                    pass
        if not invalidate and parent['state'] == 'applied':
            return {**parent, 'duplicate': True, 'already_complete': True}
        # Explicit resubmission may revive a cancelled batch, but never an uncertain web send.
        with jobs.database() as db:
            jobs._transition(parent, 'waiting_children', next_phase='继续未完成分段')
            parent.setdefault('timings', {}).pop('finished_at', None)
            parent.update(stage_records=records, stage_attempts=attempts,
                          segment_completed=sum(stage.startswith('segment:') for stage in records),
                          message='已保留成功结果，从未完成部分继续')
            db.execute('UPDATE operations SET state=?,updated=?,payload=? WHERE id=?',
                       ('waiting_children', time.time(), json.dumps(parent, ensure_ascii=False), parent['id']))
        from services.task_notifications import operations
        operations.publish()
        return {**jobs.get(parent['id']), 'resumed': True}


def retry_segment(identifier, index):
    """Explicit retry of exactly one batch stage, retaining all published results."""
    from services import text_jobs as jobs
    with jobs._LOCK:
        context.assert_current_runtime_writable('重新生成 Segment')
        with jobs.database() as db:
            exists = db.execute('SELECT 1 FROM operations WHERE id=?', (identifier,)).fetchone()
        if not exists:
            return _retry_saved_segment(identifier, index)
        parent = jobs.get(identifier)
        if parent.get('batch_version') != 1:
            raise ValueError('不是可续跑的 Beats 批次')
        if Path(parent['source']).resolve() != context.get_project_dir().resolve():
            raise ValueError('项目已切换，请重新打开对应分段')
        count = len(_read(Path(parent['source']) / 'beats.json', {}).get('beats', []))
        if not 1 <= index <= count:
            raise ValueError('Beat 不存在')
        from workflow.runninghub_queue import has_active_submission
        saved_segments = _read(Path(parent['source']) / 'video_jobs.json', {}).get('segments', [])
        target = next((s for s in saved_segments if int(s.get('segment_index', 0)) == index), {})
        if has_active_submission(target):
            raise ValueError('本段视频正在生成，请完成后再重新生成 Segment')
        stage = f'segment:{index}'
        records = dict(parent.get('stage_records') or {})
        old_id = records.get(stage)
        if old_id:
            old = jobs.get(old_id)
            if old['state'] in jobs.LIVE or old['state'] in ('needs_attention', 'ready'):
                raise ValueError('本段任务仍在执行或等待处理，请先查看原任务')
            jobs.update(old_id, superseded=True)
        records.pop(stage, None)
        attempts = dict(parent.get('stage_attempts') or {})
        attempts[stage] = int(attempts.get(stage, 0)) + 1
        timings = dict(parent.get('timings') or {})
        timings.pop('finished_at', None)
        parent = _set(parent, 'waiting_children', stage_records=records, stage_attempts=attempts,
                      timings=timings, phase=f'重新生成 Segment {index}', message='仅重试本段，其他已完成结果保留')
        parent, child = _ensure_stage(parent, stage, index)
        jobs.ensure_worker()
        jobs._WAKE.set()
        return child


def _retry_saved_segment(identifier, index):
    """Recreate one task from saved Beats after its batch history was cleared."""
    from services import text_jobs as jobs
    from workflow.runninghub_queue import has_active_submission

    source = context.get_project_dir().resolve()
    payload = _read(source / 'video_jobs.json', {})
    if not identifier or payload.get('novel_batch_id') != identifier:
        raise ValueError('原批次不存在且与当前项目不匹配，请刷新分段页面')
    beats = _read(source / 'beats.json', {}).get('beats') or []
    if not 1 <= index <= len(beats):
        raise ValueError('Beat 不存在')
    if not isinstance(beats[index - 1], dict) or not beats[index - 1].get('beat_uid'):
        raise ValueError('原 Beat 缺少稳定编号，无法安全恢复单段任务')
    target = next((row for row in payload.get('segments', [])
                   if int(row.get('segment_index', 0)) == index), {})
    if has_active_submission(target):
        raise ValueError('本段视频正在生成，请完成后再重新生成 Segment')
    # A second click must not replay an unresolved browser request, even if
    # the historical batch itself no longer exists.
    with jobs.database() as db:
        active = [jobs._decode(row) for row in db.execute(
            "SELECT * FROM operations WHERE state IN ('preparing','queued','running','waiting_children','applying','ready','needs_attention')")]
    for task in active:
        if (Path(task['source']).resolve() == source and task.get('kind') == 'novel_segment'
                and int(task['args'][0]) == index):
            if task['state'] in ('ready', 'needs_attention'):
                raise ValueError('本段任务仍在执行或等待处理，请先查看原任务')
            return {**task, 'duplicate': True}
    return jobs.submit('novel_segment', [index])


def _ensure_stage(parent, stage, index=0):
    from services import text_jobs as jobs
    attempts = parent.get('stage_attempts') or {}
    key = f"{stage}:attempt:{int(attempts.get(stage, 0))}"
    identifier = str(uuid.uuid5(uuid.UUID(parent['id']), key))
    records = {**(parent.get('stage_records') or {}), stage: identifier}
    parent = _set(parent, stage_records=records,
                  stage_tasks=list(dict.fromkeys([*(parent.get('stage_tasks') or []), identifier])))
    source = Path(parent['source'])
    with context.pin_runtime((source, parent['project'], parent['episode'])):
        child = jobs.submit('novel_beats' if stage == 'beats' else 'novel_segment',
                            parent['args'] if stage == 'beats' else [index],
                            parent.get('editors') if stage == 'beats' else {}, start=False,
                            batch={'id': parent['id'], 'stage_key': key, 'stage': stage, 'index': index,
                                   'routes': parent['routes'],
                                   'checkpoint_work': parent['work'],
                                   'input': _stage_input(source, index) if index else None})
    return jobs.get(parent['id']), child


def _budget_notice(parent):
    report = parent.get('segment_budget') or parent.get('planning_budget') or {}
    if not report.get('outside_budget'):
        return ''
    reason = (parent.get('grouping_review') or {}).get('reason')
    return ('；参考预算提示：' + report['message'] + '，继续按完整内容生成'
            + ('；分组复核：' + reason if reason else ''))


def _observe(parent, child, phase):
    if child['state'] in ('error', 'cancelled'):
        return _set(parent, 'error', phase=phase + '失败', message=child.get('message') or '子任务失败，成功结果已保留')
    if child['state'] == 'needs_attention':
        return _set(parent, 'needs_attention', phase=phase + '需要处理', message='请检查对应子任务的原网页请求；不会自动重发')
    if child['state'] == 'ready' and child.get('apply_error'):
        return _set(parent, 'error', phase=phase + '结果冲突', message=child['apply_error'])
    return _set(parent, 'waiting_children', phase=phase + ('待应用' if child['state'] == 'ready' else '执行中'),
                message=(child.get('apply_error') or '已完成结果实时保存，可离开页面后继续查看') + _budget_notice(parent))


def _finish_replacement(parent, beats):
    """Retire leftover old slots only after every new segment has succeeded."""
    from workflow.video_merge import video_jobs_lock
    from workflow.runninghub_history import retire_segment
    source = Path(parent['source'])
    with video_jobs_lock(source):
        path = source / 'video_jobs.json'
        payload = _read(path, {})
        expected = {beat['beat_uid'] for beat in beats}
        rows = payload.get('segments', [])
        if not expected.issubset({row.get('beat_uid') for row in rows}):
            raise ValueError('新分段尚未全部保存，旧分段已保留')
        obsolete = [row for row in rows if row.get('beat_uid') not in expected]
        if not obsolete:
            return
        for row in obsolete:
            retire_segment(payload, row, '新批次全部生成成功，归档多余旧分段')
        payload['segments'] = [row for row in rows if row.get('beat_uid') in expected]
        from workflow.video_merge import _write_video_jobs_atomic
        _write_video_jobs_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def reconcile(identifier):
    from services import text_jobs as jobs
    with jobs._LOCK:
        parent = jobs.get(identifier)
        if parent['state'] not in ('waiting_children', 'needs_attention'):
            return
        source = Path(parent['source'])
        parent, beats_task = _ensure_stage(parent, 'beats')
        if beats_task['state'] != 'applied':
            _observe(parent, beats_task, '生成 Beats · ')
            return
        data = _read(source / 'beats.json', {})
        beats = data.get('beats') or []
        if not beats:
            _set(parent, 'error', phase='Beats 文件缺失', message='已发布的 Beats 不存在，请重新提交恢复')
            return
        with context.pin_runtime((source, parent['project'], parent['episode'])):
            key = input_key(source, parent['args'], parent['routes'])
            if generation_model() != parent.get('batch_model_id'):
                _set(parent, 'error', phase='模型配置已变化', message='请按新的模型配置重新提交，不混用旧批次')
                return
        if parent.get('beats_task_applied') and key != parent['batch_key']:
            _set(parent, 'error', phase='生成配置已变化', message='项目设定或素材已变化，请重新提交核对输入')
            return
        parent = _set(parent, beats_task_applied=True, segment_total=len(beats), batch_key=key)
        completed = 0
        failed = 0
        warnings = 0
        uncertain = 0
        segments = _read(source / 'video_jobs.json', {}).get('segments', [])
        by_index = {int(s.get('segment_index') or 0): s for s in segments}
        estimated = dict(data, beats=[dict(beat, estimated_duration_sec=
            ((by_index.get(index, {}).get('job') or {}).get('duration_sec')
             if by_index.get(index, {}).get('beat_uid') == beat.get('beat_uid') else None)
            or beat.get('estimated_duration_sec') or beat.get('duration_sec'))
            for index, beat in enumerate(beats, 1)])
        from pipeline.planning_budget import budget_report
        parent = _set(parent, planning_budget=budget_report(data), segment_budget=budget_report(estimated),
                      grouping_review=data.get('grouping_review'))
        for index, beat in enumerate(beats, 1):
            stage = f'segment:{index}'
            parent, child = _ensure_stage(parent, stage, index)
            if child['state'] in ('error', 'cancelled') or (child['state'] == 'ready' and child.get('apply_error')):
                failed += 1
                continue
            if child['state'] == 'needs_attention':
                uncertain += 1
                continue
            if child['state'] != 'applied':
                parent = _set(parent, segment_completed=completed, segment_failed=failed,
                              segment_warnings=warnings, segment_processed=completed+failed+uncertain)
                _observe(parent, child, f'Segment {index}/{len(beats)} · ')
                return
            # Conflict checks occur before publish; later predecessor repairs are advisory.
            target = next((s for s in segments if s.get('segment_index') == index), {})
            if (child.get('result') or {}).get('planning_state') == 'invalid' or target.get('planning_state') == 'invalid':
                failed += 1
                continue
            if target.get('planning_issues'):
                warnings += 1
            completed += 1
        if not uncertain and not failed:
            _finish_replacement(parent, beats)
        state = 'needs_attention' if uncertain else 'error' if failed else 'applied'
        _set(parent, state, segment_completed=completed, segment_failed=failed, segment_warnings=warnings,
             segment_processed=completed+failed+uncertain,
             phase=f'已处理 {completed+failed+uncertain}/{len(beats)} 段 · {warnings} 段有提示 · {failed} 段待重试',
             message=('所有可执行步骤已处理，问题段可单独重新生成' if failed or warnings or uncertain else 'Beats 与全部 Segment 已逐步保存') + _budget_notice(parent),
             changes={'runtime': {}, 'meta': {}})


def reconcile_pending():
    from services import text_jobs as jobs
    with jobs.database() as db:
        parents = [jobs._decode(row) for row in db.execute("SELECT * FROM operations WHERE state IN ('waiting_children','needs_attention')")]
    for parent in parents:
        if parent.get('batch_version') == 1:
            try:
                reconcile(parent['id'])
            except Exception as exc:
                _set(jobs.get(parent['id']), 'error', phase='分步调度失败', message=str(exc))


def execute_beats(args):
    from ui import project_handlers as p
    from pipeline.novel_segments import generate_novel_beats
    from services.file_utils import save_json
    from services.text_jobs import current, phase
    text, duration_select, custom_duration, count = args
    phase('生成并保存 Beats')
    p.save_novel_source_text(text)
    duration = p._save_duration_state(duration_select, custom_duration)
    from services.ui_run_config import update_ui_run_config
    update_ui_run_config(novel_segment_count=int(count))
    data = _adopt_saved_beats(current())
    if data is None:
        data = generate_novel_beats(text, duration, int(count), p.current_default_video_model())
    if not isinstance(data, dict) or not isinstance(data.get('beats'), list) or not data['beats']:
        raise ValueError('Beats 生成结果为空')
    for beat in data['beats']:
        beat.setdefault('beat_uid', 'beat_' + uuid.uuid4().hex)
    from pipeline.planning_budget import budget_report
    data.update(target_duration_sec=duration, target_beat_count=int(count))
    data['planning_budget'] = budget_report(data)
    save_json('beats.json', data)
    # Publishing Beats must not erase usable prompts or render history.
    payload = _read(context.get_project_dir() / 'video_jobs.json', {})
    payload.setdefault('segments', [])
    payload.update(novel_batch_id=current()['batch_id'], default_model_id=p.current_default_video_model())
    save_json('video_jobs.json', payload)
    return {'planning_state': data.get('planning_state', 'ready'),
            'issues': data.get('planning_issues') or []}


def _adopt_saved_beats(child):
    """Recover a completed Beats stage left in a pre-batch failed task snapshot."""
    from services import text_jobs as jobs
    parent = jobs.get(child['batch_id'])
    with jobs.database() as db:
        previous = [jobs._decode(row) for row in db.execute("SELECT * FROM operations WHERE state IN ('error','cancelled') ORDER BY created DESC")]
    for old in previous:
        if (old.get('kind') != 'novel' or old.get('batch_version') or old.get('args') != parent['args']
                or old.get('source') != parent['source'] or old.get('routes') != parent['routes']
                or old.get('baseline') != parent['baseline'] or old.get('meta_baseline') != parent['meta_baseline']):
            continue
        path = Path(old['work']) / 'beats.json'
        try:
            data = _read(path, {})
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get('beats') or data.get('generation_source') != 'novel_beats':
            continue
        for original in child['meta_baseline']:
            saved = Path(old['work']) / 'project_meta' / Path(original).name
            if saved.is_file():
                shutil.copy2(saved, Path(child['work']) / 'project_meta' / saved.name)
        from generation.pipelines.minimax_h3.planning_checkpoints import inherit_plans
        inherit_plans(old['work'], parent['work'])
        jobs.update(parent['id'], adopted_beats_from=old['id'])
        return data
    return None


def execute_segment(args):
    from generation.service import get_generation_service
    from services.file_utils import save_json
    from services.text_jobs import current, phase
    from workflow.video_merge import load_video_jobs
    index = int(args[0])
    phase(f'生成 Segment {index}')
    data = _read(context.get_project_dir() / 'beats.json', {})
    payload = load_video_jobs()
    before = _read(context.get_project_dir() / 'video_jobs.json', copy.deepcopy(payload))
    previous = next((row for row in payload.get('segments', [])
                     if int(row.get('segment_index', 0)) == index), None)
    beat = data['beats'][index - 1]
    replacing = previous is not None and previous.get('beat_uid') != beat['beat_uid']
    if previous:
        from workflow.runninghub_queue import has_active_submission
        if has_active_submission(previous):
            raise ValueError('本段视频正在生成，请完成后再重新生成 Segment')
        if replacing:
            # Only this private snapshot drops the old slot. Do not inherit
            # another Beat's prompt, material choices, grouping or render IDs.
            payload['segments'] = [row for row in payload['segments'] if row is not previous]
        elif generation_model() == 'minimax_h3_local_ref2va':
            previous['h3_force_replan'] = True
    save_json('video_jobs.json', payload)
    try:
        get_generation_service().prepare_segments(data, segment_indices={index})
    except (ValueError, TypeError) as exc:
        if generation_model() != 'minimax_h3_local_ref2va':
            raise
        from generation.pipelines.minimax_h3.planning_drafts import draft_segment, issue
        payload = load_video_jobs()
        prior = next((row for row in payload.get('segments', []) if int(row.get('segment_index', 0)) == index), {})
        plan = {'shots': [], 'planning_issues': [issue('planning_failed', exc, blocking=True)]}
        target = draft_segment(beat, index, prior, prior.get('generation_config') or
                               {'duration_sec': beat.get('estimated_duration_sec') or 0}, plan)
        payload['segments'] = [row for row in payload.get('segments', []) if int(row.get('segment_index', 0)) != index] + [target]
        save_json('video_jobs.json', payload)
    payload = load_video_jobs()
    candidate = next(row for row in payload['segments'] if int(row.get('segment_index', 0)) == index)
    job = candidate.get('job') or {}
    prompt = str((candidate.get('generation_config') or {}).get('prompt_override')
                 or job.get('final_prompt') or job.get('prompt') or '').strip()
    failed = candidate.get('planning_state') == 'invalid' or bool(candidate.get('planning_candidate')) or not prompt
    if failed and previous is not None:
        # Failure diagnostics belong to the task result. Keep the published
        # old segment byte-for-byte instead of relabelling it as the new Beat.
        save_json('video_jobs.json', before)
        return {'planning_state': 'invalid', 'issues': candidate.get('planning_issues') or [],
                'segment_index': index, 'previous_preserved': True,
                'planning_candidate': candidate.get('planning_candidate') or candidate.get('h3_prompt_plan')}
    if failed:
        candidate['planning_state'] = 'invalid'
        candidate['prompt_saved'] = bool(prompt)
        save_json('video_jobs.json', payload)
    if replacing and not failed:
        from workflow.runninghub_history import retire_segment
        retire_segment(payload, previous, '新 Beat 提示词生成成功，替换原分段')
        save_json('video_jobs.json', payload)
    payload = load_video_jobs()
    segment = next(row for row in payload['segments'] if int(row.get('segment_index', 0)) == index)
    segment['beat_uid'] = data['beats'][index - 1]['beat_uid']
    batch_id = current().get('batch_id') or payload.get('novel_batch_id')
    if batch_id:
        segment['novel_batch_id'] = batch_id
    segment['novel_batch_stage'] = index
    payload['segments'].sort(key=lambda row: int(row['segment_index']))
    save_json('video_jobs.json', payload)
    return {'planning_state': segment.get('planning_state') or 'ready',
            'issues': segment.get('planning_issues') or [], 'segment_index': index}
