"""Model-aware submission admission rules.

Local ComfyUI jobs are serialized by default because heavyweight video models
share GPU memory.  Remote queues may opt into a bounded per-deployment width.
"""
from __future__ import annotations


class GenerationScheduler:
    def concurrency_limit(self, model_id: str, backend: str) -> int:
        normalized_backend = str(backend or "comfyui").strip().lower()
        if normalized_backend != "runninghub":
            return 1

        from services.context import CONFIG
        from workflow.submit_factory import submit_backend_config

        deployment = submit_backend_config("runninghub", model_id=model_id)
        raw = deployment.get("video_generation_concurrency_limit") or CONFIG.get("video_generation_concurrency_limit") or 3
        try:
            return max(1, min(int(raw), 8))
        except (TypeError, ValueError):
            return 3
