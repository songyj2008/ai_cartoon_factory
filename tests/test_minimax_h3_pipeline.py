import json
import tempfile
from pathlib import Path

from generation.pipelines.minimax_h3.input_stager import H3ComfyInputStager
from generation.pipelines.minimax_h3.pipeline import H3VideoJob, MiniMaxH3Ref2VAPipeline
from generation.pipelines.minimax_h3.workflow_adapter import H3Ref2VAWorkflowAdapter, H3Ref2VAWorkflowProfile
from generation.pipelines.minimax_h3.validator import validate_generation_config
from generation.service import GenerationService


ROOT = Path(__file__).resolve().parents[1]
FULL_TEMPLATE_REFERENCE_NODE_IDS = {
    "161", "205", "206", "207", "208", "209", "210", "211", "212",
    "164", "244", "245",
    "169", "238", "241", "246", "247", "248",
}


def _assert_no_dangling_connections(workflow):
    for node_id, node in workflow.items():
        for input_name, value in (node.get("inputs") or {}).items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
            ):
                assert value[0] in workflow, f"node {node_id} input {input_name} references missing node {value[0]}"


def _profile_and_template():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    profile = H3Ref2VAWorkflowProfile.from_mapping(
        config["models"]["minimax_h3_local_ref2va"]["workflow_profile"]
    )
    template = json.loads((ROOT / "templates" / "MiniMaxH3_Ref2VA_api.json").read_text(encoding="utf-8"))
    return profile, template


def test_h3_adapter_assembles_dynamic_image_video_and_audio_inputs():
    profile, template = _profile_and_template()
    generation_config = {
        "generation_mode": "ref2va",
        "asset_manifest": [
            {"id": "picture", "type": "image", "path": "picture.png", "role": "character"},
            {
                "id": "clip",
                "type": "video",
                "path": "motion.mp4",
                "role": "motion",
                "use_video_audio": True,
            },
            {"id": "voice", "type": "audio", "path": "voice.mp3", "role": "voice"},
        ],
    }
    build = H3Ref2VAWorkflowAdapter(profile=profile).build(
        template,
        {
            "plot": "A traveler turns toward a lantern in the rain.",
            "model_hints": {
                "minimax_h3_local_ref2va": {
                    "core_idea": "A rain-soaked traveler discovers a lantern.",
                    "shots": [{"time": "0–5s", "description": "Slow push-in as the traveler turns."}],
                }
            },
        },
        generation_config,
        duration_sec=5,
    )

    inputs = build.workflow["170"]["inputs"]
    assert inputs["ref_image_size"] == "match"
    assert "ref_images.ref_image_0" in inputs
    assert "ref_videos.ref_video_0" in inputs
    assert "ref_video_audios.ref_video_audio_0" in inputs
    assert "ref_audios.ref_audio_0" in inputs
    assert [slot["media_type"] for slot in build.asset_slots] == ["image", "video", "audio"]
    assert [slot["class_type"] for slot in build.asset_slots] == ["LoadImage", "LoadVideo", "LoadAudio"]
    assert {build.workflow[slot["node_id"]]["class_type"] for slot in build.asset_slots} == {
        "LoadImage",
        "LoadVideo",
        "LoadAudio",
    }
    assert build.prompt.text.index("<Picture 1>") < build.prompt.text.index("<Audio 1>")
    assert build.prompt.text.index("<Audio 1>") < build.prompt.text.index("<Video 1>")
    assert build.prompt.text.index("<Video 1>") < build.prompt.text.index("<Audio 2>")
    assert "A rain-soaked traveler discovers a lantern." in build.prompt.text
    assert "Slow push-in as the traveler turns." in build.prompt.text
    assert build.requested_media_spec.to_dict() == {"duration_sec": 5.0, "fps": 24, "has_audio": True}
    assert set(profile.reference_slots.all_node_ids) == FULL_TEMPLATE_REFERENCE_NODE_IDS
    selected_template_nodes = {"161", "246", "169", "164"}
    assert selected_template_nodes.issubset(build.workflow)
    assert (FULL_TEMPLATE_REFERENCE_NODE_IDS - selected_template_nodes).isdisjoint(build.workflow)
    assert [slot["node_id"] for slot in build.asset_slots] == ["161", "246", "164"]
    assert inputs["ref_images.ref_image_0"] == ["161", 0]
    assert inputs["ref_videos.ref_video_0"] == ["169", 0]
    assert inputs["ref_video_audios.ref_video_audio_0"] == ["169", 1]
    assert inputs["ref_audios.ref_audio_0"] == ["164", 0]
    assert build.workflow["246"]["inputs"]["keep_audio"] is True
    class_types = [node.get("class_type") for node in build.workflow.values()]
    assert class_types.count("LoadImage") == 1
    assert class_types.count("LoadVideo") == 1
    assert class_types.count("GetVideoComponents") == 1
    assert class_types.count("LoadAudio") == 1
    _assert_no_dangling_connections(build.workflow)


