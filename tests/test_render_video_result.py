from generation.render_history import begin_render, update_render


def test_completed_provider_video_replaces_inherited_old_path():
    segment = {"segment_index": 3, "segment_uid": "segment", "video_path": "old.mp4"}
    render = begin_render(segment, render_id="web", backend="doubao_web")
    render["output"] = {"video_path": "old.mp4"}
    update_render(segment, "web", {"video_path": "new.mp4"}, status="success", project=True)
    assert render["output"]["video_path"] == "new.mp4"
    assert segment["video_path"] == "new.mp4"
    update_render(segment, "web", {"video_path": ""}, status="success")
    assert render["output"]["video_path"] == "new.mp4"
