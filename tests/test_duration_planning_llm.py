import copy
import json
from types import SimpleNamespace

import pytest

from services import duration_planning_llm as timing, llm, text_jobs, web_llm_bridge as bridge


@pytest.fixture
def routing(monkeypatch):
    saved = {"id": "timing-operation", "state": "running"}
    selected = ["chatgpt", "api", "deepseek"]
    monkeypatch.setattr(text_jobs, "current", lambda: saved)
    monkeypatch.setattr(text_jobs, "get", lambda _: copy.deepcopy(saved))
    monkeypatch.setattr(text_jobs, "update", lambda _, **values: saved.update(copy.deepcopy(values)))
    monkeypatch.setattr(text_jobs, "phase", lambda _: None)

    def check():
        if saved["state"] == "cancelled":
            raise RuntimeError("任务已取消")

    monkeypatch.setattr(text_jobs, "check_cancelled", check)
    monkeypatch.setattr(bridge, "routes_for", lambda *_: selected[:])
    monkeypatch.setattr(llm, "OpenAI", lambda **_: pytest.fail("unexpected real API request"))
    monkeypatch.setattr(bridge, "wait_for_llm", lambda *a, **k: pytest.fail("unexpected web request"))
    return saved, selected


def call(user="original", **kwargs):
    return llm.call_llm("JSON timing only", user, max_tokens=3500, label="h3_prompt_retime_003", **kwargs)


def test_chatgpt_is_first_and_success_stops_chain(routing, monkeypatch):
    calls = []

    def web(*args, **kw):
        calls.append(kw)
        return {"duration_sec": 9}, 1

    monkeypatch.setattr(bridge, "wait_for_llm", web)
    monkeypatch.setattr(timing, "_api", lambda *a: pytest.fail("ChatGPT succeeded"))
    assert call()[0] == {"duration_sec": 9}
    assert [x["provider"] for x in calls] == ["chatgpt"]
    assert calls[0]["call_hint"].endswith(":duration-pro-v2:chatgpt")
    assert timing.result_source(3) == {"provider": "chatgpt", "model": None}


def test_unchecked_api_is_never_called_even_after_chatgpt_failure(routing, monkeypatch):
    routing[1][:] = ['chatgpt', 'deepseek']
    calls = []
    def web(*a, **kw):
        calls.append(kw['provider'])
        if kw['provider'] == 'chatgpt':
            raise RuntimeError('ChatGPT unavailable')
        return {'duration_sec': 8}, 0
    monkeypatch.setattr(bridge, 'wait_for_llm', web)
    monkeypatch.setattr(timing, '_api', lambda *a: pytest.fail('API was not selected'))
    assert call()[0] == {'duration_sec': 8}
    assert calls == ['chatgpt', 'deepseek']


def test_chatgpt_failure_uses_api_and_receipts_replay_without_requests(routing, monkeypatch):
    calls = []

    def web(*a, **kw):
        calls.append(kw["provider"])
        raise RuntimeError("service unavailable")

    def api(*args):
        calls.append("api")
        return {"duration_sec": 10}, {"prompt_tokens": 11500, "completion_tokens": 200}

    monkeypatch.setattr(bridge, "wait_for_llm", web)
    monkeypatch.setattr(timing, "_api", api)
    assert call()[0] == {"duration_sec": 10}
    assert call()[0] == {"duration_sec": 10}
    assert calls == ["chatgpt", "api"]
    receipt = routing[0]["timing_calls"]["h3_prompt_retime_003"]
    assert receipt["attempts"]["chatgpt"]["state"] == "error"
    assert receipt["attempts"]["api"]["usage"]["prompt_tokens"] == 11500
    assert timing.result_source(3) == {"provider": "api", "model": "deepseek-v4-pro"}


def test_without_chatgpt_api_then_deepseek_web(routing, monkeypatch):
    routing[1][:] = ["api", "doubao", "deepseek"]
    calls = []

    def api(*a):
        calls.append("api")
        raise ValueError("key is not configured")

    def web(*a, **kw):
        calls.append(kw["provider"])
        assert kw["call_hint"].endswith(":deepseek")
        return {"duration_sec": 8}, 0

    monkeypatch.setattr(timing, "_api", api)
    monkeypatch.setattr(bridge, "wait_for_llm", web)
    assert call()[0] == {"duration_sec": 8}
    assert calls == ["api", "deepseek"]


def test_full_fallback_order_and_failed_attempts_are_not_replayed(routing, monkeypatch):
    calls = []

    def web(*a, **kw):
        calls.append(kw['provider'])
        if kw['provider'] == 'chatgpt':
            raise RuntimeError('ChatGPT unavailable')
        return {'duration_sec': 8}, 0

    def api(*a):
        calls.append('api')
        raise RuntimeError('API rate limited')

    monkeypatch.setattr(timing, '_api', api)
    monkeypatch.setattr(bridge, 'wait_for_llm', web)
    assert call()[0] == call()[0] == {'duration_sec': 8}
    assert calls == ['chatgpt', 'api', 'deepseek']


