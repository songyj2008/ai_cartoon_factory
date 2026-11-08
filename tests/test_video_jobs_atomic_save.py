"""Windows replace contention never truncates or silently loses a job save."""
import json
import os
from pathlib import Path

import pytest

from workflow import video_merge


def windows_error(code):
    error = PermissionError('file is in use')
    error.winerror = code
    return error


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
def test_real_windows_reader_can_release_replace_lock_during_retry(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    target = tmp_path / 'video_jobs.json'
    target.write_text('{"old":true}', encoding='utf-8')
    # Share reads/writes, but deny delete/replace just like an open Windows reader.
    handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    delays = []

    def release_reader(delay):
        nonlocal handle
        delays.append(delay)
        assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}
        assert kernel.CloseHandle(handle)
        handle = None

    monkeypatch.setattr(video_merge.time, 'sleep', release_reader)
    try:
        video_merge._write_video_jobs_atomic(target, '{"saved":true}')
        assert delays == [0.05]
        assert json.loads(target.read_text(encoding='utf-8')) == {'saved': True}
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


@pytest.mark.parametrize('code', [5, 32, 33])
def test_transient_replace_contention_retries_complete_json(tmp_path, monkeypatch, code):
    target = tmp_path / 'video_jobs.json'
    target.write_text('{"old":true}', encoding='utf-8')
    original_replace = Path.replace
    calls, sleeps = [], []

    def replace(source, destination):
        calls.append(source)
        assert json.loads(source.read_text(encoding='utf-8')) == {'saved': True}
        assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}
        if len(calls) < 3:
            raise windows_error(code)
        return original_replace(source, destination)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(video_merge.time, 'sleep', sleeps.append)
    video_merge._write_video_jobs_atomic(target, '{"saved":true}')
    assert json.loads(target.read_text(encoding='utf-8')) == {'saved': True}
    assert sleeps == [0.05, 0.1]
    assert len(set(calls)) == 1
    assert list(tmp_path.iterdir()) == [target]


def test_persistent_contention_reports_failure_and_preserves_old_file(tmp_path, monkeypatch):
    target = tmp_path / 'video_jobs.json'
    original = b'{"old":true}'
    target.write_bytes(original)
    calls, sleeps = [], []

    def replace(source, destination):
        calls.append(source)
        raise windows_error(5)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(video_merge.time, 'sleep', sleeps.append)
    with pytest.raises(PermissionError):
        video_merge._write_video_jobs_atomic(target, '{"saved":true}')
    assert len(calls) == 6 and len(sleeps) == 5
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_other_io_errors_are_not_retried(tmp_path, monkeypatch):
    target = tmp_path / 'video_jobs.json'
    target.write_text('old', encoding='utf-8')
    calls = []

    def replace(source, destination):
        calls.append(source)
        raise OSError('disk error')

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(video_merge.time, 'sleep', lambda _: pytest.fail('unexpected retry'))
    with pytest.raises(OSError, match='disk error'):
        video_merge._write_video_jobs_atomic(target, 'new')
    assert len(calls) == 1 and target.read_text(encoding='utf-8') == 'old'
    assert list(tmp_path.iterdir()) == [target]


def test_save_uses_unique_staging_files_and_ignores_old_shared_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(video_merge, 'get_project_dir', lambda: tmp_path)
    shared = tmp_path / 'video_jobs.json.tmp'
    shared.write_text('another worker', encoding='utf-8')
    original_replace = Path.replace
    staged = []

    def replace(source, destination):
        staged.append(source)
        assert source != shared
        return original_replace(source, destination)

    monkeypatch.setattr(Path, 'replace', replace)
    for marker in ['first', 'second']:
        video_merge.save_video_jobs({'segments': [], 'marker': marker})
        result = json.loads((tmp_path / 'video_jobs.json').read_text(encoding='utf-8'))
        assert result['marker'] == marker
    assert len(set(staged)) == 2
    assert shared.read_text(encoding='utf-8') == 'another worker'
