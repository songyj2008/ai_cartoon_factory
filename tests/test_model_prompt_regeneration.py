from generation.service import GenerationService


def test_prompt_regeneration_dispatches_to_configured_model(monkeypatch):
    segment = {
        "segment_index": 2,
        "model_id": "future_model",
        "generation_config": {"prompt_override": "old", "keep": "asset settings"},
    }
    payload = {"default_model_id": "other_model", "segments": [segment]}
    saved = []
    prepared = []

    class FuturePipeline:
        def reset_prompt_for_regeneration(self, selected_segment):
            selected_segment["generation_config"].pop("prompt_override", None)
            selected_segment["generation_config"]["reset_by"] = "future_model"

    service = object.__new__(GenerationService)
    service._pipeline_for = lambda model_id: FuturePipeline() if model_id == "future_model" else None
    service._run_preparation_by_model = lambda beats, **kwargs: prepared.append((beats, kwargs)) or payload

    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: payload)
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda value: saved.append(value))
    monkeypatch.setattr("generation.service.normalize_video_jobs_payload", lambda value: value)

    result = service.regenerate_segment_prompt({"beats": [{}, {}]}, 2)

    assert result is payload
    assert segment["generation_config"] == {"keep": "asset settings", "reset_by": "future_model"}
    assert saved == [payload]
    assert prepared == [
        (
            {"beats": [{}, {}]},
            {
                "submit": False,
                "workflow_only": True,
                "segment_indices": {2},
                "merge_after": False,
            },
        )
    ]


def test_prompt_regeneration_invokes_model_owned_source_regenerator(monkeypatch):
    segment = {"segment_index": 1, "model_id": "future_model", "generation_config": {"prompt_override": "old"}}
    payload = {"default_model_id": "future_model", "segments": [segment]}
    calls = []

    class FuturePipeline:
        def reset_prompt_for_regeneration(self, selected_segment):
            selected_segment["generation_config"].pop("prompt_override", None)

        def regenerate_prompt_source(self, beats, index):
            calls.append((beats, index))

    service = object.__new__(GenerationService)
    service._pipeline_for = lambda model_id: FuturePipeline()
    service._run_preparation_by_model = lambda beats, **kwargs: payload
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: payload)
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda value: None)
    monkeypatch.setattr("generation.service.normalize_video_jobs_payload", lambda value: value)
    beats = {"beats": [{"plot": "edited"}]}

    service.regenerate_segment_prompt(beats, 1)

    assert calls == [(beats, 1)]


def test_prompt_regeneration_clears_override_written_during_model_call(monkeypatch):
    initial_segment = {
        "segment_index": 1,
        "model_id": "future_model",
        "generation_config": {"prompt_override": "old", "asset_manifest": [{"id": "selected"}]},
    }
    latest_segment = {
        "segment_index": 1,
        "model_id": "future_model",
        # Simulate an autosave that completed while the text model was busy.
        "generation_config": {"prompt_override": "stale autosave", "asset_manifest": [{"id": "latest-selected"}]},
    }
    initial = {"default_model_id": "future_model", "segments": [initial_segment]}
    latest = {"default_model_id": "future_model", "segments": [latest_segment]}
    loads = iter([initial, latest])
    saved = []

    class FuturePipeline:
        def reset_prompt_for_regeneration(self, selected_segment):
            selected_segment["generation_config"].pop("prompt_override", None)

        def regenerate_prompt_source(self, beats, index):
            return None

    service = object.__new__(GenerationService)
    service._pipeline_for = lambda model_id: FuturePipeline()
    service._run_preparation_by_model = lambda beats, **kwargs: latest
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: next(loads))
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda value: saved.append(value))
    monkeypatch.setattr("generation.service.normalize_video_jobs_payload", lambda value: value)

    service.regenerate_segment_prompt({"beats": [{"plot": "edited"}]}, 1)

    assert "prompt_override" not in latest_segment["generation_config"]
    assert latest_segment["generation_config"]["asset_manifest"] == [{"id": "latest-selected"}]
    assert saved[-1] is latest


def test_h3_prompt_reset_preserves_reference_configuration():
    from generation.pipelines.minimax_h3.pipeline import MiniMaxH3Ref2VAPipeline

    segment = {
        "generation_config": {
            "prompt_override": "edited H3 prompt",
            "generation_mode": "ref2va",
            "asset_manifest": [{"id": "image-1", "type": "image", "path": "image.png"}],
            "duration_sec": 8,
        }
    }

    MiniMaxH3Ref2VAPipeline.reset_prompt_for_regeneration(None, segment)

    assert segment["generation_config"] == {
        "generation_mode": "ref2va",
        "asset_manifest": [{"id": "image-1", "type": "image", "path": "image.png"}],
        "duration_sec": 8,
    }
