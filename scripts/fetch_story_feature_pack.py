#!/usr/bin/env python3
"""Fetch a DevOps feature and its child user stories into a persistent feature pack."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DEVOPS_ORG, DEVOPS_PROJECT
from story_curated_corpus import build_curated_story_entry

FIELDS = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.AreaPath",
    "System.AssignedTo",
    "System.CreatedBy",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.Tags",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _identity_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName", "") or "").strip()
    return str(value or "").strip()


def _batch_row(item: dict, *, parent_id: str = "") -> dict:
    fields = item.get("fields", {}) if isinstance(item, dict) else {}
    return {
        "ID": str(item.get("id", "") or "").strip(),
        "Work Item Type": str(fields.get("System.WorkItemType", "") or "").strip(),
        "Title": str(fields.get("System.Title", "") or "").strip(),
        "Assigned To": _identity_name(fields.get("System.AssignedTo")),
        "State": str(fields.get("System.State", "") or "").strip(),
        "Acceptance Criteria": str(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""),
        "Description": str(fields.get("System.Description", "") or ""),
        "Area Path": str(fields.get("System.AreaPath", "") or "").strip(),
        "Created By": _identity_name(fields.get("System.CreatedBy")),
        "Tags": str(fields.get("System.Tags", "") or "").strip(),
        "Parent": str(parent_id or "").strip(),
    }


async def _run_wiql(client: httpx.AsyncClient, *, org: str, project: str, headers: dict, query: str) -> dict:
    response = await client.post(
        f"https://dev.azure.com/{org}/{project}/_apis/wit/wiql?api-version=7.1",
        headers=headers,
        json={"query": query},
    )
    response.raise_for_status()
    return response.json()


async def _fetch_batch(
    client: httpx.AsyncClient,
    *,
    org: str,
    project: str,
    headers: dict,
    ids: list[int],
) -> list[dict]:
    if not ids:
        return []
    response = await client.post(
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch?api-version=7.1",
        headers=headers,
        json={"ids": ids, "fields": FIELDS},
    )
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("value", []) or [])


def _aggregate_feature_pack(
    *,
    feature_row: dict,
    feature_entry: dict,
    story_entries: list[dict],
    figma_url: str,
    source_area_path: str,
) -> dict:
    flows = Counter(str(entry.get("flow", "") or "").strip() for entry in story_entries if str(entry.get("flow", "") or "").strip())
    journeys = Counter(str(entry.get("journey", "") or "").strip() for entry in story_entries if str(entry.get("journey", "") or "").strip())
    ux_terms = Counter(term for entry in story_entries for term in list(entry.get("ux_terms", []) or []))
    sections = Counter(section for entry in story_entries for section in list((entry.get("sections", {}) or {}).keys()))
    creators = Counter(str(entry.get("created_by", "") or "").strip() for entry in story_entries if str(entry.get("created_by", "") or "").strip())
    title_patterns = Counter(str(entry.get("title_pattern", "") or "").strip() for entry in story_entries if str(entry.get("title_pattern", "") or "").strip())

    top_titles = [str(entry.get("title", "") or "") for entry in story_entries[:8]]
    aliases = []
    for candidate in [
        feature_entry.get("title", ""),
        feature_entry.get("domain", ""),
        feature_entry.get("journey", ""),
        feature_entry.get("flow", ""),
        *[name for name, _ in journeys.most_common(5)],
        *[name for name, _ in flows.most_common(8)],
    ]:
        text = str(candidate or "").strip()
        if text and text not in aliases:
            aliases.append(text)

    stories = []
    for entry in story_entries:
        sections_map = entry.get("sections", {}) if isinstance(entry.get("sections"), dict) else {}
        snippet = (
            str(sections_map.get("comportamento", "") or "")
            or str(sections_map.get("proveniência", "") or "")
            or str(entry.get("acceptance_text", "") or "")
            or str(entry.get("description_text", "") or "")
        )
        stories.append(
            {
                "id": str(entry.get("id", "") or ""),
                "title": str(entry.get("title", "") or ""),
                "snippet": snippet[:320],
                "url": str(entry.get("url", "") or ""),
                "ux_terms": list(entry.get("ux_terms", []) or [])[:8],
                "workitem_refs": list(entry.get("workitem_refs", []) or [])[:10],
                "title_pattern": str(entry.get("title_pattern", "") or ""),
                "origin": "devops_feature_pack",
            }
        )

    area_path = str(feature_row.get("Area Path", "") or source_area_path or "").strip()
    story_count = len(story_entries)
    feature_title = str(feature_entry.get("title", "") or feature_row.get("Title", "") or "").strip()
    domain = str(feature_entry.get("domain", "") or "").strip()
    journey = str(feature_entry.get("journey", "") or "").strip()

    notes = [
        f"Pack extraído diretamente do Azure DevOps para a feature {feature_row.get('ID')} com {story_count} user stories filhas.",
        f"Fluxos mais frequentes: {', '.join(name for name, _ in flows.most_common(4)) or 'n/a'}.",
        f"Lexicon dominante: {', '.join(term for term, _ in ux_terms.most_common(6)) or 'n/a'}.",
    ]

    return {
        "feature_id": str(feature_row.get("ID", "") or "").strip(),
        "feature_title": feature_title,
        "feature_url": str(feature_entry.get("url", "") or ""),
        "area_path": area_path,
        "domain": domain,
        "journey": journey,
        "flow": str(feature_entry.get("flow", "") or "").strip(),
        "aliases": aliases[:16],
        "top_ux_terms": [term for term, _ in ux_terms.most_common(12)],
        "top_titles": top_titles,
        "top_flows": [name for name, _ in flows.most_common(8)],
        "top_journeys": [name for name, _ in journeys.most_common(6)],
        "mandatory_sections": [name for name, count in sections.most_common(8) if count >= max(2, story_count // 3)],
        "canonical_title_pattern": title_patterns.most_common(1)[0][0] if title_patterns else "",
        "story_count": story_count,
        "created_by_top": [name for name, _ in creators.most_common(6)],
        "notes": notes,
        "figma_url": str(figma_url or "").strip(),
        "stories": stories,
        "source": "azure_devops_feature_pack",
        "source_generated_at": _utc_now_iso(),
    }


async def _fetch_feature_pack(
    *,
    pat: str,
    feature_id: int,
    area_path: str,
    figma_url: str,
    org: str,
    project: str,
) -> dict:
    headers = _headers(pat)
    safe_project = str(project or "").replace("'", "''")
    async with httpx.AsyncClient(timeout=60) as client:
        feature_items = await _fetch_batch(client, org=org, project=project, headers=headers, ids=[feature_id])
        if not feature_items:
            raise RuntimeError(f"Feature {feature_id} não encontrada no DevOps.")
        feature_item = feature_items[0]
        feature_row = _batch_row(feature_item)
        feature_entry = build_curated_story_entry(feature_row)

        wiql = (
            "SELECT [System.Id] FROM WorkItemLinks "
            f"WHERE ([Source].[System.Id] = {int(feature_id)}) "
            "AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') "
            "AND ([Target].[System.WorkItemType] = 'User Story') "
            f"AND ([Target].[System.TeamProject] = '{safe_project}') "
            "MODE (MustContain)"
        )
        relations = await _run_wiql(client, org=org, project=project, headers=headers, query=wiql)
        child_ids = []
        for relation in list(relations.get("workItemRelations", []) or []):
            target = relation.get("target", {}) if isinstance(relation, dict) else {}
            target_id = target.get("id")
            if isinstance(target_id, int) and target_id != feature_id and target_id not in child_ids:
                child_ids.append(target_id)

        child_items = await _fetch_batch(client, org=org, project=project, headers=headers, ids=child_ids)
        story_rows = []
        for item in child_items:
            row = _batch_row(item, parent_id=str(feature_id))
            if str(row.get("Work Item Type", "") or "").strip().lower() != "user story":
                continue
            if area_path and str(row.get("Area Path", "") or "").strip() != str(area_path).strip():
                continue
            story_rows.append(row)

        story_entries = sorted(
            [build_curated_story_entry(row) for row in story_rows],
            key=lambda item: str(item.get("title", "") or ""),
        )
        return _aggregate_feature_pack(
            feature_row=feature_row,
            feature_entry=feature_entry,
            story_entries=story_entries,
            figma_url=figma_url,
            source_area_path=area_path,
        )


def _upsert_entry(payload: dict, entry: dict) -> dict:
    entries = list(payload.get("entries", []) or [])
    target_id = str(entry.get("feature_id", "") or "").strip()
    filtered = [item for item in entries if str(item.get("feature_id", "") or "").strip() != target_id]
    filtered.append(entry)
    filtered.sort(key=lambda item: str(item.get("feature_id", "") or ""))
    return {
        "generated_at": _utc_now_iso(),
        "source": "azure_devops_feature_pack",
        "entries": filtered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-id", type=int, required=True)
    parser.add_argument("--area-path", default="")
    parser.add_argument("--figma-url", default="")
    parser.add_argument("--org", default=DEVOPS_ORG)
    parser.add_argument("--project", default=DEVOPS_PROJECT)
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent.parent / "data" / "story_feature_packs.json"),
    )
    args = parser.parse_args()

    pat = str(os.getenv("DEVOPS_PAT", "") or "").strip()
    if not pat:
        raise SystemExit("DEVOPS_PAT em falta no ambiente.")

    entry = asyncio.run(
        _fetch_feature_pack(
            pat=pat,
            feature_id=args.feature_id,
            area_path=args.area_path,
            figma_url=args.figma_url,
            org=args.org,
            project=args.project,
        )
    )

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": _utc_now_iso(), "source": "azure_devops_feature_pack", "entries": []}
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"generated_at": _utc_now_iso(), "source": "azure_devops_feature_pack", "entries": []}
    updated = _upsert_entry(payload, entry)
    output_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "feature_id": entry.get("feature_id"), "story_count": entry.get("story_count"), "output": str(output_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
