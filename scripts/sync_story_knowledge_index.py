#!/usr/bin/env python3
"""Sync story knowledge documents into the dedicated Azure AI Search index."""

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

import storage
from story_knowledge_index import sync_story_knowledge_index


async def _bootstrap_storage_http_client() -> None:
    if storage.http_client is None or storage.http_client.is_closed:
        storage.http_client = httpx.AsyncClient(timeout=60)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sync story knowledge into Azure AI Search.")
    parser.add_argument("--max-docs", type=int, default=1500, help="Maximum number of OMNI docs to scan.")
    parser.add_argument("--batch-size", type=int, default=150, help="Batch size for source fetch/indexing.")
    parser.add_argument("--no-state-update", action="store_true", help="Do not write sync metadata to storage.")
    args = parser.parse_args()

    await _bootstrap_storage_http_client()
    try:
        result = await sync_story_knowledge_index(
            max_docs=max(1, int(args.max_docs or 1500)),
            batch_size=max(1, int(args.batch_size or 150)),
            update_state=not args.no_state_update,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if storage.http_client and not storage.http_client.is_closed:
            await storage.http_client.aclose()
            storage.http_client = None
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
