import json
from pathlib import Path

import wave
import pytest

from services.character_voice import attach_character_voices, read_voice, remove_voice, save_voice, validate_voice
from test_asset_metadata_paste import setup_assets
from ui import asset_library_panel as panel


def sample(tmp_path, duration=3):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * (16000 * duration))
    return path


def test_save_upload_with_image_and_reload(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    audio = sample(tmp_path)
    saved = panel.save_selected_asset_metadata(str(source), "character", "", panel.NEW_ASSET,
        "吴天", "", "", "人物", "", "", str(audio), "样本原文")
    image = Path(saved[1])
    result = panel.load_character_voice(str(image), "character")
    assert Path(result[0]).parent == image.parent
    assert result[1] == "样本原文"
    _, voice = read_voice(image.parent)
    assert voice["duration_sec"] == 3
    assert panel.load_character_voice(str(source), "character")[:2] == (None, "")
    assert panel.load_character_voice(str(image), "background")[:2] == (None, "")


def test_speaker_binding_and_transcript_not_in_prompt(tmp_path):
    assets = tmp_path / "assets"
    folder = assets / "characters/identities/wutian"
    folder.mkdir(parents=True)
    (folder / "asset_meta.json").write_text(json.dumps({"identity": {"display_name": "吴天"}}), encoding="utf-8")
    save_voice(folder, sample(tmp_path), "不要当作剧情的样本文字")
    image = {"asset_id": "wu", "type": "image", "path": str(folder / "front.png"), "role": "吴天",
             "metadata": {"identity_id": "wutian", "role_id": "character_001"}}
    beat = {"dialogue_units": [{"speaker_name": "character_001", "line": "新台词"}]}
    bound = attach_character_voices([image], beat, assets)
    assert len(bound) == 2
    shot_beat = {"dialogue_units": [], "model_hints": {"minimax_h3_local_ref2va": {
        "shots": [{"dialogues": [{"speaker": "character_001", "line": "画外台词", "scope": "off-screen"}]}]}}}
    assert attach_character_voices([image], shot_beat, assets) == bound
    shot_beat["dialogue_units"] = [{"speaker": "someone_else", "line": "当前台词"}]
    assert attach_character_voices(bound, shot_beat, assets) == [image]
    assert attach_character_voices(bound, {"dialogue_units": [{"speaker": "character_001", "line": " "}]}, assets) == [image]
    from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
    from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
    from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
    config = H3GenerationConfig.from_mapping({"asset_manifest": bound, "prompt_override": "吴天说新台词。"})
    bindings = H3AssetBinder().bind(config)
    prompt = H3PromptCompiler().compile({}, config, bindings, duration_sec=5).text
    assert "<Audio 1> → <Picture 1>" in prompt
    assert "不要当作剧情" not in prompt
    assert H3PromptCompiler._with_character_voices(prompt, bindings) == prompt
    assert "不要当作剧情" not in json.dumps(bound, ensure_ascii=False)
    assert attach_character_voices(bound, beat, assets) == bound
    assert attach_character_voices(bound, {}, assets) == [image]
    remove_voice(folder)
    assert attach_character_voices(bound, beat, assets) == [image]


def test_replace_preserves_previous_file_and_remove_binding(tmp_path):
    folder = tmp_path / "person"
    folder.mkdir()
    first = save_voice(folder, sample(tmp_path), "")
    second = save_voice(folder, sample(tmp_path, 4), "新原文")
    assert first != second
    assert first.exists()
    remove_voice(folder)
    assert read_voice(folder)[1] == {}
    assert second.exists()


@pytest.mark.parametrize("duration", [1, 16])
def test_invalid_duration(tmp_path, duration):
    with pytest.raises(ValueError, match="2–15"):
        validate_voice(sample(tmp_path, duration))


def test_matched_character_voice_without_dialogue_and_controls(tmp_path):
    assets = tmp_path / "assets"
    folder = assets / "characters/identities/person"
    folder.mkdir(parents=True)
    (folder / "asset_meta.json").write_text(json.dumps({"identity": {"display_name": "Person"}}), encoding="utf-8")
    save_voice(folder, sample(tmp_path), "sample transcript")
    image = {"asset_id": "one", "type": "image", "path": str(folder / "one.png"), "metadata": {"identity_id": "person"}}
    image2 = {**image, "asset_id": "two"}
    beat = {"dialogue_units": [{"speaker": "person", "line": "Current dialogue"}]}
    assert attach_character_voices([image, image2], {}, assets) == [image, image2]
    bound = attach_character_voices([image, image2], beat, assets)
    assert len(bound) == 3
    assert bound[-1]["type"] == "audio"
    assert attach_character_voices(bound, beat, assets) == bound
    assert attach_character_voices([bound[-1]], {}, assets) == []
    disabled = {**image, "metadata": {"identity_id": "person", "voice_disabled": True}}
    assert attach_character_voices([disabled, bound[-1]], {}, assets) == [disabled]
    manual = {"asset_id": "manual", "type": "audio", "path": str(sample(tmp_path)), "metadata": {"identity_id": "person", "voice_only": True}}
    assert attach_character_voices([image, manual, bound[-1]], {}, assets) == [image, manual]
    replacement = save_voice(folder, sample(tmp_path, 4), "replacement")
    refreshed = attach_character_voices(bound, beat, assets)
    assert refreshed[-1]["path"] == str(replacement)
    assert Path(bound[-1]["path"]).exists()



@pytest.mark.parametrize("template_name", ["MiniMaxH3_Ref2VA_api.json", "MiniMaxH3_Ref2VA_720x1280_api.json", "MiniMaxH3_Ref2VA_8step_lora16_9_api.json"])
def test_automatic_voice_reaches_workflow_and_prompt(tmp_path, template_name):
    from test_minimax_h3_pipeline import _profile_and_template, _assert_no_dangling_connections
    from generation.pipelines.minimax_h3.workflow_adapter import H3Ref2VAWorkflowAdapter
    assets = tmp_path / "assets"
    folder = assets / "characters/identities/person"
    folder.mkdir(parents=True)
    save_voice(folder, sample(tmp_path), "never copy transcript")
    image = {"asset_id": "person", "type": "image", "path": "person.png", "metadata": {"identity_id": "person"}}
    manifest = attach_character_voices([image], {"dialogue_units": [{"speaker": "person", "line": "Current dialogue"}]}, assets)
    profile, template = _profile_and_template()
    template = json.loads((Path(__file__).resolve().parents[1] / "templates" / template_name).read_text(encoding="utf-8-sig"))
    build = H3Ref2VAWorkflowAdapter(profile=profile).build(template, {}, {"asset_manifest": manifest, "prompt_override": "Current dialogue."}, duration_sec=5)
    assert len(build.bindings.audios) == 1
    binding = build.bindings.audios[0]
    inputs = next(n["inputs"] for n in build.workflow.values() if binding.workflow_input in n.get("inputs", {}))
    source = inputs[binding.workflow_input][0]
    assert build.workflow[source]["class_type"] == "LoadAudio"
    assert "<Audio 1> → <Picture 1>" in build.prompt.text
    assert "never copy transcript" not in build.prompt.text
    _assert_no_dangling_connections(build.workflow)


def test_automatic_speaker_voices_stop_at_three(tmp_path, monkeypatch):
    import services.character_voice as voices
    monkeypatch.setattr(voices, "read_voice", lambda directory: ({}, {"duration_sec": 3}))
    monkeypatch.setattr(voices, "voice_path", lambda directory, voice: directory / "sample.wav")
    images = [{"type": "image", "role": f"person{i}", "metadata": {"identity_id": f"person{i}"}}
              for i in range(5)]
    beat = {"dialogue_units": [{"speaker": f"person{i}", "line": "台词"} for i in range(4)]}
    bound = voices.attach_character_voices(images, beat, tmp_path)
    audios = [item for item in bound if item["type"] == "audio"]
    assert [item["metadata"]["identity_id"] for item in audios] == ["person0", "person1", "person2"]
    assert bound[:5] == images
    assert voices.attach_character_voices(bound, beat, tmp_path) == bound


def test_editor_preserves_prepared_automatic_audio(tmp_path):
    from ui.group_panel import _h3_config_html
    audio = {"asset_id": "voice_person", "type": "audio", "path": str(tmp_path / "voice.wav"),
             "label": "人物参考音色", "metadata": {"identity_id": "person", "binding_source": "character_voice"}}
    html = _h3_config_html({"segment_index": 1, "generation_config": {"asset_manifest": [audio]}})
    assert "voice_person" in html
    assert "voice.wav" in html
    assert "音频 1/3" in html


def test_editor_offers_library_voice_on_selected_character(tmp_path, monkeypatch):
    from ui.group_panel import _h3_config_html
    from services import context
    monkeypatch.setattr(context, "ASSETS_DIR", tmp_path)
    folder = tmp_path / "characters/identities/person"
    folder.mkdir(parents=True)
    (folder / "voice.wav").write_bytes(b"sample")
    (folder / "asset_meta.json").write_text(json.dumps({"identity": {"voice_reference": {
        "file": "voice.wav", "duration_sec": 3}}}), encoding="utf-8")
    image = {"asset_id": "image_person", "type": "image", "path": "person.png",
             "metadata": {"identity_id": "person"}}
    html = _h3_config_html({"segment_index": 1, "generation_config": {"asset_manifest": [image]}})
    assert "library_voice" in html
    assert "voice.wav" in html
    assert "library_voice" not in image["metadata"]
