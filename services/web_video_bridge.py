"""Durable, account-scoped browser video queue.

Only a paired extension can claim tasks, fetch their allowlisted images, and
report a downloaded file. Claimed tasks are never automatically requeued:
after an uncertain web submission the user must inspect it before retrying.
"""
from __future__ import annotations

import json
import copy
import hashlib
import os
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from services.context import BASE_DIR, get_current_episode_name, get_current_project_name, get_project_dir, pin_runtime
from workflow.video_merge import load_video_jobs, save_video_jobs, video_jobs_lock


_DB = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "AICartoonFactory" / "web_video_bridge" / (hashlib.sha256(str(BASE_DIR).encode()).hexdigest()[:16] + ".sqlite3")
_LOCK = threading.RLock()


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


@contextmanager
def _connect():
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, channel TEXT NOT NULL, account TEXT NOT NULL,
        quota_day TEXT NOT NULL, state TEXT NOT NULL, runtime TEXT NOT NULL,
        project TEXT NOT NULL, episode TEXT NOT NULL, segment_index INTEGER NOT NULL,
        segment_uid TEXT NOT NULL, render_id TEXT NOT NULL, input_json TEXT NOT NULL,
        lease TEXT NOT NULL DEFAULT '', client_id TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    )""")
    columns={row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "dismissed" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")
    if 'target_client_id' not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN target_client_id TEXT NOT NULL DEFAULT ''")
    if 'receipt_json' not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN receipt_json TEXT NOT NULL DEFAULT '{}'")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _account(channel: str, client_id: str = '') -> str:
    # A configured account key permits multiple browser profiles later without
    # changing task or quota code. Default is one account across all projects.
    from services.context import CONFIG

    from services.web_llm_bridge import get_settings
    settings = get_settings()
    identity = settings.get('video_browser_accounts', {}).get(client_id + ':' + channel)
    if identity:
        return identity
    accounts = CONFIG.get("web_video_accounts") if isinstance(CONFIG.get("web_video_accounts"), dict) else {}
    return str(accounts.get(channel) or "default").strip() or "default"


def _online_video_browser(preferred, channel, browsers):
    eligible = [item for item in browsers if item.get('online') and channel in item.get('channels', [])]
    saved = next((item for item in browsers if item['id'] == preferred), {})
    if any(item['id'] == preferred for item in eligible):
        return preferred
    eligible.sort(key=lambda item: (item.get('type') == saved.get('type'), item.get('seen', 0)), reverse=True)
    return eligible[0]['id'] if eligible else ''


def video_browser(segment, channel):
    from services.web_llm_bridge import get_settings, browser_clients
    settings = get_settings()
    preferred = str((segment.get('generation_config') or {}).get('video_browser_id') or
                    settings.get('video_browser_defaults', {}).get(channel) or
                    settings.get('primary_text_client_id') or '')
    return _online_video_browser(preferred, channel, browser_clients())


def remaining_quota(channel: str, *, account: str | None = None, segment=None) -> int:
    from generation.channels import get_channel

    limit = getattr(get_channel(channel), "daily_limit", None)
    if limit is None:
        return 1_000_000
    with _LOCK, _connect() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
            (channel, account or _account(channel, video_browser(segment or {}, channel)), _today()),
        ).fetchone()[0]
        return max(0, int(limit) - int(used))


def _segment(payload: dict, index: int, uid: str = "") -> dict:
    item = next((item for item in payload.get("segments") or [] if isinstance(item, dict)
                 and int(item.get("segment_index") or 0) == index), None)
    if item is None or (uid and str(item.get("segment_uid") or "") != uid):
        raise ValueError("分段已变化；已停止接收旧网页任务结果")
    return item


def enqueue_segment(segment_index: int, channel) -> dict:
    from generation.job_schema import resolved_model_id
    from generation.render_history import begin_render

    with _LOCK, video_jobs_lock():
        payload = load_video_jobs()
        segment = _segment(payload, segment_index)
        from services import web_llm_bridge
        target = video_browser(segment, channel.id)
        if not target:
            raise ValueError('请先连接浏览器插件并选择视频执行浏览器')
        browser = next((item for item in web_llm_bridge.browser_clients() if item['id'] == target), None)
        if browser is None or not browser.get('online') or channel.id not in browser.get('channels', []):
            raise ValueError('所选浏览器需要升级插件并支持当前视频渠道')
        reason = channel.unavailable_reason(segment, payload)
        if reason:
            raise ValueError(reason)
        if str(segment.get("status") or "").lower() in {"queued", "running", "pending"}:
            raise ValueError("此分段已有未结束的视频任务")
        is_h3 = resolved_model_id(segment, payload) == 'minimax_h3_local_ref2va'
        # Preserve analysis as historical metadata, never as a video prerequisite.
        scene_framing = segment.get('h3_scene_framing') if is_h3 else None
        request_builder = getattr(channel, 'build_request', None)
        request = request_builder(segment) if request_builder else None
        prompt = request.get('source_prompt', request['prompt']) if request is not None else channel.prompt(segment)
        if scene_framing and prompt != scene_framing.get('source_prompt'):
            scene_framing = None
        images = [str(path) for path in channel.assets(segment)] if request is None else []
        if request is None:
            request = {"prompt": prompt, "duration_sec": channel.duration(segment), "aspect_ratio": "16:9",
                       "images": [{"id": str(i), "name": Path(path).name, "path": path} for i, path in enumerate(images)]}
        task_id = str(uuid.uuid4())
        runtime = get_project_dir().resolve()
        manifest = (segment.get('generation_config') or {}).get('asset_manifest') or []
        if request_builder and not manifest:
            manifest = (segment.get('job') or {}).get('asset_manifest') or []
        from generation.pipelines.minimax_h3.continuity.revisions import snapshot_for_render
        render = begin_render(
            segment, model_id=resolved_model_id(segment, payload),
            prompt_snapshot=prompt,
            asset_manifest=(copy.deepcopy(manifest)
                            if is_h3 else [{"asset_id": str(i), "type": "image", "path": path} for i, path in enumerate(images)]),
            backend=channel.id, status="queued",
            source="web_video", requested_media_spec={key: request[key] for key in ('duration_sec', 'aspect_ratio', 'resolution') if key in request},
            metadata={"web_video_task_id": task_id, "h3_continuity_snapshot": snapshot_for_render(segment), "h3_editor_constraints_snapshot": segment.get("h3_editor_constraints") or {},
                      "h3_scene_framing_snapshot": copy.deepcopy(scene_framing or {}),
                      **({'web_video_request_snapshot': channel.public_input(request)} if request_builder else {})},
        )
        segment.update({"status": "queued", "backend": channel.id, "task_id": task_id,
                        "submitted": False, "error": ""})
        now = time.time()
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
                (channel.id, _account(channel.id, target), _today()),
            ).fetchone()[0]
            limit = getattr(channel, "daily_limit", None)
            if limit is not None and used >= int(limit):
                raise ValueError(f"此账号今日 {limit} 次生成额度已用完")
            conn.execute("""INSERT INTO tasks (id,channel,account,quota_day,state,runtime,project,episode,
                segment_index,segment_uid,render_id,input_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, channel.id, _account(channel.id, target), _today(), "queued", str(runtime),
                 get_current_project_name(), get_current_episode_name(), segment_index,
                 str(segment.get("segment_uid") or ""), str(render["render_id"]), json.dumps(request, ensure_ascii=False), now, now),
            )
            conn.execute('UPDATE tasks SET target_client_id=? WHERE id=?', (target, task_id))
            save_video_jobs(payload)
        return load_video_jobs()


