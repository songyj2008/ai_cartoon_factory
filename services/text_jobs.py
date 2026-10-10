"""Durable text operations with isolated output and guarded application.

The UI submits snapshots only. A worker owns generation; the original runtime
is never used as its working directory. Interrupted jobs require review rather
than silently replaying a possibly submitted browser request.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid

from services import context

ACTIVE = ContextVar('text_job', default=None)
_LOCK = threading.RLock()
_WAKE = threading.Event()
_WORKER = None
_ACTIVE_EXECUTIONS = {}
MAX_CONCURRENT_OPERATIONS = 13  # Up to twelve website jobs, plus one API job.
TITLES = {'story':'生成剧情', 'beats':'拆分 Beats', 'novel':'小说 → Beats → Segments',
          'prompts':'生成分段提示词', 'segment':'重生成分段提示词',
          'source_replan':'按剧情上下文重新规划', 'pipeline':'一键流程：准备文字及工作流',
          'split':'拆分复杂 Beat', 'feedback':'分析视频问题', 'metadata':'补全素材描述',
          'revision_wait':'修正视频提示词'}
LIVE = ('preparing', 'queued', 'running')
CLEARABLE = ('ready', 'applied', 'cancelled', 'error')
APPLIED_RETENTION_SECONDS = 7 * 24 * 60 * 60
MERGE_BASELINE_DIR = '.apply_baseline'
SEGMENT_MERGE_FILES = ('beats.json','video_jobs.json')
OUTPUTS = ('story.json', 'beats.json', 'video_jobs.json', 'novel_source.json',
           'video_feedback_analysis.json')
STAGES = {
    'story':['准备任务','生成剧情','校验候选','生成完成','应用完成'],
    'beats':['准备任务','拆分 Beats','校验候选','生成完成','应用完成'],
    'novel':['准备任务','拆分 Beats 与 Segments','校验候选','生成完成','应用完成'],
    'prompts':['准备任务','生成分段提示词','校验候选','生成完成','应用完成'],
    'segment':['准备任务','重生成分段提示词','校验候选','生成完成','应用完成'],
    'source_replan':['准备任务','按剧情上下文重新规划','校验候选','生成完成','应用完成'],
    'pipeline':['准备任务','生成剧情、Beats 及提示词','校验候选','生成完成','应用完成'],
    'split':['准备任务','拆分复杂 Beat','校验候选','生成完成','应用完成'],
    'feedback':['准备任务','分析视频问题','分析完成'],
    'metadata':['准备任务','补全素材描述','补全完成'],
    'revision_wait':['准备任务','等待网页回复','保存修正候选','生成完成','应用完成'],
}


class NeedsAttentionError(RuntimeError):
    """The original browser request may have been sent; never replay it."""


def _progress(item):
    stages = STAGES.get(item['kind'], ['准备任务','处理中','已完成'])
    state, phase = item['state'], str(item.get('phase') or '')
    if state == 'preparing':
        index, node = 0, '准备网页任务'
    elif state == 'queued':
        index, node = 0, '已排队'
    elif state == 'needs_attention':
        index, node = max(0,len(stages)-2), '需要人工处理'
    elif state in ('ready','applied'):
        index, node = len(stages)-1, '生成完成' if state == 'ready' else '应用完成'
    elif state == 'applying':
        index, node = max(0,len(stages)-2), '正在应用结果'
    else:
        aliases = {
            '准备输入快照':'准备任务','生成剧情':'生成剧情','拆分 Beats':'拆分 Beats',
            '小说拆分及生成 Segments':'拆分 Beats 与 Segments','生成分段提示词':'生成分段提示词',
            '重生成指定分段提示词':'重生成分段提示词','按剧情上下文重新规划':'按剧情上下文重新规划',
            '生成剧情、Beats 及分段提示词':'生成剧情、Beats 及提示词','拆分复杂 Beat':'拆分复杂 Beat',
            '分析视频问题':'分析视频问题','补全素材描述':'补全素材描述','修正任务 · 等待网页回复':'等待网页回复',
            '保存修正候选':'保存修正候选','校验并保存候选结果':'校验候选','结果已生成':'生成完成','已应用':'应用完成',
        }
        if ' · waiting · ' in phase:
            node = '等待网页回复'
        elif ' · claimed · ' in phase:
            node = '扩展已领取'
        else:
            node = next((label for alias,label in aliases.items() if alias in phase), '处理中')
        index = next((i for i,label in enumerate(stages) if label == node), 1)
        if state == 'error':
            previous = [h for h in item.get('history',[]) if h.get('phase') and h.get('phase') != '失败']
            if previous:
                last = str(previous[-1]['phase'])
                node = next((label for alias,label in aliases.items() if alias in last), node)
                index = next((i for i,label in enumerate(stages) if label == node), index)
    percent = 100 if state in ('ready','applied') else min(95, round(index/max(1,len(stages)-1)*100))
    if state in ('preparing','queued'): percent = 0
    return {'percent':percent,'node':node,'step':min(index+1,len(stages)),'total':len(stages)}


def root():
    from services.web_llm_bridge import BRIDGE_DIR
    return BRIDGE_DIR / 'operations'


@contextmanager
def database():
    root().mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root() / 'operations.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, state TEXT, created REAL, updated REAL, payload TEXT)')
    try:
        with db:
            yield db
    finally:
        db.close()


def _decode(row):
    if row is None:
        raise ValueError('任务不存在')
    return {**json.loads(row['payload']), 'id':row['id'], 'state':row['state'],
            'created':row['created'], 'updated':row['updated']}


def _transition(item, next_state, *, next_phase=None, now=None):
    now = time.time() if now is None else now
    previous_state, previous_phase = item.get('state'), item.get('phase')
    timings = item.setdefault('timings',{})
    started = float(timings.get('state_started_at') or item.get('updated') or item.get('created') or now)
    if next_state != previous_state:
        durations = timings.setdefault('state_seconds',{})
        durations[previous_state] = round(float(durations.get(previous_state,0))+max(0,now-started),2)
        timings['state_started_at'] = now
        if next_state == 'running':
            timings.setdefault('started_at',now)
            timings['last_started_at'] = now
        else:
            timings['last_started_at'] = None
    item['state'] = next_state
    if next_phase is not None:
        item['phase'] = next_phase
    if next_state != previous_state or item.get('phase') != previous_phase:
        history = item.setdefault('history',[])
        history.append({'at':now,'state':next_state,'phase':item.get('phase','')})
        item['history'] = history[-200:]
    if next_state in ('ready','error','cancelled','applied'):
        timings['finished_at'] = now
    return item


def get(identifier):
    with database() as db:
        return _decode(db.execute('SELECT * FROM operations WHERE id=?', (identifier,)).fetchone())


def update(identifier, state=None, **values):
    now = time.time()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        item = _decode(db.execute('SELECT * FROM operations WHERE id=?', (identifier,)).fetchone())
        if item['state'] == 'cancelled':
            return item
        previous_state = item.get('state')
        previous_phase = item.get('phase')
        item.update(values)
        next_state = state or item['state']
        next_phase = item.get('phase')
        item['state'] = previous_state
        item['phase'] = previous_phase
        _transition(item,next_state,next_phase=next_phase,now=now)
        db.execute('UPDATE operations SET state=?,updated=?,payload=? WHERE id=?',
                   (next_state, now, json.dumps(item, ensure_ascii=False), identifier))
    from services.task_notifications import operations
    operations.publish()
    return get(identifier)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


_MISSING = object()


def _clone_json(value):
    return _MISSING if value is _MISSING else copy.deepcopy(value)


def _render_json_path(path):
    result = ''
    for part in path:
        text = str(part)
        result += text if text.startswith('[') else ('.' if result else '') + text
    return result or '$'


def _entity_key(row, collection, index):
    if collection == 'beats':
        stable_id = row.get('beat_uid') or row.get('segment_uid')
    else:
        stable_id = row.get('segment_uid') or row.get('beat_uid')
    if stable_id:
        return f'uid:{stable_id}'
    segment_index = row.get('segment_index')
    if segment_index not in (None, ''):
        return f'index:{segment_index}'
    return f'position:{index + 1}'


def _index_entity_rows(rows, collection):
    indexed = {}
    for index, row in enumerate(rows):
        key = _entity_key(row, collection, index)
        if key in indexed:
            return None
        indexed[key] = row
    return indexed


def _merge_entity_lists(base, candidate, current, path, conflicts):
    collection = str(path[-1])
    indexed = []
    for rows in (base, candidate, current):
        value = {} if rows is _MISSING else _index_entity_rows(rows, collection)
        if value is None:
            conflicts.append(_render_json_path(path) + '（条目编号重复）')
            return _clone_json(current)
        indexed.append(value)
    base_rows, candidate_rows, current_rows = indexed
    order = list(current_rows)
    order.extend(key for key in candidate_rows if key not in current_rows)
    order.extend(key for key in base_rows if key not in current_rows and key not in candidate_rows)
    merged = []
    for key in order:
        value = _merge_json_values(
            base_rows.get(key, _MISSING),
            candidate_rows.get(key, _MISSING),
            current_rows.get(key, _MISSING),
            (*path, f'[{key}]'), conflicts)
        if value is not _MISSING:
            merged.append(value)
    return merged


def _merge_json_values(base, candidate, current, path=(), conflicts=None):
    """Three-way merge JSON values, merging beats/segments by stable identity."""
    conflicts = conflicts if conflicts is not None else []
    if candidate == base:
        return _clone_json(current)
    if current == base:
        return _clone_json(candidate)
    if candidate == current:
        return _clone_json(candidate)

    values = (base, candidate, current)
    if any(isinstance(value, dict) for value in values if value is not _MISSING) and all(
        value is _MISSING or isinstance(value, dict) for value in values
    ):
        merged = {}
        keys = []
        for value in values:
            if isinstance(value, dict):
                keys.extend(key for key in value if key not in keys)
        for key in keys:
            value = _merge_json_values(
                base.get(key, _MISSING) if isinstance(base, dict) else _MISSING,
                candidate.get(key, _MISSING) if isinstance(candidate, dict) else _MISSING,
                current.get(key, _MISSING) if isinstance(current, dict) else _MISSING,
                (*path, key), conflicts)
            if value is not _MISSING:
                merged[key] = value
        return merged

    if path and path[-1] in ('beats', 'segments') and all(
        value is _MISSING or (isinstance(value, list) and all(isinstance(row, dict) for row in value))
        for value in values
    ):
        return _merge_entity_lists(base, candidate, current, path, conflicts)

    conflicts.append(_render_json_path(path))
    return _clone_json(current)


def _json_value(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else _MISSING


def _editor_json(value):
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    if isinstance(value, str):
        try:
            return json.loads(value.lstrip('\ufeff'))
        except (TypeError, ValueError):
            return _MISSING
    return _MISSING


def _beats_editor_only_prompt_state_changed(before, after):
    before_value, after_value = _editor_json(before), _editor_json(after)
    if not isinstance(before_value, dict) or not isinstance(after_value, dict):
        return False
    before_beats, after_beats = before_value.get('beats'), after_value.get('beats')
    if not isinstance(before_beats, list) or not isinstance(after_beats, list):
        return False
    before_root = {key:value for key,value in before_value.items() if key != 'beats'}
    after_root = {key:value for key,value in after_value.items() if key != 'beats'}
    if before_root != after_root:
        return False
    if len(before_beats) != len(after_beats):
        return False
    prompt_keys = ('model_hints','h3_shots','shots')
    for index,(left,right) in enumerate(zip(before_beats,after_beats)):
        if not isinstance(left,dict) or not isinstance(right,dict):
            return False
        if _entity_key(left, 'beats', index) != _entity_key(right, 'beats', index):
            return False
        left_input, right_input = dict(left), dict(right)
        for key in prompt_keys:
            left_input.pop(key,None)
            right_input.pop(key,None)
        if left_input != right_input:
            return False
    return True


def _merge_baseline_path(item, name):
    saved = item.get('merge_baseline_files') or {}
    if name in saved:
        relative = saved[name]
        if relative is None:
            return _MISSING
        path = Path(item['work']) / relative
        if path.is_file():
            return path
    expected = item.get('baseline', {}).get(name)
    if expected is None:
        return _MISSING
    current = Path(item['source']) / name
    if digest(current) == expected:
        return current
    # Older queued tasks did not preserve an apply baseline. A successful
    # earlier apply kept a byte-identical backup for its short retention
    # window; use it only after the hash proves it is exactly this task's base.
    filename = Path(name).name
    candidates = list(root().glob(f'*/before_apply/*_{filename}'))
    candidates.extend(root().glob(f'*/work/{MERGE_BASELINE_DIR}/{filename}'))
    return next((path for path in candidates if digest(path) == expected), None)


def _stage_merged_json(item, name, source, work):
    base_path = _merge_baseline_path(item, name)
    if base_path is None:
        return None, [], False
    try:
        base = _MISSING if base_path is _MISSING else _json_value(base_path)
        candidate = _json_value(work / name)
        current = _json_value(source / name)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f'{name} 无法安全合并 JSON：{exc}') from exc
    conflicts = []
    merged = _merge_json_values(base, candidate, current, (name,), conflicts)
    if conflicts:
        return None, sorted(set(conflicts)), True
    if merged is _MISSING:
        return None, [], True
    staged = work / '.apply_merge' / name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return staged, [], True


def _snapshot_files(source):
    return [p for p in source.iterdir() if p.is_file() and p.suffix in ('.json', '.txt')]


def _target_segment(kind, args, source):
    argument_index = {'segment':0,'source_replan':0,'split':2,'revision_wait':1}.get(kind)
    if argument_index is None:
        return None
    try:
        index = int(float(args[argument_index]))
        jobs = json.loads((source / 'video_jobs.json').read_text(encoding='utf-8-sig'))
        segment = next((row for row in jobs.get('segments', [])
                        if isinstance(row, dict) and int(row.get('segment_index') or 0) == index), None)
        if segment:
            return {'segment_index':index,'segment_uid':str(segment.get('segment_uid') or ''),
                    'beat_uid':str(segment.get('beat_uid') or '')}
    except Exception:
        pass
    return None


def submit(kind, args, editors=None, *, start=True, hold=False):
    if kind not in TITLES:
        raise ValueError('未知文字任务')
    context.assert_current_runtime_writable('提交文字任务')
    from services.web_llm_bridge import get_settings, routes_for
    if kind in ('story', 'beats', 'novel', 'pipeline', 'prompts', 'segment', 'source_replan', 'split'):
        from services.project_assets import sync_generation_assets
        sync_generation_assets()
    source = context.get_project_dir().resolve()
    identifier = str(uuid.uuid4())
    routes = get_settings()
    route_fingerprint = {'default_providers':routes.get('default_providers') or [routes.get('default_provider')],
                         'overrides':routes.get('overrides')}
    signature = hashlib.sha256(json.dumps([str(source),kind,args,editors or {},route_fingerprint], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _LOCK, database() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM operations WHERE state IN ('preparing','queued','running')"):
            item = _decode(row)
            if item['signature'] == signature:
                return {**item,'duplicate':True}
        work = root() / identifier / 'work'
        work.mkdir(parents=True)
        baseline = {name:digest(source / name) for name in OUTPUTS}
        for path in _snapshot_files(source):
            baseline[path.name] = digest(path)
            shutil.copy2(path, work / path.name)
        merge_baseline_dir = work / MERGE_BASELINE_DIR
        merge_baseline_files = {}
        mergeable_files = SEGMENT_MERGE_FILES if kind in ('segment','source_replan','revision_wait') else ()
        for name in mergeable_files:
            original = source / name
            if original.is_file():
                merge_baseline_dir.mkdir(exist_ok=True)
                shutil.copy2(original, merge_baseline_dir / name)
                merge_baseline_files[name] = f'{MERGE_BASELINE_DIR}/{name}'
            else:
                merge_baseline_files[name] = None
        # A draft marker permits candidate generation for episode snapshots.
        (work / '.draft').touch()
        from services.project_bible import project_bible_path
        from services.ui_run_config import ui_run_config_path
        meta = work / 'project_meta'
        meta.mkdir()
        meta_sources = {}
        for path in (project_bible_path(), ui_run_config_path(),
                     context.BASE_DIR / 'projects' / context.get_current_project_name() / 'industry_profile.json'):
            meta_sources[str(path.resolve())] = digest(path)
            if path.is_file():
                shutil.copy2(path, meta / path.name)
        # Preserve the asset registry used to resolve identities during generation.
        if context.ASSET_INDEX_PATH.is_file():
            shutil.copy2(context.ASSET_INDEX_PATH, meta / 'asset_index.json')
        meta_sources[str(context.ASSET_INDEX_PATH.resolve())] = digest(context.ASSET_INDEX_PATH)
        entry = {'story':'story','beats':'beats','novel':'beats','prompts':'prompt','segment':'prompt',
                 'source_replan':'prompt','pipeline':'story','feedback':'feedback','metadata':'other',
                 'split':'beats','revision_wait':'revision'}.get(kind,'other')
        provider_candidates = routes_for(entry,config=routes)
        if kind == 'revision_wait' and len(args) > 2 and isinstance(args[2],dict):
            requested = args[2].get('providers') or args[2].get('provider') or 'inherit'
            provider_candidates = routes_for(entry,requested,config=routes)
        provider=provider_candidates[0]
        recovery_entries = {
            'novel':('beats','prompt'),'pipeline':('story','beats','prompt'),
        }.get(kind,(entry,))
        recovery_providers = []
        for recovery_entry in recovery_entries:
            recovery_providers.extend(routes_for(recovery_entry,config=routes))
        recovery_safe = all(value != 'api' for value in recovery_providers)
        segment = _target_segment(kind,args,source)
        input_version = hashlib.sha256(json.dumps([baseline, meta_sources, args, editors or {}, routes],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        initial_state = 'preparing' if hold else 'queued'
        initial_phase = '准备网页任务' if hold else '已排队'
        item = {'kind':kind,'title':TITLES[kind], 'args':args,'editors':editors or {},
                'source':str(source),'project':context.get_current_project_name(),
                'episode':context.get_current_episode_name(),'work':str(work),'signature':signature,
                'baseline':baseline,'merge_baseline_files':merge_baseline_files,
                'meta_baseline':meta_sources,'routes':routes,
                'provider':provider,'provider_candidates':provider_candidates,
                'entry':entry,'segment':segment,'input_version':input_version,
                'recovery_version':1 if recovery_safe else 0,'recovery_safe':recovery_safe,'web_calls':{},
                'phase':initial_phase,'children':[], 'history':[{'at':time.time(),'state':initial_state,'phase':initial_phase}],
                'timings':{'queued_at':time.time()},'message':'已提交后台队列，可离开当前页面'}
        now = time.time()
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   (identifier,initial_state,now,now,json.dumps(item,ensure_ascii=False)))
    if start and not hold:
        ensure_worker()
        _WAKE.set()
    return get(identifier)


def release_prepared(identifier):
    """Expose a revision to the dispatcher only after its web task is linked."""
    with _LOCK:
        item = get(identifier)
        if item['state'] != 'preparing' or not item.get('children') or not item.get('args',[None])[0]:
            raise ValueError('修正任务尚未关联网页任务，不能开始执行')
        update(identifier,'queued',phase='已排队',message='网页任务已关联，等待后台处理')
    ensure_worker(); _WAKE.set()
    return get(identifier)


def current():
    return ACTIVE.get()


def check_cancelled():
    item = current()
    if item and get(item['id'])['state'] == 'cancelled':
        raise RuntimeError('任务已取消')


def phase(message):
    check_cancelled()
    item = current()
    if item:
        update(item['id'], phase=message)


def link_child(identifier):
    item = current()
    if item:
        check_cancelled()
        saved = get(item['id'])
        if identifier not in saved['children']:
            update(item['id'], children=[*saved['children'],identifier])


def record_web_call(identifier, call_key, child_id, *, provider=None, candidates=None):
    """Persist an idempotent business-stage to browser-task association."""
    with _LOCK:
        item = get(identifier)
        calls = dict(item.get('web_calls') or {})
        previous = calls.get(str(call_key))
        previous_id=previous.get('id') if isinstance(previous,dict) else previous
        if previous_id and previous_id != child_id:
            raise RuntimeError('网页阶段已关联其他任务，停止重复发送并转人工处理')
        calls[str(call_key)] = {'id':str(child_id),'provider':provider,
                                'candidates':list(candidates or ([provider] if provider else []))}
        children = list(dict.fromkeys([*(item.get('children') or []),str(child_id)]))
        update(identifier,web_calls=calls,children=children)


def attach_child(identifier, child_id):
    item = get(identifier)
    if child_id not in item.get('children',[]):
        update(identifier,children=[*item.get('children',[]),child_id])


def inspect(identifier):
    """Resume observation of an existing web task; never submit it again."""
    item = get(identifier)
    if item['state'] != 'needs_attention':
        raise ValueError('只有需要处理的任务可以检查恢复')
    if item.get('attention_reason') == 'web_timeout':
        raise ValueError('原网页任务已超时；请检查原会话，不能自动重新发送')
    resumable_revision = item['kind'] == 'revision_wait' and bool(item.get('children'))
    resumable_operation = item.get('recovery_version') == 1 and item.get('provider') != 'api'
    if not (resumable_revision or resumable_operation):
        raise ValueError('任务缺少可安全续接的网页任务编号或阶段信息，保留为待人工处理')
    from services.web_llm_bridge import task_status
    statuses = [task_status(child, current_runtime=False) for child in item.get('children',[])]
    update(identifier, 'queued', phase='恢复原网页任务',
           recovery_status=[{'id':x['id'],'status':x['status']} for x in statuses],
           message='已关联原网页任务，继续等待或读取结果；不会重新发送')
    ensure_worker(); _WAKE.set()
    return get(identifier)


def cancel(identifier):
    with _LOCK:
        item = get(identifier)
        if item['state'] not in (*LIVE, 'needs_attention'):
            return item
        update(identifier, 'cancelled', message='已停止本地任务；网页已发送的请求可能仍在生成')
        from services.web_llm_bridge import cancel_task
        for child in get(identifier)['children']:
            cancel_task(child, current_runtime=False)
    _WAKE.set()
    return get(identifier)


def _remove_work_directory(item):
    """Remove only the job-owned snapshot directory after a record is deleted."""
    work = item.get('work')
    if not work:
        return
    root_path = root().resolve()
    job_path = (root_path / item['id']).resolve()
    expected_work = job_path / 'work'
    if job_path.parent == root_path and Path(work).resolve() == expected_work:
        shutil.rmtree(job_path, ignore_errors=True)


def delete(identifier):
    """Delete a finished operation; live and unresolved jobs must be retained."""
    with _LOCK, database() as db:
        db.execute('BEGIN IMMEDIATE')
        item = _decode(db.execute('SELECT * FROM operations WHERE id=?', (identifier,)).fetchone())
        if item['state'] not in CLEARABLE:
            raise ValueError('任务仍在执行或需要处理，暂不能删除')
        db.execute('DELETE FROM operations WHERE id=?', (identifier,))
    _remove_work_directory(item)
    return {'id':identifier,'deleted':True}


def _preserve_pending_merge_baselines(db, applied_items):
    if not applied_items:
        return
    rows = db.execute("SELECT * FROM operations WHERE state IN ('preparing','queued','running','ready','needs_attention')").fetchall()
    for row in rows:
        pending = _decode(row)
        if pending.get('kind') not in ('segment','source_replan','revision_wait') or not pending.get('work'):
            continue
        saved = dict(pending.get('merge_baseline_files') or {})
        changed = False
        for name in SEGMENT_MERGE_FILES:
            existing = saved.get(name)
            if existing is not None and (Path(pending['work']) / existing).is_file():
                continue
            expected = pending.get('baseline',{}).get(name)
            if expected is None:
                if name not in saved:
                    saved[name] = None
                    changed = True
                continue
            matches = []
            for applied in applied_items:
                if applied.get('source') != pending.get('source'):
                    continue
                applied_dir = Path(applied.get('work') or '').parent
                matches.extend((applied_dir / 'before_apply').glob(f'*_{name}'))
                matches.append(applied_dir / 'work' / MERGE_BASELINE_DIR / name)
            snapshot = next((path for path in matches if path.is_file() and digest(path) == expected), None)
            if snapshot is None:
                continue
            destination = Path(pending['work']) / MERGE_BASELINE_DIR / name
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if digest(destination) != expected:
                    shutil.copy2(snapshot, destination)
                saved[name] = f'{MERGE_BASELINE_DIR}/{name}'
                changed = True
            except OSError:
                continue
        if changed:
            pending['merge_baseline_files'] = saved
            db.execute('UPDATE operations SET payload=?,updated=? WHERE id=?',
                       (json.dumps(pending,ensure_ascii=False),time.time(),pending['id']))


def _delete_expired_applied(now=None):
    """Delete applied task records and snapshots after seven days."""
    cutoff = (time.time() if now is None else now) - APPLIED_RETENTION_SECONDS
    with _LOCK, database() as db:
        db.execute('BEGIN IMMEDIATE')
        items = [_decode(row) for row in db.execute(
            "SELECT * FROM operations WHERE state='applied' AND updated<=?", (cutoff,)).fetchall()]
        if items:
            _preserve_pending_merge_baselines(db, items)
            db.executemany('DELETE FROM operations WHERE id=?', ((item['id'],) for item in items))
    for item in items:
        _remove_work_directory(item)
    return len(items)


def record_apply_error(identifier, message):
    """Expose a failed apply attempt while keeping the candidate retryable."""
    try:
        item = get(identifier)
        if item['state'] == 'ready':
            return update(identifier,apply_error=str(message),apply_attempted_at=time.time())
    except ValueError:
        pass
    return None


def clear_finished():
    """Clear finished records and their snapshots, preserving live/recovery work."""
    placeholders = ','.join('?' for _ in CLEARABLE)
    with _LOCK, database() as db:
        db.execute('BEGIN IMMEDIATE')
        items = [_decode(row) for row in db.execute(
            f'SELECT * FROM operations WHERE state IN ({placeholders})', CLEARABLE).fetchall()]
        db.execute(f'DELETE FROM operations WHERE state IN ({placeholders})', CLEARABLE)
    for item in items:
        _remove_work_directory(item)
    return {'deleted':len(items)}


def list_jobs():
    ensure_worker()
    _delete_expired_applied()
    with database() as db:
        items = [_decode(row) for row in db.execute('SELECT * FROM operations ORDER BY created DESC LIMIT 100')]
    def duration(item,state):
        timings=item.get('timings') or {}
        total=float((timings.get('state_seconds') or {}).get(state,0) or 0)
        if item['state']==state:
            total += max(0,time.time()-float(timings.get('state_started_at') or item['updated']))
        return max(0,int(total))
    listed=[]
    for item in items:
        omitted=('routes','baseline','merge_baseline_files','meta_baseline','args','work','editors','result')
        listed.append({**{k:v for k,v in item.items() if k not in omitted},
             'progress':_progress(item),'queued_seconds':duration(item,'queued'),
             'elapsed_seconds':duration(item,'running'),
             'attention_seconds':duration(item,'needs_attention'),
             'editor_keys':list(item['editors'])})
    return listed


def recover_interrupted():
    retry_apply = []
    now = time.time()
    with database() as db:
        for row in db.execute("SELECT * FROM operations WHERE state IN ('preparing','running','applying','ready')").fetchall():
            item = _decode(row)
            if item['state'] == 'preparing':
                from services.web_llm_bridge import task_status
                child_id = str(uuid.uuid5(uuid.UUID(item['id']),'revision'))
                try:
                    child = task_status(child_id,current_runtime=False)
                except ValueError:
                    child = None
                if child is None:
                    state, next_phase = 'needs_attention', '需要人工处理'
                    item['message'] = '提交网页任务时后台中断；没有可验证的原网页任务，未自动重发。'
                else:
                    item['children'] = list(dict.fromkeys([*(item.get('children') or []),child_id]))
                    item['args'][0] = child_id
                    state, next_phase = 'queued', '恢复原网页任务'
                    item['message'] = '已找到原网页任务，继续读取或等待；不会重新发送。'
            elif item['state'] == 'ready' and item.get('auto_apply_pending'):
                retry_apply.append((item['id'],item.get('editors') or {}))
                continue
            elif item['state'] == 'applying':
                _rollback_journal(item)
                state, next_phase = 'ready', '结果待应用'
                item['message'] = '保存过程曾中断，已恢复原文件；正在重新核对并应用候选结果。'
                retry_apply.append((item['id'],item.get('editors') or {}))
            elif item.get('recovery_version') == 1 and item.get('provider') != 'api':
                # Every web call is keyed by business operation + call index.
                # Replaying this function therefore reads the old result or
                # waits for the old task instead of dispatching another send.
                state, next_phase = 'queued', '恢复原任务'
                item['message'] = '后台已恢复，将读取或等待原网页任务；不会重新发送。'
            elif item['kind'] == 'revision_wait' and item.get('children'):
                state, next_phase = 'queued', '恢复原网页任务'
                item['message'] = '后台已恢复，将读取或等待原网页任务；不会重新发送。'
            else:
                state = 'needs_attention'
                item['message'] = '后台曾中断，当前阶段没有可验证的网页任务编号；需要人工检查，不会自动重发。'
                next_phase = '需要人工处理'
            _transition(item,state,next_phase=next_phase,now=now)
            db.execute("UPDATE operations SET state=?,updated=?,payload=? WHERE id=?",
                       (state,now,json.dumps(item,ensure_ascii=False),item['id']))
    for identifier,editors in retry_apply:
        try:
            apply(identifier,editors)
        except Exception as exc:
            record_apply_error(identifier,str(exc))
            update(identifier,auto_apply_pending=False,
                   message='候选结果仍保留，重新应用时遇到问题：'+str(exc))


def _execute(item):
    item['_web_call_cursor'] = 0
    token = ACTIVE.set(item)
    try:
        with context.pin_runtime((Path(item['work']), item['project'], item['episode'])):
            from services.text_job_operations import execute
            result = execute(item['kind'], item['args'])
            check_cancelled()
            baseline = item['baseline']
            after = {path.name:digest(path) for path in _snapshot_files(Path(item['work']))}
            for name in OUTPUTS:
                after.setdefault(name,digest(Path(item['work']) / name))
            runtime_changes = {}
            for name in sorted(set(baseline)|set(after)):
                before_hash, after_hash = baseline.get(name), after.get(name)
                if before_hash == after_hash:
                    continue
                path = Path(item['work']) / name
                if after_hash is None:
                    runtime_changes[name] = 'delete'
                else:
                    if path.suffix == '.json':
                        json.loads(path.read_text(encoding='utf-8-sig'))
                    runtime_changes[name] = 'write'
            meta_changes = {}
            for original, before_hash in item['meta_baseline'].items():
                staged = Path(item['work']) / 'project_meta' / Path(original).name
                after_hash = digest(staged)
                if before_hash != after_hash:
                    meta_changes[original] = 'delete' if after_hash is None else 'write'
                    if after_hash is not None and staged.suffix == '.json':
                        json.loads(staged.read_text(encoding='utf-8-sig'))
            if not runtime_changes and not meta_changes and result is None:
                raise ValueError('未产生有效结果，请查看生成错误')
            update(item['id'],'ready',changes={'runtime':runtime_changes,'meta':meta_changes},
                   files=[name for name,op in runtime_changes.items() if op=='write'],result=result,phase='结果已生成',
                   auto_apply_pending=item['kind'] != 'metadata',
                   message='结果已通过校验，正在核对原项目版本并应用。')
            try:
                if item['kind'] != 'metadata':
                    apply(item['id'],item.get('editors') or {})
            except Exception as exc:
                record_apply_error(item['id'],str(exc))
                update(item['id'],auto_apply_pending=False,
                       message='原稿或编辑器内容已变化，结果保留为待应用候选：'+str(exc))
    except NeedsAttentionError as exc:
        try:
            update(item['id'],'needs_attention',message=str(exc),phase='需要人工处理',
                   attention_reason='web_timeout')
        except ValueError:
            pass
    except Exception as exc:
        try:
            update(item['id'],'error',message=str(exc),phase='失败')
        except ValueError:
            # A temporary test/runtime store may be removed while a daemon
            # worker is unwinding; never leak an unhandled thread exception.
            pass
    finally:
        ACTIVE.reset(token)


def _claim_runnable(active_counts, limits=None):
    """Claim the oldest job with capacity in its initial service's pool."""
    if limits is None:
        from services.web_llm_bridge import get_settings
        limits = get_settings()['concurrency']
    now = time.time()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM operations WHERE state='queued' ORDER BY created").fetchall():
            item = _decode(row)
            candidates=item.get('provider_candidates') or [item.get('provider') or 'api']
            available=[provider for provider in candidates if active_counts.get(provider,0) <
                       (1 if provider=='api' else limits.get(provider,1))]
            if not available:
                continue
            web_count=sum(n for p,n in active_counts.items() if p!='api')
            available=[provider for provider in available if provider=='api' or web_count<limits['total']]
            if not available: continue
            provider=min(available,key=lambda value:active_counts.get(value,0))
            item['dispatch_provider']=provider
            _transition(item,'running',next_phase='后台处理中',now=now)
            db.execute("UPDATE operations SET state='running',updated=?,payload=? WHERE id=?",
                       (now,json.dumps(item,ensure_ascii=False),row['id']))
            return item
    return None


