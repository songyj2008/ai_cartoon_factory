"""Durable local queue and provider routing for browser-backed LLM calls.

The browser never receives file-system authority. Jobs contain immutable text
snapshots; applying a revision remains an explicit action in the project UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from services.task_notifications import web_results
from services.context import BASE_DIR, get_project_dir

BRIDGE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "AICartoonFactory" / "web_llm_bridge" / hashlib.sha256(str(BASE_DIR).encode()).hexdigest()[:16]
PROVIDERS = [
    {"id": "deepseek", "label": "DeepSeek", "url": "https://chat.deepseek.com/", "available": True},
    {"id": "chatgpt", "label": "ChatGPT", "url": "https://chatgpt.com/", "available": True},
    {"id": "doubao", "label": "豆包", "url": "https://www.doubao.com/chat/", "available": True},
    {"id": "qianwen", "label": "千问（待适配）", "url": "", "available": False},
]
ENTRIES = {"story": "剧情生成", "beats": "Beats 拆分", "prompt": "分段提示词", "feedback": "视频问题分析", "revision": "视频反馈修正", "other": "其他 LLM 调用"}
TERMINAL = {"complete", "error", "cancelled", "timeout"}
TIMEOUT = 900
_ROUTE_LOCK = threading.RLock()


def _default_concurrency():
    """Return pool defaults from the provider catalog so future adapters opt in cleanly."""
    return {
        'total': 6,
        **{provider['id']: 2 for provider in PROVIDERS if provider['available']},
    }


def _title_component(value, *, limit):
    text = re.sub(r'[\\/:*?"<>|\s_]+', '-', str(value or '')).strip('- .')
    text = re.sub(r'[^\w\-\u4e00-\u9fff]', '', text, flags=re.UNICODE)
    return text[:limit].rstrip('-')


def _conversation_title(operation, data, metadata, entry):
    """Build a concise, stable title for the task's dedicated browser chat."""
    data = data if isinstance(data, dict) else {}
    snapshot = (metadata or {}).get('snapshot') or {}
    operation = operation or {}
    kinds = {
        'story':'剧情生成', 'beats':'拆Beats', 'novel':'小说拆分',
        'prompts':'提示词生成', 'segment':'提示词重生',
        'source_replan':'提示词重规划', 'pipeline':'一键流程',
        'split':'Beat拆分', 'feedback':'问题分析', 'metadata':'素材补全',
        'revision_wait':'提示词修正', 'revision':'提示词修正',
    }
    kind = operation.get('kind')
    task_label = kinds.get(kind) or {
        'story':'剧情生成', 'beats':'拆Beats', 'prompt':'提示词生成',
        'feedback':'问题分析', 'revision':'提示词修正',
    }.get(entry, '文字任务')

    user_data = {}
    if isinstance(data.get('user'), str):
        try:
            parsed = json.loads(data['user'])
            if isinstance(parsed, dict):
                user_data = parsed
        except (TypeError, ValueError):
            pass
    operation_segment = operation.get('segment') or {}
    segment_index = (metadata or {}).get('segment_index')
    if segment_index is None:
        segment_index = operation_segment.get('segment_index', user_data.get('segment_index'))

    project = operation.get('project') or snapshot.get('project_name') or (data or {}).get('project_name')
    episode = operation.get('episode') or snapshot.get('episode_name') or (data or {}).get('episode_name')
    project = _title_component(project, limit=8) or '未知项目'
    match = re.search(r'(\d+)', str(episode or ''))
    episode_title = f"第{match.group(1).zfill(3)}集" if match else '未知集'

    segment_count = (metadata or {}).get('segment_count') or snapshot.get('segment_count')
    args = operation.get('args') or []
    count_arg = {'novel':3, 'beats':3, 'pipeline':7}.get(kind)
    if segment_count is None and count_arg is not None and len(args) > count_arg:
        try:
            requested_count = int(float(args[count_arg]))
            if requested_count > 0:
                segment_count = requested_count
        except (TypeError, ValueError):
            pass
    beats_arg = {'prompts':1, 'segment':3, 'split':1}.get(kind)
    if segment_count is None and beats_arg is not None and len(args) > beats_arg:
        raw_beats = args[beats_arg]
        if isinstance(raw_beats, str):
            try:
                raw_beats = json.loads(raw_beats)
            except (TypeError, ValueError):
                raw_beats = None
        if isinstance(raw_beats, dict):
            rows = raw_beats.get('beats') or raw_beats.get('segments')
            if isinstance(rows, list):
                segment_count = len(rows)
    work = operation.get('work')
    if segment_count is None and work:
        try:
            jobs_path = Path(work) / 'video_jobs.json'
            beats_path = Path(work) / 'beats.json'
            jobs = json.loads(jobs_path.read_text(encoding='utf-8-sig')) if jobs_path.is_file() else {}
            segments = jobs.get('segments') if isinstance(jobs, dict) else None
            if segments:
                segment_count = len(segments)
            elif beats_path.is_file():
                beats = json.loads(beats_path.read_text(encoding='utf-8-sig')).get('beats')
                segment_count = len(beats) if isinstance(beats, list) else None
        except (OSError, ValueError, TypeError):
            segment_count = None

    segment_title = ''
    current_beat = user_data.get('current_beat')
    if isinstance(current_beat, dict):
        segment_title = current_beat.get('title') or current_beat.get('plot') or ''
    if not segment_title:
        snapshot_context = snapshot.get('context') if isinstance(snapshot.get('context'), dict) else {}
        data_context = data.get('context') if isinstance(data.get('context'), dict) else {}
        segment_title = snapshot_context.get('title') or data_context.get('title') or ''
    if not segment_title:
        subject_arg = {'story':0, 'novel':0, 'beats':4}.get(kind)
        if subject_arg is not None and len(args) > subject_arg:
            segment_title = args[subject_arg]
        else:
            segment_title = data.get('video_issues') or data.get('issue') or ''
    subject = _title_component(segment_title, limit=10)
    if subject:
        task_label = f'{task_label}-{subject}'

    try:
        segment_index = int(segment_index) if segment_index is not None else None
    except (TypeError, ValueError):
        segment_index = None
    if segment_index and segment_index > 0:
        segment_part = f'第{segment_index:03d}段'
    elif segment_count:
        segment_part = f'全{int(segment_count)}段'
    else:
        segment_part = '全段'
    return '_'.join((task_label, project, episode_title, segment_part))