def _public(row, *, include_input=False):
    data = dict(row)
    request = json.loads(data.pop("input_json"))
    data['receipt'] = json.loads(data.pop('receipt_json', '{}'))
    data.pop("runtime", None)
    data.pop("lease", None)
    if data.get('target_client_id'):
        from services.web_llm_bridge import browser_clients
        browser = next((item for item in browser_clients() if item['id'] == data['target_client_id']), None)
        data['browser_name'] = browser['name'] if browser else data['target_client_id'][:8]
        data['browser_online'] = bool(browser and browser['online'])
    data['preview_ready'] = False
    if row['state'] == 'complete' and row['output_path']:
        path = Path(row['output_path'])
        jobs_path = Path(row['runtime']) / 'video_jobs.json'
        try:
            jobs = json.loads(jobs_path.read_text(encoding='utf-8-sig'))
            segment = _segment(jobs, row['segment_index'], row['segment_uid'])
            render = next(item for item in segment.get('renders') or [] if item.get('render_id') == row['render_id'])
            output = render.get('output') or {}
            media = output.get('actual_media_spec') or {}
            data['preview_ready'] = bool(path.is_file() and path.stat().st_size > 0 and
                render.get('status') == 'success' and not output.get('video_deleted') and
                Path(output.get('video_path') or '').resolve() == path.resolve() and
                float(media.get('duration_sec') or 0) > 0 and int(media.get('width') or 0) > 0)
            if data['preview_ready']:
                data['preview_url'] = '/gradio_api/file=' + path.resolve().as_posix()
                data['render_id'] = render['render_id']
        except (OSError, ValueError, TypeError, StopIteration):
            pass
    if include_input:
        from generation.channels import get_channel
        public_input = getattr(get_channel(row['channel']), 'public_input', None)
        if public_input:
            data['input'] = public_input(request)
            return data
        data["input"] = {k: v for k, v in request.items() if k != "images"}
        data["input"]["images"] = [{"id": image["id"], "name": image["name"]} for image in request["images"]]
    return data


