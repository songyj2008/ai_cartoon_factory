from generation.pipelines.minimax_h3 import auto_binder
from workflow.asset_matcher import match_beat_assets


H3 = "minimax_h3_local_ref2va"


def _bible():
    return {
        "core_characters": [],
        "supporting_characters": [],
        "allowed_backgrounds": [
            {"id": "scene_base", "asset_background_id": "space_base", "display_name": "航天基地"},
            {"id": "scene_pad", "asset_background_id": "launch_pad", "display_name": "飞船发射平台"},
        ],
    }


def _index(path_a="base.png", path_b="pad.png"):
    return {
        "characters": {"identities": []},
        "backgrounds": [
            {"id": "space_base", "path": path_a, "description_cn": "航天基地外景。"},
            {"id": "launch_pad", "path": path_b, "description_cn": "飞船发射平台。"},
        ],
    }


def test_h3_matches_and_deduplicates_multiple_shot_backgrounds():
    beat = {
        "plot": "人物走向飞船。",
        "model_hints": {
            H3: {
                "shots": [
                    {"scene_id": "scene_base", "action": "人物在航天基地交谈"},
                    {"scene_id": "scene_pad", "action": "人物跑向飞船发射平台"},
                    {"scene_id": "scene_base", "action": "回看航天基地"},
                ]
            }
        },
    }

    matched = match_beat_assets(beat, bible=_bible(), asset_index=_index(), model_id=H3, reference_role_limit=9)

    backgrounds = matched["auto_asset_match"]["backgrounds"]
    assert [item["asset_background_id"] for item in backgrounds] == ["space_base", "launch_pad"]
    assert backgrounds[0]["shot_indices"] == [1, 3]
    assert backgrounds[1]["shot_indices"] == [2]


def test_h3_does_not_supply_a_background_when_no_shot_matches():
    beat = {
        "plot": "纯色抽象空间中的角色特写。",
        "model_hints": {H3: {"shots": [{"action": "角色眨眼"}]}},
    }

    matched = match_beat_assets(beat, bible=_bible(), asset_index=_index(), model_id=H3, reference_role_limit=9)

    assert matched["scene_id"] == ""
    assert matched["auto_asset_match"]["backgrounds"] == []
    assert matched["auto_asset_match"]["background"]["asset_background_id"] == ""


def test_h3_does_not_trust_an_opaque_scene_id_without_semantic_evidence():
    bible = {
        "core_characters": [],
        "supporting_characters": [],
        "allowed_backgrounds": [
            {"id": "scene_005", "asset_background_id": "football_field", "display_name": "露天足球场"},
        ],
    }
    asset_index = {
        "characters": {"identities": []},
        "backgrounds": [
            {"id": "football_field", "path": "football.png", "description_cn": "白天的户外足球场，绿色草坪和球门"},
        ],
    }
    beat = {
        "plot": "航天基地前，三个小家伙向马斯克询问火星之旅。",
        "model_hints": {
            H3: {
                "shots": [
                    {"scene_id": "scene_005", "action": "众人站在巨大飞船前交谈"},
                ]
            }
        },
    }

    matched = match_beat_assets(beat, bible=bible, asset_index=asset_index, model_id=H3, reference_role_limit=9)

    assert matched["scene_id"] == ""
    assert matched["auto_asset_match"]["backgrounds"] == []


def test_h3_wide_shot_does_not_match_the_spanish_alias_word_de():
    bible = {
        "core_characters": [],
        "supporting_characters": [],
        "allowed_backgrounds": [
            {
                "id": "scene_008",
                "asset_background_id": "madrid_royal_palace",
                "display_name": "马德里王宫",
                "aliases": ["Palacio Real de Madrid"],
            }
        ],
    }
    asset_index = {
        "characters": {"identities": []},
        "backgrounds": [
            {
                "id": "madrid_royal_palace",
                "path": "madrid.png",
                "aliases": ["Palacio Real de Madrid"],
                "description_cn": "白天的西班牙马德里王宫外景",
            }
        ],
    }
    beat = {
        "plot": "三个小家伙转身跑向飞船。",
        "model_hints": {
            H3: {
                "shots": [
                    {
                        "shot_size": "wide",
                        "subject": "三个小家伙",
                        "action": "三人跑向飞船",
                    }
                ]
            }
        },
    }

    matched = match_beat_assets(
        beat,
        bible=bible,
        asset_index=asset_index,
        model_id=H3,
        reference_role_limit=9,
    )

    assert matched["scene_id"] == ""
    assert matched["auto_asset_match"]["backgrounds"] == []


def test_h3_auto_binder_keeps_multiple_real_backgrounds_without_a_default(tmp_path, monkeypatch):
    base = tmp_path / "base.png"
    pad = tmp_path / "pad.png"
    base.write_bytes(b"png")
    pad.write_bytes(b"png")
    monkeypatch.setattr(auto_binder, "load_project_bible", _bible)
    monkeypatch.setattr(auto_binder, "load_asset_index", lambda: _index(str(base), str(pad)))
    beat = {
        "auto_asset_match": {
            "backgrounds": [
                {
                    "scene_id": "scene_base",
                    "asset_background_id": "space_base",
                    "display_name": "航天基地",
                    "score": 100,
                    "shot_indices": [1, 3],
                },
                {
                    "scene_id": "scene_pad",
                    "asset_background_id": "launch_pad",
                    "display_name": "飞船发射平台",
                    "score": 100,
                    "shot_indices": [2],
                },
            ]
        }
    }

    manifest = auto_binder.auto_bind_asset_manifest(beat)

    assert [item["metadata"]["background_id"] for item in manifest] == ["space_base", "launch_pad"]
    assert manifest[0]["metadata"]["shot_indices"] == [1, 3]

    assert auto_binder.auto_bind_asset_manifest(
        {"auto_asset_match": {"background": {"asset_background_id": "space_base", "score": 0}}}
    ) is None


