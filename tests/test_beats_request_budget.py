import pytest

from scripts.story_script import generate_beats as beats


@pytest.mark.parametrize("config,expected", [
    ({"deepseek_model": "deepseek-v4-flash"}, 65536),
    ({"deepseek_model": "deepseek-v4-pro"}, 65536),
    ({"deepseek_model": "deepseek-chat"}, 16384),
    ({"max_tokens": 24000}, 24000),
    ({"max_tokens": 24000, "beats_max_tokens": 48000}, 48000),
])
def test_beats_request_budget(monkeypatch, tmp_path, config, expected):
    requests = []

    def complete(_config, _messages, **kwargs):
        requests.append(kwargs)
        return '{"beats": []}', {}, {"finish_reason": "stop"}

    monkeypatch.setattr(beats, "call_deepseek_chat_sdk", complete)
    assert beats._call_deepseek_messages(config, [], tmp_path) == {"beats": []}
    assert requests[0]["max_tokens"] == expected
    assert requests[0]["timeout"] == 600


def test_truncated_beats_are_not_parsed_or_silently_retried(monkeypatch, tmp_path):
    calls = []

    def complete(*args, **kwargs):
        calls.append(kwargs)
        return '{"beats": [', {"usage": {"completion_tokens": 65536}}, {"finish_reason": "length"}

    monkeypatch.setattr(beats, "call_deepseek_chat_sdk", complete)
    with pytest.raises(RuntimeError, match="beats_max_tokens"):
        beats._call_deepseek_messages({}, [], tmp_path)
    assert len(calls) == 1