def claim(client_id: str, channels: list[str]):
    if not client_id or not channels:
        return None
    from services import web_llm_bridge
    from services.extension_updates import draining
    with web_llm_bridge._connect() as db:
        if draining(db, client_id):
            return None
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from services.web_llm_bridge import get_settings, browser_clients
        browsers = browser_clients()
        primary = get_settings().get('primary_text_client_id') or client_id
        conn.execute("UPDATE tasks SET target_client_id=? WHERE state='queued' AND target_client_id=''", (primary,))
        candidates = conn.execute("SELECT * FROM tasks WHERE state='queued' AND channel IN (" + ",".join("?" for _ in channels) + ") ORDER BY created_at", channels).fetchall()
        # Reserve one active task per browser instance and web channel.
        # The transaction also protects concurrent claims and extension reloads.
        busy_channels = {row['channel'] for row in conn.execute(
            "SELECT channel FROM tasks WHERE state IN ('claimed','submitted') "
            "AND dismissed=0 AND (client_id=? OR (client_id='' AND target_client_id=?))",
            (client_id, client_id),
        )}
        row = None
        for candidate in candidates:
            from generation.channels import get_channel
            target = _online_video_browser(candidate['target_client_id'], candidate['channel'], browsers)
            if target != client_id or candidate['channel'] in busy_channels:
                continue
            if target != candidate['target_client_id']:
                account = _account(candidate['channel'], target)
                limit = getattr(get_channel(candidate['channel']), 'daily_limit', None)
                used = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE id<>? AND channel=? AND account=? AND quota_day=? "
                    "AND state NOT IN ('cancelled_unclaimed','error')",
                    (candidate['id'], candidate['channel'], account, _today()),
                ).fetchone()[0]
                if limit is not None and used >= int(limit):
                    continue
                conn.execute('UPDATE tasks SET target_client_id=?,account=?,updated_at=? WHERE id=?',
                             (target, account, time.time(), candidate['id']))
                candidate = conn.execute('SELECT * FROM tasks WHERE id=?', (candidate['id'],)).fetchone()

            limit = getattr(get_channel(candidate["channel"]), "daily_limit", None)
            if limit is not None and candidate["quota_day"] != _today():
                used = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
                    (candidate["channel"], candidate["account"], _today()),
                ).fetchone()[0]
                if used >= int(limit):
                    continue
            row = candidate
            break
        if row is None:
            return None
        lease = secrets.token_urlsafe(32)
        conn.execute("UPDATE tasks SET state='claimed',quota_day=?,lease=?,client_id=?,updated_at=? WHERE id=?",
                     (_today(), lease, client_id, time.time(), row["id"]))
        claimed = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
        return {**_public(claimed, include_input=True), "lease": lease}


