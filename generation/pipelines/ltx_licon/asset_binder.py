"""LTX/Licon reference-image and background binding boundary."""
from __future__ import annotations

from typing import Any

from services.context import CONFIG
from workflow.asset_resolver import background_image_for_scene, reference_images_for_roles


class LtxAssetBinder:
    def bind(self, reference_roles: list[str], reference_image_ids: dict[str, str], scene_id: str) -> tuple[list[str], str]:
        references = reference_images_for_roles(
            [str(role) for role in reference_roles or []],
            reference_image_ids,
            limit=int(CONFIG.get("reference_image_max") or 4),
        )
        return references, background_image_for_scene(str(scene_id or ""))
