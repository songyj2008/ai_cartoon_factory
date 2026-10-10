from pathlib import Path

import pytest

from pipeline.events import ensure_event_fields_on_beats
from workflow import asset_matcher
from workflow import asset_resolver
from workflow import segment_runner


def _variant_fixture(tmp_path: Path):
    primary_path = tmp_path / "runner_primary.png"
    action_path = tmp_path / "runner_dash_right.png"
    primary_path.write_bytes(b"primary")
    action_path.write_bytes(b"action")
    identity = {
        "id": "silver_runner",
        "display_name": "Silver Runner",
        "aliases": ["silver runner"],
        "primary_reference": {
            "id": "runner_primary",
            "path": str(primary_path),
        },
        "images": [
            {
                "id": "runner_primary",
                "path": str(primary_path),
                "tags_cn": ["standing", "identity sheet"],
                "use_for": ["identity reference"],
            },
            {
                "id": "runner_dash_right",
                "path": str(action_path),
                "tags_cn": ["dash right", "side view"],
                "use_for": ["running fast"],
            },
        ],
    }
    bible = {
        "core_characters": [
            {
                "id": "character_001",
                "display_name": "Silver Runner",
                "asset_identity_id": "silver_runner",
            }
        ]
    }
    return identity, bible


def test_identity_image_options_keep_primary_first(tmp_path):
    identity, _bible = _variant_fixture(tmp_path)

    options = asset_resolver.identity_image_options(identity)

    assert [item["id"] for item in options] == ["runner_primary", "runner_dash_right"]
    assert options[0]["is_primary"] is True
    assert options[1]["is_primary"] is False


def test_auto_match_selects_action_variant_inside_matched_role(tmp_path):
    identity, bible = _variant_fixture(tmp_path)

    image_ids, details = asset_matcher._match_reference_images(
        ["character_001"],
        "silver runner uses a side view and dash right while running fast",
        bible,
        {"silver_runner": identity},
    )

    assert image_ids == {"character_001": "runner_dash_right"}
    assert details[0]["is_primary"] is False


def test_manual_variant_selection_wins_over_text(tmp_path):
    identity, bible = _variant_fixture(tmp_path)

    image_ids, details = asset_matcher._match_reference_images(
        ["character_001"],
        "silver runner dash right",
        bible,
        {"silver_runner": identity},
        {"character_001": "runner_primary"},
    )

    assert image_ids == {"character_001": "runner_primary"}
    assert details[0]["reason"] == ["manual:image_variant"]


def test_event_normalization_keeps_only_selected_role_image_ids():
    data = ensure_event_fields_on_beats(
        {
            "beats": [
                {
                    "id": 1,
                    "important_roles": ["character_001", "character_002"],
                    "visible_roles": ["character_001"],
                    "reference_roles": ["character_001"],
                    "reference_image_ids": {
                        "character_001": "runner_dash_right",
                        "character_002": "unrelated_image",
                    },
                }
            ]
        }
    )

    assert data["beats"][0]["reference_image_ids"] == {
        "character_001": "runner_dash_right"
    }


def test_material_validation_rejects_image_from_another_role(monkeypatch):
    monkeypatch.setattr(
        segment_runner,
        "selected_project_materials",
        lambda: {
            "characters": [
                {
                    "id": "character_001",
                    "available": True,
                    "images": [
                        {
                            "id": "runner_primary",
                            "available": True,
                            "is_primary": True,
                        }
                    ],
                }
            ],
            "backgrounds": [{"id": "scene_001", "available": True}],
        },
    )

    with pytest.raises(ValueError, match="不属于当前项目已选素材"):
        segment_runner._validated_material_binding(
            ["character_001"],
            {"character_001": "another_character_image"},
            "scene_001",
        )


def test_material_validation_falls_back_to_primary(monkeypatch):
    monkeypatch.setattr(
        segment_runner,
        "selected_project_materials",
        lambda: {
            "characters": [
                {
                    "id": "character_001",
                    "available": True,
                    "images": [
                        {
                            "id": "runner_primary",
                            "available": True,
                            "is_primary": True,
                        }
                    ],
                }
            ],
            "backgrounds": [{"id": "scene_001", "available": True}],
        },
    )

    roles, image_ids, scene_id = segment_runner._validated_material_binding(
        ["character_001"],
        {},
        "scene_001",
    )

    assert roles == ["character_001"]
    assert image_ids == {"character_001": "runner_primary"}
    assert scene_id == "scene_001"