def _run_claimed(item):
    try:
        _execute(item)
    finally:
        with _LOCK:
            _ACTIVE_EXECUTIONS.pop(item['id'],None)
        _WAKE.set()


def _loop():
    while True:
        _delete_expired_applied()
        launched = False
        from services.web_llm_bridge import get_settings
        limits = get_settings()['concurrency']
        with _LOCK:
            while len(_ACTIVE_EXECUTIONS) < MAX_CONCURRENT_OPERATIONS:
                counts = {}
                for entry in _ACTIVE_EXECUTIONS.values():
                    counts[entry['provider']] = counts.get(entry['provider'],0) + 1
                item = _claim_runnable(counts,limits)
                if item is None:
                    break
                thread = threading.Thread(target=_run_claimed,args=(item,),
                                          name='text-operation-'+item['id'][:8],daemon=True)
                _ACTIVE_EXECUTIONS[item['id']] = {'provider':item.get('dispatch_provider') or item.get('provider') or 'api',
                                                   'thread':thread}
                thread.start()
                launched = True
        if not launched:
            _WAKE.wait(2)
            _WAKE.clear()


def ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        if not any(entry['thread'].is_alive() for entry in _ACTIVE_EXECUTIONS.values()):
            recover_interrupted()
        _WORKER = threading.Thread(target=_loop, name='text-operations', daemon=True)
        _WORKER.start()


