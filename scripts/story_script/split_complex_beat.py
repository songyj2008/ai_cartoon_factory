from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.beat_splitter.guard import detect_complex_beats
from pipeline.beat_splitter.io import load_beats, save_json
from pipeline.beat_splitter.splitter import split_complex_beat


def log(message: str) -> None:
    print(str(message), flush=True)


def load_json(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def default_report_path(input_path: str | Path) -> Path:
    path = Path(input_path)
    return path.with_name(path.stem + "_complexity_report.json")


def default_split_output_path(input_path: str | Path, beat_index: int) -> Path:
    path = Path(input_path)
    return path.with_name(path.stem + f"_split_beat_{beat_index:03d}.json")


def temp_dir_for_output(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.resolve().parent / "temp" / "beat_splitter"


def select_beat_index(report: dict, explicit_index: int, auto_first: bool) -> int:
    if explicit_index:
        return int(explicit_index)
    if auto_first:
        risky = report.get("risky_reports") if isinstance(report.get("risky_reports"), list) else []
        if not risky:
            raise ValueError("no risky beat found")
        return int(risky[0].get("beat_index") or 0)
    raise ValueError("splitting requires --beat-index or --auto-first")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect and split one complex beat without changing the main generation flow.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--input", required=True, help="Path to beats.json")
    parser.add_argument("--output", help="Path for the new beats JSON. Defaults to beats_split_beat_XXX.json.")
    parser.add_argument("--report-output", help="Path for the complexity report JSON.")
    parser.add_argument("--beat-index", type=int, default=0, help="1-based beat index to split.")
    parser.add_argument("--auto-first", action="store_true", help="Split the first risky beat found by the detector.")
    parser.add_argument("--detect-only", action="store_true", help="Only write a complexity report; do not call the LLM.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite --input with the split beats output.")
    args = parser.parse_args()

    try:
        beats_data = load_beats(args.input)
        report = detect_complex_beats(beats_data)
        report_path = Path(args.report_output) if args.report_output else default_report_path(args.input)
        save_json(report_path, report)
        log(f"[beat_splitter] report saved: {report_path}")
        log(f"[beat_splitter] risky_count={report.get('risky_count', 0)} beat_count={report.get('beat_count', 0)}")

        if args.detect_only:
            return 0

        beat_index = select_beat_index(report, args.beat_index, args.auto_first)
        output_path = Path(args.input) if args.in_place else Path(args.output or default_split_output_path(args.input, beat_index))
        config = load_json(args.config)
        result = split_complex_beat(
            beats_data=beats_data,
            beat_index=beat_index,
            config=config,
            temp_dir=temp_dir_for_output(output_path),
        )
        save_json(output_path, result["beats"])
        detail_path = output_path.with_name(output_path.stem + "_split_detail.json")
        save_json(
            detail_path,
            {
                "beat_index": result["beat_index"],
                "risk_report": result["risk_report"],
                "replacement_count": result["replacement_count"],
                "replacement_beats": result["replacement_beats"],
                "output": str(output_path),
            },
        )
        log(f"[beat_splitter] replacement_count={result['replacement_count']}")
        log(f"[beat_splitter] split beats saved: {output_path}")
        log(f"[beat_splitter] split detail saved: {detail_path}")
        return 0
    except Exception as exc:
        log("[beat_splitter][error] failed")
        log(f"[beat_splitter][error] {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
