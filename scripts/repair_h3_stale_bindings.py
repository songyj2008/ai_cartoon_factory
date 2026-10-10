"""One-time repair for stale H3 ``generation_config.asset_manifest`` bindings.

Root cause (already fixed in code):

- ``_split_placeholder`` deep-copied the pre-split segment, so a split segment
  inherited the *previous* beat's ``asset_manifest`` instead of starting clean.
- ``_prepare_segment`` treated any persisted ``asset_manifest`` as authoritative
  (``has_persisted_manifest``), so the stale binding was never refreshed even
  when the segment's beat had moved on.

As a result, segments 5/6/7 kept the bindings of beats 1/2/3 (the "previous
page, same position" symptom).  Their ``job.final_prompt`` was later recompiled
correctly, but the persisted ``generation_config`` (what the fullscreen editor
shows and re-saves) still holds the old binding plus a stale ``prompt_override``.

This script recomputes the auto-binding for the named segments directly from
their current beats and clears the stale prompt so the next regeneration
recompiles it.  It is safe to re-run (idempotent for already-correct segments).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.context import get_project_dir  # noqa: E402
from generation.pipelines.minimax_h3.auto_binder import auto_bind_asset_manifest  # noqa: E402
from generation.pipelines.minimax_h3.pipeline import _beat_asset_signature  # noqa: E402
from workflow.video_merge import json_text  # noqa: E402


H3 = "minimax_h3_local_ref2va"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _manifest_signature(manifest: list[dict[str, Any]]) -> str:
    """Fingerprint the *current* manifest the same way the pipeline does a beat."""
    identities: list[str] = []
    backgrounds: list[str] = []
    for item in manifest or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        kind = str(meta.get("asset_kind") or "").strip().lower()
        if kind == "background":
            backgrounds.append(str(meta.get("background_id") or "").strip())
        elif kind == "character":
            identities.append(str(meta.get("identity_id") or "").strip())
    return json.dumps(
        {"characters": sorted(set(i for i in identities if i)), "backgrounds": sorted(set(b for b in backgrounds if b))},
        ensure_ascii=False,
        sort_keys=True,
    )


def repair(project_dir: Path, segment_indices: list[int], apply: bool) -> int:
    beats_path = project_dir / "beats.json"
    jobs_path = project_dir / "video_jobs.json"
    beats = _load(beats_path)
    beat_list = beats.get("beats") if isinstance(beats, dict) else beats
    if not isinstance(beat_list, list):
        print("beats.json has no 'beats' list", file=sys.stderr)
        return 1

    payload = _load(jobs_path)
    segments = payload.get("segments") or []
    by_index = {int(s.get("segment_index") or 0): s for s in segments if isinstance(s, dict)}

    changed = 0
    for idx in segment_indices:
        segment = by_index.get(idx)
        if not segment:
            print(f"[skip] segment {idx} not found", file=sys.stderr)
            continue
        if segment.get("model_id") != H3:
            print(f"[skip] segment {idx} is not {H3}", file=sys.stderr)
            continue
        if not (0 < idx <= len(beat_list)):
            print(f"[skip] segment {idx} has no beat", file=sys.stderr)
            continue

        beat = beat_list[idx - 1]
        if not isinstance(beat, dict):
            print(f"[skip] beat {idx} is not a dict", file=sys.stderr)
            continue

        fresh = auto_bind_asset_manifest(dict(beat))
        new_signature = _beat_asset_signature(dict(beat))
        gc = segment.get("generation_config") if isinstance(segment.get("generation_config"), dict) else {}
        old_manifest = gc.get("asset_manifest") or []
        old_sig = _manifest_signature(old_manifest)
        old_po = bool(gc.get("prompt_override"))

        if old_sig == new_signature and not old_po:
            print(f"[ok] segment {idx} already matches beat {idx} ({len(old_manifest)} items)")
            continue

        changed += 1
        print(f"[fix] segment {idx} ({segment.get('part_id')}):")
        print(f"      old manifest: {_manifest_summary(old_manifest)}")
        print(f"      old signature: {old_sig}")
        print(f"      new manifest : {_manifest_summary(fresh or [])}")
        print(f"      new signature: {new_signature}")
        print(f"      prompt_override present: {old_po} -> cleared")

        if not apply:
            continue

        gc["asset_manifest"] = fresh or []
        gc["asset_match_signature"] = new_signature
        gc.pop("prompt_override", None)
        segment["generation_config"] = gc
        segment.pop("compiled_prompt_snapshot", None)
        segment["status"] = "needs_regenerate"
        segment["prompt_saved"] = False
        segment["stale_reason"] = "binding repaired; regenerate this part"
        job = segment.get("job") if isinstance(segment.get("job"), dict) else {}
        for key in ("prompt", "director_prompt", "final_prompt"):
            job[key] = ""
        segment["job"] = job

    if not changed:
        print("nothing to repair")
        return 0

    if not apply:
        print("\n[dry-run] no changes written. Re-run with --apply to persist.")
        return 0

    # Back up before overwriting.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = jobs_path.with_name(f"video_jobs.json.bak_{stamp}")
    shutil.copy2(jobs_path, backup)
    jobs_path.write_text(json_text(payload), encoding="utf-8")
    print(f"\n[applied] {changed} segment(s) repaired; backup at {backup.name}")
    return 0


def _manifest_summary(manifest: list[dict[str, Any]]) -> str:
    chars: list[str] = []
    bgs: list[str] = []
    for item in manifest or []:
        meta = item.get("metadata") if isinstance(item, dict) and isinstance(item.get("metadata"), dict) else {}
        kind = str(meta.get("asset_kind") or "").strip().lower()
        if kind == "background":
            bgs.append(str(meta.get("background_id") or "").strip())
        elif kind == "character":
            chars.append(str(meta.get("identity_id") or "").strip())
    return f"chars={chars} bgs={bgs}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair stale H3 asset bindings")
    parser.add_argument("segments", nargs="*", type=int, help="segment indices to repair (default: 5 6 7)")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = parser.parse_args()

    indices = args.segments or [5, 6, 7]
    project_dir = get_project_dir()
    print(f"project: {project_dir}")
    print(f"segments: {indices}")
    return repair(project_dir, indices, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