def test_manual_scene_stays_first_and_auto_backgrounds_are_only_appended_once():
    manual = [
        {
            "id": "manual-scene",
            "type": "image",
            "path": "base.png",
            "source": "project",
            "metadata": {"asset_kind": "background", "background_id": "space_base"},
        }
    ]
    automatic = [
        {
            "id": "auto-base",
            "type": "image",
            "path": "base.png",
            "source": "auto_background_match",
            "metadata": {"asset_kind": "background", "background_id": "space_base"},
        },
        {
            "id": "auto-pad",
            "type": "image",
            "path": "pad.png",
            "source": "auto_background_match",
            "metadata": {"asset_kind": "background", "background_id": "launch_pad"},
        },
    ]

    merged = auto_binder.merge_auto_backgrounds(manual, automatic)

    assert [item["id"] for item in merged] == ["manual-scene"]


def test_h3_auto_binding_does_not_duplicate_a_configured_character_identity():
    configured = [
        {
            "id": "manual-front",
            "type": "image",
            "path": "front.png",
            "source": "project",
            "metadata": {"asset_kind": "character", "identity_id": "girl_player_03"},
        }
    ]
    automatic = [
        {
            "id": "auto-pair-layout",
            "type": "image",
            "path": "pair.png",
            "source": "auto_character_match",
            "metadata": {
                "asset_kind": "character",
                "identity_id": "girl_player_03",
                "binding_source": "auto_character_match",
            },
        }
    ]

    assert auto_binder.merge_auto_backgrounds(configured, automatic) == configured


def test_h3_refresh_recognizes_auto_binding_after_editor_persists_segment_source():
    persisted_auto = [
        {
            "id": "asset_2",
            "type": "image",
            "path": "old.png",
            "source": "segment",
            "metadata": {
                "asset_kind": "character",
                "identity_id": "girl_player_03",
                "binding_source": "auto_character_match",
            },
        }
    ]
    refreshed_auto = [
        {
            "id": "char_girl_player_03_front",
            "type": "image",
            "path": "front.png",
            "source": "auto_character_match",
            "metadata": {
                "asset_kind": "character",
                "identity_id": "girl_player_03",
                "binding_source": "auto_character_match",
            },
        }
    ]

    assert auto_binder.merge_auto_backgrounds(persisted_auto, refreshed_auto) == [
        {**refreshed_auto[0], "order": 1}
    ]


def test_h3_refreshes_a_deleted_registered_character_variant(tmp_path, monkeypatch):
    current = tmp_path / "girl_player_03_single_front_v1.png"
    current.write_bytes(b"png")
    deleted = tmp_path / "girl_player_03.png"
    identity = {
        "id": "girl_player_03",
        "description_cn": "identity description",
        "images": [
            {
                "id": "girl_player_03_single_front_v1",
                "path": str(current),
                "description_cn": "single front reference",
            }
        ],
    }
    monkeypatch.setattr(
        auto_binder,
        "load_asset_index",
        lambda: {"characters": {"identities": [identity]}, "backgrounds": []},
    )
    manifest = [
        {
            "id": "asset_2",
            "type": "image",
            "path": str(deleted),
            "source": "segment",
            "metadata": {
                "asset_kind": "character",
                "identity_id": "girl_player_03",
                "image_id": "girl_player_03",
                "binding_source": "auto_character_match",
            },
        }
    ]

    refreshed = auto_binder.enrich_asset_manifest_metadata(manifest)

    assert refreshed[0]["path"] == str(current.resolve())
    assert refreshed[0]["metadata"]["image_id"] == "girl_player_03_single_front_v1"
    assert refreshed[0]["metadata"]["image_description"] == "single front reference"


def test_h3_does_not_replace_an_unknown_missing_manual_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auto_binder,
        "load_asset_index",
        lambda: {"characters": {"identities": []}, "backgrounds": []},
    )
    missing = tmp_path / "manual.png"
    manifest = [{"id": "manual", "type": "image", "path": str(missing), "source": "manual"}]

    assert auto_binder.enrich_asset_manifest_metadata(manifest) == manifest


def test_legacy_automatic_background_is_removed_when_it_no_longer_matches():
    legacy_auto = [
        {
            "id": "bg_football_field",
            "type": "image",
            "path": "football.png",
            "metadata": {
                "asset_kind": "background",
                "background_id": "football_field",
                "shot_indices": [1, 2, 3],
            },
        }
    ]

    assert auto_binder.merge_auto_backgrounds(legacy_auto, []) == []


def test_h3_auto_binder_stores_bible_role_id_on_character_metadata(tmp_path, monkeypatch):
    image = tmp_path / "wufa.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(auto_binder, "load_project_bible", lambda: {})
    monkeypatch.setattr(
        auto_binder,
        "load_asset_index",
        lambda: {
            "characters": {
                "identities": [
                    {
                        "id": "wufa",
                        "display_name": "吴法",
                        "images": [{"id": "primary", "path": str(image), "is_primary": True}],
                    }
                ]
            },
            "backgrounds": [],
        },
    )
    beat = {
        "auto_asset_match": {
            "character_images": [
                {
                    "role_id": "character_004",
                    "asset_identity_id": "wufa",
                    "display_name": "吴法",
                    "image_id": "primary",
                }
            ]
        }
    }

    manifest = auto_binder.auto_bind_asset_manifest(beat)

    assert manifest[0]["metadata"]["role_id"] == "character_004"
    assert manifest[0]["metadata"]["identity_id"] == "wufa"
