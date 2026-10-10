from pathlib import Path
from types import SimpleNamespace

from generation.job_schema import VIDEO_JOBS_SCHEMA_VERSION, normalize_video_jobs_payload
from generation.registry import default_model_id, get_model_registry
from workflow.licon_msr import VideoJobContract, patch_licon_msr_workflow
from workflow.submit_factory import submit_backend_config
from workflow.submitters.runninghub_provider import RunningHubSubmitProvider


LTX_MODEL_ID = "ltx23_licon_msr_v2"
H3_MODEL_ID = "minimax_h3_local_ref2va"


def _minimal_ltx_template() -> dict:
    return {
        "5": {"inputs": {"text": ""}},
        "20": {"inputs": {"filename_prefix": ""}},
        "28": {"inputs": {"1": ["80", 0], "2": ["81", 0], "3": ["82", 0], "4": ["83", 0]}},
        "50": {"inputs": {"value": 0}},
        "80": {"inputs": {"file_name": ""}},
        "81": {"inputs": {"file_name": ""}},
        "82": {"inputs": {"file_name": ""}},
        "83": {"inputs": {"file_name": ""}},
        "84": {"inputs": {"file_name": ""}},
    }


def test_registry_exposes_ltx_and_the_configured_h3_pipeline():
    registry = get_model_registry()

    assert registry.has(LTX_MODEL_ID)
    assert registry.has(H3_MODEL_ID)
    assert [spec.id for spec in registry.specs()] == [LTX_MODEL_ID, H3_MODEL_ID]
    h3 = registry.get(H3_MODEL_ID).spec
    assert h3.capabilities.duration_min_sec == 4
    assert h3.capabilities.duration_max_sec == 15
    assert h3.capabilities.default_fps == 24
    assert h3.capabilities.reference_image_max == 9
    assert h3.capabilities.reference_video_max == 3
    assert h3.capabilities.reference_audio_max == 3
    assert h3.capabilities.reference_file_max == 12
    assert default_model_id() == H3_MODEL_ID


def test_legacy_video_job_payload_is_upgraded_without_losing_ltx_result_metadata():
    payload = {
        "mode": "licon_msr_segmented",
        "segments": [
            {
                "segment_index": 1,
                "part_id": "part_001",
                "video_path": "F:/existing.mp4",
                "job": {"duration_sec": 15, "fps": 50},
            }
        ],
    }

    normalized = normalize_video_jobs_payload(payload)
    segment = normalized["segments"][0]

    assert normalized["schema_version"] == VIDEO_JOBS_SCHEMA_VERSION
    assert normalized["default_model_id"] == H3_MODEL_ID
    assert segment["video_path"] == "F:/existing.mp4"
    assert segment["model_id"] == LTX_MODEL_ID
    assert segment["resolved_model"]["id"] == LTX_MODEL_ID
    assert segment["requested_media_spec"] == {"duration_sec": 15, "fps": 50}
    assert segment["active_render_id"]
    assert segment["renders"][0]["model"]["id"] == LTX_MODEL_ID
    assert segment["renders"][0]["output"]["video_path"] == "F:/existing.mp4"


def test_unknown_future_model_snapshot_is_not_rewritten_as_ltx():
    payload = {
        "default_model_id": LTX_MODEL_ID,
        "segments": [{"model_id": "future_model", "resolved_model": {"id": "future_model", "revision": "x"}}],
    }

    segment = normalize_video_jobs_payload(payload)["segments"][0]

    assert segment["model_id"] == "future_model"
    assert segment["resolved_model"] == {"id": "future_model", "revision": "x"}


def test_ltx_adapter_emits_asset_slots_while_preserving_licon_node_wiring(tmp_path):
    reference = tmp_path / "reference.png"
    background = tmp_path / "background.png"
    reference.write_bytes(b"reference")
    background.write_bytes(b"background")
    job = VideoJobContract(
        prompt="A stable LTX prompt",
        duration_sec=2,
        fps=50,
        reference_images=[str(reference)],
        background_image=str(background),
        part_id="part_001",
    )

    patched = patch_licon_msr_workflow(_minimal_ltx_template(), job)

    assert patched["5"]["inputs"]["text"] == "A stable LTX prompt"
    assert patched["50"]["inputs"]["value"] == 100
    assert patched["28"]["inputs"]["1"] == ["80", 0]
    assert "2" not in patched["28"]["inputs"]
    assert "81" not in patched
    assert patched["28"]["inputs"]["background"] == ["84", 0]
    assert [slot["name"] for slot in job.asset_slots] == ["reference_1", "background"]


def test_runninghub_workflow_id_is_resolved_from_the_ltx_model_deployment():
    config = submit_backend_config("runninghub", model_id=LTX_MODEL_ID)

    assert config["workflow_id"] == "2064586545209888769"


def test_runninghub_workflow_id_is_resolved_from_the_h3_model_deployment():
    config = submit_backend_config("runninghub", model_id=H3_MODEL_ID)

    assert config["enabled"] is True
    assert config["workflow_id"] == "2087406299922391041"


def test_runninghub_preserves_h3_loader_types_and_loader_options(monkeypatch):
    provider = object.__new__(RunningHubSubmitProvider)
    uploaded_paths = []

    def fake_upload(path):
        uploaded_paths.append(str(path))
        return f"uploaded-{Path(path).name}"

    monkeypatch.setattr(provider, "_upload_or_reuse", fake_upload)
    workflow = {
        "161": {"class_type": "LoadImage", "inputs": {"image": "staged/picture.png"}},
        "246": {"class_type": "LoadVideo", "inputs": {"file": "staged/motion.mp4", "keep_audio": True}},
        "164": {"class_type": "LoadAudio", "inputs": {"audio": "staged/voice.mp3"}},
    }
    job = SimpleNamespace(
        asset_slots=[
            {"node_id": "161", "input_name": "image", "class_type": "LoadImage", "path": "staged/picture.png", "source_path": "source/picture.png"},
            {"node_id": "246", "input_name": "file", "class_type": "LoadVideo", "path": "staged/motion.mp4", "source_path": "source/motion.mp4"},
            {"node_id": "164", "input_name": "audio", "class_type": "LoadAudio", "path": "staged/voice.mp3", "source_path": "source/voice.mp3"},
        ]
    )

    patched = provider._patch_uploaded_media(workflow, job)

    assert uploaded_paths == ["source/picture.png", "source/motion.mp4", "source/voice.mp3"]
    assert patched["161"] == {"class_type": "LoadImage", "inputs": {"image": "uploaded-picture.png"}}
    assert patched["246"] == {
        "class_type": "LoadVideo",
        "inputs": {"file": "uploaded-motion.mp4", "keep_audio": True},
    }
    assert patched["164"] == {"class_type": "LoadAudio", "inputs": {"audio": "uploaded-voice.mp3"}}
