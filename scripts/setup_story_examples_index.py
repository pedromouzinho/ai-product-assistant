#!/usr/bin/env python3
"""Create or update the dedicated Azure AI Search index for curated user story examples."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import API_VERSION_SEARCH, EMBEDDING_VECTOR_DIMENSIONS, SEARCH_KEY, SEARCH_SERVICE, STORY_EXAMPLES_INDEX
from tools_knowledge import get_embedding


def _index_url() -> str:
    return f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{STORY_EXAMPLES_INDEX}?api-version={API_VERSION_SEARCH}"


async def _build_index_payload() -> dict:
    probe = await get_embedding("story example index probe")
    dimensions = len(probe or []) or max(1, int(EMBEDDING_VECTOR_DIMENSIONS or 1536))
    return {
        "name": STORY_EXAMPLES_INDEX,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True, "sortable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
            {"name": "search_text", "type": "Edm.String", "searchable": True},
            {"name": "description_text", "type": "Edm.String", "searchable": True},
            {"name": "acceptance_text", "type": "Edm.String", "searchable": True},
            {"name": "sections_json", "type": "Edm.String", "searchable": False},
            {"name": "ux_terms", "type": "Collection(Edm.String)", "searchable": True, "filterable": True, "facetable": True},
            {"name": "tags", "type": "Collection(Edm.String)", "searchable": True, "filterable": True, "facetable": True},
            {"name": "workitem_refs", "type": "Collection(Edm.String)", "searchable": True, "filterable": True, "facetable": True},
            {"name": "title_pattern", "type": "Edm.String", "searchable": True},
            {"name": "domain", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True, "sortable": True},
            {"name": "journey", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
            {"name": "flow", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
            {"name": "detail", "type": "Edm.String", "searchable": True},
            {"name": "area_path", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
            {"name": "url", "type": "Edm.String", "searchable": False},
            {"name": "status", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True},
            {"name": "visibility", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True},
            {"name": "source_kind", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True},
            {"name": "source_draft_id", "type": "Edm.String", "searchable": False, "filterable": True, "sortable": True},
            {"name": "source_user_sub", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True},
            {"name": "promoted_by", "type": "Edm.String", "searchable": False, "filterable": True},
            {"name": "quality_score", "type": "Edm.Double", "filterable": True, "sortable": True},
            {"name": "updated_at", "type": "Edm.String", "filterable": True, "sortable": True},
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "dimensions": dimensions,
                "vectorSearchProfile": "story-example-profile",
            },
        ],
        "vectorSearch": {
            "algorithms": [{"name": "story-example-hnsw", "kind": "hnsw"}],
            "profiles": [{"name": "story-example-profile", "algorithm": "story-example-hnsw"}],
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create/update the Azure AI Search index for curated story examples.")
    parser.add_argument("--dry-run", action="store_true", help="Only print the payload.")
    args = parser.parse_args()

    if not SEARCH_SERVICE or not SEARCH_KEY or not STORY_EXAMPLES_INDEX:
        raise RuntimeError("SEARCH_SERVICE, SEARCH_KEY e STORY_EXAMPLES_INDEX são obrigatórios.")

    payload = await _build_index_payload()
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    headers = {"api-key": SEARCH_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.put(_index_url(), headers=headers, json=payload)
        print(json.dumps({"status_code": resp.status_code, "body": resp.json() if resp.content else {}}, ensure_ascii=False, indent=2))
        resp.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
