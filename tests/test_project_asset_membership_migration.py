import json

from services import project_assets, project_bible
from ui import project_bible_panel


def test_project_bible_materials_are_imported_once_into_project_assignments(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "legacy-project"
    project_dir.mkdir(parents=True)
    monkeypatch.setattr(project_assets, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(project_bible, "BASE_DIR", tmp_path)

    (project_dir / "project_bible.json").write_text(
        json.dumps(
            {
                "core_characters": [{"asset_identity_id": "legacy-character"}],
                "supporting_characters": [{"asset_identity_id": "supporting-character"}],
                "allowed_backgrounds": [{"asset_background_id": "legacy-background"}],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "asset_assignments.json").write_text(
        json.dumps({"characters": ["manually-assigned-character"], "backgrounds": []}),
        encoding="utf-8",
    )

    owners = project_assets.asset_project_owners()
    assert owners["characters"] == {
        "manually-assigned-character": ["legacy-project"],
        "legacy-character": ["legacy-project"],
        "supporting-character": ["legacy-project"],
    }
    assert owners["backgrounds"] == {"legacy-background": ["legacy-project"]}

    # Once imported, assignment changes are canonical and old Bible bindings
    # must not silently restore a membership that a user removed.
    project_assets.save_project_asset_assignments(
        {"characters": ["manually-assigned-character"], "backgrounds": []},
        "legacy-project",
    )
    owners = project_assets.asset_project_owners()
    assert owners["characters"] == {"manually-assigned-character": ["legacy-project"]}
    assert owners["backgrounds"] == {}


def test_bulk_assignment_uses_checked_projects_as_the_exact_target(monkeypatch):
    records = [
        {"key": "character::hero", "name": "Hero"},
        {"key": "background::studio", "name": "Studio"},
    ]
    monkeypatch.setattr(
        project_bible_panel,
        "load_asset_index",
        lambda: {
            "characters": {"identities": [{"id": "hero"}]},
            "backgrounds": [{"id": "studio"}],
        },
    )
    monkeypatch.setattr(
        project_bible_panel,
        "asset_project_owners",
        lambda: {
            "characters": {"hero": ["old-project"]},
            "backgrounds": {"studio": []},
        },
    )
    saved = []
    synchronized = []
    monkeypatch.setattr(project_bible_panel, "set_asset_projects", lambda *args: saved.append(args))
    monkeypatch.setattr(
        project_bible_panel,
        "_sync_project_bible_to_assignments",
        lambda *args: synchronized.append(args),
    )
    monkeypatch.setattr(project_bible_panel, "project_asset_management_state", lambda _project: ("refreshed",))
    monkeypatch.setattr(project_bible_panel, "log", lambda *_args, **_kwargs: None)

    result = project_bible_panel.save_project_asset_memberships(
        [record["key"] for record in records],
        ["new-project", "shared-project"],
        "new-project",
        records,
        [],
    )

    assert result == ("已保存 2 项素材的项目归属。", "refreshed")
    assert saved == [
        ("characters", "hero", ["new-project", "shared-project"]),
        ("backgrounds", "studio", ["new-project", "shared-project"]),
    ]
    assert {args[0] for args in synchronized} == {"old-project", "new-project", "shared-project"}


def test_unbind_action_clears_owners_for_selected_assets(monkeypatch):
    records = [{"key": "character::hero", "name": "Hero"}]
    monkeypatch.setattr(
        project_bible_panel,
        "load_asset_index",
        lambda: {"characters": {"identities": [{"id": "hero"}]}, "backgrounds": []},
    )
    monkeypatch.setattr(
        project_bible_panel,
        "asset_project_owners",
        lambda: {"characters": {"hero": ["project-a"]}, "backgrounds": {}},
    )
    saved = []
    monkeypatch.setattr(project_bible_panel, "set_asset_projects", lambda *args: saved.append(args))
    monkeypatch.setattr(project_bible_panel, "_sync_project_bible_to_assignments", lambda *_args: None)
    monkeypatch.setattr(project_bible_panel, "project_asset_management_state", lambda _project: ())
    monkeypatch.setattr(project_bible_panel, "log", lambda *_args, **_kwargs: None)

    result = project_bible_panel.unbind_selected_project_assets(
        ["character::hero"], "project-a", records, [],
    )

    assert result[0] == "已解除 1 项素材与项目的绑定。"
    assert saved == [("characters", "hero", [])]


def test_gallery_card_click_toggles_multi_selection():
    class Event:
        def __init__(self, index):
            self.index = index

    records = [
        {"key": "character::hero", "name": "Hero", "detail": "人物"},
        {"key": "background::studio", "name": "Studio", "detail": "背景"},
    ]
    selected, _ = project_bible_panel._toggle_project_asset(records, records, [], [], Event(0))
    selected, label = project_bible_panel._toggle_project_asset(records, records, [], selected, Event(1))
    assert selected == ["character::hero", "background::studio"]
    assert label.startswith("已选择 2 项")
    selected, label = project_bible_panel._toggle_project_asset(records, records, [], selected, Event(0))
    assert selected == ["background::studio"]
    assert label.startswith("已选择 1 项")
