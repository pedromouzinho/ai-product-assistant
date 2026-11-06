#!/usr/bin/env python3
"""Build persistent policy packs for the user story lane."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOMAIN_PROFILES_PATH = ROOT / "data" / "story_domain_profiles.json"
OUTPUT_PATH = ROOT / "data" / "story_policy_packs.json"
DEFAULT_TITLE_PATTERN = "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]"
DEFAULT_MANDATORY_SECTIONS = [
    "business_goal",
    "provenance",
    "conditions",
    "rules_constraints",
    "acceptance_criteria",
    "test_scenarios",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detail_level(curated_example_count: int) -> str:
    if curated_example_count >= 20:
        return "high"
    if curated_example_count >= 5:
        return "medium"
    return "medium"


def _terminology_overrides(preferred_lexicon: list[str]) -> list[dict]:
    lexicon = {str(item or "").strip() for item in preferred_lexicon if str(item or "").strip()}
    overrides = []
    if "CTA" in lexicon:
        overrides.append({"from": "botão", "to": "CTA", "when": "ação navegacional ou acionável na interface"})
    if "Primary CTA" in lexicon:
        overrides.append({"from": "botão principal", "to": "Primary CTA", "when": "ação principal do passo"})
    if "Card" in lexicon:
        overrides.append({"from": "caixa", "to": "Card", "when": "container visual de resumo ou agrupamento"})
    if "Sidebar" in lexicon:
        overrides.append({"from": "menu lateral", "to": "Sidebar", "when": "navegação lateral persistente"})
    if "Dropdown" in lexicon:
        overrides.append({"from": "lista", "to": "Dropdown", "when": "seleção fechada de opções"})
    return overrides


def build_story_policy_packs() -> dict:
    profiles = json.loads(DOMAIN_PROFILES_PATH.read_text(encoding="utf-8")).get("entries", [])
    entries = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        preferred_lexicon = [str(item) for item in profile.get("preferred_lexicon", []) if item]
        top_title_patterns = [str(item) for item in profile.get("top_title_patterns", []) if item]
        top_journeys = [str(item) for item in profile.get("top_journeys", []) if item]
        top_flows = [str(item) for item in profile.get("top_flows", []) if item]
        notes = [str(item) for item in profile.get("routing_notes", []) if item]
        detail_level = _detail_level(int(profile.get("curated_example_count", 0) or 0))
        entries.append(
            {
                "id": f"policy:{str(profile.get('domain', '')).strip().lower().replace(' ', '_')}",
                "domain": str(profile.get("domain", "") or ""),
                "aliases": [str(item) for item in profile.get("aliases", []) if item],
                "language": "pt-PT",
                "template_version": "us-v1",
                "detail_level": detail_level,
                "canonical_title_pattern": top_title_patterns[0] if top_title_patterns else DEFAULT_TITLE_PATTERN,
                "mandatory_sections": list(DEFAULT_MANDATORY_SECTIONS),
                "acceptance_style": "CA-xx com comportamento observável, mensurável e testável.",
                "test_style": "Cenários Dado/Quando/Então alinhados com os critérios.",
                "preferred_lexicon": preferred_lexicon[:12],
                "terminology_overrides": _terminology_overrides(preferred_lexicon),
                "top_journeys": top_journeys[:6],
                "top_flows": top_flows[:8],
                "section_emphasis": [str(item) for item in profile.get("section_emphasis", []) if item][:6],
                "notes": notes[:6],
                "coverage_score": float(profile.get("coverage_score", 0.0) or 0.0),
                "production_confidence": float(profile.get("production_confidence", 0.0) or 0.0),
                "curated_example_count": int(profile.get("curated_example_count", 0) or 0),
                "design_file_title": str(profile.get("design_file_title", "") or ""),
            }
        )
    return {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source": "story_domain_profiles",
        "entries": entries,
    }


def main() -> None:
    payload = build_story_policy_packs()
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(payload.get('entries', []))} entries.")


if __name__ == "__main__":
    main()
