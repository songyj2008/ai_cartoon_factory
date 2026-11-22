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
    {"id": "qianwen", "label": "千问", "url": "https://www.qianwen.com/", "available": True},
]
ENTRIES = {"story": "剧情生成", "beats": "Beats 拆分", "prompt": "分段提示词", "feedback": "视频问题分析", "revision": "视频反馈修正", "video_learning": "视频 OK 经验分析", "other": "其他 LLM 调用"}
TERMINAL = {"complete", "error", "cancelled", "timeout"}
TIMEOUT = 900
QUEUE_TIMEOUT = 900
HISTORY_RETENTION = 24 * 60 * 60
FAILURE_RETENTION = 7 * 24 * 60 * 60
_ROUTE_LOCK = threading.RLock()


class TaskGoneError(ValueError):
    """An authenticated extension may retire a task absent from this backend."""


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
        'novel_beats':'小说Beats', 'novel_segment':'分段规划',
        'prompts':'提示词生成', 'segment':'提示词重生',
        'source_replan':'重新规划时长', 'pipeline':'一键流程',
        'split':'Beat拆分', 'feedback':'问题分析', 'metadata':'素材补全',
        'revision_wait':'提示词修正', 'revision':'提示词修正', 'video_learning':'成片经验分析',
    }
    kind = operation.get('kind')
    task_label = kinds.get(kind) or {
        'story':'剧情生成', 'beats':'拆Beats', 'prompt':'提示词生成',
        'feedback':'问题分析', 'revision':'提示词修正', 'video_learning':'成片经验分析',
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
    conn.execute("CREATE TABLE IF NOT EXISTS browser_clients (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS extension_updates (client_id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS client_capabilities (id TEXT PRIMARY KEY, seen REAL, capabilities TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS task_tombstones (id TEXT PRIMARY KEY, deleted REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS queue_reset (id INTEGER PRIMARY KEY, epoch REAL NOT NULL)")
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
        if 'primary_text_client_id' in payload:
            client = str(payload['primary_text_client_id'])
            if not db.execute('SELECT 1 FROM clients WHERE id=?', (client,)).fetchone():
                raise ValueError('浏览器实例不存在')
            value['primary_text_client_id'] = client
        if 'video_browser_defaults' in payload:
            defaults = payload['video_browser_defaults']
            if not isinstance(defaults, dict):
                raise ValueError('视频浏览器默认配置格式错误')
            for client in defaults.values():
                if client and not db.execute('SELECT 1 FROM clients WHERE id=?', (str(client),)).fetchone():
                    raise ValueError('视频浏览器实例不存在')
            value.setdefault('video_browser_defaults', {}).update(defaults)
        if 'video_browser_accounts' in payload:
            accounts = payload['video_browser_accounts']
            if not isinstance(accounts, dict) or any(not isinstance(v, str) for v in accounts.values()):
                raise ValueError('视频账号标识格式错误')
            value.setdefault('video_browser_accounts', {}).update({str(k): v.strip()[:100] for k, v in accounts.items()})
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
            if entry == 'video_learning' and 'api' in selected:
                raise ValueError('视频 OK 经验分析请选择网页 LLM 服务')
            value["overrides"][entry] = selected[0] if selected==['inherit'] else selected
        value["routing_defaults_migrated"] = True
        db.execute("UPDATE settings SET value=? WHERE id=1", (_dump(value),))
        return value


def resolve_page_providers(providers, *, config=None):
    """A fullscreen page's Inherit means global, not an entry override."""
    requested=validate_routes(providers,inherit=True)
    if 'api' in requested:
        raise ValueError('本页面其他功能仅支持网页 LLM；API 请使用“仅规划时长”复选框')
    if requested!=['inherit']:
        return requested
    config=config or get_settings()
    resolved = validate_routes(config['default_providers'] if 'default_providers' in config
                               else [config.get('default_provider') or 'deepseek'])
    if 'api' in resolved:
        raise ValueError('本页面默认渠道须为网页 LLM；API 请使用“仅规划时长”复选框')
    return resolved


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
    # Continuity operations share the Other route, including the historical
    # state-trace label whose "prompt" substring must not select prompt routing.
    if label == "h3_composition_patch" or label.startswith("h3_prompt_state_trace_"):
        return "other"
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
        raise TaskGoneError("原网页任务已结束或已清理")
    value = json.loads(row["payload"])
    value.update(status=row["status"], updated_at=row["updated"], created_at=row["created"])
    if row["status"] == "cancelled":
        value["message"] = "已取消等待；网页中的生成不会自动停止，迟到结果不会应用。"
    elif row["status"] == "timeout":
        value["message"] = (
            f"等待扩展领取已达 {QUEUE_TIMEOUT} 秒，已释放队列占用；请检查扩展连接和登录状态。"
            if value.get('timeout_phase') == 'queue' else
            f"任务等待已达 {TIMEOUT} 秒，已停止等待，未自动重发。请检查扩展、登录状态和网页额度。")
    return value


def _expire(db):
    # Queue residence is not execution time. Only claimed work expires.
    now = time.time()
    for row in db.execute("SELECT id,payload FROM tasks WHERE status='queued' AND created<=?",
                          (now - QUEUE_TIMEOUT,)).fetchall():
        value = json.loads(row['payload'])
        value['timeout_phase'] = 'queue'
        db.execute("UPDATE tasks SET status='timeout',updated=?,payload=? WHERE id=?",
                   (now, _dump(value), row['id']))
    for row in db.execute("SELECT id,created,payload FROM tasks WHERE status IN ('claimed','waiting')").fetchall():
        payload = json.loads(row['payload'])
        deadline = payload.get('execution_deadline_at') or payload.get('execution_started_at',row['created']) + TIMEOUT
        if now >= deadline:
            db.execute("UPDATE tasks SET status='timeout',updated=? WHERE id=?",(now,row['id']))


def cleanup_history(protected_ids=(), *, now=None):
    """Keep referenced results; retain small tombstones to prevent replay."""
    now = time.time() if now is None else now
    protected = set(protected_ids)
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute("SELECT id FROM tasks WHERE (status='complete' AND updated<=?) "
                          "OR (status IN ('error','cancelled','timeout') AND updated<=?)",
                          (now - HISTORY_RETENTION, now - FAILURE_RETENTION)).fetchall()
        identifiers = [(row['id'],) for row in rows if row['id'] not in protected]
        db.executemany('INSERT OR REPLACE INTO task_tombstones VALUES (?,?)',
                       ((identifier, now) for (identifier,) in identifiers))
        db.executemany('DELETE FROM tasks WHERE id=?', identifiers)
        db.execute('DELETE FROM task_tombstones WHERE deleted<=?', (now - FAILURE_RETENTION,))
        db.execute('DELETE FROM clients WHERE seen<=?', (now - FAILURE_RETENTION,))
    return len(identifiers)


def _h3_revision_shot_speech(prompt, manifest=None):
    """Read the same dialogue state the Shot editor displays, from the selected draft."""
    from generation.pipelines.minimax_h3.shot_editor import parse_prompt

    try:
        document = parse_prompt(prompt, 'revision', manifest)
    except ValueError:
        # Unstructured drafts remain editable; do not invent a silent timeline.
        return []
    result = []
    for index, shot in enumerate(document['shots'], 1):
        options = {option['key']: option for option in shot['speaker_options']}
        dialogues = [
            {'speaker': options[row['speaker_key']]['tag'], 'scope': row['scope'], 'line': row['line'], **{k: row[k] for k in ('delivery_spans', 'source_type', 'voice_asset_id') if k in row}}
            for row in shot['dialogues']
        ]
        result.append({'shot': index, 'start_sec': shot['original_start_cs'] / 100,
                       'end_sec': shot['original_end_cs'] / 100,
                       'editor_state': ('有台词' if shot['speech_state'] == 'tagged' else
                                        '无台词' if shot['speech_state'] in ('explicit_silent', 'silent') else '未确认'),
                       'speech_state': shot['speech_state'], 'issues': shot['speech_issues'],
                       **({'rule_issues': shot['speech_rule_issues']} if shot.get('speech_rule_issues') else {}),
                       'dialogues': dialogues})
    return result


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
            "剧情、台词、说话归属及镜头内容均以 base_prompt 为准，除非 video_issues 或 expected_result 明确要求改变。"
            "context 仅为辅助信息，不得从其中恢复底稿已删除的台词、人物发言或事件，也不得用旧上下文覆盖当前底稿。"
        )
        if data.get("model_id") == "minimax_h3_local_ref2va":
            from generation.pipelines.minimax_h3.rules import h3_segment_planning_rules
            from services.video_experience import retrieve, PLANNING_RULES as EXPERIENCE_RULES

            shot_speech = _h3_revision_shot_speech(data.get('base_prompt') or '', metadata.get('speech_manifest'))
            if shot_speech:
                data = {**data, 'shot_speech': shot_speech}
            try:
                experiences = retrieve(metadata.get('speech_manifest') or [], prompt=data.get('base_prompt') or '', purpose='revision')
            except Exception as exc:
                from services.logger import log
                log(f'[video_learning] 经验库暂不可用，本次按所选底稿修正：{exc}', 'WARN')
                experiences = []
            if experiences:
                data = {**data, 'approved_prompt_experiences': experiences}
                instructions += '\n\n' + EXPERIENCE_RULES + (
                    '\n本次仅修正反馈指定范围及直接冲突，base_prompt 仍是唯一创作底稿。'
                    '案例只辅助本次相关问题的声画衔接与声音归属，不授权新增剧情、人物、动作或台词，'
                    '也不授权重写无关镜头。保持本次 revised_prompt 输出协议。')
            instructions += "\n\n" + h3_segment_planning_rules() + (
                "\n当前为反馈修正：以 base_prompt 的台词、人物和参考编号为准，保留现有素材编号。"
                "\n【按现有 Shot 状态修正声音（依据 H3 手册：画面过程说明、镜头怎么拆）】"
                "shot_speech 如有提供，是所选 base_prompt 按现有 Shot 编辑器读取的台词状态，"
                "逐镜列出原起止时间、说话人、发声方式和台词，不是另一份创作底稿。"
                "speech_state 为 unverified 或标记‘未确认’时，只表示结构未能确认，必须结合原文及跨镜声音理解，"
                "不能据此令全镜静默；修正版须明确写出对白标签或全镜无对白。"
                "标记‘无台词’的 Shot，明确写本镜无对白，仅保留底稿已有动作声、环境声及必要呼吸声；"
                "台词状态与写法合规性分开判断：rule_issues 是待修正的写法问题，不改变无台词状态。"
                "仅当本次反馈涉及声音、口型或明确要求全段规范化时，补齐相关镜头的无对白与听者说明；"
                "不得新增台词、凭空添加声音或改变 Shot 时长。明确的跨镜续说须保留并注明范围；"
                "画内人物不做说话口型，不为填时添加对白、旁白、电话声，也不从上下文恢复已删除的台词。"
                "有台词时按 dialogues 的 speaker、scope、line 明确谁说、从哪里发声及原文；"
                "正文采用自然语言：画内用‘人物说’，画外用‘人物在画外说’，电话用‘人物在画外通过电话说’，旁白用‘画外旁白说’，其后接‘：<d>[Chinese]台词原文</d>’。speaker、scope、line 只用于内部归属，不抄写 telephone、onscreen 等内部字段值；电话和画外音不要求额外音色素材，有参考时按指定用途使用。"
                "不额外添加‘对白标签’或‘说话人’标题，不改变台词原文。"
                "offscreen 写清画外说话人，telephone 写清电话另一端声音，narration 写清旁白；"
                "画内听者和其他未发言人物不代说、不对这些声音做说话口型。"
                "若底稿明确一句话跨 Shot 延续，保留同一声音身份和续说关系，明确覆盖的镜头；"
                "空对白列表仅表示本镜没有独立台词，不能截断明确的跨镜续说，不能因切镜重说整句。"
                "人物在某镜画外发声不等于整段不得出镜；音色参考只锁定对应人物音色，不整段播放参考录音。"
                "【输出从简】有对白时保证自然发声，辅助表演尽量并行；无对白时以必要事件和表演为主。"
                "默认局部修正：保留底稿有效的镜头风格、必要动作、站位、情绪和表演，只修改反馈指定问题及直接冲突，不重新组织整段表演。仅用户明确要求精简或去冗余时清理已有冗余。"
                "每镜围绕台词按需表达动作与情绪，不强制补齐表演字段，不机械限制细节数量；保留剧情必要动作。"
                "电话或画外对白明确说话人、音色引用、声音来源及听者口型覆盖范围，台词原文只在 <d> 中出现一次，声音段不再转述、解释或指导逐字发音。"
                "音色绑定只在独立的【人物音色绑定】章节写一次，主体定义只写人物简介与图片绑定。全段通用的音色参考、防代说和口型约束集中说明一次，每个 Shot 必须保留当前发声、声源及听者状态；无台词镜头保留明确无对白说明。"
                "只在发生必要变化时补充连续性，不反复列出沿用的站位、持物、姿态或听者状态；清理与本镜台词状态矛盾的描述。"
                "底稿时间轴已经覆盖 target_duration_sec 时，保留 Shot 数量、起止时间和 At 时间戳；"
                "仅在本次目标总时长不同或反馈明确要求调整时间时重新安排。"
                "video_issues 或 expected_result 明确要求改变某镜台词状态时按该要求修改，其他镜头保持。"
                "输出仍采用本次 revised_prompt 协议，不输出规划器的 Shots JSON。"
                "修正后的提示词正文使用自然语言描述画面人物，例如‘画面人物：<Subject 1>、<Subject 2>。’，"
                "不要输出 visible_roles: [...] 等内部结构字段或数组；原稿有这种写法时，保留人物范围并改为上述自然语言。"
                "若约束无法同时满足，在 changes 说明冲突，revised_prompt 保留底稿，不能伪造可执行的新稿。"
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
    from services.llm_payloads import request_metrics
    input_metrics = request_metrics(request)
    request_fingerprint = hashlib.sha256(_dump([provider,kind,entry,data,request]).encode()).hexdigest()
    conversation_title = _conversation_title(operation, data, metadata, entry)
    value = {"id": identifier, "provider": provider, "kind": kind, "entry": entry,
             "request": request, "input": data, "metadata": metadata or {}, "input_metrics": input_metrics,
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
        reset = db.execute('SELECT epoch FROM queue_reset WHERE id=1').fetchone()
        if operation and reset and float(operation.get('created', now)) <= reset[0]:
            raise TaskGoneError('任务已被一键清空，不再创建网页请求')
        if not previous and db.execute('SELECT 1 FROM task_tombstones WHERE id=?', (identifier,)).fetchone():
            raise TaskGoneError('原网页任务已清理，不会重新发送')
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
        if row is None:
            raise TaskGoneError('原网页任务已结束或已清理')
        if row and current_runtime and row["runtime"] != runtime_key():
            raise ValueError("此任务属于其他项目或集数")
        value = _decode(row)
        return value


def leased_task(identifier, lease):
    with _connect() as db:
        _expire(db)
        row = db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
        value = _decode(row)
        if row['status'] not in {'claimed','waiting'} or not lease or not secrets.compare_digest(row['lease'], lease):
            raise PermissionError('视频附件领取凭据无效或任务已结束')
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


def claim(client_id, providers, *, tab_id=None, capabilities=None):
    if capabilities is not None and (not isinstance(capabilities, list) or not all(isinstance(v, str) for v in capabilities)):
        raise ValueError('扩展能力列表格式错误')
    providers = [p for p in providers if p in {x["id"] for x in PROVIDERS if x["available"]}]
    if not client_id or len(client_id) > 100:
        raise ValueError("扩展客户端编号无效")
    if tab_id is not None and (not isinstance(tab_id,int) or isinstance(tab_id,bool) or tab_id <= 0):
        raise ValueError('绑定标签页编号无效')
    limits = get_settings()['concurrency']
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _expire(db)
        if not _text_owner(db, client_id):
            return None
        from services.extension_updates import draining
        if draining(db, client_id):
            return None
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
            required = value.get('metadata', {}).get('required_capability')
            if required and required not in (capabilities or []):
                continue
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
        reset = db.execute('SELECT epoch FROM queue_reset WHERE id=1').fetchone()
    return {'limits':limits,'queued':queued,'reset_epoch':reset[0] if reset else 0}


def clear_all_tasks():
    """Invalidate every lease and tell connected extensions to drop local tasks."""
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        ids = [row[0] for row in db.execute('SELECT id FROM tasks')]
        epoch = time.time()
        db.executemany('INSERT OR REPLACE INTO task_tombstones VALUES (?,?)', [(i, epoch) for i in ids])
        db.execute('DELETE FROM tasks')
        db.execute('INSERT OR REPLACE INTO queue_reset VALUES (1,?)', (epoch,))
    return {'web_tasks':len(ids), 'reset_epoch':epoch}


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
            if value['kind'] == 'revision' and value.get('input', {}).get('model_id') == 'minimax_h3_local_ref2va':
                from generation.pipelines.minimax_h3.revision_review import compare_revision
                from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
                result = {**result, 'revised_prompt': H3PromptCompiler.normalize_visible_role_lines(result['revised_prompt'])}
                inputs = value['input']
                result = {**result, 'revision_validation': compare_revision(
                    inputs.get('base_prompt') or '', result['revised_prompt'],
                    manifest=(value.get('metadata') or {}).get('speech_manifest'),
                    target_duration_sec=inputs.get('target_duration_sec'))}
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
    primary = get_settings().get('primary_text_client_id')
    with _connect() as db:
        rows = db.execute("SELECT * FROM clients WHERE seen>? AND id=?", (time.time() - 90, primary)).fetchall()
    return {"connected": bool(rows), "providers": sorted({p for row in rows for p in json.loads(row["providers"])})}


def heartbeat(client_id, providers, capabilities=None, provider_capabilities=None, browser=None):
    get_settings()
    if not client_id or len(client_id) > 100:
        raise ValueError("扩展客户端编号无效")
    providers = [p for p in providers if p in {x["id"] for x in PROVIDERS if x["available"]}]
    if provider_capabilities is not None and (
        not isinstance(provider_capabilities, dict) or
        any(key not in {p['id'] for p in PROVIDERS} or not isinstance(values, list) or
            not all(isinstance(value, str) for value in values)
            for key, values in provider_capabilities.items())
    ):
        raise ValueError('按服务声明的扩展能力格式错误')
    advertised = ({provider: provider_capabilities.get(provider, []) for provider in providers}
                  if provider_capabilities is not None else
                  [v for v in capabilities or [] if isinstance(v, str)])
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR REPLACE INTO clients VALUES (?,?,?)", (client_id, time.time(), _dump(providers)))
        if browser is not None:
            if not isinstance(browser, dict):
                raise ValueError('浏览器信息格式错误')
            if not isinstance(browser.get('channels', []), list):
                raise ValueError('浏览器视频渠道格式错误')
            info = {key: str(browser.get(key) or '')[:100] for key in ('name', 'type', 'version')}
            info['channels'] = [str(v) for v in browser.get('channels', []) if isinstance(v, str)]
            db.execute('INSERT OR REPLACE INTO browser_clients VALUES (?,?)', (client_id, _dump(info)))
        _text_owner(db, client_id, register=True)
        db.execute('INSERT OR REPLACE INTO client_capabilities VALUES (?,?,?)',
                   (client_id, time.time(), _dump(advertised)))


def browser_clients():
    with _connect() as db:
        rows = db.execute('SELECT clients.*,browser_clients.metadata FROM clients LEFT JOIN browser_clients ON clients.id=browser_clients.id').fetchall()
    result = []
    for row in rows:
        info = json.loads(row['metadata'] or '{}')
        result.append({'id': row['id'], **info, 'name': info.get('name') or row['id'][:8],
                       'online': row['seen'] > time.time()-90, 'seen': row['seen']})
    return result


def _text_owner(db, client_id, *, register=False):
    row = db.execute('SELECT value FROM settings WHERE id=1').fetchone()
    if row is None:
        return True
    settings = json.loads(row[0])
    primary = settings.get('primary_text_client_id')
    if not primary:
        if not register:
            return True
        primary = client_id
        settings['primary_text_client_id'] = primary
        db.execute('UPDATE settings SET value=? WHERE id=1', (_dump(settings),))
    active = db.execute("SELECT payload FROM tasks WHERE status IN ('claimed','waiting')").fetchall()
    owners = {json.loads(task[0]).get('claimed_client_id') for task in active}
    return client_id == primary and not (owners - {None, primary})


def text_owner(client_id):
    get_settings()
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _expire(db)
        from services.extension_updates import draining
        return not draining(db, client_id) and _text_owner(db, client_id)


def supports_capability(capability, providers):
    with _connect() as db:
        rows = db.execute('SELECT clients.id,clients.providers,client_capabilities.capabilities FROM clients '
                          'JOIN client_capabilities ON clients.id=client_capabilities.id '
                          'WHERE clients.seen>? AND client_capabilities.seen>?', (time.time()-90,time.time()-90)).fetchall()
    for row in rows:
        if row['id'] != get_settings().get('primary_text_client_id'):
            continue
        advertised = json.loads(row['capabilities'])
        shared = set(providers).intersection(json.loads(row['providers']))
        if isinstance(advertised, dict):
            if any(capability in advertised.get(provider, []) for provider in shared):
                return True
        elif capability in advertised and shared:
            return True
    return False


def wait_for_llm(system_prompt, user_prompt, *, kind, entry, provider, call_hint=None, metadata=None):
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
                           metadata={**(metadata or {}), **({'call_key':call_key} if job else {})})
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
                               metadata={**(metadata or {}), **({'call_key':call_key} if job else {})})
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
            if (metadata or {}).get('ordered_timing_fallback'):
                # This task is terminal; any late browser reply cannot become
                # the timing result. The next channel has a different call key.
                raise RuntimeError('网页时长规划已超时，转下一渠道；不会重发原网页任务')
            if task.get('timeout_phase') == 'queue':
                raise RuntimeError(task['message'])
            raise NeedsAttentionError('原网页任务已超时，无法确认请求是否已发送；请检查原会话和任务记录，不会自动重发。')
        if task["status"] in TERMINAL:
            if task['status'] == 'cancelled' and (metadata or {}).get('ordered_timing_fallback'):
                raise NeedsAttentionError('网页时长规划已取消，停止后续渠道调用', reason='cancelled_by_user')
            if task['status'] == 'error' and (metadata or {}).get('preserve_invalid_json') and task.get('raw'):
                return {'_parse_error': task.get('message') or 'JSON 无法解析',
                        '_raw_text': task['raw']}, round(time.time() - started, 2)
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
    beat_index, beat_duration = int(index), None
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
                beat_index = next(i for i, row in enumerate(beats, 1) if row is beat)
                beat_duration = beat.get("estimated_duration_sec") or beat.get("duration_sec")
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
            "model_id": segment.get("model_id") or jobs.get("default_model_id") or "",
            "segment_count": len(jobs.get("segments") or []),
            "segment_index": index, "segment_uid": segment.get("segment_uid", ""),
            "beat_uid": segment.get("beat_uid", ""),
            "render_id": render.get("render_id", ""), "video_url": "/gradio_api/file=" + video.resolve().as_posix() if video else "",
            "video_name": video.name if video else "", "video_created_at": render.get("created_at", ""),
            "original_prompt": original, "current_prompt": current, "snapshot_known": bool(original),
            "original_asset_manifest": render.get('asset_manifest'),
            "prompt_version_info": prompt_version_info,
            "latest_prompt_revision": latest_prompt_revision,
            "prompt_matches_revision": bool(revision_prompt) and current.strip().replace("\r\n", "\n").replace("\r", "\n") == revision_prompt.replace("\r\n", "\n").replace("\r", "\n"),
            "duration_sec": job.get("duration_sec"),
            "beat_index": beat_index, "beat_duration_sec": beat_duration,
            "duration_source": segment.get("h3_prompt_source", ""),
            "context": {"h3_editor_constraints": segment.get("h3_editor_constraints") or {}, "title": segment.get("title", ""), "source_excerpt": source_excerpt,
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
    if data['model_id'] == 'minimax_h3_local_ref2va':
        from generation.pipelines.minimax_h3.editor_draft import snapshot as editor_snapshot
        saved = editor_snapshot(segment)
        data['reference_revision'] = saved['reference_revision']
        data['reference_manifest'] = saved['asset_manifest']
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
    context = snapshot["context"]
    if use_editor:
        # Historical story/dialogue and render metadata are not another draft.
        # The editor owns all creative content, including unsaved deletions.
        media = context.get("requested_media_spec") or {}
        context = {"requested_media_spec": {key: media[key] for key in ("duration_sec", "fps", "has_audio") if key in media}}
    from services.llm_payloads import reference_context
    context = dict(context)
    if "asset_manifest" in context:
        context["asset_manifest"] = reference_context(context["asset_manifest"])
    for field in ("reference_images", "background_image"):
        context.pop(field, None)
    llm_input = {"base_prompt": base_prompt, "target_duration_sec": snapshot["duration_sec"],
                 "video_issues": issue, "expected_result": str(payload.get("expected") or ""),
                 "context": context}
    constraints=(snapshot.get('context') or {}).get('h3_editor_constraints')
    if constraints:
        from generation.pipelines.minimax_h3.continuity.state import planning_constraints
        selected_manifest=(snapshot.get('context') or {}).get('asset_manifest') if use_editor else snapshot.get('original_asset_manifest')
        valid_constraints=planning_constraints(constraints,base_prompt,selected_manifest or [])
        if valid_constraints:llm_input['editor_constraints']=valid_constraints
    if snapshot.get("model_id") == "minimax_h3_local_ref2va":
        llm_input["model_id"] = snapshot["model_id"]
    speech_manifest = (snapshot.get('context') or {}).get('asset_manifest') if use_editor else snapshot.get('original_asset_manifest')
    current_version = int((snapshot.get("prompt_version_info") or {}).get("version") or 0)
    latest_version = int((snapshot.get("latest_prompt_revision") or {}).get("version") or 0)
    return create_task(provider, "revision", llm_input,
                       metadata={"segment_index": index, "snapshot": snapshot,
                                 "prompt_version": max(current_version, latest_version) + 1,
                                 "provider_candidates":providers,
                                 "prompt_source": "editor" if use_editor else "video",
                                 "force_new_conversation": use_editor,
                                 "editor_prompt": edited,
                                 "speech_manifest": speech_manifest,
                                 "editor_references": str(payload.get("editor_references") or ""),
                                 **({'operation_id':str(operation_id),'call_key':'revision'} if operation_id else {})})
