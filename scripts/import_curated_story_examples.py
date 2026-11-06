from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from story_curated_corpus import build_curated_story_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Import curated user story CSV into local JSON corpus.")
    parser.add_argument("csv_path", help="Path to the exported CSV file.")
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "data" / "curated_story_examples.json"),
        help="Output JSON path.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    payload = build_curated_story_payload(rows, source_path=str(csv_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = payload.get("metadata", {})
    print(
        json.dumps(
            {
                "output": str(output_path),
                "count": metadata.get("count", 0),
                "top_domains": metadata.get("top_domains", [])[:5],
                "top_authors": metadata.get("top_authors", [])[:5],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
