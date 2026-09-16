import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Generate story.json from a topic")
    parser.add_argument("--topic", default="")
    parser.add_argument("--duration-sec", type=int, default=60)
    parser.add_argument("--output", default="")
    parser.add_argument("--model-id", default="")
    args = parser.parse_args()

    topic = str(args.topic or "").strip()
    if not topic:
        parser.error("--topic is required")

    from pipeline.story import generate_story

    text, _ = generate_story(topic, duration_sec=args.duration_sec, model_id=args.model_id or None)
    if not text:
        raise RuntimeError("story generation failed: story.json is empty")
    data = json.loads(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