def test_h3_adapter_materializes_only_selected_reference_counts():
    profile, template = _profile_and_template()
    assert FULL_TEMPLATE_REFERENCE_NODE_IDS.issubset(template)
    generation_config = {
        "generation_mode": "ref2va",
        "asset_manifest": [
            {"id": "picture-1", "type": "image", "path": "picture-1.png"},
            {"id": "picture-2", "type": "image", "path": "picture-2.png"},
            {
                "id": "clip",
                "type": "video",
                "path": "motion.mp4",
                "use_video_audio": False,
            },
            {"id": "voice-1", "type": "audio", "path": "voice-1.mp3"},
            {"id": "voice-2", "type": "audio", "path": "voice-2.mp3"},
        ],
    }

    build = H3Ref2VAWorkflowAdapter(profile=profile).build(
        template,
        {"plot": "Two travelers follow a moving light."},
        generation_config,
        duration_sec=5,
    )

    inputs = build.workflow["170"]["inputs"]
    dynamic_inputs = {
        key
        for key in inputs
        if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios."))
    }
    assert dynamic_inputs == {
        "ref_images.ref_image_0",
        "ref_images.ref_image_1",
        "ref_videos.ref_video_0",
        "ref_audios.ref_audio_0",
        "ref_audios.ref_audio_1",
    }
    selected_template_nodes = {"161", "205", "246", "169", "164", "244"}
    assert selected_template_nodes.issubset(build.workflow)
    assert (FULL_TEMPLATE_REFERENCE_NODE_IDS - selected_template_nodes).isdisjoint(build.workflow)
    assert [slot["node_id"] for slot in build.asset_slots] == ["161", "205", "246", "164", "244"]
    assert build.workflow["246"]["inputs"]["keep_audio"] is False
    class_types = [node.get("class_type") for node in build.workflow.values()]
    assert class_types.count("LoadImage") == 2
    assert class_types.count("LoadVideo") == 1
    assert class_types.count("GetVideoComponents") == 1
    assert class_types.count("LoadAudio") == 2
    _assert_no_dangling_connections(build.workflow)


def test_h3_adapter_applies_profile_declared_reference_controls():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    profile_data = dict(config["models"]["minimax_h3_local_ref2va"]["workflow_profile"])
    profile_data["reference_controls"] = [
        {
            "node_id": "249",
            "input_name": "enabled",
            "media_kind": "image",
            "enabled_value": "images-on",
            "disabled_value": "images-off",
        },
        {
            "node_id": "251",
            "input_name": "enabled",
            "media_kind": "video",
            "enabled_value": "videos-on",
            "disabled_value": "videos-off",
        },
        {
            "node_id": "254",
            "input_name": "enabled",
            "media_kind": "audio",
            "enabled_value": "audio-on",
            "disabled_value": "audio-off",
        },
    ]
    profile = H3Ref2VAWorkflowProfile.from_mapping(profile_data)
    _, template = _profile_and_template()
    template.update(
        {
            "249": {"class_type": "TestGate", "inputs": {"enabled": "old"}},
            "251": {"class_type": "TestGate", "inputs": {"enabled": "old"}},
            "254": {"class_type": "TestGate", "inputs": {"enabled": "old"}},
        }
    )

    build = H3Ref2VAWorkflowAdapter(profile=profile).build(
        template,
        {"plot": "A lantern glows."},
        {"generation_mode": "ref2va", "asset_manifest": [{"id": "image", "type": "image", "path": "image.png"}]},
        duration_sec=5,
    )

    assert build.workflow["249"]["inputs"]["enabled"] == "images-on"
    assert build.workflow["251"]["inputs"]["enabled"] == "videos-off"
    assert build.workflow["254"]["inputs"]["enabled"] == "audio-off"


