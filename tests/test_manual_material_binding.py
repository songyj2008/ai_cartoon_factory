import json

from workflow import segment_runner


def test_manual_material_binding_preserves_story_structure(monkeypatch):
    data = {
        "beats": [
            {
                "important_roles": ["character_001", "character_002"],
                "visible_roles": ["character_001"],
                "offscreen_speakers": ["character_002"],
                "mentioned_roles": ["character_003"],
                "reference_roles": ["character_001"],
                "reference_image_ids": {"character_001": "old"},
                "scene_id": "scene_old",
            }
        ]
    }
    saved = {}

    monkeypatch.setattr(segment_runner, "load_json_file_silent", lambda _name: json.dumps(data))
    monkeypatch.setattr(segment_runner, "save_json", lambda name, value: saved.update({"name": name, "value": value}))

    segment_runner._save_manual_binding_to_beat(
        1,
        ["character_001"],
        {"character_001": "new"},
        "scene_new",
    )

    restored = saved["value"]["beats"][0]
    assert saved["name"] == "beats.json"
    assert restored["reference_roles"] == ["character_001"]
    assert restored["reference_image_ids"] == {"character_001": "new"}
    assert restored["scene_id"] == "scene_new"
    assert restored["important_roles"] == ["character_001", "character_002"]
    assert restored["visible_roles"] == ["character_001"]
    assert restored["offscreen_speakers"] == ["character_002"]
    assert restored["mentioned_roles"] == ["character_003"]
