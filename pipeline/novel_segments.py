"""已有完整小说 → Beats → Segments：先按模型规则把小说切分为带对白/原文的 Beats，再走统一分段管线。"""
from __future__ import annotations

from typing import Any
import hashlib
import json

from asset_index import load_asset_index
from generation.model_rules import rules_for_model
from pipeline.events import ensure_event_fields_on_beats
from services.context import CONFIG
from services.file_utils import get_project_temp_dir
from services.project_bible import ensure_project_bible, load_project_bible
from workflow.asset_matcher import match_beats_assets


def generate_novel_beats(novel_text: str, duration_sec: int, segment_count: int, model_id: str) -> dict[str, Any]:
    """把完整小说切分为 Beats（保留对白与原文），再绑定资产，供统一分段管线使用。"""
    from scripts.story_script.generate_beats import BEAT_GROUPING_REVISION, generate_novel_beats as _generate_novel_beats

    text = str(novel_text or "").strip()
    if not text:
        raise ValueError("完整小说不能为空")
    count = int(segment_count or 0)
    if count <= 0:
        raise ValueError("参考 Beat 数量必须为正数")
    rules = rules_for_model(model_id)
    bible = load_project_bible() or ensure_project_bible(topic=text[:300], asset_index=load_asset_index())
    payload = {
        "task": "Segment an existing novel into filmable Beats",
        "novel_text": text,
        "target_duration_sec": int(duration_sec),
        "target_beat_count": count,
        "project_bible": bible,
        "generation_policy": {"model_id": rules.model_id, "rules_revision": rules.revision,
                              "beat_grouping_revision": BEAT_GROUPING_REVISION},
    }
    # Keep the completed upstream stage when an H3 segment later fails.
    # Storage is enabled only inside an isolated text-operation draft.
    from generation.pipelines.minimax_h3.contracts import H3_REF2VA_MODEL_ID
    from generation.pipelines.minimax_h3.planning_checkpoints import load_novel_beats, save_novel_beats
    checkpoint_key = hashlib.sha256(json.dumps(
        {"payload": payload, "config": CONFIG, "binding_schema_version": 1}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if rules.model_id == H3_REF2VA_MODEL_ID:
        cached = load_novel_beats(checkpoint_key)
        if cached is not None:
            from services.logger import log
            log("[novel_beats] 复用已完成的 Beats 草稿，继续生成分段", "STEP")
            return cached
    from pipeline.novel_chunks import generate_chunked_beats
    data = generate_chunked_beats(payload, model_id=rules.model_id, config=CONFIG,
                                 temp_dir=get_project_temp_dir(), generate=_generate_novel_beats)
    data["generation_source"] = "novel_beats"
    if not data.get('planning_issues'):
        data = ensure_event_fields_on_beats(match_beats_assets(data))
    else:
        data = ensure_event_fields_on_beats(data)
    if rules.model_id == H3_REF2VA_MODEL_ID:
        save_novel_beats(checkpoint_key, data)
    return data