def result(identifier):
    item = get(identifier)
    web_tasks = []
    if item.get('children'):
        from services.web_llm_bridge import task_status
        for child in item['children']:
            try:
                value = task_status(child, current_runtime=False)
                web_tasks.append({'id':child,'provider':value.get('provider'),'status':value.get('status'),
                                  'message':value.get('message',''),'started_at':value.get('execution_started_at'),
                                  'updated_at':value.get('updated_at'),
                                  'late_results':value.get('late_results',[])})
            except Exception:
                web_tasks.append({'status':'unknown','message':'网页任务记录暂不可读取'})
    history = []
    for entry in item.get('history',[]):
        phase_name = entry.get('phase') or ''
        if not phase_name or (history and history[-1]['node'] == phase_name):
            continue
        history.append({'node':phase_name,'state':entry.get('state'),'at':entry.get('at')})
    preview = {}
    work = Path(item.get('work') or '')
    if work.is_dir():
        for name,operation in item.get('changes',{}).get('runtime',{}).items():
            path = work / name
            if operation != 'write' or not path.is_file() or path.stat().st_size > 750_000:
                continue
            try:
                text = path.read_text(encoding='utf-8-sig')
                preview[name] = json.loads(text) if path.suffix == '.json' else text
            except (OSError,ValueError):
                preview[name] = '候选文件无法读取'
    return {'id':identifier,'title':item['title'],'kind':item['kind'],
            'state':item['state'],'project':item['project'],'episode':item['episode'],
            'entry':item.get('entry'),'segment':item.get('segment'),
            'editors':item.get('editors',{}),'preview':preview,'candidate':item.get('result'),
            'progress':_progress(item),'phase':item.get('phase',''),
            'failure_reason':item.get('message','') if item['state'] in ('error','needs_attention') else '',
            'message':item.get('message',''),'history':history,'web_tasks':web_tasks,
            'timings':item.get('timings',{})}


