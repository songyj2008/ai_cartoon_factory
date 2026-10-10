import pytest

from pipeline.beats import build_beat_generation_input
from scripts.story_script.generate_beats import normalize_beats


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
                "shots": [{"start_sec": 0, "end_sec": shot_end, "action": "完成当前事件"}],
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


def test_h3_invalid_total_requires_model_repair():
    durations = [10, 8, 12, 10, 12, 12, 12]
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    with pytest.raises(ValueError):
        normalize_beats(_raw(beats), target_duration_sec=60, target_beat_count=len(beats), model_id=H3)


def test_h3_does_not_mechanically_compress_thirteen_beats_into_seven():
    beats = [_beat(index, 9) for index in range(1, 14)]

    with pytest.raises(ValueError, match="does not match target_beat_count=7"):
        normalize_beats(_raw(beats), target_duration_sec=60, target_beat_count=7, model_id=H3)


def test_h3_does_not_rescale_shots_to_correct_total():
    durations = [9]
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    with pytest.raises(ValueError):
        normalize_beats(_raw(beats), target_duration_sec=8, target_beat_count=len(beats), model_id=H3)


def test_h3_preserves_plot_allocated_durations():
    durations = [8, 8, 9, 8, 9, 9, 9]
    beats = [_beat(i, d, shot_end=d) for i,d in enumerate(durations,1)]
    result = normalize_beats(_raw(beats), target_duration_sec=60, target_beat_count=len(beats), model_id=H3)
    assert [b["estimated_duration_sec"] for b in result["beats"]] == durations


def test_ltx_normalization_keeps_its_existing_duration_policy():
    result = normalize_beats(
        _raw([_beat(1, 30), _beat(2, 30)]),
        target_duration_sec=60,
        model_id=LTX,
    )

    assert [beat["estimated_duration_sec"] for beat in result["beats"]] == [30, 30]
