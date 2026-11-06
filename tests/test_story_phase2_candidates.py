from __future__ import annotations

import json
from pathlib import Path

from scripts import build_story_phase2_candidates as phase2


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_candidate_domain_profiles_adds_phase1_product_domains():
    existing_profiles = [
        {
            "domain": "Pagamentos",
            "aliases": ["Payments"],
            "design_file_title": "Pagamentos",
            "top_journeys": ["Pagamentos"],
            "top_flows": ["servicePayments"],
            "preferred_lexicon": ["CTA"],
            "section_emphasis": ["business_goal"],
            "routing_notes": ["Usar shell de pagamentos."],
            "production_confidence": 0.71,
            "coverage_score": 0.48,
            "curated_example_count": 4,
        }
    ]
    figma_entries = [
        {
            "domain": "Pagamentos",
            "title": "Pagamentos Figma",
            "ux_terms": ["Primary CTA", "Sidebar"],
            "routing_note": "Confirmar variantes com design.",
            "production_confidence": 0.77,
        }
    ]
    phase1_modules = [
        {
            "business_domain": "Transferencias",
            "module_path": "Transfers",
            "team_scope": "ACME/Transfers",
            "layers": ["frontend", "api"],
            "screens": ["spinTransfers", "scheduledTransfers"],
            "journeys": ["Transferências"],
            "repos": ["Acme.Portal.Transfers.Frontend", "Acme.Portal.Transfers.Api"],
            "federated": True,
        }
    ]
    phase1_flow_entries = [
        {
            "domain": "Transferencias",
            "journey": "Transferências",
            "flow": "spinTransfers",
            "evidence_repos": ["Acme.Portal.Transfers.Frontend"],
            "evidence_layers": ["frontend", "api"],
            "production_confidence": 0.83,
        }
    ]

    payload = phase2.build_candidate_domain_profiles(
        existing_profiles=existing_profiles,
        figma_entries=figma_entries,
        phase1_modules=phase1_modules,
        phase1_flow_entries=phase1_flow_entries,
    )

    entries = {entry["domain"]: entry for entry in payload["entries"]}

    assert "Pagamentos" in entries
    assert "Transferências" in entries
    assert "CTA" in entries["Transferências"]["preferred_lexicon"]
    assert "Input" in entries["Transferências"]["preferred_lexicon"]
    assert any("GitHub main" in note for note in entries["Transferências"]["routing_notes"])
    assert "spinTransfers" in entries["Transferências"]["top_flows"]


def test_run_phase_2_writes_candidate_files_with_new_repo_flows(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    phase1_dir = tmp_path / "phase1"
    output_dir = tmp_path / "phase2"

    _write_json(
        data_dir / "story_domain_profiles.json",
        {
            "entries": [
                {
                    "domain": "Pagamentos",
                    "aliases": ["Payments"],
                    "design_file_title": "Pagamentos",
                    "design_file_url": "",
                    "top_title_patterns": [],
                    "top_journeys": ["Pagamentos"],
                    "top_flows": ["servicePayments"],
                    "preferred_lexicon": ["CTA"],
                    "section_emphasis": ["business_goal"],
                    "routing_notes": ["Confirmar componentes partilhados."],
                    "production_confidence": 0.7,
                    "coverage_score": 0.4,
                    "curated_example_count": 3,
                }
            ]
        },
    )
    _write_json(
        data_dir / "story_flow_map.json",
        {
            "entries": [
                {
                    "id": "curated:pagamentos:servicepayments",
                    "dedupe_key": "pagamentos|pagamentos|servicepayments",
                    "domain": "Pagamentos",
                    "journey": "Pagamentos",
                    "flow": "servicePayments",
                }
            ]
        },
    )
    _write_json(
        data_dir / "figma_story_map.json",
        {
            "entries": [
                {
                    "domain": "Pagamentos",
                    "title": "Pagamentos Board",
                    "url": "https://example.test/figma",
                    "routing_note": "Confirmar alinhamento visual com design.",
                    "ux_terms": ["Primary CTA"],
                    "currentness_score": 0.93,
                }
            ]
        },
    )
    _write_json(
        phase1_dir / "repo_atlas.json",
        {
            "modules": [
                {
                    "business_domain": "Transferencias",
                    "module_path": "Transfers",
                    "team_scope": "ACME/Transfers",
                    "layers": ["frontend", "api"],
                    "screens": ["spinTransfers"],
                    "journeys": ["Transferências"],
                    "repos": ["Acme.Portal.Transfers.Frontend", "Acme.Portal.Transfers.Api"],
                    "federated": True,
                }
            ]
        },
    )
    _write_json(
        phase1_dir / "flow_seeds.json",
        {
            "entries": [
                {
                    "id": "phase1:transferencias|transferencias|spintransfers",
                    "domain": "Transferencias",
                    "journey": "Transferências",
                    "flow": "spinTransfers",
                    "title": "Transferências | spinTransfers",
                    "aliases": ["spin transfers"],
                    "ux_terms": ["CTA"],
                    "production_confidence": 0.81,
                    "evidence_repos": ["Acme.Portal.Transfers.Frontend"],
                    "evidence_layers": ["frontend", "api"],
                    "site_placement": "workspace",
                    "routing_note": "Ir ao módulo de transferências.",
                }
            ]
        },
    )

    monkeypatch.setattr(phase2, "DATA_DIR", data_dir)

    counts = phase2.run_phase_2(phase1_dir, output_dir)

    assert counts["domain_profile_count"] == 2
    assert counts["flow_map_count"] == 2
    assert counts["policy_pack_count"] == 2

    flow_map = json.loads((output_dir / "story_flow_map.phase2.json").read_text(encoding="utf-8"))
    new_entry = next(entry for entry in flow_map["entries"] if entry["flow"] == "spinTransfers")
    assert new_entry["domain"] == "Transferências"
    assert new_entry["source_kind"] == "repo_flow_seed"
    assert new_entry["production_confidence"] >= 0.81

    policy_packs = json.loads((output_dir / "story_policy_packs.phase2.json").read_text(encoding="utf-8"))
    transfers_pack = next(entry for entry in policy_packs["entries"] if entry["domain"] == "Transferências")
    assert "acceptance_criteria" in transfers_pack["mandatory_sections"]
    assert transfers_pack["detail_level"] == "medium"
