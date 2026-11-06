#!/usr/bin/env python3
"""Build persistent domain profiles for the user story lane."""

from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIGMA_MAP_PATH = ROOT / "data" / "figma_story_map.json"
CURATED_PATH = ROOT / "data" / "curated_story_examples.json"
OUTPUT_PATH = ROOT / "data" / "story_domain_profiles.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(folded.split())


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_profiles() -> dict:
    figma_entries = [entry for entry in _load_json(FIGMA_MAP_PATH).get("entries", []) if isinstance(entry, dict)]
    curated_entries = [entry for entry in _load_json(CURATED_PATH).get("entries", []) if isinstance(entry, dict)]

    by_domain: dict[str, dict] = {}
    curated_grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for entry in curated_entries:
        domain = str(entry.get("domain", "") or "").strip()
        if domain:
            curated_grouped[_normalize_text(domain)].append(entry)

    for figma_entry in figma_entries:
        domain = str(figma_entry.get("domain", "") or "").strip()
        if not domain:
            continue
        domain_key = _normalize_text(domain)
        curated_subset = curated_grouped.get(domain_key, [])
        journeys = Counter(str(entry.get("journey", "") or "").strip() for entry in curated_subset if str(entry.get("journey", "") or "").strip())
        flows = Counter(str(entry.get("flow", "") or "").strip() for entry in curated_subset if str(entry.get("flow", "") or "").strip())
        lexicon = Counter(term for entry in curated_subset for term in entry.get("ux_terms", []) or [])
        patterns = Counter(str(entry.get("title_pattern", "") or "").strip() for entry in curated_subset if str(entry.get("title_pattern", "") or "").strip())
        sections = Counter(section for entry in curated_subset for section in (entry.get("sections", {}) or {}).keys())
        curated_notes = []
        if curated_subset:
            top_journey = journeys.most_common(1)[0][0] if journeys else ""
            if top_journey:
                curated_notes.append(f"O corpus curado puxa sobretudo para a jornada {top_journey}.")
        coverage_score = min(1.0, len(curated_subset) / 20.0)
        by_domain[domain_key] = {
            "domain": domain,
            "aliases": [str(item) for item in figma_entry.get("aliases", []) if item],
            "design_file_title": str(figma_entry.get("title", "") or ""),
            "design_file_url": str(figma_entry.get("url", "") or ""),
            "top_title_patterns": [item for item, _ in patterns.most_common(3)],
            "top_journeys": [item for item, _ in journeys.most_common(6)] or [str(item) for item in figma_entry.get("journeys", []) if item],
            "top_flows": [item for item, _ in flows.most_common(8)],
            "preferred_lexicon": [item for item, _ in lexicon.most_common(12)] or [str(item) for item in figma_entry.get("ux_terms", []) if item],
            "section_emphasis": [item for item, _ in sections.most_common(6)],
            "routing_notes": [str(figma_entry.get("routing_note", "") or "")] + curated_notes,
            "production_confidence": float(figma_entry.get("production_confidence", 0.0) or 0.0),
            "coverage_score": round(coverage_score, 4),
            "curated_example_count": len(curated_subset),
        }

    return {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source": "figma_story_map + curated_story_examples",
        "entries": sorted(by_domain.values(), key=lambda item: item.get("domain", "")),
    }


def main() -> None:
    payload = _build_profiles()
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(payload.get('entries', []))} profiles.")


if __name__ == "__main__":
    main()
