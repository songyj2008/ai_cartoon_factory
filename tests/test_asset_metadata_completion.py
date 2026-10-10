import gradio as gr
import pytest

from ui import asset_library_panel as panel


def run_completion(monkeypatch, tmp_path, response=None, error=None):
    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    calls = []

    def llm(system, user, **kwargs):
        calls.append((system, user, kwargs))
        if error:
            raise error
        return response, 0.1

    monkeypatch.setattr(panel, "call_llm", llm)
    inputs = ["蓝衣男孩，正面", str(path), "character", *(["原值"] * 6)]
    steps = list(panel.complete_asset_metadata(*inputs))
    return inputs, steps, calls


def metadata():
    return dict(display_name="蓝衣男孩", aliases=[], tags_cn=["蓝衣", "男孩"],
                description_cn="完整描述。\n" * 1000, use_for=[], pose="正面")


def test_complete_and_apply_full_description(monkeypatch, tmp_path):
    data = metadata()
    inputs, steps, calls = run_completion(monkeypatch, tmp_path, data)
    assert steps[0][2]["interactive"] is False
    result = panel.apply_completed_asset_metadata(steps[-1][0], *inputs)
    assert result[:6] == ("蓝衣男孩", "", "蓝衣，男孩", data["description_cn"].strip(), "", "正面")
    assert result[-1]["interactive"] is True
    assert calls[0][2]["provider_override"] == "inherit"
    assert calls[0][2]["label"] == "asset_metadata"


@pytest.mark.parametrize("index", [0, 1, 2, 3, 6])
def test_changed_selection_or_edits_discard_result(monkeypatch, tmp_path, index):
    inputs, steps, _ = run_completion(monkeypatch, tmp_path, metadata())
    inputs[index] = "已修改"
    result = panel.apply_completed_asset_metadata(steps[-1][0], *inputs)
    assert result[:6] == (gr.update(),) * 6
    assert "未填入" in result[6]


@pytest.mark.parametrize("data", [{}, [], {**metadata(), "aliases": "bad"},
                                   {**metadata(), "description_cn": ""}])
def test_invalid_response_preserves_fields(monkeypatch, tmp_path, data):
    inputs, steps, _ = run_completion(monkeypatch, tmp_path, data)
    assert steps[-1][0] is None
    assert "失败" in steps[-1][1]
    assert panel.apply_completed_asset_metadata(None, *inputs)[:6] == (gr.update(),) * 6


def test_provider_failure_unlocks_button(monkeypatch, tmp_path):
    _, steps, _ = run_completion(monkeypatch, tmp_path, error=RuntimeError("网页连接超时"))
    assert steps[-1][0] is None
    assert "网页连接超时" in steps[-1][1]
    assert steps[-1][2]["interactive"] is True


def test_missing_input_does_not_call_llm(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid input must not call LLM")
    monkeypatch.setattr(panel, "call_llm", unexpected)
    path = tmp_path / "image.png"
    path.touch()
    for text, image in [("描述", ""), ("", str(path)), ("描述", str(tmp_path / "missing.png"))]:
        steps = list(panel.complete_asset_metadata(text, image, "character"))
        assert steps[-1][0] is None
