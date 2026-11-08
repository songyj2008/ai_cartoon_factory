import copy
import json
import re

import pytest

from generation.pipelines.minimax_h3 import prompt_retimer


PROMPT = """【任务概述】
[reference generation + audio reference] 8 秒，会议室 B12，2 人交流。

【参考保留关系】
<Subject 1>：角色95，服装2号，沿用已有身份。
<Audio 1>：6秒音色参考，录音时间 00:03.000；不复述参考内容。

【详细镜头描述】
[Shot 1] (0-3 秒)：使用 <Subject 1>、<Subject 2>、<Subject 3>。
动作：00:00.000-00:01.000 内抬眼，0.500秒轻点文件；情绪克制。
<Subject 1> (S1)说：<d>[Chinese]再等3秒，编号95的文件给我。</d>
背景牌上写着“3秒通道”，桌上有2个文件夹。
[Shot 2] At 00:03.000 (3-8 秒)：切向 <Subject 2> 的近景。
00:03.000-00:08.000 内保持对视，轻微点头。
<Subject 2> (S2)说：<d>[Chinese]我已经确认过第8页，没有问题。</d>

【人物音色绑定】
<Audio 2> -> <Picture 2>，5秒参考，音频起点 00:01.000。
"""

EXPECTED_PROMPT = PROMPT.replace(
    "[reference generation + audio reference] 8 秒", "[reference generation + audio reference] 11 秒"
).replace(
    "[Shot 1] (0-3 秒)", "[Shot 1] (0-4 秒)"
).replace(
    "00:00.000-00:01.000 内抬眼，0.500秒", "00:00.000-00:01.250 内抬眼，0.750秒"
).replace(
    "[Shot 2] At 00:03.000 (3-8 秒)", "[Shot 2] At 00:04.000 (4-11 秒)"
).replace(
    "00:03.000-00:08.000 内保持对视", "00:04.000-00:11.000 内保持对视"
)


def _response(payload):
    chosen = {0: 0, 0.5: 0.75, 1: 1.25, 3: 4, 8: 11}
    return {
        "duration_sec": 11,
        "duration_reason": "保留全部对白，以自然语速完成；抬眼、点头与对白并行。",
        "timings": {slot["id"]: chosen[slot["seconds"]] for slot in payload["time_slots"]},
    }


def _mock_model(monkeypatch, mutate=None, *, response_factory=None):
    calls = []

    def fake_model(system, user, **kwargs):
        payload = json.loads(user)
        calls.append({"system": system, "payload": payload, "kwargs": kwargs})
        result = response_factory(payload) if response_factory else _response(payload)
        if mutate:
            mutate(result, payload)
        return copy.deepcopy(result), 0.1

    monkeypatch.setattr(prompt_retimer, "_call_model", fake_model)
    return calls


def _slot(payload, original, index=0):
    return [slot["id"] for slot in payload["time_slots"] if slot["original"] == original][index]


def test_retimer_changes_only_timing_preserving_dialogue_action_shots_and_references(monkeypatch):
    manifest = [{"id": "voice_95", "type": "audio", "metadata": {"duration_sec": 6}}]
    original_manifest = copy.deepcopy(manifest)
    calls = _mock_model(monkeypatch)

    result = prompt_retimer.retime_h3_prompt(PROMPT, 2, reference_duration_sec=8, asset_manifest=manifest)

    assert result["prompt"] == EXPECTED_PROMPT
    assert result["duration_sec"] == 11
    assert result["duration_reason"]
    assert re.findall(r"<d>.*?</d>", result["prompt"]) == re.findall(r"<d>.*?</d>", PROMPT)
    assert result["prompt"].count("[Shot ") == 2
    assert manifest == original_manifest
    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["reference_duration_sec"] == 8
    assert payload["asset_manifest"] == manifest
    assert calls[0]["kwargs"]["label"] == "h3_prompt_retime_002"
    assert "再等3秒" in payload["prompt_with_time_slots"]
    assert "背景牌上写着“3秒通道”" in payload["prompt_with_time_slots"]
    assert "<Audio 1>：6秒音色参考，录音时间 00:03.000" in payload["prompt_with_time_slots"]
    assert "<Audio 2> -> <Picture 2>，5秒参考，音频起点 00:01.000" in payload["prompt_with_time_slots"]
    assert all(set(slot) == {"id", "original", "seconds"} for slot in payload["time_slots"])
    assert {slot["seconds"] for slot in payload["time_slots"]} == {0, 0.5, 1, 3, 8}
    for slot in payload["time_slots"]:
        assert payload["prompt_with_time_slots"].count("⟦" + slot["id"] + "⟧") == 1


def test_retimer_preserves_whitespace_and_unrelated_numbers_exactly(monkeypatch):
    raw = " \n\t" + PROMPT.replace("[Shot 1] (0-3 秒)", "[Shot 1]  ( 0 - 3 秒 )") + "\n  "
    expected = " \n\t" + EXPECTED_PROMPT.replace("[Shot 1] (0-4 秒)", "[Shot 1]  ( 0 - 4 秒 )") + "\n  "
    _mock_model(monkeypatch)

    result = prompt_retimer.retime_h3_prompt(raw, 1, reference_duration_sec=8)

    assert result["prompt"] == expected


def test_retimer_does_not_accept_model_rewritten_content(monkeypatch):
    def add_rewrite(result, payload):
        result["prompt"] = "改成第三个镜头，新增人物和台词。"

    _mock_model(monkeypatch, add_rewrite)
    result = prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)
    assert result["prompt"] == EXPECTED_PROMPT