@contextmanager
def _connect():
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BRIDGE_DIR / "bridge.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, runtime TEXT, status TEXT, created REAL, updated REAL, lease TEXT, payload TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, seen REAL, providers TEXT)")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _dump(value):
    return json.dumps(value, ensure_ascii=False)


def get_settings():
    with _connect() as db:
        row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        if row:
            value = json.loads(row[0])
            changed = False
            # Older releases initialized every generation entry to the API,
            # which silently shadowed the global provider even when the user
            # had never chosen an entry-specific route. Treat those legacy
            # defaults as "follow global" once; explicitly selected providers
            # remain untouched, and API can still be selected per entry.
            if not value.get("routing_defaults_migrated"):
                overrides = value.setdefault("overrides", {})
                for entry in ENTRIES:
                    if overrides.get(entry) == "api":
                        overrides[entry] = "inherit"
                value["routing_defaults_migrated"] = True
                changed = True
            if 'default_providers' not in value:
                value['default_providers'] = [value.get('default_provider') or 'deepseek']
                changed = True
            overrides = value.setdefault('overrides', {})
            for entry in ENTRIES:
                if entry != 'revision' and entry not in overrides:
                    overrides[entry] = 'inherit'
                    changed = True
            limits = value.setdefault('concurrency', {})
            for key, limit in _default_concurrency().items():
                if key not in limits:
                    limits[key] = limit
                    changed = True
            if changed:
                db.execute("UPDATE settings SET value=? WHERE id=1", (_dump(value),))
            return value
        value = {"default_provider": "deepseek", "token": secrets.token_urlsafe(32),
                 "default_providers": ["deepseek"],
                 "overrides": {entry: "inherit" for entry in ENTRIES if entry != "revision"},
                 "routing_defaults_migrated": True,
                 "concurrency": _default_concurrency()}
        db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (_dump(value),))
        return json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])


def validate_route(route, *, inherit=False):
    allowed = {p["id"] for p in PROVIDERS if p["available"]} | {"api"}
    if inherit:
        allowed.add("inherit")
    if route not in allowed:
        raise ValueError("所选 LLM 服务尚未适配或不存在")
    return route


def validate_routes(routes, *, inherit=False, allow_empty=False):
    if isinstance(routes,str):
        routes=[routes]
    if allow_empty and routes == []:
        return []
    if not isinstance(routes,list) or not routes:
        raise ValueError('请至少选择一个 LLM 服务')
    result=[]
    for route in routes:
        route=validate_route(str(route),inherit=inherit)
        if route not in result:
            result.append(route)
    if 'inherit' in result and len(result)>1:
        raise ValueError('跟随默认不能与具体服务同时选择')
    if 'api' in result and len(result)>1:
        raise ValueError('API 当前只能单独使用，不能加入网页 LLM 混合调度')
    return result