def retarget(task_id, client_id):
    from services.web_llm_bridge import browser_clients
    browser = next((item for item in browser_clients() if item['id'] == client_id), None)
    with _LOCK, _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None or row['state'] != 'queued':
            raise ValueError('仅排队且尚未领取的视频任务可以更换浏览器')
        if Path(row['runtime']).resolve() != get_project_dir().resolve():
            raise ValueError('任务属于其他项目，请切换到所属项目后操作')
        if browser is None or not browser.get('online') or row['channel'] not in browser.get('channels', []):
            raise ValueError('所选浏览器未声明当前视频渠道能力')
        account = _account(row['channel'], client_id)
        from generation.channels import get_channel
        limit = getattr(get_channel(row['channel']), 'daily_limit', None)
        used = conn.execute("SELECT COUNT(*) FROM tasks WHERE id<>? AND channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
                            (task_id, row['channel'], account, _today())).fetchone()[0]
        if limit is not None and used >= limit:
            raise ValueError('目标账号今日生成额度已用完')
        conn.execute('UPDATE tasks SET target_client_id=?,account=?,updated_at=? WHERE id=?',
                     (client_id, account, time.time(), task_id))
    return task_status(task_id)


def asset(task_id: str, asset_id: str, lease: str) -> Path:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or not secrets.compare_digest(row["lease"], lease) or row["state"] not in {"claimed", "submitted"}:
        raise PermissionError("网页视频任务凭据无效")
    request = json.loads(row["input_json"])
    from generation.channels import get_channel
    resolve_asset = getattr(get_channel(row['channel']), 'resolve_asset', None)
    if resolve_asset:
        return resolve_asset(request, asset_id)
    item = next((item for item in request["images"] if item["id"] == asset_id), None)
    if item is None:
        raise ValueError("参考图片不在此任务中")
    path = Path(item["path"]).resolve()
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("参考图片已丢失")
    return path


