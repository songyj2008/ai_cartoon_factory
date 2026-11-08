import pytest

from pipeline.beats import build_beat_generation_input
from scripts.story_script.generate_beats import normalize_beats, beat_count_policy_prompt


H3 = "minimax_h3_local_ref2va"
LTX = "ltx23_licon_msr_v2"


def _beat(index: int, duration: int, *, shot_end: int | None = None):
    beat = {
        "title": f"事件 {index}",
        "plot": f"连续剧情事件 {index}",
        "estimated_duration_sec": duration,
    }
    if shot_end is not None:
        beat["model_hints"] = {
            H3: {
                "shot_mode": "multi_shot",
                "shots": [{"start_sec": 0, "end_sec": shot_end, "action": "完成当前事件", "audio": "本镜无对白"}],
            }
        }
    return beat


def _raw(beats):
    return {"title": "测试", "locked_outcome": "故事结局保持不变。", "beats": beats}


def test_h3_supplies_a_model_default_count_but_ltx_does_not(monkeypatch):
    monkeypatch.setattr("pipeline.beats.load_project_bible", lambda: {})
    monkeypatch.setattr("pipeline.beats.ensure_project_bible", lambda **kwargs: {})
    monkeypatch.setattr("pipeline.beats.load_asset_index", lambda: {})
    monkeypatch.setattr("pipeline.beats.load_industry_profile", lambda *_args: {})

    h3 = build_beat_generation_input({}, 60, model_id=H3)
    ltx = build_beat_generation_input({}, 60, model_id=LTX)

    assert h3["target_beat_count"] == 7
    assert h3["target_beat_count_source"] == "model_default"
    assert "target_beat_count" not in ltx


@pytest.mark.parametrize('target, durations', [(90, [10, 8, 12, 10, 12, 10, 12]),
                                            (60, [10, 8, 12, 10, 12, 12, 12])])
def test_h3_accepts_actual_total_above_or_below_reference(target, durations):
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    result = normalize_beats(_raw(beats), target_duration_sec=target, target_beat_count=1, model_id=H3)
    assert result['target_duration_sec'] == target
    assert result['total_duration_sec'] == sum(durations)
    assert [row['estimated_duration_sec'] for row in result['beats']] == durations
    assert [row['model_hints'][H3]['shots'][0]['end_sec'] for row in result['beats']] == durations


def test_h3_does_not_mechanically_compress_thirteen_beats_into_seven():
    durations = [5] * 8 + [4] * 5
    beats = [_beat(index, duration) for index, duration in enumerate(durations, 1)]
    result = normalize_beats(_raw(beats), target_duration_sec=60, target_beat_count=7, model_id=H3)
    assert len(result['beats']) == 13
    assert [beat['title'] for beat in result['beats']] == [beat['title'] for beat in beats]
    assert result['total_duration_sec'] == 60


@pytest.mark.parametrize('reference, durations', [(4, [10] * 6), (7, [15] * 4), (1, [10] * 6), (None, [10] * 6)])
def test_beat_count_is_only_a_reference(reference, durations):
    result = normalize_beats(_raw([_beat(i, duration) for i, duration in enumerate(durations, 1)]),
                             target_duration_sec=60, target_beat_count=reference, model_id=H3)
    assert len(result['beats']) == len(durations)
    assert [row['estimated_duration_sec'] for row in result['beats']] == durations


def test_count_guidance_prioritizes_complete_dialogue_over_reference_count():
    from scripts.story_script.generate_beats import project_beat_policy
    prompt = beat_count_policy_prompt(60, 6, policy=project_beat_policy(H3))
    assert '参考 Beat 数量为 6' in prompt
    assert '规划目标' in prompt and '适当增减' in prompt
    assert '不遗漏、改写或重复台词' in prompt
    assert '必须恰好输出' not in prompt
    assert '参考总时长为 60 秒' in prompt
    assert '不要求各段时长之和与参考值相等' in prompt
    assert '总和必须恰好' not in prompt


def test_novel_generation_reviews_large_count_deviation_once(monkeypatch, tmp_path):
    from scripts.story_script import generate_beats
    calls = []

    def model(config, messages, temp_dir, **kwargs):
        import json
        calls.append(json.loads(messages[-1]['content']))
        return _raw([_beat(i, 10) for i in range(1, 7)])

    monkeypatch.setattr(generate_beats, '_call_deepseek_messages', model)
    result = generate_beats.generate_novel_beats(
        {'novel_text': '原文', 'target_duration_sec': 90, 'target_beat_count': 1},
        config={}, model_id=H3, temp_dir=tmp_path)
    assert len(calls) == 2
    assert calls[0]['target_beat_count'] == 1
    assert calls[1]['planning_budget']['exceeds_reference'] is True
    assert len(result['beats']) == 6
    assert result['target_duration_sec'] == 90
    assert result['total_duration_sec'] == 60


def test_h3_does_not_rescale_shots_to_correct_total():
    durations = [9]
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    result = normalize_beats(_raw(beats), target_duration_sec=8, target_beat_count=len(beats), model_id=H3)
    assert result['total_duration_sec'] == 9
    assert result['beats'][0]['model_hints'][H3]['shots'][0]['end_sec'] == 9


@pytest.mark.parametrize('duration', [-1, 0, 3, 16])
def test_h3_still_rejects_durations_outside_model_limits(duration):
    with pytest.raises(ValueError, match='每段时长须在'):
        normalize_beats(_raw([_beat(1, duration)]), target_duration_sec=90, model_id=H3)


def test_h3_preserves_plot_allocated_durations():
    durations = [8, 8, 9, 8, 9, 9, 9]
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    result = normalize_beats(_raw(beats), target_duration_sec=60, target_beat_count=len(beats), model_id=H3)
    assert [b["estimated_duration_sec"] for b in result["beats"]] == durations


def test_ltx_also_treats_total_duration_as_a_reference():
    result = normalize_beats(
        _raw([_beat(1, 30), _beat(2, 30)]),
        target_duration_sec=90,
        model_id=LTX,
    )

    assert [beat["estimated_duration_sec"] for beat in result["beats"]] == [30, 30]
    assert result['target_duration_sec'] == 90
    assert result['total_duration_sec'] == 60
