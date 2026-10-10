import copy
import json

import pytest

from generation.service import GenerationService
from services import context
from ui import project_handlers


@pytest.fixture
def duration_project(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "get_project_dir", lambda: tmp_path)
    monkeypatch.setattr(context, "assert_current_runtime_writable", lambda *_: None)
    monkeypatch.setattr(project_handlers, "log", lambda *args: None)
    beats = {"beats": [{"estimated_duration_sec": 8, "duration_sec": 8, "plot": "unchanged"}, {"estimated_duration_sec": 4}], "total_duration_sec": 12}
    workflow = {"50": {"inputs": {"totalframe": 400}}, "5": {"inputs": {"text": "8 seconds, unchanged"}}}
    wf = tmp_path / "workflow.json"
    wf.write_text(json.dumps(workflow), encoding="utf-8")
    jobs = {"segments": [{"segment_index": 1, "workflow_path": str(wf), "job": {"duration_sec": 8, "fps": 50, "prompt": "unchanged"}, "generation_config": {"duration_sec": 8, "prompt_override": "unchanged"}, "requested_media_spec": {"duration_sec": 8, "fps": 50}, "renders": [{"prompt_snapshot": "historical", "requested_media_spec": {"duration_sec": 8}}]}, {"segment_index": 2, "job": {"duration_sec": 4, "fps": 50}}]}
    for name, value in [("beats.json", beats), ("video_jobs.json", jobs)]:
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    return tmp_path, beats, jobs


def test_segment_duration_matches_beat_sync(duration_project):
    root, beats, jobs = duration_project
    expected_beats = copy.deepcopy(beats)
    expected_beats["beats"][0].update(estimated_duration_sec=10, duration_sec=10)
    expected = json.loads(project_handlers._sync_video_jobs_with_beat_durations(expected_beats, root / "video_jobs.json"))
    (root / "video_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    actual = GenerationService.update_segment_duration.__wrapped__(GenerationService(), 1, 10)
    assert actual == expected
    saved = json.loads((root / "beats.json").read_text(encoding="utf-8"))
    assert saved["beats"][0]["estimated_duration_sec"] == saved["beats"][0]["duration_sec"] == 10
    assert saved["total_duration_sec"] == 14
    segment = actual["segments"][0]
    assert segment["job"]["total_frames"] == 500
    assert segment["generation_config"]["duration_sec"] == 10
    assert segment["requested_media_spec"]["aligned_frame_count"] == 500
    assert segment["renders"] == jobs["segments"][0]["renders"]
    assert actual["segments"][1] == jobs["segments"][1]
    workflow = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["50"]["inputs"]["totalframe"] == 500
    assert workflow["5"]["inputs"]["text"] == "8 seconds, unchanged"


@pytest.mark.parametrize("value", [0, -1, "bad", float("nan"), float("inf")])
def test_invalid_duration_does_not_write(duration_project, value):
    root, _, _ = duration_project
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ValueError):
        GenerationService.update_segment_duration.__wrapped__(GenerationService(), 1, value)
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
