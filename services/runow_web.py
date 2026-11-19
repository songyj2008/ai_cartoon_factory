"""Runow-only media contract. No ComfyUI workflow or Doubao assumptions."""
from __future__ import annotations

import copy
import math
import re
import secrets
import time
from pathlib import Path

from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from services.context import BASE_DIR


MODEL_ID = "minimax_h3_local_ref2va"
SUFFIXES = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
    "video": {".mp4", ".mov", ".webm"},
}
LIMITS = {"image": 9, "audio": 3, "video": 3}
NAMES = {"image": "图片", "audio": "音频", "video": "视频"}
REFERENCE = re.compile(r"<(Picture|Image|Audio|Video)\s+(\d+)>|@(图片|音频|视频)(\d+)(?!\d)|@(Picture|Image|Audio|Video)\s+(\d+)(?!\d)", re.I)


def compile_prompt(prompt: str, media: list[dict]) -> tuple[str, list[dict]]:
    """Compile references, keeping <d> dialogue text opaque and unchanged."""
    tags = {item["source_tag"].lower(): item for item in media}
    tags.update({"<image " + item["source_tag"].split(" ", 1)[1].lower(): item
                 for item in media if item["type"] == "image"})
    labels = {item["web_label"]: item for item in media}
    parts = []
    for index, block in enumerate(re.split(r"(<d\s*>.*?</d\s*>)", prompt, flags=re.I | re.S)):
        if index % 2:
            parts.append({"text": block})
            continue
        start = 0
        for match in REFERENCE.finditer(block):
            if match.start() > start:
                parts.append({"text": block[start:match.start()]})
            if match.group(1) or match.group(5):
                kind, number = (match.group(1), match.group(2)) if match.group(1) else (match.group(5), match.group(6))
                key = f"<{kind} {int(number)}>".lower()
                item = tags.get(key)
            else:
                item = labels.get(f"@{match.group(3)}{int(match.group(4))}")
            if item is None:
                raise ValueError(f"Runow 提示词引用没有对应素材：{match.group(0)}")
            parts.append({"reference_id": item["id"], "label": item["web_label"]})
            start = match.end()
        if start < len(block):
            parts.append({"text": block[start:]})
    return "".join(part.get("text", part.get("label", "")) for part in parts), parts


def build_request(segment: dict) -> dict:
    job = segment.get("job") or {}
    config = dict(segment.get("generation_config") or {})
    if not config.get("asset_manifest"):
        config["asset_manifest"] = copy.deepcopy(job.get("asset_manifest") or [])
    prompt = str(job.get("director_prompt") or config.get("prompt_override") or
                 job.get("final_prompt") or job.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Runow 缺少视频提示词")
    duration = float(job.get("duration_sec") or config.get("duration_sec") or segment.get("duration_sec") or 0)
    if not math.isfinite(duration) or not duration.is_integer() or not 4 <= duration <= 15:
        raise ValueError("Runow H3 需要 4–15 秒整数时长；不会自动修改分段")
    ratio = str(config.get("aspect_ratio") or job.get("aspect_ratio") or "16:9")
    if ratio not in {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
        raise ValueError(f"Runow 不支持此画幅比例：{ratio}")
    resolution = str(config.get("runow_resolution") or "1080p").lower()
    if resolution not in {"480p", "768p", "1080p"}:
        raise ValueError("Runow 分辨率需为 480p、768p 或 1080p")
    bindings = H3AssetBinder().bind(H3GenerationConfig.from_mapping(config))
    media, names, ids = [], set(), set()
    for kind in ("image", "video", "audio"):
        items = getattr(bindings, {"image": "images", "video": "videos", "audio": "audios"}[kind])
        if len(items) > LIMITS[kind]:
            raise ValueError(f"Runow 最多选择 {LIMITS[kind]} 个{NAMES[kind]}")
        for n, binding in enumerate(items, 1):
            asset = binding.asset
            if binding.paired_audio_tag:
                raise ValueError("Runow 视频内嵌音轨不能作为独立音频引用；请提供单独音频素材")
            if not asset.path:
                raise ValueError("Runow 素材缺少本地文件路径")
            path = Path(asset.path)
            path = (path if path.is_absolute() else BASE_DIR / path).resolve()
            if not path.is_file() or path.suffix.lower() not in SUFFIXES[kind]:
                raise ValueError(f"Runow 素材不存在或格式不支持：{asset.path}")
            if path.name.casefold() in names or asset.asset_id in ids:
                raise ValueError(f"Runow 素材文件名或 ID 重复，无法唯一匹配资源库：{path.name}")
            names.add(path.name.casefold())
            ids.add(asset.asset_id)
            stat = path.stat()
            media.append({"id": asset.asset_id, "type": kind, "name": path.name, "path": str(path),
                          "role": asset.role, "source_tag": binding.prompt_tag,
                          "web_label": f"@{NAMES[kind]}{n}",
                          "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    if not any(item["type"] in {"image", "video"} for item in media):
        raise ValueError("Runow 多模态参考至少需要一张图片或一个视频")
    compiled, parts = compile_prompt(prompt, media)
    return {"prompt": compiled, "source_prompt": prompt, "prompt_parts": parts,
            "duration_sec": int(duration), "aspect_ratio": ratio, "resolution": resolution,
            "images": [], "media": media}


def public_input(request: dict) -> dict:
    result = copy.deepcopy(request)
    for item in result["media"]:
        for key in ("path", "mtime_ns"):
            item.pop(key, None)
    return result


def resolve_asset(request: dict, asset_id: str) -> Path:
    item = next((item for item in request.get("media", []) if item["id"] == asset_id), None)
    if item is None:
        raise ValueError("素材不在此 Runow 任务中")
    path = Path(item["path"]).resolve()
    if not path.is_file() or path.suffix.lower() not in SUFFIXES[item["type"]]:
        raise ValueError("Runow 素材已丢失或格式不支持")
    stat = path.stat()
    if stat.st_size != item["size"] or stat.st_mtime_ns != item["mtime_ns"]:
        raise ValueError("Runow 素材在排队后发生变化，请重新创建任务")
    return path


def progress(task_id: str, lease: str) -> None:
    """Renew only this Runow preparation lease; never resurrect settled work."""
    from services import web_video_bridge as bridge

    with bridge._LOCK, bridge._connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if (row is None or row['channel'] not in {'runninghub_web', 'runninghub_international_web'} or row['state'] != 'claimed'
                or not row['lease'] or not secrets.compare_digest(row['lease'], lease)):
            raise PermissionError("Runow 任务凭据或状态无效")
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), task_id))

