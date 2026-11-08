"""HTTP failures must not be disguised as JSON syntax errors."""
import asyncio
import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from starlette.requests import Request

from ui.api_routes import _pinned_segment_route
from ui.scripts import APP_JS


def test_non_json_response_reports_endpoint_and_status_without_leaking_body(tmp_path):
    reader = APP_JS.split('window.AICF.readJsonResponse = async function(response, url) {', 1)[1].split(
        'window.AICF.bindProjectAssetCardSelection', 1)[0]
    script = tmp_path / 'http_errors.cjs'
    script.write_text(r'''
const assert=require('node:assert/strict');global.window={AICF:{}};
window.AICF.readJsonResponse=async function(response,url){
''' + reader + r'''
(async()=>{
 const read=window.AICF.readJsonResponse;
 for(const [status,body] of [[500,'Internal Server Error'],[502,'<html>proxy failure</html>'],[200,''],[200,'null']]){
  const response={status,text:async()=>body};
  await assert.rejects(read(response,'/api/segment/editor/continuity?token=private'),error=>{
   assert.equal(error.code,'INVALID_API_RESPONSE');assert.equal(error.status,status);
   assert.ok(error.message.includes('/api/segment/editor/continuity'));
   assert.ok(error.message.includes('HTTP '+status));
   assert.ok(!error.message.includes('Unexpected token'));
   assert.ok(!error.message.includes('<html>'));assert.ok(!error.message.includes('private'));
   return true;
  });
 }
 const structured={ok:false,code:'API_INTERNAL_ERROR',message:'实际后端异常'};
 assert.deepEqual(await read({status:500,text:async()=>JSON.stringify(structured)},'/api/test'),structured);
 const good={ok:true,result:{state:'ready'}};
 assert.deepEqual(await read({status:200,text:async()=>JSON.stringify(good)},'/api/test'),good);
})().catch(error=>{console.error(error);process.exitCode=1;});
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_boundary_failure_returns_json_and_logs_traceback(monkeypatch):
    from services import context
    logs = []

    @contextmanager
    def broken_runtime():
        raise RuntimeError('runtime unavailable')
        yield

    monkeypatch.setattr(context, 'pin_runtime', broken_runtime)
    monkeypatch.setattr('ui.api_routes.log', lambda message, level: logs.append(message))

    @_pinned_segment_route
    async def endpoint(request):
        raise AssertionError('the broken runtime must stop the request first')

    request = Request({'type': 'http', 'headers': [], 'path': '/api/segment/editor/extract-state'})
    response = asyncio.run(endpoint(request))
    data = json.loads(response.body)
    assert response.status_code == 500
    assert data['ok'] is False and data['code'] == 'API_INTERNAL_ERROR'
    assert 'runtime unavailable' in data['message']
    assert 'extract-state' in data['message']
    assert 'Traceback' in logs[0] and 'runtime unavailable' in logs[0]


@pytest.mark.parametrize('action', ['continuity', 'extract-state'])
def test_scene_state_error_is_serialized_as_details_not_a_python_method(monkeypatch, action):
    from ui import api_routes
    from generation.pipelines.minimax_h3.continuity.readiness import SceneStateError

    error = SceneStateError({'segment_uid': 'previous', 'part_id': 'Part 1'},
                            is_target=False, reason='场景初态已变化', reason_code='initial_state_changed')

    def blocked(*args, **kwargs):
        raise error

    monkeypatch.setattr(api_routes, 'ensure_gradio_app_runtime', lambda *a: None)
    monkeypatch.setattr(api_routes, 'log', lambda *a: None)
    monkeypatch.setattr('workflow.video_merge.load_video_jobs', lambda: {'segments': []})
    monkeypatch.setattr('generation.pipelines.minimax_h3.continuity.readiness.read_ready_context', blocked)
    monkeypatch.setattr('services.scene_state_jobs.request_scene_state_refresh', blocked)
    monkeypatch.setattr('services.text_jobs.submit', lambda *a, **k: pytest.fail('blocked state cannot submit LLM'))
    app = FastAPI()
    api_routes.register_api_routes(SimpleNamespace(app=app))
    response = TestClient(app).post('/api/segment/editor/' + action, json={
        'operation_contract': 'scene_state_v3', 'segment_uid': 'target', 'draft': {}})
    assert response.status_code == 400
    assert response.headers['content-type'].startswith('application/json')
    data = response.json()
    assert data['ok'] is False
    assert '重新提取本场景首段状态' in data['message']
    assert data['details'] == {'blocking_segment_uid': 'previous', 'blocking_part_id': 'Part 1',
                               'is_target': False, 'reason_code': 'initial_state_changed'}
