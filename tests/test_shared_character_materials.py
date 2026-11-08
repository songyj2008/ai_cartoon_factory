import json

from generation.pipelines.minimax_h3 import auto_binder
from services.character_voice import refresh_character_voices
from ui import asset_library_panel as panel


def test_project_names_refresh_image_voice_and_projected_description(monkeypatch):
    from services.project_assets import apply_project_character_names
    monkeypatch.setattr("services.project_assets.load_project_asset_assignments", lambda project: {
        "characters": ["boss"], "character_names": {"boss": {"display_name": "王振国", "aliases": ["王总"]}}})
    image = {"type": "image", "role": "张老板", "label": "张老板", "metadata": {
        "identity_id": "boss", "subject_description": "张老板，穿西装。"}}
    audio = {"type": "audio", "label": "张老板参考音色", "role": "仅用于张老板", "metadata": {
        "identity_id": "boss", "binding_source": "character_voice"}}
    result = apply_project_character_names([image, audio], "project")
    assert result[0]["role"] == result[0]["label"] == "王振国"
    assert result[0]["metadata"]["subject_description"] == "王振国，穿西装。"
    assert "王总" in result[0]["metadata"]["aliases"]
    assert result[1]["label"] == "王振国参考音色"
    assert "张老板" not in result[1]["role"]
    assert image["role"] == "张老板"
    assert image["metadata"]["subject_description"] == "张老板，穿西装。"


def test_description_is_shared_across_image_variants():
    data = {"identity": {}, "images": {"front": {"description_cn": "old"}, "side": {"description_cn": "other", "pose": "side"}}}
    meta = {"description_cn": "new", "pose": "front"}
    panel._set_shared_character_description(data, meta, "共享人物描述")
    assert data["identity"]["description_cn"] == "共享人物描述"
    assert all("description_cn" not in image for image in data["images"].values())
    assert "description_cn" not in meta
    assert data["images"]["side"]["pose"] == "side"


def test_existing_manifest_refreshes_description_without_mutating_snapshot(tmp_path, monkeypatch):
    image = tmp_path / "front.png"
    image.write_bytes(b"image")
    identity = {"id": "person", "description_cn": "新人物描述", "images": [{"id": "front", "path": str(image)}]}
    monkeypatch.setattr(auto_binder, "load_asset_index", lambda: {"characters": {"identities": [identity]}})
    original = [{"type": "image", "path": str(image), "role": "项目名字", "metadata": {"identity_id": "person", "subject_description": "旧描述", "voice_disabled": True}}]
    refreshed = auto_binder.enrich_asset_manifest_metadata(original)
    assert refreshed[0]["metadata"]["subject_description"] == "新人物描述"
    assert refreshed[0]["role"] == "项目名字"
    assert refreshed[0]["metadata"]["voice_disabled"] is True
    assert original[0]["metadata"]["subject_description"] == "旧描述"


def test_voice_replacement_and_removal_refresh_existing_bindings(tmp_path):
    directory = tmp_path / "characters" / "identities" / "person"
    directory.mkdir(parents=True)
    current = directory / "new.wav"
    current.write_bytes(b"audio")
    meta_path = directory / "asset_meta.json"
    meta_path.write_text(json.dumps({"identity": {"voice_reference": {"file": "new.wav", "duration_sec": 4}}}), encoding="utf-8")
    old = {"type": "audio", "path": "old.wav", "source": "character_library", "metadata": {"identity_id": "person"}}
    manual = {"type": "audio", "path": "manual.wav", "source": "upload", "metadata": {"identity_id": "person"}}
    refreshed = refresh_character_voices([old, manual], tmp_path)
    assert refreshed[0]["path"] == str(current)
    assert refreshed[0]["metadata"]["duration_sec"] == 4
    assert refreshed[1] == manual
    assert old["path"] == "old.wav"
    meta_path.write_text('{"identity": {}}', encoding="utf-8")
    assert refresh_character_voices([old, manual], tmp_path) == [manual]