def test_h3_validator_enforces_model_limits_without_rewriting_assets():
    report = validate_generation_config(
        {
            "generation_mode": "ref2va",
            "asset_manifest": [{"id": "audio", "type": "audio", "path": "voice.mp3"}],
        },
        duration_sec=16,
    )

    assert not report.is_valid
    assert {issue.code for issue in report.errors} >= {"duration_out_of_range", "audio_requires_visual_reference"}


def test_h3_manual_prompt_can_replace_a_stale_longer_beat_timeline(tmp_path, monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    manual_prompt = "[reference generation] 5 秒。\n[Shot 1]（0–3 秒）：人物奔跑。\n[Shot 2]（3–5 秒）：人物停下。"
    beat = {
        "id": 1,
        "title": "manual timeline",
        "estimated_duration_sec": 5,
        "model_hints": {
            pipeline.spec.id: {
                "shot_mode": "multi_shot",
                "shots": [
                    {"start_sec": 0, "end_sec": 3, "action": "人物奔跑"},
                    {"start_sec": 3, "end_sec": 6, "action": "人物停下"},
                    {"start_sec": 6, "end_sec": 8, "action": "人物回头"},
                ],
            }
        },
    }
    existing = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": pipeline.spec.id,
        "generation_config": {
            "generation_mode": "ref2va",
            "duration_sec": 5,
            "asset_manifest": [],
            "prompt_override": manual_prompt,
        },
    }
    monkeypatch.setattr(pipeline, "_save_workflow", lambda workflow, part_id: tmp_path / "workflow.json")
    monkeypatch.setattr(pipeline, "_validate_source_assets", lambda *args, **kwargs: None)

    prepared = pipeline._prepare_segment(pipeline._load_template(), beat, 1, existing, check_files=False)

    assert prepared["compiled_prompt_snapshot"] == manual_prompt
    assert prepared["job"]["final_prompt"] == manual_prompt
    assert prepared["job"]["duration_sec"] == 5


def test_h3_prompt_regeneration_does_not_append_auto_assets_to_persisted_picker_selection(tmp_path, monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    selected = [
        {"id": "panda", "type": "image", "path": "panda.png", "role": "金色熊猫"},
        {"id": "sugar", "type": "image", "path": "sugar.png", "role": "3号糖宝宝"},
        {"id": "grain", "type": "image", "path": "grain.png", "role": "8号禾宝宝"},
    ]
    automatic = selected + [
        {
            "id": "earth",
            "type": "image",
            "path": "earth.png",
            "role": "太空中的地球远景",
            "metadata": {"asset_kind": "background", "binding_source": "auto_background_match"},
        }
    ]
    existing = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": pipeline.spec.id,
        "generation_config": {"generation_mode": "ref2va", "asset_manifest": selected},
    }
    beat = {"id": 1, "title": "current selection wins", "plot": "三人看到地球", "estimated_duration_sec": 5}

    monkeypatch.setattr(
        "generation.pipelines.minimax_h3.auto_binder.auto_bind_asset_manifest",
        lambda _beat: automatic,
    )
    monkeypatch.setattr(pipeline, "_save_workflow", lambda workflow, part_id: tmp_path / "workflow.json")
    monkeypatch.setattr(pipeline, "_validate_source_assets", lambda *args, **kwargs: None)

    prepared = pipeline._prepare_segment(pipeline._load_template(), beat, 1, existing, check_files=False)

    manifest = prepared["generation_config"]["asset_manifest"]
    assert [item["asset_id"] for item in manifest] == ["panda", "sugar", "grain"]
    assert "太空中的地球远景" not in prepared["job"]["final_prompt"]
    assert "<Subject 4>" not in prepared["job"]["final_prompt"]


def test_h3_beat_asset_signature_fingerprints_matched_identities_and_backgrounds():
    from generation.pipelines.minimax_h3.pipeline import _beat_asset_signature

    beat = {
        "auto_asset_match": {
            "character_images": [
                {"asset_identity_id": "wutian"},
                {"asset_identity_id": "wufa"},
                {"asset_identity_id": "wutian"},  # duplicate is deduplicated
            ],
            "backgrounds": [{"asset_background_id": "classroom"}],
        }
    }

    assert _beat_asset_signature(beat) == json.dumps(
        {"characters": ["wufa", "wutian"], "backgrounds": ["classroom"]},
        ensure_ascii=False,
        sort_keys=True,
    )


