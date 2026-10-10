from workflow.video_merge import _needs_media_normalization, _target_media_spec, existing_segment_video


def test_existing_segment_video_reuses_stale_render_after_prompt_only_change(tmp_path):
    previous_render = tmp_path / "part_002_previous.mp4"
    previous_render.write_bytes(b"video")

    segment = {
        "status": "needs_regenerate",
        "stale_reason": "prompt changed",
        "stale_video_path": str(previous_render),
    }

    assert existing_segment_video(segment) == str(previous_render.resolve())


def test_existing_segment_video_does_not_reuse_stale_render_after_content_change(tmp_path):
    previous_render = tmp_path / "part_002_previous.mp4"
    previous_render.write_bytes(b"video")

    segment = {
        "status": "needs_regenerate",
        "stale_reason": "beat content changed",
        "stale_video_path": str(previous_render),
    }

    assert existing_segment_video(segment) == ""


def test_existing_segment_video_reuses_prompt_change_render_while_replacement_is_running(tmp_path):
    previous_render = tmp_path / "part_002_previous.mp4"
    previous_render.write_bytes(b"video")

    segment = {
        "status": "running",
        "job": {
            "stale_reason": "prompt changed",
            "stale_video_path": str(previous_render),
        },
    }

    assert existing_segment_video(segment) == str(previous_render.resolve())


def test_mixed_model_outputs_require_normalization_before_concat():
    source_specs = [
        {"width": 1280, "height": 720, "fps": 50, "has_audio": True, "audio_sample_rate": 48000},
        {"width": 768, "height": 1366, "fps": 24, "has_audio": True, "audio_sample_rate": 32000},
    ]

    assert _needs_media_normalization(
        source_specs,
        ["ltx23_licon_msr_v2", "minimax_h3_local_ref2va"],
    )
    assert _target_media_spec(source_specs) == {
        "width": 1280,
        "height": 720,
        "fps": 50,
        "has_audio": True,
        "audio_sample_rate": 48000,
    }


def test_matching_single_model_outputs_keep_the_lossless_concat_path():
    source_specs = [
        {"width": 1280, "height": 720, "fps": 50, "has_audio": True, "audio_sample_rate": 48000},
        {"width": 1280, "height": 720, "fps": 50, "has_audio": True, "audio_sample_rate": 48000},
    ]

    assert not _needs_media_normalization(source_specs, ["ltx23_licon_msr_v2", "ltx23_licon_msr_v2"])
