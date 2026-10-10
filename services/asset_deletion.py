"""Recoverable removal of whole character identities from the asset library."""
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from services.context import ASSETS_DIR, ASSET_INDEX_PATH, PROJECTS_DIR
from asset_index import sync_asset_index_with_assets


def character_snapshot(current_path):
    root = (ASSETS_DIR / "characters" / "identities").resolve()
    candidate = Path(str(current_path or ""))
    if candidate.is_symlink() or candidate.parent.is_symlink():
        raise ValueError("不支持删除链接目录中的人物")
    image = candidate.resolve()
    relative = image.relative_to(root)
    if len(relative.parts) != 2 or not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("请先选择素材库中已保存的人物图片")
    folder = (root / relative.parts[0]).resolve()
    if folder.parent != root or folder.is_symlink() or any(p.is_symlink() for p in folder.rglob("*")):
        raise ValueError("人物目录路径无效")
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    fingerprint = hashlib.sha256()
    for file in files:
        fingerprint.update(str(file.relative_to(folder)).encode())
        fingerprint.update(file.read_bytes())
    meta = folder / "asset_meta.json"
    data = json.loads(meta.read_text(encoding="utf-8-sig")) if meta.exists() else {}
    name = (data.get("identity") or {}).get("display_name") or folder.name
    count = sum(p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for p in files)
    return {"id": folder.name, "name": name, "images": count, "fingerprint": fingerprint.hexdigest()}


def delete_character(current_path, expected):
    actual = character_snapshot(current_path)
    if not expected or actual != expected:
        raise ValueError("人物选择或文件已变化，请重新查看删除范围后确认")
    source = (ASSETS_DIR / "characters" / "identities" / actual["id"]).resolve()
    root = (ASSETS_DIR / "characters" / "identities").resolve()
    if source.parent != root:
        raise ValueError("人物目录超出素材库范围")
    archive = ASSETS_DIR / "_trash" / (actual["id"] + "_" + uuid.uuid4().hex)
    edits = {}
    for project in (PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []):
        if not project.is_dir():
            continue
        assignment = project / "asset_assignments.json"
        if assignment.exists():
            data = json.loads(assignment.read_text(encoding="utf-8-sig"))
            if actual["id"] in data.get("characters", []):
                data["characters"] = [v for v in data["characters"] if v != actual["id"]]
                edits[assignment] = data
        bible = project / "project_bible.json"
        if bible.exists():
            data = json.loads(bible.read_text(encoding="utf-8-sig"))
            changed = False
            for group in ("core_characters", "supporting_characters"):
                for role in data.get(group, []):
                    if role.get("asset_identity_id") == actual["id"]:
                        role["asset_identity_id"] = ""
                        changed = True
            if changed:
                edits[bible] = data
    originals = {p: p.read_bytes() for p in edits}
    if ASSET_INDEX_PATH.exists():
        originals[ASSET_INDEX_PATH] = ASSET_INDEX_PATH.read_bytes()
    archive.mkdir(parents=True)
    try:
        for i, (path, content) in enumerate(originals.items()):
            (archive / f"backup_{i}.json").write_bytes(content)
        (archive / "restore_manifest.json").write_text(json.dumps({"character": actual, "original_directory": str(source), "backups": {str(p): f"backup_{i}.json" for i,p in enumerate(originals)}}, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.move(str(source), str(archive / "character"))
        for path, data in edits.items():
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        sync_asset_index_with_assets()
    except Exception:
        for path, content in originals.items():
            path.write_bytes(content)
        if (archive / "character").exists():
            shutil.move(str(archive / "character"), str(source))
        raise
    return actual, archive