def report(task_id: str, lease: str, state: str, *, output_path: str = "", error: str = "",
           receipt: dict | None = None) -> dict:
    from generation.media import inspect_media
    from generation.render_history import update_render

    if state not in {"submitted", "complete", "error", "uncertain"}:
        raise ValueError("不支持的任务状态")
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or not row["lease"] or not secrets.compare_digest(row["lease"], lease):
            raise PermissionError("网页视频任务凭据无效")
        if row["state"] in {"complete", "error", "uncertain"}:
            if row["state"] == state:
                return _public(row)
            raise ValueError("任务已经结束，不接受重复回报")
        if state == "complete" and row["state"] != "submitted":
            raise ValueError("任务尚未确认网页提交")
        if state == "error" and row["state"] != "claimed":
            raise ValueError("已提交的网页任务只能标记为结果不确定，不能释放额度")
        saved_receipt = json.loads(row['receipt_json'])
        merged_receipt = saved_receipt
        if row['channel'] in {'runninghub_web', 'runninghub_international_web'}:
            if receipt is not None:
                if (not isinstance(receipt, dict) or receipt.get('source') != 'runninghub_commit'
                        or not isinstance(receipt.get('taskId'), str) or not receipt['taskId'].strip()):
                    raise ValueError('RunningHub 提交回执缺少可靠远端任务 ID')
                if saved_receipt.get('taskId') and receipt['taskId'] != saved_receipt['taskId']:
                    raise ValueError('RunningHub 结果与提交回执的远端任务 ID 不一致')
                merged_receipt = {**saved_receipt, **receipt}
            if state == 'complete' and (not saved_receipt.get('taskId')
                    or not merged_receipt.get('resultTaskId')
                    or not str(merged_receipt.get('resultUrl') or '').startswith(('https://', 'http://'))):
                raise ValueError('RunningHub 缺少已绑定任务的视频结果回执，请核对原任务')
        source = Path(output_path).resolve() if output_path else None
        if state == "complete" and (source is None or not source.is_file() or source.suffix.lower() not in {".mp4", ".webm", ".mov"}):
            raise ValueError("已下载的视频文件不存在或格式不支持")
        runtime = Path(row["runtime"]).resolve()
        destination = runtime / "videos" / f"web_{row['channel']}_{task_id}{source.suffix.lower()}" if source else None
        media = {}
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            media = inspect_media(destination).to_dict()
            if not media.get("duration_sec"):
                destination.unlink(missing_ok=True)
                raise ValueError("下载文件不是有效视频")
        with pin_runtime((runtime, row["project"], row["episode"])), video_jobs_lock():
            payload = load_video_jobs()
            segment = _segment(payload, row["segment_index"], row["segment_uid"])
            if str(segment.get("task_id") or "") != task_id:
                raise ValueError("分段已关联其他任务；不会覆盖其视频")
            render_state = "success" if state == "complete" else "running" if state == "submitted" else "failed"
            update_render(segment, row["render_id"],
                          {"backend": row["channel"], "task_id": task_id, "submitted": state in {"submitted", "complete"},
                           "video_path": str(destination) if destination else "", "error": error},
                          status=render_state, actual_media_spec=media, project=state == "complete")
            if merged_receipt:
                render = next(item for item in segment['renders'] if item['render_id'] == row['render_id'])
                render.setdefault('metadata', {})['web_video_receipt'] = copy.deepcopy(merged_receipt)
            segment.update({"status": render_state, "backend": row["channel"], "task_id": task_id,
                            "submitted": state in {"submitted", "complete"}, "error": error})
            if destination:
                segment["video_path"] = str(destination)
                segment["actual_media_spec"] = media
            save_video_jobs(payload)
        conn.execute("UPDATE tasks SET state=?,error=?,output_path=?,updated_at=?,receipt_json=? WHERE id=?",
                     (state, error, str(destination) if destination else "", time.time(),
                      json.dumps(merged_receipt, ensure_ascii=False), task_id))
        return _public(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def task_status(task_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("网页视频任务不存在")
    if row["state"] == "claimed" and time.time() - float(row["updated_at"]) > 300:
        return report(task_id, row["lease"], "uncertain",
                      error="扩展领取后超过 5 分钟未确认网页发送；请检查原页面，不会自动重发")
    return _public(row)


def execution_status(task_id: str, client_id: str) -> dict | None:
    """Let the owning extension retire its local copy after a queue reset."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    if not client_id or client_id != str(row['client_id'] or row['target_client_id']):
        raise PermissionError("网页视频任务不属于此浏览器")
    return _public(row)


def list_tasks(limit: int = 50) -> list[dict]:
    """Read-only status feed for the project task center."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE dismissed=0 ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [_public(row) for row in rows]


_ACTIVE_SEGMENT_STATUSES = {"queued", "running", "pending"}
_WEB_TERMINAL_TASK_STATES = {"complete", "error", "uncertain", "cancelled_unclaimed"}
_WEB_LIVE_TASK_TIMEOUT = {"queued": 30 * 60, "claimed": 5 * 60, "submitted": 45 * 60}
_WEB_ORPHAN_TIMEOUT = 30 * 60
RESET_QUEUED_REASON = "排队任务已重置；原网页任务不再接收结果"


def _segment_index(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _find_segment_quiet(payload, index, uid="") -> dict | None:
    """Locate a segment without raising on stale indices or split beats."""
    wanted = _segment_index(index)
    if wanted <= 0:
        return None
    for item in payload.get("segments") or []:
        if not isinstance(item, dict) or _segment_index(item.get("segment_index")) != wanted:
            continue
        if uid and str(item.get("segment_uid") or "") != str(uid):
            return None
        return item
    return None


def _release_segment(segment, *, task_id, render_id, error, backend="") -> None:
    """Mark one web-video segment and its owning render as failed.

    Never resubmits the web task: an uncertain submission may still be
    rendering on the website and retrying would spend the account twice.
    """
    from generation.render_history import update_render

    render_id = str(render_id or "")
    if not render_id and task_id:
        render_id = next((str(item.get('render_id') or '') for item in segment.get('renders') or []
                          if str((item.get('submission') or {}).get('task_id') or
                                 (item.get('metadata') or {}).get('web_video_task_id') or '') == task_id), '')
    if render_id:
        try:
            update_render(
                segment, render_id,
                {"backend": backend, "task_id": task_id, "submitted": False, "error": error},
                status="failed",
            )
        except Exception as exc:
            from services.logger import log

            log(f"[web_video][render][warn] {task_id}: {exc}", "WARN")
    segment.update({"status": "failed", "error": error, "submitted": False})
    segment.pop("video_queue_entered_at", None)


def _release_segments_for_tasks(rows, *, reason: str) -> list[int]:
    """Mirror settled bridge tasks back into each owning runtime's job file.

    The bridge database and video_jobs.json are two independent state machines.
    Clearing or dismissing tasks used to only touch SQLite, so a segment could
    stay flagged as active forever while its task was already gone. Reconcile
    every settled task with the segment that still points at it.
    """
    from services.logger import log

    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for raw in rows:
        row = dict(raw)
        if str(row.get("state") or "") not in _WEB_TERMINAL_TASK_STATES:
            continue
        runtime = str(row.get("runtime") or "").strip()
        if not runtime:
            continue
        key = (runtime, str(row.get("project") or ""), str(row.get("episode") or ""))
        grouped.setdefault(key, []).append(row)

    released: list[int] = []
    for (runtime_text, project, episode), items in grouped.items():
        runtime = Path(runtime_text)
        if not (runtime / "video_jobs.json").is_file():
            continue
        try:
            with pin_runtime((runtime.resolve(), project, episode)), video_jobs_lock():
                payload = load_video_jobs()
                changed = False
                for row in items:
                    task_id = str(row.get("id") or "")
                    segment = _find_segment_quiet(payload, row.get("segment_index"), row.get("segment_uid"))
                    if segment is None:
                        continue
                    if str(segment.get("status") or "").strip().lower() not in _ACTIVE_SEGMENT_STATUSES:
                        continue
                    if task_id and str(segment.get("task_id") or "") != task_id:
                        continue
                    error = str(row.get("error") or "").strip() or reason
                    _release_segment(segment, task_id=task_id, render_id=row.get("render_id"),
                                     error=error, backend=str(row.get("channel") or ""))
                    released.append(_segment_index(row.get("segment_index")))
                    changed = True
                if changed:
                    save_video_jobs(payload)
        except Exception as exc:
            log(f"[web_video][reconcile][warn] {runtime_text}: {exc}", "WARN")
    return released


def reconcile_queued_segments(*, now: float | None = None) -> list[int]:
    """Release web-channel segments whose bridge task can no longer finish.

    Web channels are deliberately never auto-resubmitted (unlike the local
    RunningHub slot queue). This is the server-owned recovery path for a
    ``queued`` segment whose task is missing, settled/dismissed, or silent for
    longer than its state-specific timeout. It only unlocks the segment.
    """
    from services.logger import log

    current = get_project_dir().resolve()
    if not (current / "video_jobs.json").is_file():
        return []
    moment = float(now if now is not None else time.time())
    with _LOCK, video_jobs_lock():
        # A superseded claim may no longer be referenced by any current segment,
        # but it still occupies the browser/channel slot. Renewed preparation
        # heartbeats keep real work alive; silent claims use the existing 5m limit.
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE tasks SET state='uncertain', error=?, updated_at=? "
                "WHERE runtime=? AND state='claimed' AND updated_at<?",
                ("网页准备超过 5 分钟未更新；已结束本地跟踪，提交结果不确定", moment,
                 str(current), moment - _WEB_LIVE_TASK_TIMEOUT['claimed']),
            )
        payload = load_video_jobs()
        candidates = [
            item for item in payload.get("segments") or []
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower() in _ACTIVE_SEGMENT_STATUSES
            and str(item.get("backend") or "").strip().lower().endswith("_web")
        ]
        if not candidates:
            return []
        with _connect() as conn:
            task_rows = {}
            for task_id in {str(item.get("task_id") or "") for item in candidates if item.get("task_id")}:
                found = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if found is not None:
                    task_rows[task_id] = dict(found)

        released: list[int] = []
        expired_ids: list[str] = []
        for segment in candidates:
            task_id = str(segment.get("task_id") or "")
            row = task_rows.get(task_id) if task_id else None
            state = str((row or {}).get("state") or "")
            reason = ""
            if row is None:
                entered = float(segment.get("video_queue_entered_at") or 0)
                if entered and moment - entered < _WEB_ORPHAN_TIMEOUT:
                    continue
                reason = "排队任务记录缺失；已释放分段，未重发网页任务"
            elif state in _WEB_TERMINAL_TASK_STATES or int(row.get("dismissed") or 0):
                reason = str(row.get("error") or "").strip() or "网页任务已结束但未产出视频；已释放分段"
            else:
                timeout = _WEB_LIVE_TASK_TIMEOUT.get(state)
                last = max(float(row.get("updated_at") or 0), float(row.get("created_at") or 0))
                if timeout is None or not last or moment - last < timeout:
                    continue
                reason = f"网页任务长时间无进展（{state}）；已释放分段"
                expired_ids.append(task_id)
            _release_segment(segment, task_id=task_id,
                             render_id=(row or {}).get("render_id"),
                             error=reason, backend=str(segment.get("backend") or ""))
            released.append(_segment_index(segment.get("segment_index")))

        if expired_ids:
            with _connect() as conn:
                conn.executemany(
                    "UPDATE tasks SET state=CASE WHEN state='queued' THEN 'cancelled_unclaimed' "
                    "ELSE 'uncertain' END, dismissed=1, lease='', updated_at=? WHERE id=?",
                    [(moment, task_id) for task_id in expired_ids],
                )
        if released:
            save_video_jobs(payload)
    if released:
        log(f"[web_video][reconcile] released queued segments {released}", "STEP")
    return released


def reset_queued_segment(segment_index: int, segment_uid: str = "") -> dict:
    """Explicit user action: cancel a stuck web-video queue entry and unlock the segment."""
    from services.logger import log

    with _LOCK, video_jobs_lock():
        payload = load_video_jobs()
        segment = _segment(payload, _segment_index(segment_index), segment_uid)
        status = str(segment.get("status") or "").strip().lower()
        backend = str(segment.get("backend") or "").strip().lower()
        if status != "queued" or not backend.endswith("_web"):
            raise ValueError("该分段没有卡住的网页排队任务，无需重置")
        task_id = str(segment.get("task_id") or "")
        if task_id:
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE tasks SET state=CASE WHEN state='queued' THEN 'cancelled_unclaimed' "
                    "WHEN state IN ('claimed','submitted') THEN 'uncertain' ELSE state END, "
                    "dismissed=1, lease='', error=?, updated_at=? WHERE id=?",
                    (RESET_QUEUED_REASON, time.time(), task_id),
                )
        render = None
        with _connect() as conn:
            found = conn.execute("SELECT render_id FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id else None
            if found is not None:
                render = found["render_id"]
        _release_segment(segment, task_id=task_id, render_id=render,
                         error=RESET_QUEUED_REASON, backend=backend)
        save_video_jobs(payload)
    log(f"[web_video][reset] segment {segment_index} released", "STEP")
    return payload


def dismiss_tasks(task_id: str | None = None) -> int:
    """Hide settled tasks and mirror their terminal state into the job file."""
    with _LOCK, _connect() as conn:
        if task_id:
            row = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("网页视频任务不存在")
            if row["state"] not in _WEB_TERMINAL_TASK_STATES:
                raise ValueError("视频任务仍在执行，暂不能移除")
        suffix = " AND id=?" if task_id else ""
        params = (task_id,) if task_id else ()
        sql = "UPDATE tasks SET dismissed=1 WHERE dismissed=0 AND state IN ('complete','error','uncertain','cancelled_unclaimed')"
        count = conn.execute(sql + suffix, params).rowcount
        rows = [dict(item) for item in conn.execute(
            "SELECT * FROM tasks WHERE dismissed=1 AND state IN ('complete','error','uncertain','cancelled_unclaimed')" + suffix,
            params,
        )]
    _release_segments_for_tasks(rows, reason="任务已从任务中心移除；本次网页任务未产出视频")
    return count


def dismiss_completed_tasks() -> int:
    """Hide successful tasks without touching video files or render history."""
    with _LOCK, _connect() as conn:
        return conn.execute("UPDATE tasks SET dismissed=1 WHERE dismissed=0 AND state='complete'").rowcount


def clear_all_tasks():
    """Stop tracking all work, preserving media, render history and spent quota."""
    with _LOCK, _connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM tasks WHERE dismissed=0').fetchone()[0]
        conn.execute("UPDATE tasks SET state=CASE WHEN state='queued' THEN 'cancelled_unclaimed' "
                     "WHEN state IN ('claimed','submitted') THEN 'uncertain' ELSE state END, "
                     "dismissed=1,lease='',updated_at=?", (time.time(),))
        rows = [dict(item) for item in conn.execute(
            "SELECT * FROM tasks WHERE dismissed=1 AND state IN ('complete','error','uncertain','cancelled_unclaimed')"
        )]
    _release_segments_for_tasks(rows, reason="任务中心已清空；本次网页任务未产出视频")
    return count