@pytest.mark.parametrize("invalid", [3, 16, 10.5, True, None, "nine", float("nan"), float("inf")])
def test_retimer_rejects_invalid_total_duration(monkeypatch, invalid):
    _mock_model(monkeypatch, lambda result, payload: result.update(duration_sec=invalid))
    with pytest.raises(ValueError, match="时长|duration_sec"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_requires_total_duration(monkeypatch):
    _mock_model(monkeypatch, lambda result, payload: result.pop("duration_sec"))
    with pytest.raises(ValueError, match="duration_sec"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


@pytest.mark.parametrize("defect", ["missing", "extra", "not_object"])
def test_retimer_requires_exact_input_slot_set(monkeypatch, defect):
    def mutate(result, payload):
        if defect == "missing":
            result["timings"].pop(payload["time_slots"][0]["id"])
        elif defect == "extra":
            result["timings"]["time_invented"] = 2
        else:
            result["timings"] = []

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError, match="全部原有时间标记"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


@pytest.mark.parametrize("invalid", [-1, 11.001, 0.1234, True, "1", None, float("nan"), float("inf")])
def test_retimer_rejects_invalid_slot_values(monkeypatch, invalid):
    def mutate(result, payload):
        result["timings"][_slot(payload, "0.500")] = invalid

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError, match="时间数值"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


@pytest.mark.parametrize("defect", ["summary", "at", "gap", "overlap", "final_end"])
def test_retimer_rejects_inconsistent_total_and_shot_timing(monkeypatch, defect):
    def mutate(result, payload):
        if defect == "summary":
            result["timings"][payload["time_slots"][0]["id"]] = 10
        elif defect == "at":
            result["timings"][_slot(payload, "00:03.000")] = 4.5
        elif defect in {"gap", "overlap"}:
            result["timings"][_slot(payload, "3", 1)] = 4.5 if defect == "gap" else 3.5
        else:
            result["timings"][_slot(payload, "8", 1)] = 10

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError, match="总秒数|At 时间|Shot"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_rejects_conflicting_references_to_same_absolute_time(monkeypatch):
    def mutate(result, payload):
        # Shot 2 starts at 4 sec, but its body refers to that same old 3-sec
        # boundary as 4.5 sec. Repeated absolute clocks must remain consistent.
        result["timings"][_slot(payload, "00:03.000", 1)] = 4.5

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_rejects_reversed_body_time_range(monkeypatch):
    def mutate(result, payload):
        result["timings"][_slot(payload, "00:03.000", 1)] = 11
        result["timings"][_slot(payload, "00:08.000")] = 4

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


@pytest.mark.parametrize("reason", ["missing", None, "", "  "])
def test_retimer_requires_timing_reason(monkeypatch, reason):
    def mutate(result, payload):
        if reason == "missing":
            result.pop("duration_reason")
        else:
            result["duration_reason"] = reason

    _mock_model(monkeypatch, mutate)
    with pytest.raises(ValueError, match="时长依据"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_surfaces_need_to_split_without_rewriting(monkeypatch):
    calls = _mock_model(monkeypatch, response_factory=lambda payload: {
        "status": "needs_replan", "reason": "完整对白自然说完需要超过 15 秒，需要拆段。",
    })
    with pytest.raises(ValueError, match="需要拆段.*完整对白"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [
    "<Subject 1>说：<d>[Chinese]再等3秒。</d>，看向<Subject 2>。",
    "【参考保留关系】\n<Audio 1>：8秒参考，00:03.000起。\n【详细镜头描述】\n人物交谈。",
    "背景牌上写着“3秒通道”，桌上有2个文件夹。",
])
def test_retimer_fails_before_model_when_no_adjustable_time_fields(monkeypatch, raw):
    calls = _mock_model(monkeypatch)
    with pytest.raises(ValueError, match="没有可调整的时间描述"):
        prompt_retimer.retime_h3_prompt(raw, 1, reference_duration_sec=8)
    assert calls == []


@pytest.mark.parametrize("raw", ["", " \n\t"])
def test_retimer_rejects_empty_editor_before_model(monkeypatch, raw):
    calls = _mock_model(monkeypatch)
    with pytest.raises(ValueError, match="编辑区提示词为空"):
        prompt_retimer.retime_h3_prompt(raw, 1, reference_duration_sec=8)
    assert calls == []


def test_retimer_rejects_non_object_model_response(monkeypatch):
    _mock_model(monkeypatch, response_factory=lambda payload: ["invalid result"])
    with pytest.raises(ValueError, match="JSON 对象"):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_rejects_action_time_leaving_its_original_shot(monkeypatch):
    def response(payload):
        result = _response(payload)
        result['timings'][_slot(payload, '00:01.000')] = 8
        return result

    _mock_model(monkeypatch, response_factory=response)
    with pytest.raises(ValueError, match='超出所属 Shot'):
        prompt_retimer.retime_h3_prompt(PROMPT, 1, reference_duration_sec=8)


def test_retimer_preserves_existing_cross_shot_speech_interval(monkeypatch):
    original = PROMPT.replace('动作：00:00.000-00:01.000 内抬眼',
                              '对白跨镜：00:00.000-00:04.000 原句继续')

    def response(payload):
        chosen = {0: 0, 0.5: 0.75, 3: 4, 4: 5, 8: 11}
        return {'duration_sec': 11, 'duration_reason': '保持原句跨镜续说。',
                'timings': {slot['id']: chosen[slot['seconds']] for slot in payload['time_slots']}}

    _mock_model(monkeypatch, response_factory=response)
    result = prompt_retimer.retime_h3_prompt(original, 1, reference_duration_sec=8)
    assert '对白跨镜：00:00.000-00:05.000 原句继续' in result['prompt']
    assert '[Shot 1] (0-4 秒)' in result['prompt']