def save_settings(payload):
    # Partial per-entry writes are transactional; separate tabs cannot silently
    # reset the other entries with an out-of-date whole-settings snapshot.
    get_settings()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        value = json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])
        value.setdefault('overrides', {})
        limits = value.setdefault('concurrency', _default_concurrency())
        for key, limit in (payload.get('concurrency') or {}).items():
            maximum = 12 if key == 'total' else 4
            if key not in limits or type(limit) is not int or not 1 <= limit <= maximum:
                raise ValueError('并发上限须为整数：总数 1–12，每个服务 1–4')
            limits[key] = limit
        if "default_provider" in payload:
            value["default_provider"] = validate_route(str(payload["default_provider"]))
            if value["default_provider"] == "api":
                raise ValueError("全局网页服务请选择 DeepSeek、ChatGPT 或豆包；API 可在各入口选择")
            value['default_providers']=[value['default_provider']]
        if 'default_providers' in payload:
            providers=validate_routes(payload['default_providers'],allow_empty=True)
            if 'api' in providers:
                raise ValueError('全局默认请选择网页服务；API 可在各功能中单独选择')
            value['default_providers']=providers
            value['default_provider']=providers[0] if providers else ''
        for entry, route in (payload.get("overrides") or {}).items():
            if entry not in ENTRIES:
                raise ValueError("未知的 LLM 功能入口")
            selected=validate_routes(route,inherit=True,allow_empty=True)
            value["overrides"][entry] = selected[0] if selected==['inherit'] else selected
        value["routing_defaults_migrated"] = True
        db.execute("UPDATE settings SET value=? WHERE id=1", (_dump(value),))
        return value


def routes_for(entry, override="inherit", *, config=None):
    requested=validate_routes(override,inherit=True)
    from services.text_jobs import current, check_cancelled
    check_cancelled()
    job = current()
    config = config or (job['routes'] if job else get_settings())
    route = requested if requested != ['inherit'] else validate_routes((config.get("overrides") or {}).get(entry,"inherit"),inherit=True)
    if route==['inherit']:
        route=validate_routes(config['default_providers'] if 'default_providers' in config else [config.get('default_provider') or 'deepseek'])
    return route


def route_for(entry, override="inherit"):
    """Compatibility helper for call sites that still require one provider."""
    return routes_for(entry,override)[0]


def choose_provider(providers, seed=''):
    """Choose the least-loaded eligible web provider with deterministic tie rotation."""
    candidates=validate_routes(providers)
    if len(candidates)==1:
        return candidates[0]
    with _connect() as db:
        counts={provider:0 for provider in candidates}
        for row in db.execute("SELECT payload FROM tasks WHERE status IN ('queued','claimed','waiting')"):
            provider=json.loads(row[0]).get('provider')
            if provider in counts:
                counts[provider]+=1
    offset=int(hashlib.sha256(str(seed).encode()).hexdigest()[:8],16)%len(candidates)
    ordered=candidates[offset:]+candidates[:offset]
    return min(ordered,key=lambda provider:counts[provider])


def entry_for_label(label):
    label = str(label).lower()
    if "story" in label:
        return "story"
    if "beat" in label:
        return "beats"
    if "prompt" in label:
        return "prompt"
    if "feedback" in label:
        return "feedback"
    return "other"


def runtime_key(root=None):
    return hashlib.sha256(str((root or get_project_dir()).resolve()).encode()).hexdigest()[:24]


def _decode(row):
    if not row:
        raise ValueError("修正任务不存在")
    value = json.loads(row["payload"])
    value.update(status=row["status"], updated_at=row["updated"], created_at=row["created"])
    if row["status"] == "cancelled":
        value["message"] = "已取消等待；网页中的生成不会自动停止，迟到结果不会应用。"
    elif row["status"] == "timeout":
        value["message"] = f"任务等待已达 {TIMEOUT} 秒，已停止等待，未自动重发。请检查扩展、登录状态和网页额度。"
    return value


def _expire(db):
    # Queue residence is not execution time. Only claimed work expires.
    now = time.time()
    for row in db.execute("SELECT id,created,payload FROM tasks WHERE status IN ('claimed','waiting')").fetchall():
        payload = json.loads(row['payload'])
        deadline = payload.get('execution_deadline_at') or payload.get('execution_started_at',row['created']) + TIMEOUT
        if now >= deadline:
            db.execute("UPDATE tasks SET status='timeout',updated=? WHERE id=?",(now,row['id']))


