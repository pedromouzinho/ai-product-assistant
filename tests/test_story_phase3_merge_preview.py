from __future__ import annotations

import json
from pathlib import Path

from scripts import build_story_phase3_merge_preview as phase3


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_profile_merge_preview_promotes_high_signal_and_holds_low_signal():
    existing_profiles = [
        {
            "domain": "Pagamentos",
            "aliases": ["Payments"],
            "design_file_title": "Pagamentos III",
            "design_file_url": "",
            "top_title_patterns": [],
            "top_journeys": ["Pagamentos"],
            "top_flows": ["servicePayments"],
            "preferred_lexicon": ["CTA"],
            "section_emphasis": ["business_goal"],
            "routing_notes": ["Preferir handoff atual."],
            "production_confidence": 0.82,
            "coverage_score": 0.44,
            "curated_example_count": 5,
        }
    ]
    candidate_profiles = [
        {
            "domain": "Pagamentos",
            "aliases": ["ACME Payments"],
            "design_file_title": "Repo atlas | Pagamentos",
            "design_file_url": "",
            "top_title_patterns": [],
            "top_journeys": ["Pagar Serviços"],
            "top_flows": ["servicePayments", "socialSecurityPayments"],
            "preferred_lexicon": ["CTA", "Input"],
            "section_emphasis": ["conditions"],
            "routing_notes": ["Repo atlas aponta para Payments."],
            "production_confidence": 0.9,
            "coverage_score": 0.85,
            "curated_example_count": 0,
        },
        {
            "domain": "Documentos",
            "aliases": ["Documents"],
            "design_file_title": "Repo atlas | Documentos",
            "design_file_url": "",
            "top_title_patterns": [],
            "top_journeys": ["Documentos"],
            "top_flows": ["digitalDocuments", "fileUpload", "fileConsult", "digitalDocumentsList"],
            "preferred_lexicon": ["CTA", "Card"],
            "section_emphasis": ["business_goal"],
            "routing_notes": ["Repo atlas aponta para Documents."],
            "production_confidence": 0.86,
            "coverage_score": 0.95,
            "curated_example_count": 0,
        },
        {
            "domain": "Notificações",
            "aliases": ["Notifications"],
            "design_file_title": "Repo atlas | Notificações",
            "design_file_url": "",
            "top_title_patterns": [],
            "top_journeys": ["Notificações"],
            "top_flows": ["notifications"],
            "preferred_lexicon": ["CTA"],
            "section_emphasis": ["business_goal"],
            "routing_notes": ["Repo atlas aponta para Notifications."],
            "production_confidence": 0.6,
            "coverage_score": 0.1,
            "curated_example_count": 0,
        },
    ]

    merged, report = phase3.build_profile_merge_preview(existing_profiles, candidate_profiles)

    entries = {entry["domain"]: entry for entry in merged["entries"]}
    assert "Pagamentos" in entries
    assert "Documentos" in entries
    assert "Notificações" not in entries
    assert "socialSecurityPayments" in entries["Pagamentos"]["top_flows"]
    assert "Input" in entries["Pagamentos"]["preferred_lexicon"]
    assert report["promoted_new_domains"] == ["Documentos"]
    assert report["review_domains"] == ["Notificações"]
    pagamentos_action = next(item for item in report["domain_actions"] if item["domain"] == "Pagamentos")
    assert pagamentos_action["action"] == "augment"


