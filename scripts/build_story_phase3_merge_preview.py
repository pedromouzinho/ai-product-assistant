#!/usr/bin/env python3
"""Build a controlled Phase 3 merge preview from current data and Phase 2 candidates."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_PHASE2_DIR = ROOT / "tmp" / "phase2_story_assets"
DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "phase3_merge_preview"
PHASE_3_MOTTO = "seguranca de dados confidenciais sempre"

DEFAULT_TITLE_PATTERN = "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]"
DEFAULT_MANDATORY_SECTIONS = [
    "business_goal",
    "provenance",
    "conditions",
    "rules_constraints",
    "acceptance_criteria",
    "test_scenarios",
]
DEFAULT_ACCEPTANCE_STYLE = "CA-xx com comportamento observável, mensurável e testável."
DEFAULT_TEST_STYLE = "Cenários Dado/Quando/Então alinhados com os critérios."


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def merge_unique(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip()
        if not item:
            continue
        marker = normalize_text(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def terminology_overrides(preferred_lexicon: list[str]) -> list[dict[str, str]]:
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


def generated_policy_pack(profile: dict[str, Any]) -> dict[str, Any]:
    preferred_lexicon = [str(item) for item in profile.get("preferred_lexicon", []) if item]
    top_title_patterns = [str(item) for item in profile.get("top_title_patterns", []) if item]
    top_journeys = [str(item) for item in profile.get("top_journeys", []) if item]
    top_flows = [str(item) for item in profile.get("top_flows", []) if item]
    return {
        "id": f"policy:{normalize_text(profile.get('domain', '')).replace(' ', '_')}",
        "domain": str(profile.get("domain", "") or ""),
        "aliases": [str(item) for item in profile.get("aliases", []) if item],
        "language": "pt-PT",
        "template_version": "us-v1",
        "detail_level": "high" if float(profile.get("coverage_score", 0.0) or 0.0) >= 0.85 else "medium",
        "canonical_title_pattern": top_title_patterns[0] if top_title_patterns else DEFAULT_TITLE_PATTERN,
        "mandatory_sections": list(DEFAULT_MANDATORY_SECTIONS),
        "acceptance_style": DEFAULT_ACCEPTANCE_STYLE,
        "test_style": DEFAULT_TEST_STYLE,
        "preferred_lexicon": preferred_lexicon[:12],
        "terminology_overrides": terminology_overrides(preferred_lexicon),
        "top_journeys": top_journeys[:6],
        "top_flows": top_flows[:10],
        "section_emphasis": [str(item) for item in profile.get("section_emphasis", []) if item][:6],
        "notes": [str(item) for item in profile.get("routing_notes", []) if item][:6],
        "coverage_score": float(profile.get("coverage_score", 0.0) or 0.0),
        "production_confidence": float(profile.get("production_confidence", 0.0) or 0.0),
        "curated_example_count": int(profile.get("curated_example_count", 0) or 0),
        "design_file_title": str(profile.get("design_file_title", "") or ""),
    }


def merge_profile(existing: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    merged = {
        "domain": existing.get("domain") or candidate.get("domain") or "",
        "aliases": merge_unique(
            list(existing.get("aliases", []) or []) + list(candidate.get("aliases", []) or []),
            limit=14,
        ),
        "design_file_title": existing.get("design_file_title") or candidate.get("design_file_title") or "",
        "design_file_url": existing.get("design_file_url") or candidate.get("design_file_url") or "",
        "top_title_patterns": merge_unique(
            list(existing.get("top_title_patterns", []) or []) + list(candidate.get("top_title_patterns", []) or []),
            limit=4,
        ),
        "top_journeys": merge_unique(
            list(existing.get("top_journeys", []) or []) + list(candidate.get("top_journeys", []) or []),
            limit=8,
        ),
        "top_flows": merge_unique(
            list(existing.get("top_flows", []) or []) + list(candidate.get("top_flows", []) or []),
            limit=12,
        ),
        "preferred_lexicon": merge_unique(
            list(existing.get("preferred_lexicon", []) or []) + list(candidate.get("preferred_lexicon", []) or []),
            limit=12,
        ),
        "section_emphasis": merge_unique(
            list(existing.get("section_emphasis", []) or []) + list(candidate.get("section_emphasis", []) or []),
            limit=6,
        ),
        "routing_notes": merge_unique(
            list(existing.get("routing_notes", []) or []) + list(candidate.get("routing_notes", []) or []),
            limit=8,
        ),
        "production_confidence": round(
            max(float(existing.get("production_confidence", 0.0) or 0.0), float(candidate.get("production_confidence", 0.0) or 0.0)),
            4,
        ),
        "coverage_score": round(max(float(existing.get("coverage_score", 0.0) or 0.0), float(candidate.get("coverage_score", 0.0) or 0.0)), 4),
        "curated_example_count": int(existing.get("curated_example_count", 0) or 0),
    }
    delta = {
        "added_aliases": max(0, len(merged["aliases"]) - len(list(existing.get("aliases", []) or []))),
        "added_journeys": max(0, len(merged["top_journeys"]) - len(list(existing.get("top_journeys", []) or []))),
        "added_flows": max(0, len(merged["top_flows"]) - len(list(existing.get("top_flows", []) or []))),
        "added_lexicon": max(0, len(merged["preferred_lexicon"]) - len(list(existing.get("preferred_lexicon", []) or []))),
        "added_notes": max(0, len(merged["routing_notes"]) - len(list(existing.get("routing_notes", []) or []))),
    }
    return merged, delta


def new_domain_decision(candidate: dict[str, Any]) -> tuple[str, list[str]]:
    confidence = float(candidate.get("production_confidence", 0.0) or 0.0)
    coverage = float(candidate.get("coverage_score", 0.0) or 0.0)
    flow_count = len([item for item in candidate.get("top_flows", []) if str(item or "").strip()])
    reasons = [
        f"confidence={confidence:.2f}",
        f"coverage={coverage:.2f}",
        f"flow_count={flow_count}",
    ]
    if confidence >= 0.75 and (coverage >= 0.4 or flow_count >= 4):
        return "promote", reasons
    return "review", reasons


def build_profile_merge_preview(
    existing_profiles: list[dict[str, Any]],
    candidate_profiles: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_by_domain = {normalize_text(item.get("domain", "")): item for item in existing_profiles if item.get("domain")}
    merged_entries: list[dict[str, Any]] = []
    domain_actions: list[dict[str, Any]] = []
    promoted_new_domains: list[str] = []
    review_domains: list[str] = []
    retained_existing_domains: list[str] = []

    candidate_by_domain = {normalize_text(item.get("domain", "")): item for item in candidate_profiles if item.get("domain")}
    all_domain_keys = sorted(set(existing_by_domain) | set(candidate_by_domain))

    for domain_key in all_domain_keys:
        existing = existing_by_domain.get(domain_key)
        candidate = candidate_by_domain.get(domain_key)
        if existing and candidate:
            merged, delta = merge_profile(existing, candidate)
            merged_entries.append(merged)
            action = "augment" if any(delta.values()) else "retain"
            domain_actions.append(
                {
                    "domain": merged["domain"],
                    "action": action,
                    "reasons": [f"{key}={value}" for key, value in delta.items() if value > 0] or ["no_material_change"],
                    "production_confidence": merged["production_confidence"],
                    "coverage_score": merged["coverage_score"],
                }
            )
            retained_existing_domains.append(merged["domain"])
            continue

        if existing:
            merged_entries.append(existing)
            domain_actions.append(
                {
                    "domain": existing.get("domain", ""),
                    "action": "retain",
                    "reasons": ["existing_only"],
                    "production_confidence": float(existing.get("production_confidence", 0.0) or 0.0),
                    "coverage_score": float(existing.get("coverage_score", 0.0) or 0.0),
                }
            )
            retained_existing_domains.append(str(existing.get("domain", "") or ""))
            continue

        if not candidate:
            continue

        decision, reasons = new_domain_decision(candidate)
        domain_actions.append(
            {
                "domain": candidate.get("domain", ""),
                "action": decision,
                "reasons": reasons,
                "production_confidence": float(candidate.get("production_confidence", 0.0) or 0.0),
                "coverage_score": float(candidate.get("coverage_score", 0.0) or 0.0),
            }
        )
        if decision == "promote":
            merged_entries.append(candidate)
            promoted_new_domains.append(str(candidate.get("domain", "") or ""))
        else:
            review_domains.append(str(candidate.get("domain", "") or ""))

    merged_payload = {
        "version": 3,
        "generated_at": utc_now_iso(),
        "source": "current_story_profiles + phase2_candidates",
        "motto": PHASE_3_MOTTO,
        "entries": sorted(merged_entries, key=lambda item: str(item.get("domain", "") or "")),
    }
    report = {
        "retained_existing_domains": sorted(item for item in retained_existing_domains if item),
        "promoted_new_domains": sorted(item for item in promoted_new_domains if item),
        "review_domains": sorted(item for item in review_domains if item),
        "domain_actions": sorted(domain_actions, key=lambda item: str(item.get("domain", "") or "")),
    }
    return merged_payload, report


def merge_flow_map(
    existing_entries: list[dict[str, Any]],
    candidate_entries: list[dict[str, Any]],
    allowed_domains: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_keys = {str(item.get("dedupe_key", "") or item.get("id", "") or "") for item in existing_entries}
    additional: list[dict[str, Any]] = []
    added_by_domain: Counter[str] = Counter()
    skipped_duplicate = 0
    skipped_review_domain = 0

    for candidate in candidate_entries:
        domain = str(candidate.get("domain", "") or "")
        if domain not in allowed_domains:
            skipped_review_domain += 1
            continue
        key = str(candidate.get("dedupe_key", "") or candidate.get("id", "") or "")
        if not key or key in existing_keys:
            skipped_duplicate += 1
            continue
        existing_keys.add(key)
        additional.append(candidate)
        added_by_domain[domain] += 1

    merged_entries = list(existing_entries) + sorted(
        additional,
        key=lambda item: (
            str(item.get("domain", "") or ""),
            str(item.get("journey", "") or ""),
            str(item.get("flow", "") or ""),
        ),
    )
    payload = {
        "version": 3,
        "generated_at": utc_now_iso(),
        "source": "current_story_flow_map + phase2_candidates",
        "motto": PHASE_3_MOTTO,
        "metadata": {
            "entry_count": len(merged_entries),
            "added_repo_flow_seeds": len(additional),
            "skipped_duplicate_or_existing": skipped_duplicate,
            "skipped_review_domain": skipped_review_domain,
        },
        "entries": merged_entries,
    }
    report = {
        "added_count": len(additional),
        "added_by_domain": dict(sorted(added_by_domain.items())),
        "skipped_duplicate_or_existing": skipped_duplicate,
        "skipped_review_domain": skipped_review_domain,
    }
    return payload, report


def merge_policy_packs(
    existing_packs: list[dict[str, Any]],
    merged_profiles: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_by_domain = {normalize_text(item.get("domain", "")): item for item in existing_packs if item.get("domain")}
    generated_by_domain = {normalize_text(item.get("domain", "")): generated_policy_pack(item) for item in merged_profiles if item.get("domain")}
    merged_entries: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for domain_key in sorted(generated_by_domain):
        generated = generated_by_domain[domain_key]
        existing = existing_by_domain.get(domain_key)
        if not existing:
            merged_entries.append(generated)
            actions.append({"domain": generated.get("domain", ""), "action": "promote"})
            continue

        merged = {
            "id": existing.get("id") or generated.get("id") or "",
            "domain": existing.get("domain") or generated.get("domain") or "",
            "aliases": merge_unique(list(existing.get("aliases", []) or []) + list(generated.get("aliases", []) or []), limit=14),
            "language": existing.get("language") or generated.get("language") or "pt-PT",
            "template_version": existing.get("template_version") or generated.get("template_version") or "us-v1",
            "detail_level": existing.get("detail_level") or generated.get("detail_level") or "medium",
            "canonical_title_pattern": existing.get("canonical_title_pattern") or generated.get("canonical_title_pattern") or DEFAULT_TITLE_PATTERN,
            "mandatory_sections": merge_unique(
                list(existing.get("mandatory_sections", []) or []) + list(generated.get("mandatory_sections", []) or []),
                limit=8,
            ),
            "acceptance_style": existing.get("acceptance_style") or generated.get("acceptance_style") or DEFAULT_ACCEPTANCE_STYLE,
            "test_style": existing.get("test_style") or generated.get("test_style") or DEFAULT_TEST_STYLE,
            "preferred_lexicon": merge_unique(
                list(existing.get("preferred_lexicon", []) or []) + list(generated.get("preferred_lexicon", []) or []),
                limit=12,
            ),
            "terminology_overrides": existing.get("terminology_overrides") or generated.get("terminology_overrides") or [],
            "top_journeys": merge_unique(
                list(existing.get("top_journeys", []) or []) + list(generated.get("top_journeys", []) or []),
                limit=8,
            ),
            "top_flows": merge_unique(
                list(existing.get("top_flows", []) or []) + list(generated.get("top_flows", []) or []),
                limit=12,
            ),
            "section_emphasis": merge_unique(
                list(existing.get("section_emphasis", []) or []) + list(generated.get("section_emphasis", []) or []),
                limit=6,
            ),
            "notes": merge_unique(
                list(existing.get("notes", []) or []) + list(generated.get("notes", []) or []),
                limit=8,
            ),
            "coverage_score": round(max(float(existing.get("coverage_score", 0.0) or 0.0), float(generated.get("coverage_score", 0.0) or 0.0)), 4),
            "production_confidence": round(
                max(float(existing.get("production_confidence", 0.0) or 0.0), float(generated.get("production_confidence", 0.0) or 0.0)),
                4,
            ),
            "curated_example_count": int(existing.get("curated_example_count", 0) or 0),
            "design_file_title": existing.get("design_file_title") or generated.get("design_file_title") or "",
        }
        merged_entries.append(merged)
        actions.append({"domain": merged.get("domain", ""), "action": "augment"})

    payload = {
        "version": 3,
        "generated_at": utc_now_iso(),
        "source": "current_story_policy_packs + phase3_merged_profiles",
        "motto": PHASE_3_MOTTO,
        "entries": sorted(merged_entries, key=lambda item: str(item.get("domain", "") or "")),
    }
    report = {
        "promoted_or_augmented_domains": sorted(actions, key=lambda item: str(item.get("domain", "") or "")),
    }
    return payload, report


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_phase_3(phase2_dir: Path, output_dir: Path) -> dict[str, int]:
    existing_profiles = load_json(DATA_DIR / "story_domain_profiles.json").get("entries", []) or []
    existing_flow_map = load_json(DATA_DIR / "story_flow_map.json").get("entries", []) or []
    existing_policy_packs = load_json(DATA_DIR / "story_policy_packs.json").get("entries", []) or []

    candidate_profiles = load_json(phase2_dir / "story_domain_profiles.phase2.json").get("entries", []) or []
    candidate_flow_map = load_json(phase2_dir / "story_flow_map.phase2.json").get("entries", []) or []

    merged_profiles, profile_report = build_profile_merge_preview(existing_profiles, candidate_profiles)
    allowed_domains = {str(item.get("domain", "") or "") for item in merged_profiles.get("entries", []) if item.get("domain")}
    merged_flow_map, flow_report = merge_flow_map(existing_flow_map, candidate_flow_map, allowed_domains)
    merged_policy_packs, policy_report = merge_policy_packs(existing_policy_packs, merged_profiles.get("entries", []) or [])

    write_json(output_dir / "story_domain_profiles.phase3.preview.json", merged_profiles)
    write_json(output_dir / "story_flow_map.phase3.preview.json", merged_flow_map)
    write_json(output_dir / "story_policy_packs.phase3.preview.json", merged_policy_packs)
    write_json(
        output_dir / "merge_report.json",
        {
            "generated_at": utc_now_iso(),
            "motto": PHASE_3_MOTTO,
            "source_phase2_dir": str(phase2_dir),
            "summary": {
                "merged_domain_profile_count": len(merged_profiles.get("entries", []) or []),
                "merged_flow_map_count": len(merged_flow_map.get("entries", []) or []),
                "merged_policy_pack_count": len(merged_policy_packs.get("entries", []) or []),
                "promoted_new_domain_count": len(profile_report.get("promoted_new_domains", []) or []),
                "review_domain_count": len(profile_report.get("review_domains", []) or []),
            },
            "profile_report": profile_report,
            "flow_report": flow_report,
            "policy_report": policy_report,
        },
    )

    return {
        "merged_domain_profile_count": len(merged_profiles.get("entries", []) or []),
        "merged_flow_map_count": len(merged_flow_map.get("entries", []) or []),
        "merged_policy_pack_count": len(merged_policy_packs.get("entries", []) or []),
        "promoted_new_domain_count": len(profile_report.get("promoted_new_domains", []) or []),
        "review_domain_count": len(profile_report.get("review_domains", []) or []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a controlled Phase 3 merge preview from Phase 2 candidates.")
    parser.add_argument("--phase2-dir", default=str(DEFAULT_PHASE2_DIR), help="Directory with Phase 2 outputs.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for Phase 3 preview outputs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    phase2_dir = Path(args.phase2_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    counts = run_phase_3(phase2_dir, output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "motto": PHASE_3_MOTTO,
                "phase2_dir": str(phase2_dir),
                "output_dir": str(output_dir),
                **counts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