def create_task(provider, kind, data, *, entry="revision", metadata=None):
    validate_route(provider)
    from services.text_jobs import current
    operation = current()
    metadata = {**(metadata or {}), **({'operation_id':operation['id']} if operation else {})}
    operation_id = str(metadata.get('operation_id') or '')
    call_key = str(metadata.get('call_key') or '')
    # A deterministic web-task identity closes the crash window between
    # inserting a browser request and linking it back to its business task.
    identifier = (str(uuid.uuid5(uuid.UUID(operation_id), call_key))
                  if operation_id and call_key else str(uuid.uuid4()))
    start, end = f"AICF_RESULT_{identifier}_START", f"AICF_RESULT_{identifier}_END"
    if kind == "revision":
        instructions = (
            "你是视频提示词编辑。依据反馈输出完整修正版。保留剧情目标、人物身份、参考编号和对白归属。"
            "target_duration_sec 是本次目标总时长，优先于底稿及历史视频中的旧时长。"
            "目标时长存在时，必须同步任务概述总秒数、全部 Shot 起止时间、At 时间戳及动作时间描述，"
            "从 0 秒连续安排到目标总时长，最后一个 Shot 必须在目标总时长结束；按新时长合理安排动作与对白节奏。"
            "只修改反馈涉及的问题；不生成全局代码规则。没有提供视频，不要声称看过视频。"
            "只以 base_prompt 作为修正底稿；任务输入中不会包含其他候选提示词。"
        )
        example = {"request_id": identifier, "changes": ["修改说明"], "revised_prompt": "完整新提示词"}
    else:
        instructions = (
            "按下面 system 和 user 字段完成任务。保持所要求的业务数据结构。"
            "任务输入中的输出格式、字段数量和只输出要求仅约束 data 内部的业务结果。"
            "整个回复必须遵守本次外层结果包协议：保留 request_id 和 data，不能将业务字段直接放在外层。"
            "文本任务将完整文本放在 data 字符串中；JSON 任务将完整业务对象放在 data 对象中。"
        )
        example = {"request_id": identifier, "data": "任务要求的完整文本" if kind == "text" else {"任务要求的字段": "内容"}}
    format_rules = (
        "只返回一个结果包：开始标记、一个完整 JSON 对象、结束标记。不要输出解释、第二份结果或代码围栏。"
        "JSON 使用多行和两空格缩进，字段名必须用一对英文双引号包裹，字段名后必须有冒号。"
        "字符串中的英文双引号、换行和反斜杠必须按 JSON 规则转义；禁止尾随逗号。"
        "不得缩略数组或使用省略号。输出结束标记前，检查整份 JSON 的括号配对、逗号、字段和转义。"
        "下面只是包裹结构示例，data 内部必须采用任务实际要求的完整结构。"
    )
    request = f"{instructions}\n\n任务输入：\n{_dump(data)}\n\n{format_rules}\n{start}\n{json.dumps(example, ensure_ascii=False, indent=2)}\n{end}"
    request_fingerprint = hashlib.sha256(_dump([provider,kind,entry,data,request]).encode()).hexdigest()
    conversation_title = _conversation_title(operation, data, metadata, entry)
    value = {"id": identifier, "provider": provider, "kind": kind, "entry": entry,
             "request": request, "input": data, "metadata": metadata or {},
             "conversation_title": conversation_title, "request_fingerprint":request_fingerprint,
             "message": "等待扩展领取任务"}
    # Only consecutive revisions of the same segment share conversational scope.
    segment = (metadata or {}).get("segment_index")
    value["conversation_key"] = f"{runtime_key()}:revision:{segment}" if entry == "revision" and segment is not None else identifier
    now = time.time()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _expire(db)
        previous = db.execute("SELECT * FROM tasks WHERE id=?", (identifier,)).fetchone()
        if previous:
            saved = _decode(previous)
            if saved.get('request_fingerprint') != request_fingerprint:
                raise RuntimeError('恢复时网页任务输入与原请求不一致，已停止重发并转人工处理')
            return saved
        if kind == "revision":
            rows = db.execute("SELECT payload FROM tasks WHERE runtime=? AND status IN ('queued','claimed','waiting')", (runtime_key(),)).fetchall()
            if any(json.loads(row[0]).get("metadata", {}).get("segment_index") == value["metadata"].get("segment_index") for row in rows):
                raise ValueError("本段已有未结束的修正任务，请先等待或取消")
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", (identifier, runtime_key(), "queued", now, now, "", _dump(value)))
    return task_status(identifier)


