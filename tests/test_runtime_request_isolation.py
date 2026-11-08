import asyncio
import json
from urllib.parse import quote

import pytest

from starlette.requests import Request

from services import context
from ui.api_routes import _pinned_segment_route


@pytest.fixture(autouse=True)
def allow_global_runtime_switch(isolated_runtime):
    # These cases provide their own temporary runtime and verify global switches.
    token = context._PINNED_RUNTIME.set(None)
    try:
        yield
    finally:
        context._PINNED_RUNTIME.reset(token)


def request_for(root):
    return Request({'type': 'http', 'headers': [(b'x-aicf-runtime', str(root).encode())]})


def test_old_project_or_episode_request_is_rejected_before_mutation(monkeypatch, tmp_path):
    old, new = tmp_path / 'draft1', tmp_path / 'draft2'
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (new, 'project', 'draft2'))
    calls = []

    @_pinned_segment_route
    async def save(request):
        calls.append(context.get_project_dir())

    response = asyncio.run(save(request=request_for(old)))
    assert response.status_code == 409
    assert json.loads(response.body)['ok'] is False
    assert calls == []


def test_started_request_stays_in_original_episode_after_switch(monkeypatch, tmp_path):
    old, new = tmp_path / 'draft1', tmp_path / 'draft2'
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (old, 'project', 'draft1'))

    @_pinned_segment_route
    async def save(request):
        monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (new, 'project', 'draft2'))
        await asyncio.sleep(0)
        return context.capture_runtime()

    assert asyncio.run(save(request_for(old))) == (old, 'project', 'draft1')
    assert context.capture_runtime() == (new, 'project', 'draft2')


@pytest.mark.parametrize('name', ['六千万订单/第四集', '中文 空格%20🚚', 'literal%2Fname'])
def test_encoded_unicode_runtime_is_decoded_once_before_guard(monkeypatch, tmp_path, name):
    root = tmp_path / name
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (root, 'project', 'episode'))
    @_pinned_segment_route
    async def save(request):
        return context.get_project_dir()
    request = Request({'type': 'http', 'headers': [
        (b'x-aicf-runtime', quote(str(root), safe='').encode('ascii')),
        (b'x-aicf-runtime-encoding', b'uri')]})
    assert asyncio.run(save(request)) == root


def test_encoded_old_chinese_project_is_rejected(monkeypatch, tmp_path):
    old, new = tmp_path / '旧项目', tmp_path / '新项目'
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (new, 'project', 'episode'))
    @_pinned_segment_route
    async def save(request):
        pytest.fail('old project request must not reach the handler')
    request = Request({'type': 'http', 'headers': [
        (b'x-aicf-runtime', quote(str(old), safe='').encode('ascii')),
        (b'x-aicf-runtime-encoding', b'uri')]})
    assert asyncio.run(save(request)).status_code == 409


def test_invalid_utf8_runtime_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(context, '_ACTIVE_RUNTIME', (tmp_path, 'project', 'episode'))
    @_pinned_segment_route
    async def save(request):
        pytest.fail('invalid encoding must not reach the handler')
    request = Request({'type': 'http', 'headers': [
        (b'x-aicf-runtime', b'%FF'), (b'x-aicf-runtime-encoding', b'uri')]})
    assert asyncio.run(save(request)).status_code == 400
