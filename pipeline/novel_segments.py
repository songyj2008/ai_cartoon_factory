"""已有完整小说 → Beats → Segments：先按模型规则把小说切分为带对白/原文的 Beats，再走统一分段管线。"""
from __future__ import annotations

from typing import Any

from asset_index import load_asset_index
from generation.model_rules import rules_for_model
from pipeline.events import ensure_event_fields_on_beats
from services.context import CONFIG
from services.file_utils import get_project_temp_dir
from services.project_bible import ensure_project_bible, load_project_bible
from workflow.asset_matcher import match_beats_assets


def generate_novel_beats(novel_text: str, duration_sec: int, segment_count: int, model_id: str) -> dict[str, Any]:
    """把完整小说切分为 Beats（保留对白与原文），再绑定资产，供统一分段管线使用。"""
    from scripts.story_script.generate_beats import generate_novel_beats as _generate_novel_beats

    text = str(novel_text or "").strip()
    if not text:
        raise ValueError("完整小说不能为空")
    count = int(segment_count or 0)
    if count <= 0:
        raise ValueError("目标视频分段数必须为正数")
    rules = rules_for_model(model_id)
    bible = load_project_bible() or ensure_project_bible(topic=text[:300], asset_index=load_asset_index())
    payload = {
        "task": "Segment an existing novel into filmable Beats",
        "novel_text": text,
        "target_duration_sec": int(duration_sec),
        "target_beat_count": count,
        "project_bible": bible,
        "generation_policy": {"model_id": rules.model_id, "rules_revision": rules.revision},
    }
    data = _generate_novel_beats(
        payload,
        model_id=rules.model_id,
        config=CONFIG,
        temp_dir=get_project_temp_dir(),
    )
    data["generation_source"] = "novel_beats"
    return ensure_event_fields_on_beats(match_beats_assets(data))
