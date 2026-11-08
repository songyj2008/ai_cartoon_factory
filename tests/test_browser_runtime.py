import asyncio
import subprocess
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import context
from services.browser_runtime import bind_api, bind_demo
from ui.browser_runtime_ui import BROWSER_RUNTIME_JS


@pytest.fixture
def browsers(monkeypatch):
    monkeypatch.setattr(context, '_BROWSER_RUNTIMES', {})
    monkeypatch.setattr(context, '_BROWSER_EPISODES', {})
    token = context._PINNED_RUNTIME.set(None)
    try:
        yield
    finally:
        context._PINNED_RUNTIME.reset(token)


def test_gradio_windows_keep_independent_project_and_draft_selections(browsers):
    async def process_api(inputs, session_hash=None):
        if inputs:
            project, draft = inputs
            context.set_current_project_name(project)
            context.set_current_episode(draft)
        await asyncio.sleep(0)
        return context.capture_runtime()
    demo = SimpleNamespace(process_api=process_api)
    bind_demo(demo)
    async def run():
        a = await demo.process_api(('A', 'draft1'), session_hash='window-a')
        b = await demo.process_api(('B', 'draft2'), session_hash='window-b')
        assert a[1:] == ('A', 'draft1') and b[1:] == ('B', 'draft2')
        assert await demo.process_api(None, session_hash='window-a') == a
        assert await demo.process_api(None, session_hash='window-b') == b
        await demo.process_api(('A', 'draft3'), session_hash='window-b')
        assert await demo.process_api(None, session_hash='window-a') == a
    asyncio.run(run())


def test_api_resolves_window_owner_and_rejects_stale_same_window_request(browsers):
    for session, project in [('tab-a', 'A'), ('tab-b', 'B')]:
        with context.browser_session(session), context.pin_runtime():
            context.set_current_project_name(project)
    app = FastAPI()
    bind_api(app)
    writes = []
    @app.post('/api/write')
    def write():
        writes.append(context.get_current_project_name())
        return {'owner': context.get_current_project_name()}
    with TestClient(app) as client:
        a = context._BROWSER_RUNTIMES['tab-a'][0]
        headers = {'X-AICF-Session': 'tab-a', 'X-AICF-Runtime': quote(str(a)), 'X-AICF-Runtime-Encoding': 'uri'}
        assert client.post('/api/write', headers=headers).json() == {'owner': 'A'}
        with context.browser_session('tab-a'), context.pin_runtime():
            context.set_current_project_name('C')
        assert client.post('/api/write', headers=headers).status_code == 409
        assert client.post('/api/write', headers={'X-AICF-Session': 'tab-b'}).json() == {'owner': 'B'}
    assert writes == ['A', 'B']


def test_inflight_callback_stays_pinned_when_another_window_switches(browsers):
    with context.browser_session('tab-a'), context.pin_runtime():
        context.set_current_project_name('A')
        original = context.capture_runtime()
        with context.browser_session('tab-b'), context.pin_runtime():
            context.set_current_project_name('B')
        assert context.capture_runtime() == original


def test_window_remembers_initial_draft_when_other_window_changes_that_project(browsers):
    context.set_current_project_name('A')
    context.set_current_episode('')
    with context.browser_session('tab-b'), context.pin_runtime():
        context.set_current_project_name('B')
    with context.browser_session('tab-a'), context.pin_runtime():
        context.set_current_project_name('A')
        context.set_current_episode('different-draft')
    with context.browser_session('tab-b'), context.pin_runtime():
        context.set_current_project_name('A')
        assert context.get_current_episode_name() == ''


def test_frontend_carries_session_and_owner_for_all_api_mutations(tmp_path):
    script = tmp_path / 'browser-runtime.cjs'
    script.write_text('''
const assert=require('node:assert/strict');
const calls=[];
global.window={AICF:{},location:{href:'http://localhost/',origin:'http://localhost'},
 fetch:async(input,options)=>{calls.push([input,options]);return {ok:true,clone:()=>({json:async()=>({project_dir:'F:/project A/draft'})})};}};
''' + BROWSER_RUNTIME_JS + '''
(async()=>{
 await window.fetch('/gradio_api/queue/join',{method:'POST',body:JSON.stringify({session_hash:'tab-123'})});
 await window.fetch('/api/project/current_state');
 await window.fetch('/api/segment/regenerate_video',{method:'POST',body:'{}'});
 const headers=calls.at(-1)[1].headers;
 assert.equal(headers.get('X-AICF-Session'),'tab-123');
 assert.equal(decodeURIComponent(headers.get('X-AICF-Runtime')),'F:/project A/draft');
 await window.fetch('/api/segment/save_prompt',{method:'POST',headers:{'X-AICF-Runtime':'explicit'}});
 assert.equal(calls.at(-1)[1].headers.get('X-AICF-Runtime'),'explicit');
 await window.fetch('http://elsewhere/api/write',{method:'POST'});
 assert.equal(calls.at(-1)[1].headers,undefined);
})().catch(e=>{console.error(e);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
