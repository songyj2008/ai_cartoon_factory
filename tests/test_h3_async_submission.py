import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.continuity.revisions import revision
from workflow import video_merge


def setup_pipeline(monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    prompt = '【详细镜头描述】\n[Shot 1]（0–4 秒）：画面人物：无；声音：本镜无对白。'
    payload = {"default_model_id": pipeline.spec.id, "segments": [
        {"segment_index": i, "segment_uid": f'segment-{i}', "model_id": pipeline.spec.id,
         "status": "workflow_ready", "workflow_path": "saved.json",
         "generation_config": {"prompt_override": prompt, "duration_sec": 4, "asset_manifest": []},
         "job": {"final_prompt": prompt, "duration_sec": 4, "asset_manifest": []}}
        for i in range(1, 7)
    ]}
    for segment in payload['segments']:
        segment['workflow_source_revision'] = revision(segment)
    saved = []
    calls = []
    monkeypatch.setattr(video_merge, "load_video_jobs", lambda: copy.deepcopy(payload))

    def save(value):
        snapshot = copy.deepcopy(value)
        payload.clear()
        payload.update(snapshot)
        saved.append(snapshot)

    monkeypatch.setattr(video_merge, "save_video_jobs", save)
    monkeypatch.setattr(pipeline, "_submit_provider", lambda: SimpleNamespace(name="runninghub"))
    monkeypatch.setattr("generation.scheduler.GenerationScheduler.concurrency_limit", lambda *args: 3)
    monkeypatch.setattr("workflow.runninghub_usage.refresh_runninghub_usage_summaries", lambda value: value)

    def submit(provider, segment, *, wait):
        assert wait is False, "Remote generation must not block subsequent submissions"
        # Every earlier accepted ID must already be on disk before the next call.
        for index in calls:
            assert payload["segments"][index - 1]["task_id"] == f"task-{index}"
        index = segment["segment_index"]
        calls.append(index)
        segment.update(status="running", backend="runninghub", submitted=True, task_id=f"task-{index}")

    monkeypatch.setattr(pipeline, "_submit_segment", submit)
    return pipeline, payload, saved, calls


def test_initial_batch_is_nonblocking_and_persisted_before_next_submit(monkeypatch):
    pipeline, payload, _, calls = setup_pipeline(monkeypatch)
    pipeline.submit_saved(wait=True)
    assert calls == [1, 2, 3]
    assert [s["status"] for s in payload["segments"]] == ["running"] * 3 + ["queued"] * 3
    pipeline.submit_saved(wait=True)
    assert calls == [1, 2, 3]
    payload["segments"][0].update(status="success", video_path="done.mp4")
    pipeline.submit_saved({4, 5, 6}, wait=False)
    assert calls == [1, 2, 3, 4]


def test_concurrent_callers_do_not_submit_same_batch_twice(monkeypatch):
    pipeline, _, _, calls = setup_pipeline(monkeypatch)
    ready = Barrier(2)

    def run():
        ready.wait(timeout=5)
        pipeline.submit_saved({1, 2, 3}, wait=True)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    assert calls == [1, 2, 3]


def test_regenerate_preparation_preserves_accepted_task(monkeypatch):
    pipeline, payload, _, calls = setup_pipeline(monkeypatch)
    pipeline.submit_saved({1}, wait=False)
    accepted = copy.deepcopy(payload["segments"][0])
    monkeypatch.setattr(pipeline, "_load_template", lambda: {})
    monkeypatch.setattr(pipeline, "_prepare_segment", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not rebuild an accepted task")))
    pipeline.generate({"beats": [{}]}, submit=False, workflow_only=True, segment_indices={1})
    pipeline.submit_saved({1}, wait=False)
    assert payload["segments"][0] == accepted
    assert calls == [1]


def test_retry_does_not_duplicate_task_hidden_by_stale_card_status(monkeypatch):
    pipeline, payload, _, calls = setup_pipeline(monkeypatch)
    segment = payload["segments"][0]
    segment.update(status="needs_regenerate", task_id="")
    segment["renders"] = [{"status": "running", "submission": {"task_id": "accepted"}}]
    pipeline.submit_saved({1}, wait=False)
    assert calls == []


def test_completed_history_allows_new_submission(monkeypatch):
    pipeline, payload, _, calls = setup_pipeline(monkeypatch)
    payload["segments"][0]["renders"] = [
        {"status": "success", "submission": {"task_id": "completed"}}
    ]
    pipeline.submit_saved({1}, wait=False)
    assert calls == [1]


def test_merge_waits_for_all_videos_and_runs_once(monkeypatch):
    pipeline, payload, _, _ = setup_pipeline(monkeypatch)
    merged = []
    monkeypatch.setattr(video_merge, "merge_segment_videos", lambda value: merged.append(True) or "final.mp4")
    pipeline.submit_saved(wait=True, merge_after=True)
    video_merge.finish_requested_video_merge()
    assert merged == []
    assert payload["merge_when_complete"] is True
    for segment in payload["segments"]:
        segment.update(status="success", video_path="done.mp4")
    video_merge.finish_requested_video_merge()
    video_merge.finish_requested_video_merge()
    assert merged == [True]
    assert payload["final_video_path"] == "final.mp4"
