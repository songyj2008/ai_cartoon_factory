from __future__ import annotations

from generation.channels.comfyui import ComfyUIChannel


class RunningHubChannel(ComfyUIChannel):
    id = "runninghub"
    label = "RunningHub"


channel = RunningHubChannel()
