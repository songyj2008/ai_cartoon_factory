"""File utilities for project dirs, JSON I/O, image helpers, and text compacting."""
from __future__ import annotations

import json
import re
import struct
import time
import zlib
from pathlib import Path
from typing import Any

from services.context import get_project_dir
from services.logger import log


def short_path_name(path):
    if not path:
        return ""
    try:
        return Path(path).name
    except Exception:
        return str(path)


def display_data_name(name):
    mapping = {
        "story.json": "完整剧情",
        "beats.json": "剧情 Beats",
        "video_jobs.json": "视频模型任务",
        "project_asset_candidates.json": "项目候选素材",
    }
    return mapping.get(str(name), str(name))


def display_json_label(label):
    mapping = {
        "story": "完整剧情",
        "beats": "剧情 Beats",
        "video_jobs": "视频模型任务",
    }
    return mapping.get(str(label), str(label))


def ensure_project_dirs():
    project = get_project_dir()
    project.mkdir(parents=True, exist_ok=True)
    for sub in [
        "images/generated",
        "images/cache",
        "images/references",
        "workflows/licon_msr",
        "workflows/minimax_h3",
        "media/h3",
        "temp",
        "videos",
        "logs",
        "final",
    ]:
        (project / sub).mkdir(parents=True, exist_ok=True)


def get_temp_dir_from_output_dir(output_dir):
    temp_dir = Path(output_dir) / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def get_project_temp_dir():
    temp_dir = get_temp_dir_from_output_dir(get_project_dir())
    log(f"[temp] 临时文件目录: {temp_dir}")
    return temp_dir


def save_json(name, data):
    ensure_project_dirs()
    path = get_project_dir() / name
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    log(f"保存{display_data_name(name)}成功")
    return path


def load_json_file(name):
    path = get_project_dir() / name
    if not path.exists():
        log(f"未找到{display_data_name(name)}", "WARN")
        return ""
    log(f"加载{display_data_name(name)}成功")
    # Project JSON may be edited externally on Windows, where UTF-8 BOM is
    # common.  ``utf-8-sig`` accepts both BOM and normal UTF-8 so a valid
    # video_jobs.json cannot make the segment panel appear empty.
    return path.read_text(encoding="utf-8-sig")


def load_json_file_silent(name):
    path = get_project_dir() / name
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception:
        return ""


def safe_json_loads(text, label="JSON"):
    display_label = display_json_label(label)
    if not text or not str(text).strip():
        raise ValueError(f"{display_label}为空")
    try:
        return json.loads(text)
    except Exception as exc:
        bad_path = get_project_temp_dir() / f"bad_{str(label).lower()}_{int(time.time())}.txt"
        bad_path.write_text(str(text), encoding="utf-8")
        raise ValueError(f"{display_label}解析失败，原文已保存\n错误：{exc}") from exc


def write_black_png(path, width=720, height=1280):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + (b"\x00\x00\x00" * width) for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def compact_text(text: Any, max_len=80):
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ,.;，。")
    return value[:max_len].strip(" ,.;，。")