def test_h3_beat_asset_signature_falls_back_to_characters_list():
    from generation.pipelines.minimax_h3.pipeline import _beat_asset_signature

    beat = {"auto_asset_match": {"characters": [{"identity_id": "foo"}]}}

    assert json.loads(_beat_asset_signature(beat)) == {"characters": ["foo"], "backgrounds": []}


def test_h3_does_not_replace_persisted_manifest_when_beat_signature_changes(tmp_path, monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    persisted_auto = [
        {
            "id": "old-char",
            "type": "image",
            "path": "old.png",
            "role": "旧角色",
            "metadata": {"asset_kind": "character", "identity_id": "old_char", "binding_source": "auto_character_match"},
        }
    ]
    fresh_auto = [
        {
            "id": "new-char",
            "type": "image",
            "path": "new.png",
            "role": "新角色",
            "metadata": {"asset_kind": "character", "identity_id": "new_char", "binding_source": "auto_character_match"},
        }
    ]
    existing = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": pipeline.spec.id,
        "generation_config": {
            "generation_mode": "ref2va",
            "asset_manifest": persisted_auto,
            "asset_match_signature": "sig-old",
        },
    }
    beat = {"id": 1, "title": "beat changed", "plot": "新场景", "estimated_duration_sec": 5}

    monkeypatch.setattr("generation.pipelines.minimax_h3.pipeline._beat_asset_signature", lambda _beat: "sig-new")
    monkeypatch.setattr("generation.pipelines.minimax_h3.auto_binder.auto_bind_asset_manifest", lambda _beat: fresh_auto)
    monkeypatch.setattr(pipeline, "_save_workflow", lambda workflow, part_id: tmp_path / "workflow.json")
    monkeypatch.setattr(pipeline, "_validate_source_assets", lambda *args, **kwargs: None)

    prepared = pipeline._prepare_segment(pipeline._load_template(), beat, 1, existing, check_files=False)

    # A beat text edit changes the signature but is not a structural split;
    # the persisted manifest is authoritative and must not silently drop the
    # bound character merely because the matcher now returns a different one.
    identities = [item.get("metadata", {}).get("identity_id") for item in prepared["generation_config"]["asset_manifest"]]
    assert identities == ["old_char"]


