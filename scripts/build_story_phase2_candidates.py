#!/usr/bin/env python3
"""Build Phase 2 candidate story seeds from Phase 1 repo-crawl artifacts.

Phase 2 keeps current live data untouched and writes candidate files to tmp/.
The generator merges:
- existing story_domain_profiles.json
- existing story_flow_map.json
- existing story_policy_packs.json
- figma_story_map.json
- tmp/phase1_repo_crawl/repo_atlas.json
- tmp/phase1_repo_crawl/flow_seeds.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_PHASE1_DIR = ROOT / "tmp" / "phase1_repo_crawl"
DEFAULT_OUTPUT_DIR = ROOT / "tmp" / "phase2_story_assets"
PHASE_2_MOTTO = "seguranca de dados confidenciais sempre"

DEFAULT_TITLE_PATTERN = "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]"
DEFAULT_MANDATORY_SECTIONS = [
    "business_goal",
    "provenance",
    "conditions",
    "rules_constraints",
    "acceptance_criteria",
    "test_scenarios",
]
DEFAULT_UI_LEXICON = ["CTA", "Card", "Input", "Modal", "Dropdown"]
ACCENTED_DOMAIN_OVERRIDES = {
    "autenticacao": "Autenticação",
    "beneficiarios": "Beneficiários",
    "cartoes": "Cartões",
    "credito": "Crédito",
    "notificacoes": "Notificações",
    "operacoes": "Operações",
    "publico": "Público",
    "recebiveis": "Recebíveis",
    "repositorio handoffs": "Repositório Handoffs",
    "sessao global": "Sessão Global",
    "transferencias": "Transferências",
}
DOMAIN_CANONICAL_ALIASES = {
    "agenda": "Dashboard",
    "credenciais": "Autenticação",
    "fundos europeus": "Onboarding",
    "integrated solutions pending ops": "Operações",
    "mobis pending ops": "Operações",
    "register sibs screens": "Autenticação",
}
EXCLUDED_PHASE1_DOMAINS = {
    "base",
    "cdt",
    "common",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def humanize_identifier(value: str) -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in text.split())


def canonical_domain_name(raw: str, known_domains: dict[str, str]) -> str:
    normalized = normalize_text(raw)
    if not normalized:
        return ""
    if normalized in EXCLUDED_PHASE1_DOMAINS:
        return ""
    if normalized in DOMAIN_CANONICAL_ALIASES:
        return DOMAIN_CANONICAL_ALIASES[normalized]
    if normalized in known_domains:
        return known_domains[normalized]
    if normalized in ACCENTED_DOMAIN_OVERRIDES:
        return ACCENTED_DOMAIN_OVERRIDES[normalized]
    return humanize_identifier(raw)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


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


def derive_section_emphasis(layers: list[str]) -> list[str]:
    emphasis: list[str] = []
    layer_set = set(layers or [])
    if "wiki" in layer_set:
        emphasis.append("provenance")
    if "frontend" in layer_set:
        emphasis.extend(["business_goal", "conditions"])
    if "api" in layer_set or "experience_backend" in layer_set or "process_backend" in layer_set or "backend" in layer_set:
        emphasis.extend(["rules_constraints", "acceptance_criteria", "test_scenarios"])
    return merge_unique(emphasis, limit=6)


def build_phase1_domain_stats(
    modules: list[dict[str, Any]],
    flow_entries: list[dict[str, Any]],
    known_domains: dict[str, str],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}

    for module in modules:
        layers = list(module.get("layers", []) or [])
        screens = list(module.get("screens", []) or [])
        if not screens and "frontend" not in layers and "federated" not in module:
            continue
        domain = canonical_domain_name(module.get("business_domain", ""), known_domains)
        if not domain:
            continue
        current = stats.setdefault(
            normalize_text(domain),
            {
                "domain": domain,
                "aliases": set(),
                "journeys": set(),
                "flows": set(),
                "modules": set(),
                "layers": set(),
                "repos": set(),
                "frontend_present": False,
            },
        )
        current["aliases"].update(
            {
                module.get("business_domain", ""),
                module.get("module_path", ""),
                module.get("team_scope", ""),
            }
        )
        current["journeys"].update(module.get("journeys", []) or [])
        current["flows"].update(screens)
        current["modules"].add(str(module.get("module_path", "") or ""))
        current["layers"].update(layers)
        current["repos"].update(module.get("repos", []) or [])
        current["frontend_present"] = current["frontend_present"] or ("frontend" in layers)

    for flow in flow_entries:
        domain = canonical_domain_name(flow.get("domain", ""), known_domains)
        if not domain:
            continue
        current = stats.setdefault(
            normalize_text(domain),
            {
                "domain": domain,
                "aliases": set(),
                "journeys": set(),
                "flows": set(),
                "modules": set(),
                "layers": set(),
                "repos": set(),
                "frontend_present": False,
            },
        )
        current["aliases"].update({flow.get("domain", ""), flow.get("journey", ""), flow.get("flow", "")})
        current["journeys"].add(str(flow.get("journey", "") or ""))
        current["flows"].add(str(flow.get("flow", "") or ""))
        current["repos"].update(flow.get("evidence_repos", []) or [])
        current["layers"].update(flow.get("evidence_layers", []) or [])
        current["frontend_present"] = True

    return stats


def build_candidate_domain_profiles(
    *,
    existing_profiles: list[dict[str, Any]],
    figma_entries: list[dict[str, Any]],
    phase1_modules: list[dict[str, Any]],
    phase1_flow_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    known_domains = {normalize_text(entry.get("domain", "")): str(entry.get("domain", "") or "") for entry in existing_profiles if entry.get("domain")}
    for entry in figma_entries:
        if entry.get("domain"):
            known_domains.setdefault(normalize_text(entry.get("domain", "")), str(entry.get("domain", "") or ""))

    phase1_stats = build_phase1_domain_stats(phase1_modules, phase1_flow_entries, known_domains)
    existing_by_domain = {normalize_text(entry.get("domain", "")): entry for entry in existing_profiles if entry.get("domain")}
    figma_by_domain = {normalize_text(entry.get("domain", "")): entry for entry in figma_entries if entry.get("domain")}
    flow_backed_keys = {
        normalize_text(canonical_domain_name(flow.get("domain", ""), known_domains))
        for flow in phase1_flow_entries
        if canonical_domain_name(flow.get("domain", ""), known_domains)
    }

    candidate_keys = set(existing_by_domain) | set(figma_by_domain) | flow_backed_keys
    entries: list[dict[str, Any]] = []

    for domain_key in sorted(candidate_keys):
        existing = dict(existing_by_domain.get(domain_key, {}))
        figma = dict(figma_by_domain.get(domain_key, {}))
        phase1 = phase1_stats.get(domain_key, {})

        domain = existing.get("domain") or figma.get("domain") or phase1.get("domain") or ""
        if not domain:
            continue

        layers = sorted(phase1.get("layers", set()) or [])
        flows = sorted(phase1.get("flows", set()) or [])
        journeys = sorted(phase1.get("journeys", set()) or [])
        repos = sorted(phase1.get("repos", set()) or [])
        module_paths = sorted(phase1.get("modules", set()) or [])

        aliases = merge_unique(
            list(existing.get("aliases", []) or [])
            + list(figma.get("aliases", []) or [])
            + list(phase1.get("aliases", set()) or []),
            limit=12,
        )
        top_journeys = merge_unique(
            list(existing.get("top_journeys", []) or [])
            + [str(item) for item in figma.get("journeys", []) if item]
            + journeys
            + [domain],
            limit=8,
        )
        top_flows = merge_unique(
            list(existing.get("top_flows", []) or [])
            + flows,
            limit=12,
        )
        preferred_lexicon = merge_unique(
            list(existing.get("preferred_lexicon", []) or [])
            + [str(item) for item in figma.get("ux_terms", []) if item]
            + (DEFAULT_UI_LEXICON if phase1.get("frontend_present") else []),
            limit=12,
        )
        section_emphasis = merge_unique(
            list(existing.get("section_emphasis", []) or [])
            + derive_section_emphasis(layers),
            limit=6,
        )

        routing_notes = merge_unique(
            list(existing.get("routing_notes", []) or [])
            + ([str(figma.get("routing_note", "") or "")] if figma.get("routing_note") else [])
            + (
                [
                    f"Repo atlas aponta para modules {', '.join(module_paths[:4])} com layers {', '.join(layers)}."
                    if module_paths or layers
                    else ""
                ]
            )
            + (
                [
                    f"Flows observados na Fase 1: {', '.join(top_flows[:6])}."
                    if top_flows
                    else ""
                ]
            )
            + ["Confirmar sempre detalhes finais no GitHub main."],
            limit=6,
        )

        flow_count = len(top_flows)
        module_count = len(module_paths)
        computed_confidence = 0.42 + min(0.24, len(layers) * 0.08) + min(0.18, flow_count * 0.02)
        if phase1.get("frontend_present"):
            computed_confidence += 0.08
        production_confidence = round(
            max(
                float(existing.get("production_confidence", 0.0) or 0.0),
                float(figma.get("production_confidence", 0.0) or 0.0),
                min(computed_confidence, 0.92),
            ),
            4,
        )
        coverage_score = round(
            max(
                float(existing.get("coverage_score", 0.0) or 0.0),
                min(1.0, min(0.65, flow_count / 10.0) + min(0.35, module_count / 5.0)),
            ),
            4,
        )

        entries.append(
            {
                "domain": domain,
                "aliases": aliases,
                "design_file_title": existing.get("design_file_title") or figma.get("title", "") or f"Repo atlas | {domain}",
                "design_file_url": existing.get("design_file_url") or figma.get("url", "") or "",
                "top_title_patterns": list(existing.get("top_title_patterns", []) or []),
                "top_journeys": top_journeys,
                "top_flows": top_flows,
                "preferred_lexicon": preferred_lexicon,
                "section_emphasis": section_emphasis,
                "routing_notes": routing_notes,
                "production_confidence": production_confidence,
                "coverage_score": coverage_score,
                "curated_example_count": int(existing.get("curated_example_count", 0) or 0),
            }
        )

    return {
        "version": 2,
        "generated_at": utc_now_iso(),
        "source": "current_story_profiles + figma_story_map + phase1_repo_crawl",
        "motto": PHASE_2_MOTTO,
        "entries": entries,
    }


def build_candidate_flow_map(
    *,
    existing_flow_entries: list[dict[str, Any]],
    figma_entries: list[dict[str, Any]],
    phase1_flow_entries: list[dict[str, Any]],
    known_domains: dict[str, str],
) -> dict[str, Any]:
    figma_by_domain = {normalize_text(entry.get("domain", "")): entry for entry in figma_entries if entry.get("domain")}
    existing_dedupe = {str(entry.get("dedupe_key", "") or "") for entry in existing_flow_entries}
    new_entries: list[dict[str, Any]] = []

    for flow in phase1_flow_entries:
        domain = canonical_domain_name(flow.get("domain", ""), known_domains)
        if not domain:
            continue
        journey = canonical_domain_name(flow.get("journey", ""), known_domains) if normalize_text(flow.get("journey", "")) in known_domains else str(flow.get("journey", "") or domain)
        dedupe_key = "|".join(
            [
                normalize_text(domain),
                normalize_text(journey),
                normalize_text(flow.get("flow", "")),
            ]
        )
        if not dedupe_key or dedupe_key in existing_dedupe:
            continue
        figma = figma_by_domain.get(normalize_text(domain), {})
        new_entries.append(
            {
                "id": str(flow.get("id", "") or f"phase2:{dedupe_key}"),
                "dedupe_key": dedupe_key,
                "source_kind": "repo_flow_seed",
                "domain": domain,
                "journey": journey,
                "flow": str(flow.get("flow", "") or ""),
                "detail": "Flow inferido por crawl semantico de repos e promovido para candidate seed.",
                "title": str(flow.get("title", "") or ""),
                "file_key": str(figma.get("file_key", "") or ""),
                "file_title": str(figma.get("title", "") or flow.get("file_title", "") or ""),
                "url": str(figma.get("url", "") or ""),
                "site_placement": str(figma.get("site_placement", "") or flow.get("site_placement", "") or ""),
                "routing_note": str(figma.get("routing_note", "") or flow.get("routing_note", "") or ""),
                "aliases": merge_unique(
                    [str(item) for item in figma.get("aliases", []) if item]
                    + list(flow.get("aliases", []) or []),
                    limit=10,
                ),
                "ui_components": merge_unique(
                    [str(item) for item in figma.get("ux_terms", []) if item]
                    or DEFAULT_UI_LEXICON,
                    limit=8,
                ),
                "ux_terms": merge_unique(
                    list(flow.get("ux_terms", []) or []) + [str(item) for item in figma.get("ux_terms", []) if item],
                    limit=10,
                ),
                "currentness_score": max(0.62, float(figma.get("currentness_score", 0.0) or 0.0)),
                "production_confidence": round(max(0.66, float(flow.get("production_confidence", 0.0) or 0.0)), 4),
                "quality_score": 0.12,
                "source_work_item_id": "",
            }
        )

    combined = list(existing_flow_entries) + sorted(
        new_entries,
        key=lambda item: (item.get("domain", ""), item.get("journey", ""), item.get("flow", "")),
    )
    return {
        "version": 2,
        "generated_at": utc_now_iso(),
        "source": "current_story_flow_map + phase1_repo_crawl + figma_story_map",
        "motto": PHASE_2_MOTTO,
        "metadata": {
            "entry_count": len(combined),
            "added_repo_flow_seeds": len(new_entries),
        },
        "entries": combined,
    }


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


def build_candidate_policy_packs(candidate_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for profile in candidate_profiles:
        preferred_lexicon = [str(item) for item in profile.get("preferred_lexicon", []) if item]
        curated_count = int(profile.get("curated_example_count", 0) or 0)
        coverage_score = float(profile.get("coverage_score", 0.0) or 0.0)
        detail_level = "high" if curated_count >= 20 or coverage_score >= 0.85 else "medium"
        entries.append(
            {
                "id": f"policy:{normalize_text(profile.get('domain', '')).replace(' ', '_')}",
                "domain": str(profile.get("domain", "") or ""),
                "aliases": [str(item) for item in profile.get("aliases", []) if item],
                "language": "pt-PT",
                "template_version": "us-v1",
                "detail_level": detail_level,
                "canonical_title_pattern": (
                    (profile.get("top_title_patterns", []) or [DEFAULT_TITLE_PATTERN])[0] if profile.get("top_title_patterns") else DEFAULT_TITLE_PATTERN
                ),
                "mandatory_sections": list(DEFAULT_MANDATORY_SECTIONS),
                "acceptance_style": "CA-xx com comportamento observável, mensurável e testável.",
                "test_style": "Cenários Dado/Quando/Então alinhados com os critérios.",
                "preferred_lexicon": preferred_lexicon[:12],
                "terminology_overrides": terminology_overrides(preferred_lexicon),
                "top_journeys": [str(item) for item in profile.get("top_journeys", []) if item][:6],
                "top_flows": [str(item) for item in profile.get("top_flows", []) if item][:10],
                "section_emphasis": [str(item) for item in profile.get("section_emphasis", []) if item][:6],
                "notes": [str(item) for item in profile.get("routing_notes", []) if item][:6],
                "coverage_score": coverage_score,
                "production_confidence": float(profile.get("production_confidence", 0.0) or 0.0),
                "curated_example_count": curated_count,
                "design_file_title": str(profile.get("design_file_title", "") or ""),
            }
        )
    return {
        "version": 2,
        "generated_at": utc_now_iso(),
        "source": "phase2_candidate_profiles",
        "motto": PHASE_2_MOTTO,
        "entries": entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_phase_2(phase1_dir: Path, output_dir: Path) -> dict[str, int]:
    existing_profiles = load_json(DATA_DIR / "story_domain_profiles.json").get("entries", []) or []
    existing_flow_map = load_json(DATA_DIR / "story_flow_map.json").get("entries", []) or []
    figma_entries = load_json(DATA_DIR / "figma_story_map.json").get("entries", []) or []
    phase1_atlas = load_json(phase1_dir / "repo_atlas.json")
    phase1_flow_seeds = load_json(phase1_dir / "flow_seeds.json")

    candidate_profiles = build_candidate_domain_profiles(
        existing_profiles=existing_profiles,
        figma_entries=figma_entries,
        phase1_modules=phase1_atlas.get("modules", []) or [],
        phase1_flow_entries=phase1_flow_seeds.get("entries", []) or [],
    )
    known_domains = {normalize_text(entry.get("domain", "")): str(entry.get("domain", "") or "") for entry in candidate_profiles.get("entries", [])}
    candidate_flow_map = build_candidate_flow_map(
        existing_flow_entries=existing_flow_map,
        figma_entries=figma_entries,
        phase1_flow_entries=phase1_flow_seeds.get("entries", []) or [],
        known_domains=known_domains,
    )
    candidate_policy_packs = build_candidate_policy_packs(candidate_profiles.get("entries", []) or [])

    write_json(output_dir / "story_domain_profiles.phase2.json", candidate_profiles)
    write_json(output_dir / "story_flow_map.phase2.json", candidate_flow_map)
    write_json(output_dir / "story_policy_packs.phase2.json", candidate_policy_packs)
    write_json(
        output_dir / "manifest.json",
        {
            "generated_at": utc_now_iso(),
            "motto": PHASE_2_MOTTO,
            "source_phase1_dir": str(phase1_dir),
            "domain_profile_count": len(candidate_profiles.get("entries", []) or []),
            "flow_map_count": len(candidate_flow_map.get("entries", []) or []),
            "policy_pack_count": len(candidate_policy_packs.get("entries", []) or []),
        },
    )

    return {
        "domain_profile_count": len(candidate_profiles.get("entries", []) or []),
        "flow_map_count": len(candidate_flow_map.get("entries", []) or []),
        "policy_pack_count": len(candidate_policy_packs.get("entries", []) or []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 2 candidate story seeds from Phase 1 artifacts.")
    parser.add_argument("--phase1-dir", default=str(DEFAULT_PHASE1_DIR), help="Directory with Phase 1 outputs.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for candidate Phase 2 outputs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    phase1_dir = Path(args.phase1_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not phase1_dir.exists():
        raise SystemExit(f"Phase 1 directory not found: {phase1_dir}")
    result = run_phase_2(phase1_dir, output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "motto": PHASE_2_MOTTO,
                "phase1_dir": str(phase1_dir),
                "output_dir": str(output_dir),
                **result,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