def test_run_phase_3_writes_preview_and_filters_review_domains(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    phase2_dir = tmp_path / "phase2"
    output_dir = tmp_path / "phase3"

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
                    "routing_notes": ["Preferir perfil atual."],
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
        data_dir / "story_policy_packs.json",
        {
            "entries": [
                {
                    "id": "policy:pagamentos",
                    "domain": "Pagamentos",
                    "aliases": ["Payments"],
                    "language": "pt-PT",
                    "template_version": "us-v1",
                    "detail_level": "medium",
                    "canonical_title_pattern": "[Prefix] | [Domínio]",
                    "mandatory_sections": ["acceptance_criteria"],
                    "acceptance_style": "Atual.",
                    "test_style": "Atual.",
                    "preferred_lexicon": ["CTA"],
                    "terminology_overrides": [],
                    "top_journeys": ["Pagamentos"],
                    "top_flows": ["servicePayments"],
                    "section_emphasis": ["business_goal"],
                    "notes": ["Atual."],
                    "coverage_score": 0.4,
                    "production_confidence": 0.7,
                    "curated_example_count": 3,
                    "design_file_title": "Pagamentos",
                }
            ]
        },
    )
    _write_json(
        phase2_dir / "story_domain_profiles.phase2.json",
        {
            "entries": [
                {
                    "domain": "Pagamentos",
                    "aliases": ["ACME Payments"],
                    "design_file_title": "Repo atlas | Pagamentos",
                    "design_file_url": "",
                    "top_title_patterns": [],
                    "top_journeys": ["Pagar Serviços"],
                    "top_flows": ["servicePayments", "socialSecurityPayments"],
                    "preferred_lexicon": ["CTA", "Input"],
                    "section_emphasis": ["conditions"],
                    "routing_notes": ["Repo atlas aponta para Payments."],
                    "production_confidence": 0.9,
                    "coverage_score": 0.85,
                    "curated_example_count": 0,
                },
                {
                    "domain": "Documentos",
                    "aliases": ["Documents"],
                    "design_file_title": "Repo atlas | Documentos",
                    "design_file_url": "",
                    "top_title_patterns": [],
                    "top_journeys": ["Documentos"],
                    "top_flows": ["digitalDocuments", "fileUpload", "fileConsult", "digitalDocumentsList"],
                    "preferred_lexicon": ["CTA", "Card"],
                    "section_emphasis": ["business_goal"],
                    "routing_notes": ["Repo atlas aponta para Documents."],
                    "production_confidence": 0.86,
                    "coverage_score": 0.95,
                    "curated_example_count": 0,
                },
                {
                    "domain": "Notificações",
                    "aliases": ["Notifications"],
                    "design_file_title": "Repo atlas | Notificações",
                    "design_file_url": "",
                    "top_title_patterns": [],
                    "top_journeys": ["Notificações"],
                    "top_flows": ["notifications"],
                    "preferred_lexicon": ["CTA"],
                    "section_emphasis": ["business_goal"],
                    "routing_notes": ["Repo atlas aponta para Notifications."],
                    "production_confidence": 0.6,
                    "coverage_score": 0.1,
                    "curated_example_count": 0,
                },
            ]
        },
    )
    _write_json(
        phase2_dir / "story_flow_map.phase2.json",
        {
            "entries": [
                {
                    "id": "phase2:pagamentos|pagamentos|socialsecuritypayments",
                    "dedupe_key": "pagamentos|pagamentos|socialsecuritypayments",
                    "source_kind": "repo_flow_seed",
                    "domain": "Pagamentos",
                    "journey": "Pagamentos",
                    "flow": "socialSecurityPayments",
                },
                {
                    "id": "phase2:documentos|documentos|digitaldocuments",
                    "dedupe_key": "documentos|documentos|digitaldocuments",
                    "source_kind": "repo_flow_seed",
                    "domain": "Documentos",
                    "journey": "Documentos",
                    "flow": "digitalDocuments",
                },
                {
                    "id": "phase2:notificacoes|notificacoes|notifications",
                    "dedupe_key": "notificacoes|notificacoes|notifications",
                    "source_kind": "repo_flow_seed",
                    "domain": "Notificações",
                    "journey": "Notificações",
                    "flow": "notifications",
                },
            ]
        },
    )

    monkeypatch.setattr(phase3, "DATA_DIR", data_dir)

    counts = phase3.run_phase_3(phase2_dir, output_dir)

    assert counts["merged_domain_profile_count"] == 2
    assert counts["merged_flow_map_count"] == 3
    assert counts["merged_policy_pack_count"] == 2
    assert counts["promoted_new_domain_count"] == 1
    assert counts["review_domain_count"] == 1

    flow_map = json.loads((output_dir / "story_flow_map.phase3.preview.json").read_text(encoding="utf-8"))
    flow_domains = {entry["domain"] for entry in flow_map["entries"]}
    assert "Documentos" in flow_domains
    assert "Notificações" not in flow_domains

    report = json.loads((output_dir / "merge_report.json").read_text(encoding="utf-8"))
    assert report["profile_report"]["promoted_new_domains"] == ["Documentos"]
    assert report["profile_report"]["review_domains"] == ["Notificações"]