def test_h3_does_not_re_add_removed_character_when_beat_signature_unchanged(tmp_path, monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    curated = [
        {
            "id": "kept",
            "type": "image",
            "path": "kept.png",
            "role": "保留角色",
            "metadata": {"asset_kind": "character", "identity_id": "kept", "binding_source": "auto_character_match"},
        }
    ]
    full_match = curated + [
        {
            "id": "removed",
            "type": "image",
            "path": "removed.png",
            "role": "被删角色",
            "metadata": {"asset_kind": "character", "identity_id": "removed", "binding_source": "auto_character_match"},
        }
    ]
    existing = {
        "segment_index": 1,
        "part_id": "part_001",
        "model_id": pipeline.spec.id,
        "generation_config": {
            "generation_mode": "ref2va",
            "asset_manifest": curated,
            "asset_match_signature": "sig-same",
        },
    }
    beat = {"id": 1, "title": "unchanged", "plot": "同场景", "estimated_duration_sec": 5}

    monkeypatch.setattr("generation.pipelines.minimax_h3.pipeline._beat_asset_signature", lambda _beat: "sig-same")
    monkeypatch.setattr("generation.pipelines.minimax_h3.auto_binder.auto_bind_asset_manifest", lambda _beat: full_match)
    monkeypatch.setattr(pipeline, "_save_workflow", lambda workflow, part_id: tmp_path / "workflow.json")
    monkeypatch.setattr(pipeline, "_validate_source_assets", lambda *args, **kwargs: None)

    prepared = pipeline._prepare_segment(pipeline._load_template(), beat, 1, existing, check_files=False)

    identities = [item.get("metadata", {}).get("identity_id") for item in prepared["generation_config"]["asset_manifest"]]
    assert identities == ["kept"]


def test_model_neutral_manifest_retains_h3_video_audio_semantics():
    manifest = GenerationService._normalise_asset_manifest(
        [
            {
                "id": "clip",
                "type": "video",
                "path": "clip.mp4",
                "use_video_audio": True,
                "metadata": {"duration_sec": 5, "has_audio": True},
            }
        ]
    )

    assert manifest == [
        {
            "id": "clip",
            "type": "video",
            "path": "clip.mp4",
            "role": "",
            "label": "",
            "order": 1,
            "source": "segment",
            "use_video_audio": True,
            "metadata": {"duration_sec": 5, "has_audio": True},
        }
    ]


def test_h3_input_stager_uses_a_namespaced_comfy_relative_path():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        input_dir = root / "ComfyUI" / "input"
        source_dir = root / "project" / "media"
        input_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        source = source_dir / "reference.png"
        source.write_bytes(b"reference")

        stager = H3ComfyInputStager(input_dir=input_dir, base_dir=root / "project", scope="episode_part_001")
        relative = stager.stage(str(source))

        assert not Path(relative).is_absolute()
        assert relative.startswith("ai_cartoon_factory/minimax_h3/episode_part_001/")
        assert (input_dir / relative).read_bytes() == b"reference"
        assert stager.stage(str(source)) == relative


def test_h3_runninghub_assets_do_not_require_a_local_comfy_input_directory(tmp_path, monkeypatch):
    source = tmp_path / "reference.png"
    source.write_bytes(b"reference")
    pipeline = MiniMaxH3Ref2VAPipeline()
    pipeline._config = {
        "video_submit_backend": "runninghub",
        "submit_backends": {"comfyui": {"input_dir": str(tmp_path / "missing-comfy-input")}},
    }
    monkeypatch.setattr(
        pipeline,
        "_input_stager",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RunningHub must not stage into ComfyUI input")),
    )
    workflow = {"161": {"class_type": "LoadImage", "inputs": {"image": "old/staged/reference.png"}}}
    job = H3VideoJob(
        prompt="A valid prompt",
        duration_sec=5,
        aligned_frame_count=124,
        output_prefix="MiniMax_H3/segments/part_001",
        part_id="part_001",
        asset_manifest=[{"id": "picture", "type": "image", "path": str(source)}],
        asset_slots=[
            {
                "asset_id": "picture",
                "node_id": "161",
                "input_name": "image",
                "class_type": "LoadImage",
                "path": "old/staged/reference.png",
            }
        ],
    )

    changed = pipeline._prepare_remote_workflow_assets(workflow, job)

    assert pipeline._submit_backend_name() == "runninghub"
    assert changed is True
    assert job.asset_slots[0]["source_path"] == str(source.resolve())
    assert workflow["161"]["inputs"]["image"] == "old/staged/reference.png"


def test_h3_submit_provider_is_bound_to_pipeline_instance(monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    sentinel = object()
    requested_model_ids = []

    monkeypatch.setattr(
        "workflow.submit_factory.get_submit_provider",
        lambda *, model_id=None: requested_model_ids.append(model_id) or sentinel,
    )

    assert pipeline._submit_provider() is sentinel
    assert requested_model_ids == [pipeline.spec.id]


def test_h3_runninghub_queue_full_is_kept_in_local_queue(monkeypatch):
    pipeline = MiniMaxH3Ref2VAPipeline()
    payload = {
        "default_model_id": pipeline.spec.id,
        "segments": [
            {
                "segment_index": 1,
                "part_id": "part_001",
                "model_id": pipeline.spec.id,
                "status": "workflow_ready",
                "job": {"model_id": pipeline.spec.id},
            }
        ],
    }
    saved = []
    provider = type("Provider", (), {"name": "runninghub"})()

    monkeypatch.setattr(pipeline, "_submit_provider", lambda: provider)
    monkeypatch.setattr(
        pipeline,
        "_submit_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("RunningHub queue is full: {'code': 421, 'msg': 'TASK_QUEUE_MAXED'}")
        ),
    )
    monkeypatch.setattr("workflow.video_merge.load_video_jobs", lambda: payload)
    monkeypatch.setattr("workflow.video_merge.save_video_jobs", lambda value: saved.append(value))
    monkeypatch.setattr("workflow.runninghub_usage.refresh_runninghub_usage_summaries", lambda value: value)

    result = pipeline.submit_saved({1}, wait=False)

    segment = result["segments"][0]
    assert segment["status"] == "queued"
    assert segment["backend"] == "runninghub"
    assert segment["submitted"] is False
    assert segment["error"] == ""
    assert saved
