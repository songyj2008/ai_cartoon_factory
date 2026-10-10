from workflow import master


H3_MODEL_ID = "minimax_h3_local_ref2va"


def test_h3_prompt_regeneration_clears_only_prompt_override(monkeypatch):
    jobs = {
        "default_model_id": H3_MODEL_ID,
        "segments": [
            {
                "segment_index": 1,
                "model_id": H3_MODEL_ID,
                "generation_config": {
                    "prompt_override": "edited prompt",
                    "asset_manifest": [{"id": "image-1", "type": "image", "path": "image.png"}],
                },
            }
        ],
    }
    calls = []

    class FakeService:
        def project_default_model_id(self):
            return H3_MODEL_ID

        def regenerate_segment_prompt(self, beats_data, index):
            calls.append(("regenerate", beats_data, index))
            return jobs

    monkeypatch.setattr(master, "_assert_brand_verified", lambda action: None)
    monkeypatch.setattr(master, "_load_beats_data", lambda: {"beats": [{"id": 1}]})
    monkeypatch.setattr(master, "load_video_jobs", lambda: jobs)
    monkeypatch.setattr(master, "load_json_file_silent", lambda name: {})
    monkeypatch.setattr(master, "assert_quality_gate_clear", lambda **kwargs: None)
    monkeypatch.setattr(master, "get_generation_service", lambda: FakeService())
    monkeypatch.setattr(master, "_groups_html", lambda result: "groups")

    master.regenerate_segment_prompt_from_slot(1)

    assert calls == [("regenerate", {"beats": [{"id": 1}]}, 1)]
