import pytest
import asyncio
import gradio as gr
from gradio.exceptions import Error
from gradio.state_holder import SessionState

from ui.gradio_components import null_safe_multiselect_dropdown


def test_null_safe_multiselect_normalizes_transient_empty_values():
    dropdown = null_safe_multiselect_dropdown(choices=["background"], value=[])

    assert dropdown.preprocess(None) == []
    assert dropdown.preprocess([None]) == []
    assert dropdown.preprocess([None, "background"]) == ["background"]


def test_null_safe_multiselect_still_rejects_unknown_values():
    dropdown = null_safe_multiselect_dropdown(choices=["background"], value=[])

    with pytest.raises(Error):
        dropdown.preprocess(["unknown"])


def test_null_handling_survives_gradio_choices_updates():
    with gr.Blocks() as demo:
        dropdown = null_safe_multiselect_dropdown(choices=["old"], value=[])
        refresh = gr.Button()
        event = refresh.click(
            lambda: gr.update(choices=["new"], value=[]), outputs=dropdown
        )
        echo = gr.Button()
        output = gr.JSON()
        echo_event = echo.click(lambda value: value, inputs=dropdown, outputs=output)
    state = SessionState(demo)

    async def run():
        for _ in range(2):
            await demo.process_api(event["id"], [], state=state)
            result = await demo.process_api(echo_event["id"], [[None, "new"]], state=state)
            assert result["data"][0].root == ["new"]
            assert state[dropdown._id].get_block_name() == "dropdown"
            with pytest.raises(Error):
                await demo.process_api(echo_event["id"], [["old"]], state=state)

    asyncio.run(run())
