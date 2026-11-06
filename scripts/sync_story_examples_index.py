#!/usr/bin/env python3
"""Resync approved user story curated entries into Azure AI Search."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import storage
from user_story_lane import sync_user_story_examples_search_index


async def _bootstrap_storage_http_client() -> None:
    import httpx

    if storage.http_client is None or storage.http_client.is_closed:
        storage.http_client = httpx.AsyncClient(timeout=60)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sync curated user story examples into Azure AI Search.")
    parser.add_argument("--draft-id", default="", help="Optional single draft id to sync.")
    parser.add_argument("--top", type=int, default=200, help="How many curated rows to scan when draft-id is omitted.")
    args = parser.parse_args()

    await _bootstrap_storage_http_client()
    try:
        summary = await sync_user_story_examples_search_index(
            draft_id=str(args.draft_id or "").strip(),
            top=max(1, int(args.top or 200)),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        if storage.http_client and not storage.http_client.is_closed:
            await storage.http_client.aclose()
            storage.http_client = None
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
