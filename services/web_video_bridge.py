"""Durable, account-scoped browser video queue.

Only a paired extension can claim tasks, fetch their allowlisted images, and
report a downloaded file. Claimed tasks are never automatically requeued:
after an uncertain web submission the user must inspect it before retrying.
"""
from __future__ import annotations

import json
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
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _account(channel: str) -> str:
    # A configured account key permits multiple browser profiles later without
    # changing task or quota code. Default is one account across all projects.
    from services.context import CONFIG

    accounts = CONFIG.get("web_video_accounts") if isinstance(CONFIG.get("web_video_accounts"), dict) else {}
    return str(accounts.get(channel) or "default").strip() or "default"


def remaining_quota(channel: str, *, account: str | None = None) -> int:
    from generation.channels import get_channel

    limit = getattr(get_channel(channel), "daily_limit", None)
    if limit is None:
        return 1_000_000
    with _LOCK, _connect() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
            (channel, account or _account(channel), _today()),
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
        reason = channel.unavailable_reason(segment, payload)
        if reason:
            raise ValueError(reason)
        if str(segment.get("status") or "").lower() in {"queued", "running", "pending"}:
            raise ValueError("此分段已有未结束的视频任务")
        prompt = channel.prompt(segment)
        images = [str(path) for path in channel.assets(segment)]
        task_id = str(uuid.uuid4())
        runtime = get_project_dir().resolve()
        render = begin_render(
            segment, model_id=resolved_model_id(segment, payload),
            prompt_snapshot=prompt,
            asset_manifest=[{"asset_id": str(i), "type": "image", "path": path} for i, path in enumerate(images)],
            backend=channel.id, status="queued",
            source="web_video", requested_media_spec={"duration_sec": channel.duration(segment), "aspect_ratio": "16:9"},
            metadata={"web_video_task_id": task_id},
        )
        segment.update({"status": "queued", "backend": channel.id, "task_id": task_id,
                        "submitted": False, "error": ""})
        request = {"prompt": prompt, "duration_sec": channel.duration(segment), "aspect_ratio": "16:9",
                   "images": [{"id": str(i), "name": Path(path).name, "path": path} for i, path in enumerate(images)]}
        now = time.time()
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE channel=? AND account=? AND quota_day=? AND state NOT IN ('cancelled_unclaimed','error')",
                (channel.id, _account(channel.id), _today()),
            ).fetchone()[0]
            limit = getattr(channel, "daily_limit", None)
            if limit is not None and used >= int(limit):
                raise ValueError(f"此账号今日 {limit} 次生成额度已用完")
            conn.execute("""INSERT INTO tasks (id,channel,account,quota_day,state,runtime,project,episode,
                segment_index,segment_uid,render_id,input_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, channel.id, _account(channel.id), _today(), "queued", str(runtime),
                 get_current_project_name(), get_current_episode_name(), segment_index,
                 str(segment.get("segment_uid") or ""), str(render["render_id"]), json.dumps(request, ensure_ascii=False), now, now),
            )
            save_video_jobs(payload)
        return load_video_jobs()


def _public(row, *, include_input=False):
    data = dict(row)
    request = json.loads(data.pop("input_json"))
    data.pop("runtime", None)
    data.pop("lease", None)
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
        data["input"] = {k: v for k, v in request.items() if k != "images"}
        data["input"]["images"] = [{"id": image["id"], "name": image["name"]} for image in request["images"]]
    return data


def claim(client_id: str, channels: list[str]):
    if not client_id or not channels:
        return None
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute("SELECT * FROM tasks WHERE state='queued' AND channel IN (" + ",".join("?" for _ in channels) + ") ORDER BY created_at", channels).fetchall()
        row = None
        for candidate in candidates:
            from generation.channels import get_channel

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


def asset(task_id: str, asset_id: str, lease: str) -> Path:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or not secrets.compare_digest(row["lease"], lease) or row["state"] not in {"claimed", "submitted"}:
        raise PermissionError("网页视频任务凭据无效")
    request = json.loads(row["input_json"])
    item = next((item for item in request["images"] if item["id"] == asset_id), None)
    if item is None:
        raise ValueError("参考图片不在此任务中")
    path = Path(item["path"]).resolve()
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("参考图片已丢失")
    return path


def report(task_id: str, lease: str, state: str, *, output_path: str = "", error: str = "") -> dict:
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
            segment.update({"status": render_state, "backend": row["channel"], "task_id": task_id,
                            "submitted": state in {"submitted", "complete"}, "error": error})
            if destination:
                segment["video_path"] = str(destination)
                segment["actual_media_spec"] = media
            save_video_jobs(payload)
        conn.execute("UPDATE tasks SET state=?,error=?,output_path=?,updated_at=? WHERE id=?",
                     (state, error, str(destination) if destination else "", time.time(), task_id))
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


def list_tasks(limit: int = 50) -> list[dict]:
    """Read-only status feed for the project task center."""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE dismissed=0 ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [_public(row) for row in rows]


def dismiss_tasks(task_id: str | None = None) -> int:
    """Hide settled tasks without deleting quota, render history, or media."""
    with _LOCK, _connect() as conn:
        if task_id:
            row=conn.execute("SELECT state FROM tasks WHERE id=?",(task_id,)).fetchone()
            if row is None:
                raise ValueError("网页视频任务不存在")
            if row["state"] not in {"complete","error","uncertain","cancelled_unclaimed"}:
                raise ValueError("视频任务仍在执行，暂不能移除")
        sql="UPDATE tasks SET dismissed=1 WHERE dismissed=0 AND state IN ('complete','error','uncertain','cancelled_unclaimed')"
        return conn.execute(sql + (" AND id=?" if task_id else ""), (task_id,) if task_id else ()).rowcount
