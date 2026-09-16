"""LTX/Licon workflow construction boundary."""
from __future__ import annotations

from typing import Any

from workflow.licon_msr import MODEL_ID, build_and_optionally_submit


class LtxLiconWorkflowAdapter:
    def build_and_optionally_submit(self, **kwargs: Any) -> dict[str, Any]:
        return build_and_optionally_submit(model_id=MODEL_ID, **kwargs)
