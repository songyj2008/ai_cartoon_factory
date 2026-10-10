"""Identity-level voice samples, stored beside the character images."""
import json
import shutil
import uuid
from pathlib import Path

from generation.media import inspect_media


def validate_voice(path):
    source = Path(path)
    if not source.is_file() or source.suffix.lower() not in {".wav", ".mp3"}:
        raise ValueError("参考音色需要 WAV 或 MP3 文件")
    if source.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("参考音色不能超过 15 MB")
    info = inspect_media(source)
    duration = info.duration_sec
    if not info.has_audio or duration is None:
        raise ValueError("无法读取参考音频，请检查文件及 ffprobe 配置")
    if not 2 <= duration <= 15:
        raise ValueError("参考音色时长需为 2–15 秒")
    return duration


def read_voice(directory):
    meta = Path(directory) / "asset_meta.json"
    data = json.loads(meta.read_text(encoding="utf-8-sig")) if meta.exists() else {}
    voice = (data.get("identity") or {}).get("voice_reference") or {}
    return data, voice


def voice_path(directory, voice):
    filename = str(voice.get("file") or "")
    path = (Path(directory) / filename).resolve()
    if not filename or path.parent != Path(directory).resolve() or not path.is_file():
        return None
    return path


def save_voice(directory, source, transcript):
    duration = validate_voice(source)
    directory = Path(directory)
    data, old = read_voice(directory)
    old_path = voice_path(directory, old)
    source = Path(source).resolve()
    # Unique names keep existing render snapshots valid after a replacement.
    target = old_path if source == old_path else directory / ("voice_" + uuid.uuid4().hex + source.suffix.lower())
    if target != source:
        shutil.copy2(source, target)
    data.setdefault("identity", {})["voice_reference"] = {
        "file": target.name, "transcript": str(transcript or "").strip(), "duration_sec": duration,
    }
    (directory / "asset_meta.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def remove_voice(directory):
    data, _ = read_voice(directory)
    data.setdefault("identity", {}).pop("voice_reference", None)
    (Path(directory) / "asset_meta.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def attach_character_voices(manifest, beat, assets_dir):
    """Attach identity voices only for characters with dialogue in this segment."""
    units = [unit for unit in beat.get("dialogue_units") or []
             if isinstance(unit, dict) and str(unit.get("line") or unit.get("text") or "").strip()]
    # Use the same source precedence as the H3 prompt compiler.
    if not units:
        hints = (beat.get("model_hints") or {}).get("minimax_h3_local_ref2va") or {}
        for shot in hints.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            values = shot.get("dialogues")
            if not isinstance(values, list):
                value = shot.get("dialogue")
                values = value if isinstance(value, list) else [value]
            units.extend(unit for unit in values if isinstance(unit, dict)
                         and str(unit.get("line") or unit.get("text") or "").strip())
    speakers = {str(unit.get(key) or "").strip() for unit in units
                for key in ("speaker", "speaker_name", "speaker_id", "role_id")}
    speakers.discard("")
    result = [dict(item) for item in manifest if (item.get("metadata") or {}).get("binding_source") != "character_voice"]
    bound = {str((item.get("metadata") or {}).get("identity_id")) for item in result if item.get("type") == "audio"}
    disabled = {str((item.get("metadata") or {}).get("identity_id")) for item in result
                if item.get("type") == "image" and (item.get("metadata") or {}).get("voice_disabled")}
    root = (Path(assets_dir) / "characters" / "identities").resolve()
    for item in list(result):
        meta = item.get("metadata") or {}
        identity_id = str(meta.get("identity_id") or "")
        if item.get("type") != "image" or not identity_id or identity_id in bound or identity_id in disabled:
            continue
        aliases = {identity_id, str(meta.get("role_id") or ""),
                   str(item.get("role") or ""), str(item.get("label") or "")}
        if not speakers.intersection(aliases):
            continue
        directory = (root / identity_id).resolve()
        if directory.parent != root:
            continue
        data, voice = read_voice(directory)
        identity = data.get("identity") or {}
        path = voice_path(directory, voice)
        if not path:
            continue
        name = identity.get("display_name") or item.get("role") or identity_id
        if sum(item.get("type") == "audio" for item in result) >= 3:
            break
        result.append({"asset_id": "voice_" + identity_id, "type": "audio", "path": str(path),
                       "label": f"{name}参考音色", "source": "character_voice",
                       "role": f"仅用于{name}的音色参考；台词严格使用当前剧情，不复述参考录音内容，其他人物不得使用此音色",
                       "metadata": {"identity_id": identity_id, "role_id": meta.get("role_id", ""),
                                    "binding_source": "character_voice",
                                    "duration_sec": voice.get("duration_sec"), "voice_only": True}})
        bound.add(identity_id)
    return result