def task_status(identifier, *, current_runtime=False):
    with _connect() as db:
        _expire(db)
        row = db.execute("SELECT * FROM tasks WHERE id=?", (identifier,)).fetchone()
        if row and current_runtime and row["runtime"] != runtime_key():
            raise ValueError("此任务属于其他项目或集数")
        value = _decode(row)
        if value["status"] == "timeout":
            value["message"] = f"任务等待已达 {TIMEOUT} 秒，已停止等待，未自动重发。请检查扩展、登录状态和网页额度。"
        return value


def attach_operation(identifier, operation_id):
    """Persist the business-operation link on an already-created web task."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM tasks WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("修正任务不存在")
        value = json.loads(row['payload'])
        value.setdefault('metadata', {})['operation_id'] = str(operation_id)
        db.execute("UPDATE tasks SET payload=?,updated=? WHERE id=?", (_dump(value),time.time(),identifier))
    return task_status(identifier)


def latest_task(segment_index):
    with _connect() as db:
        _expire(db)
        rows = db.execute("SELECT * FROM tasks WHERE runtime=? ORDER BY created DESC LIMIT 100", (runtime_key(),)).fetchall()
        return next((_decode(row) for row in rows if json.loads(row["payload"]).get("metadata", {}).get("segment_index") == segment_index), None)


def cancel_task(identifier, *, current_runtime=True):
    task_status(identifier, current_runtime=current_runtime)
    with _connect() as db:
        db.execute("UPDATE tasks SET status='cancelled', updated=? WHERE id=? AND status NOT IN ('complete','error','timeout')", (time.time(), identifier))
    return task_status(identifier)


def claim(client_id, providers, *, tab_id=None):
    providers = [p for p in providers if p in {x["id"] for x in PROVIDERS if x["available"]}]
    if not client_id or len(client_id) > 100:
        raise ValueError("扩展客户端编号无效")
    if tab_id is not None and (not isinstance(tab_id,int) or isinstance(tab_id,bool) or tab_id <= 0):
        raise ValueError('绑定标签页编号无效')
    limits = get_settings()['concurrency']
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _expire(db)
        db.execute("INSERT OR REPLACE INTO clients VALUES (?,?,?)", (client_id, time.time(), _dump(providers)))
        # Reserve capacity and the tab together in this transaction.
        active = [_decode(row) for row in db.execute(
            "SELECT * FROM tasks WHERE status IN ('claimed','waiting')").fetchall()]
        if len(active) >= limits['total']:
            return None
        busy_tabs = {(task.get('claimed_client_id'),task.get('claimed_tab_id')) for task in active}
        if tab_id is not None and (client_id,tab_id) in busy_tabs:
            return None
        # A lease is never auto-requeued: uncertain sends must not duplicate.
        rows = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created").fetchall()
        for row in rows:
            value = _decode(row)
            provider = value['provider']
            same_provider = [task for task in active if task['provider'] == provider]
            # Old extensions without tab identities stay serial. They cannot
            # safely share a provider with pooled execution.
            capacity = limits.get(provider,1) if tab_id is not None else 1
            if (provider not in providers or len(same_provider) >= capacity or
                    any(task.get('claimed_tab_id') is None for task in same_provider)):
                continue
            lease = secrets.token_urlsafe(24)
            value['execution_started_at'] = time.time()
            value['execution_deadline_at'] = value['execution_started_at'] + TIMEOUT
            value['claimed_client_id'] = client_id
            value['claimed_tab_id'] = tab_id
            db.execute("UPDATE tasks SET status='claimed', lease=?, updated=?,payload=? WHERE id=?", (lease, time.time(), _dump(value), row["id"]))
            return {**value, "status": "claimed", "lease": lease}
    return None


def pool_status():
    limits = get_settings()['concurrency']
    with _connect() as db:
        _expire(db)
        queued = {}
        for row in db.execute("SELECT payload FROM tasks WHERE status='queued'"):
            provider = json.loads(row[0])['provider']
            queued[provider] = queued.get(provider,0) + 1
    return {'limits':limits,'queued':queued}


def validate_result(task, result):
    if not isinstance(result, dict):
        raise ValueError("扩展结果必须是 JSON 对象")
    if task["kind"] == "revision":
        if not isinstance(result.get("revised_prompt"), str) or not result["revised_prompt"].strip():
            raise ValueError("缺少完整修正提示词")
        if not isinstance(result.get("changes"), list) or not all(isinstance(v, str) for v in result["changes"]):
            raise ValueError("修改说明格式错误")
    elif task["kind"] == "json" and not isinstance(result.get("data"), dict):
        raise ValueError("LLM 返回值必须是 JSON 对象")
    elif task["kind"] == "text" and not isinstance(result.get("data"), str):
        raise ValueError("LLM 返回值必须是文本")


def report(identifier, lease, status, *, result=None, message="", raw="", diagnostics=None):
    if status not in {"waiting", "complete", "error"}:
        raise ValueError("无效任务状态")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _expire(db)
        row = db.execute("SELECT * FROM tasks WHERE id=?", (identifier,)).fetchone()
        value = _decode(row)
        if not lease or not secrets.compare_digest(row["lease"], lease):
            raise PermissionError("任务领取凭据不匹配")
        if row["status"] in TERMINAL:
            # Keep a late browser response for inspection, while leaving the
            # cancelled/failed/timed-out task terminal and unapplied.
            if status == "complete":
                try:
                    validate_result(value, result)
                    late = {'received_at':time.time(),'result':result,'raw':str(raw)[:500000]}
                except Exception as exc:
                    late = {'received_at':time.time(),'message':str(exc),'raw':str(raw)[:500000]}
                value.setdefault('late_results',[]).append(late)
                value['late_results'] = value['late_results'][-5:]
                db.execute("UPDATE tasks SET payload=?,updated=? WHERE id=?",
                           (_dump(value),time.time(),identifier))
            return value
        if status == "complete":
            validate_result(value, result)
            value["result"] = result
        message = str(message)[:2000]
        history = value.setdefault("status_history", [])
        if not history or history[-1].get("status") != status or history[-1].get("message") != message:
            history.append({"at": time.time(), "status": status, "message": message})
            value["status_history"] = history[-200:]
        value.update(message=message, raw=str(raw)[:500000])
        if isinstance(diagnostics, list):
            value["continuation_diagnostics"] = [
                entry for entry in diagnostics[-200:]
                if isinstance(entry, dict) and len(_dump(entry)) <= 4000
            ]
        db.execute("UPDATE tasks SET status=?, updated=?, payload=? WHERE id=?", (status, time.time(), _dump(value), identifier))
    web_results.publish()
    return task_status(identifier)


def connection_status():
    with _connect() as db:
        rows = db.execute("SELECT * FROM clients WHERE seen>?", (time.time() - 90,)).fetchall()
    return {"connected": bool(rows), "providers": sorted({p for row in rows for p in json.loads(row["providers"])})}


def heartbeat(client_id, providers):
    if not client_id or len(client_id) > 100:
        raise ValueError("扩展客户端编号无效")
    providers = [p for p in providers if p in {x["id"] for x in PROVIDERS if x["available"]}]
    with _connect() as db:
        db.execute("INSERT OR REPLACE INTO clients VALUES (?,?,?)", (client_id, time.time(), _dump(providers)))


def wait_for_llm(system_prompt, user_prompt, *, kind, entry, provider, call_hint=None):
    from services.text_jobs import NeedsAttentionError, check_cancelled, current, link_child, phase
    check_cancelled()
    started = time.time()
    job = current()
    request_data = {"system": str(system_prompt), "user": str(user_prompt)}
    call_index = int(job.get('_web_call_cursor',0)) if job else 0
    call_key = 'llm-'+(re.sub(r'[^a-zA-Z0-9_.:-]+','-',str(call_hint)).strip('-')[:120]
                       if call_hint else f"{call_index:04d}")
    if job and not call_hint:
        job['_web_call_cursor'] = call_index + 1
    candidates=validate_routes(provider)
    existing=None
    if job:
        child_id=str(uuid.uuid5(uuid.UUID(job['id']),call_key))
        try:
            existing=task_status(child_id,current_runtime=False)
        except ValueError:
            pass
    if existing:
        selected = existing['provider']
        task = create_task(selected, kind, request_data, entry=entry,
                           metadata={'call_key':call_key} if job else None)
    else:
        # Keep selection and insertion together inside this process. Concurrent
        # segment compilers then observe one another's queued tasks and spread
        # across the least loaded selected services.
        with _ROUTE_LOCK:
            if job:
                try:
                    existing = task_status(child_id, current_runtime=False)
                except ValueError:
                    existing = None
            selected = existing['provider'] if existing else choose_provider(
                candidates, f"{job['id'] if job else runtime_key()}:{call_key}")
            task = create_task(selected, kind, request_data, entry=entry,
                               metadata={'call_key':call_key} if job else None)
    try:
        if job:
            from services.text_jobs import record_web_call
            record_web_call(job['id'],call_key,task['id'],provider=selected,candidates=candidates)
        else:
            link_child(task['id'])
        check_cancelled()
    except Exception:
        cancel_task(task['id'],current_runtime=False)
        raise
    from services.logger import log
    log(f"[网页 LLM] {entry}: 等待 {selected} 扩展任务 {task['id']}", "STEP")
    last_progress, last_logged = None, started
    while True:
        try:
            check_cancelled()
        except Exception:
            cancel_task(task['id'],current_runtime=False)
            raise
        revision = web_results.revision()
        task = task_status(task["id"])
        phase(f"{entry} · {selected} · {task['status']} · {task.get('message','')}")
        now = time.time()
        progress = (task["status"], task.get("message", ""))
        if progress != last_progress or now - last_logged >= 30:
            log(f"[网页 LLM] {entry}: {progress[0]} · {progress[1]} · 已等待 {int(now - started)} 秒", "STEP")
            last_progress, last_logged = progress, now
        if task["status"] == "complete":
            return task["result"]["data"], round(time.time() - started, 2)
        if task["status"] == "timeout":
            raise NeedsAttentionError('原网页任务已超时，无法确认请求是否已发送；请检查原会话和任务记录，不会自动重发。')
        if task["status"] in TERMINAL:
            raise RuntimeError(task.get("message") or f"网页 LLM 任务 {task['status']}")
        web_results.wait(revision, 1)


def segment_snapshot(index):
    root = get_project_dir()
    path = root / "video_jobs.json"
    jobs = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    segment = next((s for s in jobs.get("segments", []) if int(s.get("segment_index", 0)) == index), None)
    if segment is None:
        raise ValueError("找不到当前分段")
    job = segment.get("job") or {}
    config = segment.get("generation_config") or {}
    videos = []
    for render in segment.get("renders") or []:
        output = render.get("output") or {}
        video = Path(output.get("video_path") or "")
        if output.get("video_path") and not output.get("video_deleted") and video.is_file():
            videos.append((render, video))
    videos.sort(key=lambda pair: (pair[0].get("created_at", ""), pair[1].stat().st_mtime), reverse=True)
    render, video = videos[0] if videos else ({}, None)
    if video is None:
        for candidate in (segment.get("video_path"), segment.get("stale_video_path")):
            if candidate and Path(candidate).is_file():
                video = Path(candidate)
                break
    # Never label today's edited prompt as the historical video prompt.
    original = str(render.get("prompt_snapshot") or "")
    current = str(config.get("prompt_override") or job.get("final_prompt") or job.get("prompt") or job.get("director_prompt") or "")
    asset_manifest = config.get("asset_manifest") or job.get("asset_manifest") or []
    source_excerpt = str(segment.get("source_excerpt") or "").strip()
    beats_path = root / "beats.json"
    if beats_path.is_file():
        try:
            beats_data = json.loads(beats_path.read_text(encoding="utf-8-sig"))
            beats = beats_data.get("beats") if isinstance(beats_data, dict) else None
            beat_uid = str(segment.get("beat_uid") or "")
            beat = next((item for item in beats or [] if isinstance(item, dict) and beat_uid and str(item.get("beat_uid") or "") == beat_uid), None)
            if beat is None and isinstance(beats, list) and 0 < int(index) <= len(beats):
                beat = beats[int(index) - 1]
            if isinstance(beat, dict):
                source_excerpt = str(beat.get("source_excerpt") or source_excerpt).strip()
        except (OSError, ValueError, TypeError):
            pass
    try:
        from services.context import get_current_episode_name, get_current_project_name
        project_name = get_current_project_name()
        episode_name = get_current_episode_name()
    except Exception:
        project_name = root.parent.name if root.parent.name else root.name
        episode_name = root.name if root.parent.name == project_name else ""
    prompt_version_info = config.get("prompt_version_info") if isinstance(config.get("prompt_version_info"), dict) else {}
    latest_prompt_revision = config.get("latest_prompt_revision") if isinstance(config.get("latest_prompt_revision"), dict) else {}
    revision_prompt = str(latest_prompt_revision.get("prompt") or "").strip()
    data = {"runtime": runtime_key(root), "project_name": project_name, "episode_name": episode_name,
            "segment_count": len(jobs.get("segments") or []),
            "segment_index": index, "segment_uid": segment.get("segment_uid", ""),
            "beat_uid": segment.get("beat_uid", ""),
            "render_id": render.get("render_id", ""), "video_url": "/gradio_api/file=" + video.resolve().as_posix() if video else "",
            "video_name": video.name if video else "", "video_created_at": render.get("created_at", ""),
            "original_prompt": original, "current_prompt": current, "snapshot_known": bool(original),
            "prompt_version_info": prompt_version_info,
            "latest_prompt_revision": latest_prompt_revision,
            "prompt_matches_revision": bool(revision_prompt) and current.strip().replace("\r\n", "\n").replace("\r", "\n") == revision_prompt.replace("\r\n", "\n").replace("\r", "\n"),
            "duration_sec": job.get("duration_sec"),
            "context": {"title": segment.get("title", ""), "source_excerpt": source_excerpt,
                        "plot": (segment.get("job") or {}).get("plot", ""),
                        "requested_media_spec": {**(segment.get("requested_media_spec") or {}), "duration_sec": job.get("duration_sec"), "fps": job.get("fps")},
            "historical_video_media_spec": render.get("requested_media_spec") or {},
            "asset_manifest": asset_manifest, "reference_images": job.get("reference_images") or [],
            "background_image": job.get("background_image") or ""}}
    # Version labels and timestamps are display metadata. They must not make a
    # pending revision look stale when the actual prompt and references are unchanged.
    fingerprint_data = {
        key: value for key, value in data.items()
        if key not in {"prompt_version_info", "latest_prompt_revision", "prompt_matches_revision"}
    }
    data["fingerprint"] = hashlib.sha256(_dump(fingerprint_data).encode()).hexdigest()
    return data


def revision_task(index, payload, *, operation_id=None, provider_override=None, snapshot_override=None):
    snapshot = snapshot_override or segment_snapshot(index)
    if payload.get("runtime") != snapshot["runtime"] or payload.get("fingerprint") != snapshot["fingerprint"]:
        raise ValueError("项目、视频或保存的提示词已变化，请刷新预览后重新提交")
    issue = str(payload.get("issue") or "").strip()
    edited = str(payload.get("edited_prompt") or "").strip()
    use_editor = payload.get("use_editor_prompt") is True
    original = "" if use_editor else (snapshot["original_prompt"] or str(payload.get("original_prompt") or "").strip())
    base_prompt = edited if use_editor else original
    if not issue or not base_prompt:
        raise ValueError("问题描述及所选提示词不能为空；未勾选时需要生成视频时的原提示词")
    providers = routes_for("revision", provider_override or payload.get('providers') or payload.get("provider") or "inherit")
    if providers == ["api"]:
        raise ValueError("视频反馈修正当前使用网页扩展，请在此入口选择 DeepSeek、ChatGPT 或豆包")
    existing_id=str(uuid.uuid5(uuid.UUID(str(operation_id)),'revision')) if operation_id else ''
    try:
        existing_task=task_status(existing_id,current_runtime=False) if existing_id else None
    except ValueError:
        existing_task=None
    provider=existing_task['provider'] if existing_task else choose_provider(providers,
        f"{operation_id or snapshot['runtime']}:{index}:revision")
    existing = latest_task(index)
    same_operation = bool(operation_id and existing and
                          existing.get('metadata',{}).get('operation_id') == str(operation_id))
    if existing and existing["status"] not in TERMINAL and not same_operation:
        raise ValueError("本段已有未结束的修正任务，请先等待或取消")
    llm_input = {"base_prompt": base_prompt, "target_duration_sec": snapshot["duration_sec"],
                 "video_issues": issue, "expected_result": str(payload.get("expected") or ""),
                 "context": snapshot["context"]}
    current_version = int((snapshot.get("prompt_version_info") or {}).get("version") or 0)
    latest_version = int((snapshot.get("latest_prompt_revision") or {}).get("version") or 0)
    return create_task(provider, "revision", llm_input,
                       metadata={"segment_index": index, "snapshot": snapshot,
                                 "prompt_version": max(current_version, latest_version) + 1,
                                 "provider_candidates":providers,
                                 "prompt_source": "editor" if use_editor else "video",
                                 "force_new_conversation": use_editor,
                                 "editor_prompt": edited,
                                 "editor_references": str(payload.get("editor_references") or ""),
                                 **({'operation_id':str(operation_id),'call_key':'revision'} if operation_id else {})})
