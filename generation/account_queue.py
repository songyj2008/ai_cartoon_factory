"""Process-wide admission for the shared RunningHub account.

Hold admission_lock before a runtime's job lock. Files remain the durable
queue; switching projects and restarting the worker do not lose its order.
"""
import json
import time
from threading import RLock

admission_lock = RLock()


def runtime_paths():
    from services import context
    paths = {context.get_project_dir().resolve()}
    if context.OUTPUT_DIR.exists():
        paths.update(p.resolve() for p in context.OUTPUT_DIR.iterdir() if p.is_dir())
    if context.EPISODES_DIR.exists():
        paths.update(p.parent.resolve() for p in context.EPISODES_DIR.glob('*/*/.draft'))
    return sorted(paths, key=str)


def _rows(payload):
    return [s for s in payload.get('segments', []) if isinstance(s, dict)]


def _active_ids(rows):
    ids = set()
    for row in rows:
        from workflow.runninghub_queue import active_task_id
        if row.get('backend') == 'runninghub' and row.get('status') in ('running', 'pending'):
            task_id = active_task_id(row)
            if task_id:
                ids.add(task_id)
        for render in row.get('renders') or []:
            if (render.get('backend') or row.get('backend')) != 'runninghub':
                continue
            submission = render.get('submission') or {}
            if render.get('status') in ('running', 'pending', 'submitted') and submission.get('task_id'):
                ids.add(str(submission['task_id']))
    return ids


def admitted_indices(payload, targets, *, model_id, limit):
    """Return the selected targets entitled to the account's next free slots."""
    from services import context
    current = context.get_project_dir().resolve()
    now = time.time()
    for row in targets:
        row.setdefault('video_queue_entered_at', now)
    datasets = [(current, payload)]
    for path in runtime_paths():
        if path == current or not (path / 'video_jobs.json').is_file():
            continue
        # An unreadable queue must not be treated as an empty account.
        with (path / 'video_jobs.json').open(encoding='utf-8-sig') as handle:
            datasets.append((path, json.load(handle)))
    active = set()
    pending = []
    selected = {int(s['segment_index']) for s in targets}
    for path, data in datasets:
        for row in _rows(data):
            active.update(_active_ids([row]))
            index = int(row.get('segment_index') or 0)
            is_selected = path == current and index in selected
            if is_selected or (row.get('backend') == 'runninghub' and row.get('status') == 'queued'):
                pending.append((float(row.get('video_queue_entered_at') or 0), str(path), index))
    available = max(0, int(limit) - len(active))
    winners = sorted(set(pending))[:available]
    return {index for _, path, index in winners if path == str(current) and index in selected}
