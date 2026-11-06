#!/usr/bin/env python3
"""Build a hybrid story flow map from the Figma design registry and curated story corpus."""

from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from figma_story_map import _load_story_design_map  # type: ignore

OUTPUT_PATH = ROOT / "data" / "story_flow_map.json"
CURATED_CORPUS_PATH = ROOT / "data" / "curated_story_examples.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(folded.split())


def _load_curated_entries() -> list[dict]:
    payload = json.loads(CURATED_CORPUS_PATH.read_text(encoding="utf-8"))
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _aliases(entry: dict) -> list[str]:
    values = [str(entry.get("domain", "") or "")]
    values.extend(str(item) for item in entry.get("aliases", []) if item)
    values.extend(str(item) for item in entry.get("journeys", []) if item)
    return [value for value in values if value.strip()]


def _match_design_entry(curated_entry: dict, design_entries: list[dict]) -> dict:
    curated_domain = _normalize_text(curated_entry.get("domain", ""))
    curated_journey = _normalize_text(curated_entry.get("journey", ""))
    best = {}
    best_score = 0.0
    for design_entry in design_entries:
        score = 0.0
        for alias in _aliases(design_entry):
            alias_norm = _normalize_text(alias)
            if not alias_norm:
                continue
            if curated_domain and curated_domain == alias_norm:
                score += 1.0
            elif curated_domain and curated_domain in alias_norm:
                score += 0.6
            if curated_journey and curated_journey and curated_journey in alias_norm:
                score += 0.3
        if score > best_score:
            best = design_entry
            best_score = score
    return best if best_score >= 0.5 else {}


def _journey_seed_entries(design_entries: list[dict]) -> list[dict]:
    seeds = []
    for design_entry in design_entries:
        status = str(design_entry.get("status", "") or "")
        if status == "meta_repository":
            continue
        journeys = [str(item) for item in design_entry.get("journeys", []) if str(item or "").strip()]
        if not journeys:
            journeys = [str(design_entry.get("domain", "") or "")]
        for journey in journeys:
            journey_clean = str(journey or "").strip()
            if not journey_clean:
                continue
            seeds.append(
                {
                    "id": f"{design_entry.get('id', '')}:journey:{_normalize_text(journey_clean).replace(' ', '_')}",
                    "dedupe_key": f"{_normalize_text(design_entry.get('domain', ''))}|{_normalize_text(journey_clean)}",
                    "source_kind": "design_journey",
                    "domain": str(design_entry.get("domain", "") or ""),
                    "journey": journey_clean,
                    "flow": journey_clean,
                    "detail": "",
                    "title": str(design_entry.get("title", "") or ""),
                    "file_key": str(design_entry.get("file_key", "") or ""),
                    "file_title": str(design_entry.get("title", "") or ""),
                    "url": str(design_entry.get("url", "") or ""),
                    "site_placement": str(design_entry.get("site_placement", "") or ""),
                    "routing_note": str(design_entry.get("routing_note", "") or ""),
                    "aliases": [str(item) for item in design_entry.get("aliases", []) if item],
                    "ui_components": [str(item) for item in design_entry.get("ux_terms", []) if item],
                    "ux_terms": [str(item) for item in design_entry.get("ux_terms", []) if item],
                    "currentness_score": float(design_entry.get("currentness_score", 0.0) or 0.0),
                    "production_confidence": float(design_entry.get("production_confidence", 0.0) or 0.0),
                    "quality_score": 0.0,
                    "source_work_item_id": "",
                }
            )
    return seeds


def _curated_entries(curated_entries: list[dict], design_entries: list[dict]) -> list[dict]:
    result = []
    for curated_entry in curated_entries:
        design_entry = _match_design_entry(curated_entry, design_entries)
        if not design_entry:
            continue
        curated_domain = str(curated_entry.get("domain", "") or "").strip()
        mapped_domain = str(design_entry.get("domain", "") or "").strip()
        domain = mapped_domain or curated_domain
        journey = str(curated_entry.get("journey", "") or "").strip()
        flow = str(curated_entry.get("flow", "") or journey or domain).strip()
        detail = str(curated_entry.get("detail", "") or "").strip()
        result.append(
            {
                "id": f"curated:{curated_entry.get('id', '')}",
                "dedupe_key": "|".join(
                    [
                        _normalize_text(domain),
                        _normalize_text(journey),
                        _normalize_text(flow),
                    ]
                ),
                "source_kind": "curated_story",
                "domain": domain,
                "journey": journey,
                "flow": flow,
                "detail": detail,
                "title": str(curated_entry.get("title", "") or ""),
                "file_key": str(design_entry.get("file_key", "") or ""),
                "file_title": str(design_entry.get("title", "") or ""),
                "url": str(curated_entry.get("url", "") or design_entry.get("url", "") or ""),
                "site_placement": str(design_entry.get("site_placement", "") or ""),
                "routing_note": str(design_entry.get("routing_note", "") or ""),
                "aliases": [str(item) for item in design_entry.get("aliases", []) if item],
                "ui_components": [str(item) for item in curated_entry.get("ux_terms", []) if item],
                "ux_terms": [str(item) for item in curated_entry.get("ux_terms", []) if item],
                "currentness_score": float(design_entry.get("currentness_score", 0.0) or 0.0),
                "production_confidence": float(design_entry.get("production_confidence", 0.0) or 0.0),
                "quality_score": float(curated_entry.get("quality_score", 0.0) or 0.0),
                "source_work_item_id": str(curated_entry.get("id", "") or ""),
            }
        )
    return result


def build_story_flow_map() -> dict:
    design_entries = [entry for entry in _load_story_design_map() if isinstance(entry, dict)]
    curated_entries = _load_curated_entries()
    entries = _journey_seed_entries(design_entries) + _curated_entries(curated_entries, design_entries)
    domains = Counter(_normalize_text(entry.get("domain", "")) for entry in entries if entry.get("domain"))
    source_kinds = Counter(str(entry.get("source_kind", "") or "") for entry in entries if entry.get("source_kind"))
    return {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source": "figma_story_map + curated_story_examples",
        "metadata": {
            "entry_count": len(entries),
            "domains": domains.most_common(),
            "source_kinds": source_kinds.most_common(),
        },
        "entries": entries,
    }


def main() -> None:
    payload = build_story_flow_map()
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(payload.get('entries', []))} entries.")


if __name__ == "__main__":
    main()
