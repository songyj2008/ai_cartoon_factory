import json
import time

from services import text_jobs


def test_all_live_tasks_remain_visible_after_more_than_100_newer_tasks():
    now = time.time()
    with text_jobs.database() as db:
        for index in range(177):
            state = 'running' if index < 2 else 'applied'
            item = {'kind': 'story', 'title': 'story', 'phase': '执行', 'editors': {},
                    'timings': {'state_seconds': {}, 'state_started_at': now}, 'history': []}
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                       (f'task-{index:03d}', state, now+index, now+index, json.dumps(item)))
    first = text_jobs.list_jobs_page(50, 0)
    second = text_jobs.list_jobs_page(50, 50)
    last = text_jobs.list_jobs_page(50, 150)
    assert first['pagination'] == {'offset': 0, 'limit': 50, 'total': 175, 'active': 2, 'has_more': True}
    active = {'task-000', 'task-001'}
    assert active <= {row['id'] for row in first['tasks']}
    assert active <= {row['id'] for row in second['tasks']}
    assert active <= {row['id'] for row in last['tasks']}
    assert not last['pagination']['has_more'] and len(last['tasks']) == 27
    assert len(text_jobs.list_jobs()) == 177
    assert not ({r['id'] for r in first['tasks']} - active) & ({r['id'] for r in second['tasks']} - active)


def test_empty_or_out_of_range_history_has_stable_pagination():
    result = text_jobs.list_jobs_page(500, 999)
    assert result['tasks'] == []
    assert result['pagination'] == {'offset': 0, 'limit': 100, 'total': 0, 'active': 0, 'has_more': False}