def test_exhausted_channels_preserve_diagnostics(routing, monkeypatch):
    routing[1][:] = ['api', 'deepseek']
    monkeypatch.setattr(timing, '_api', lambda *a: (_ for _ in ()).throw(RuntimeError('API offline')))
    monkeypatch.setattr(bridge, 'wait_for_llm', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('web offline')))
    with pytest.raises(RuntimeError, match='各渠道均调用失败') as error:
        call()
    assert 'API offline' in str(error.value) and 'web offline' in str(error.value)
    assert all(a['state'] == 'error' for a in routing[0]['timing_calls']['h3_prompt_retime_003']['attempts'].values())


@pytest.mark.parametrize("result", [{"status": "needs_replan", "reason": "需要拆段"},
                                    {"duration_sec": 10, "timings": {}}])
def test_business_result_is_validated_by_retimer_without_trying_other_models(routing, monkeypatch, result):
    monkeypatch.setattr(bridge, "wait_for_llm", lambda *a, **k: (result, 0))
    monkeypatch.setattr(timing, "_api", lambda *a: pytest.fail("must not replace business decision"))
    assert call()[0] == result


def test_cancelled_operation_never_falls_back_to_paid_api(routing, monkeypatch):
    def cancelled(*a, **k):
        routing[0]["state"] = "cancelled"
        raise RuntimeError("cancelled")

    monkeypatch.setattr(bridge, "wait_for_llm", cancelled)
    monkeypatch.setattr(timing, "_api", lambda *a: pytest.fail("cancelled"))
    with pytest.raises(RuntimeError, match="任务已取消"):
        call()


def test_interrupted_paid_attempt_never_resends_or_uses_unconfirmed_result(routing, monkeypatch):
    routing[1][:] = ["api"]
    monkeypatch.setattr(timing, "_api", lambda *a: ({"duration_sec": 8}, {}))
    call()
    attempt = routing[0]["timing_calls"]["h3_prompt_retime_003"]["attempts"]["api"]
    attempt.update(state="running")
    monkeypatch.setattr(timing, "_api", lambda *a: pytest.fail("duplicate paid request"))
    with pytest.raises(text_jobs.NeedsAttentionError, match="不会自动重复请求"):
        call()


def test_changed_input_cannot_reuse_old_receipt(routing, monkeypatch):
    monkeypatch.setattr(bridge, "wait_for_llm", lambda *a, **k: ({"duration_sec": 8}, 0))
    call()
    with pytest.raises(text_jobs.NeedsAttentionError, match="输入已变化"):
        call("changed dialogue")


def test_api_uses_pro_json_non_thinking_and_explicit_network_timeout(tmp_path, monkeypatch):
    from services import file_utils
    seen = {}

    def create(**request):
        seen["request"] = request
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"duration_sec":9}', reasoning_content="unfinished"),
            finish_reason="stop")], usage={"prompt_tokens": 100, "completion_tokens": 10})

    def client(**kw):
        seen["client"] = kw
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm, "OpenAI", client)
    monkeypatch.setattr(llm, "_deepseek_api_key", lambda: "test-secret")
    monkeypatch.setattr(file_utils, "get_project_temp_dir", lambda: tmp_path)
    monkeypatch.setitem(llm.CONFIG, "deepseek_model", "deepseek-flash")
    assert timing._api("JSON", "timings", 3500, "timing")[0] == {"duration_sec": 9}
    assert seen["request"]["model"] == "deepseek-v4-pro"
    assert seen["request"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert seen["request"]["response_format"] == {"type": "json_object"}
    assert seen["client"]["timeout"] == 120.0 and seen["client"]["max_retries"] == 0
    assert llm.CONFIG["deepseek_model"] == "deepseek-flash"
    request_file = next(tmp_path.glob("*_request.json"))
    assert "test-secret" not in request_file.read_text(encoding="utf-8")


def test_reasoning_is_never_a_final_json_response():
    response = {"choices": [{"message": {"content": "", "reasoning_content": '{"duration_sec":4}'},
                             "finish_reason": "length"}]}
    assert llm.extract_response_text(response)[:2] == ("", "")


def test_other_llm_operations_keep_existing_selection(monkeypatch):
    monkeypatch.setattr(timing, "call_duration_llm", lambda *a, **k: pytest.fail("not a timing task"))
    monkeypatch.setattr(bridge, "routes_for", lambda *a: ["doubao"])
    monkeypatch.setattr(bridge, "wait_for_llm", lambda *a, **k: ({"provider": k["provider"]}, 0))
    assert llm.call_llm("system", "user", label="h3_prompt_regenerate_003")[0] == {"provider": "doubao"}


@pytest.mark.parametrize("status", ["timeout", "cancelled"])
def test_ordered_web_timeout_is_terminal_but_user_cancellation_stops(monkeypatch, status):
    monkeypatch.setattr(text_jobs, "current", lambda: None)
    monkeypatch.setattr(text_jobs, "check_cancelled", lambda: None)
    monkeypatch.setattr(text_jobs, "link_child", lambda _: None)
    monkeypatch.setattr(text_jobs, "phase", lambda _: None)
    monkeypatch.setattr(bridge, "create_task", lambda *a, **k: {"id": "child"})
    monkeypatch.setattr(bridge, "task_status", lambda *a, **k: {"id": "child", "status": status})
    expected = text_jobs.NeedsAttentionError if status == "cancelled" else RuntimeError
    with pytest.raises(expected, match="取消" if status == "cancelled" else "转下一渠道"):
        bridge.wait_for_llm("system", "user", kind="json", entry="prompt", provider="chatgpt",
                            metadata={"ordered_timing_fallback": True})