def apply(identifier, editors):
    # Application is serialized and always targets the immutable submission path.
    from workflow.video_merge import video_jobs_lock
    source = Path(get(identifier)['source'])
    with _LOCK, video_jobs_lock(source):
        item = get(identifier)
        if item['state'] == 'applied':
            return item
        if item['state'] != 'ready':
            raise ValueError('任务尚无可应用结果')
        source = Path(item['source'])
        if item.get('episode') and not (source / '.draft').is_file():
            raise PermissionError('任务所属集数已归档，候选结果保留为待应用状态')
        target = item.get('segment') or {}
        if item.get('kind') in ('segment','source_replan','revision_wait') and not (
                target.get('segment_uid') or target.get('beat_uid')):
            raise ValueError('原分段缺少稳定编号，无法安全自动应用；候选结果已保留')
        changed_editors = []
        for key,value in item['editors'].items():
            actual = editors.get(key)
            if actual == value:
                continue
            if key == 'beats_editor' and item.get('kind') == 'segment' and \
                _beats_editor_only_prompt_state_changed(value, actual):
                continue
            changed_editors.append(key)
        if changed_editors:
            raise ValueError('页面输入已变化，结果保留为候选。请查看结果并手动合并，或按当前输入重新提交。')
        work = Path(item['work'])
        runtime_changes = item.get('changes',{}).get('runtime',{})
        meta_changes = item.get('changes',{}).get('meta',{})
        merged_sources = {}
        merged_deletes = set()
        conflicts = []
        for name,op in runtime_changes.items():
            if item.get('kind') in ('segment','source_replan','revision_wait') and op == 'write' and name in SEGMENT_MERGE_FILES:
                staged, fields, mergeable = _stage_merged_json(item, name, source, work)
                if fields:
                    conflicts.extend(fields)
                    continue
                if mergeable:
                    if staged is None:
                        merged_deletes.add(name)
                    else:
                        merged_sources[name] = staged
                    continue
            if digest(source / name) != item['baseline'].get(name):
                conflicts.append(name)
        for original,op in meta_changes.items():
            if digest(Path(original)) != item['meta_baseline'].get(original):
                conflicts.append(Path(original).name)
        if conflicts:
            details = '、'.join(sorted(set(conflicts))[:8])
            if len(set(conflicts)) > 8:
                details += f' 等 {len(set(conflicts))} 处'
            raise ValueError('原项目对应内容已变化或缺少合并快照，未覆盖：' + details)
        backup = work.parent / 'before_apply'
        backup.mkdir(exist_ok=True)
        targets = []
        for name,op in runtime_changes.items():
            if name in merged_deletes:
                op = 'delete'
            targets.append((source/name,merged_sources.get(name,work/name),op))
        targets += [(Path(original),work/'project_meta'/Path(original).name,op)
                    for original,op in meta_changes.items()]
        journal = []
        for index,(target,staged,op) in enumerate(targets):
            backup_path = backup / f'{index:03d}_{target.name}'
            existed = target.exists()
            if existed:
                shutil.copy2(target,backup_path)
            journal.append({'target':str(target),'backup':str(backup_path),'existed':existed})
        update(identifier,'applying',message='正在保存结果',journal=journal)
        try:
            for target,staged,op in targets:
                if op == 'delete':
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temp = target.with_name(target.name + '.' + identifier + '.tmp')
                    shutil.copy2(staged,temp)
                    os.replace(temp,target)
        except Exception:
            _rollback_journal(get(identifier))
            update(identifier,'ready',message='保存失败，原文件已恢复，可重试应用')
            raise
        return update(identifier,'applied',phase='已应用',journal=[],apply_error=None,
                      message='结果已保存到提交时所属的项目和集数。')


def _rollback_journal(item):
    for record in reversed(item.get('journal') or []):
        target, backup = Path(record['target']), Path(record['backup'])
        if record.get('existed') and backup.is_file():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(backup,target)
        elif not record.get('existed'):
            target.unlink(missing_ok=True)
