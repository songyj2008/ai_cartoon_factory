from __future__ import annotations


class ComfyUIChannel:
    id = "comfyui"
    label = "ComfyUI"
    uses_workflow = True

    def unavailable_reason(self, segment, payload):
        from generation.job_schema import resolved_model_id
        from workflow.submit_factory import submit_backend_config

        model_id = resolved_model_id(segment, payload)
        if submit_backend_config(self.id, model_id).get("enabled") is False:
            return "此模型的 ComfyUI 渠道未启用"
        return ""

    def submit(self, service, segment_index, *, wait=False):
        return service._submit_saved_direct({segment_index}, backend=self.id, wait=wait)


channel = ComfyUIChannel()
