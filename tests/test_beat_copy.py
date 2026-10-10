import copy
import json

import pytest

from ui import beat_copy


def sample(count=16):
    beats = {"beats": [{"id": i, "beat_uid": f"b{i}", "title": f"Beat {i}",
                        "plot": "plot", "source_excerpt": "novel", "estimated_duration_sec": 10,
                        "reference_image_ids": {"role": "image"}} for i in range(1, count + 1)]}
    jobs = {"segments": [{"segment_index": i, "segment_uid": f"s{i}", "status": "success",
                           "task_id": f"task{i}", "video_path": f"video{i}.mp4",
                           "renders": [{"task_id": f"task{i}"}], "source_excerpt": "novel",
                           "generation_config": {"prompt_override": "manual", "asset_manifest": [{"path": "ref.png"}]},
                           "compiled_prompt_snapshot": "manual",
                           "job": {"prompt": "manual", "duration_sec": 10, "fps": 50,
                                   "reference_images": ["ref.png"], "background_image": "bg.png",
                                   "output_prefix": f"old{i}", "task_id": "nested_task"}}
                          for i in range(1, count + 1)], "final_video_path": "merged.mp4"}
    return beats, jobs


@pytest.mark.parametrize("source,target", [(10, 15), (15, 10), (10, 1), (10, 17), (10, 10)])
def test_copy_independent_inputs_and_shifted_history(source, target):
    beats, jobs = sample()
    before = copy.deepcopy((beats, jobs))
    result, payload = beat_copy.build_beat_copy(beats, jobs, source, target)
    duplicate = result["beats"][target - 1]
    segment = payload["segments"][target - 1]
    assert duplicate["beat_uid"] not in {b["beat_uid"] for b in beats["beats"]}
    assert duplicate["plot"] == "plot" and "source_excerpt" not in duplicate
    assert segment["segment_uid"] not in {s["segment_uid"] for s in jobs["segments"]}
    assert segment["generation_config"] == jobs["segments"][source - 1]["generation_config"]
    assert segment["job"]["prompt"] == "manual"
    assert segment["job"]["reference_images"] == ["ref.png"]
    assert segment["job"]["background_image"] == "bg.png"
    assert not {"task_id", "renders", "video_path", "source_excerpt"} & segment.keys()
    assert not {"task_id", "output_prefix"} & segment["job"].keys()
    for original in jobs["segments"]:
        shifted = next(s for s in payload["segments"] if s["segment_uid"] == original["segment_uid"])
        assert shifted["segment_index"] == original["segment_index"] + (original["segment_index"] >= target)
        assert shifted["task_id"] == original["task_id"]
        assert shifted["video_path"] == original["video_path"]
        assert shifted["renders"] == original["renders"]
    assert len(result["beats"]) == 17
    assert result["total_duration_sec"] == 170
    assert "final_video_path" not in payload
    duplicate["reference_image_ids"]["role"] = "changed"
    segment["generation_config"]["asset_manifest"][0]["path"] = "changed"
    assert (beats, jobs) == before


@pytest.mark.parametrize("source,target", [(0, 1), (17, 1), (1, 18), (1, 1.5), (None, 1)])
def test_invalid_positions(source, target):
    with pytest.raises(ValueError):
        beat_copy.build_beat_copy(*sample(), source, target)


def test_no_segment_prompts_yet():
    beats, _ = sample()
    data, jobs = beat_copy.build_beat_copy(beats, {"segments": []}, 1, 2)
    assert len(data["beats"]) == 17
    assert jobs["segments"] == []


def test_running_destination_blocks_copy():
    beats, jobs = sample()
    jobs["segments"][14]["status"] = "running"
    with pytest.raises(ValueError, match="正在生成"):
        beat_copy.build_beat_copy(beats, jobs, 10, 15)


def test_commit_rolls_back_both_files(monkeypatch, tmp_path):
    beats, jobs = sample()
    for name, data in [("beats.json", beats), ("video_jobs.json", jobs)]:
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    originals = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(beat_copy, "load_video_jobs", lambda: jobs)
    def fail(payload):
        (tmp_path / "video_jobs.json").write_text("partial", encoding="utf-8")
        raise OSError("disk error")
    monkeypatch.setattr(beat_copy, "save_video_jobs", fail)
    with pytest.raises(OSError):
        beat_copy.persist_beat_copy(tmp_path, beats, 10, 15)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == originals


def test_commit_with_real_normalization(monkeypatch, tmp_path):
    from workflow import video_merge
    beats, jobs = sample()
    (tmp_path / "video_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (tmp_path / "logs").mkdir()
    log_path = tmp_path / "logs/render_log.json"
    log_path.write_text(json.dumps({"parts": [{"id": "part_015", "video_path": "original.mp4"}]}))
    monkeypatch.setattr(video_merge, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr(video_merge, "load_json_file_silent", lambda name: (tmp_path / name).read_text(encoding="utf-8"))
    _, result = beat_copy.persist_beat_copy(tmp_path, beats, 10, 15)
    new = json.loads((tmp_path / "video_jobs.json").read_text(encoding="utf-8"))["segments"][14]
    assert not new.get("renders") and not new.get("runninghub_attempts")
    assert not new.get("task_id") and not new.get("video_path")
    assert new["job"]["prompt"] == "manual"
    assert json.loads(log_path.read_text())["parts"] == [{"id": "part_016", "video_path": "original.mp4"}]
