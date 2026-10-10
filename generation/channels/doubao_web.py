"""Doubao free-account web video channel capability and submission boundary."""
from __future__ import annotations

from pathlib import Path

from services.context import BASE_DIR


def _duration(segment):
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    return float(job.get("duration_sec") or segment.get("duration_sec") or 0)


def _prompt(segment):
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    config = segment.get("generation_config") if isinstance(segment.get("generation_config"), dict) else {}
    return str(job.get("director_prompt") or config.get("prompt_override") or job.get("final_prompt") or job.get("prompt") or "").strip()


def _assets(segment):
    job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
    config = segment.get("generation_config") if isinstance(segment.get("generation_config"), dict) else {}
    manifest = config.get("asset_manifest") or job.get("asset_manifest")
    if isinstance(manifest, list) and manifest:
        items = [item for item in manifest if isinstance(item, dict)]
        if any(str(item.get("type") or item.get("media_type") or "image").lower() != "image" for item in items):
            raise ValueError("豆包网页版目前只支持图片参考素材")
        paths = [str(item.get("path") or item.get("source_path") or "") for item in items]
    else:
        paths = [str(value) for value in job.get("reference_images") or []]
        if job.get("background_image"):
            paths.append(str(job["background_image"]))
    resolved = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = BASE_DIR / path
        path = path.resolve()
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError(f"豆包参考图不存在或格式不支持：{raw}")
        if path not in resolved:
            resolved.append(path)
    return resolved


class DoubaoWebChannel:
    id = "doubao_web"
    label = "豆包网页版（免费账号）"
    uses_workflow = False
    daily_limit = 10

    def unavailable_reason(self, segment, payload):
        duration = _duration(segment)
        if duration < 4 or duration > 10 or not duration.is_integer():
            return "免费账号仅支持 4–10 秒整数时长；请调整分段时长后重新规划"
        if not _prompt(segment):
            return "缺少视频提示词"
        try:
            assets = _assets(segment)
        except ValueError as exc:
            return str(exc)
        if not assets:
            return "缺少参考图片"
        from services.web_video_bridge import remaining_quota

        if remaining_quota(self.id) <= 0:
            return "此账号今日 10 次免费生成额度已用完"
        return ""

    def submit(self, service, segment_index, *, wait=False):
        from services.web_video_bridge import enqueue_segment

        return enqueue_segment(segment_index, self)

    def assets(self, segment):
        return _assets(segment)

    def duration(self, segment):
        return int(_duration(segment))

    def prompt(self, segment):
        return _prompt(segment)


channel = DoubaoWebChannel()
