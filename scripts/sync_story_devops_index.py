#!/usr/bin/env python3
"""Sync DevOps backlog items into the dedicated Azure AI Search index for user stories."""

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
from story_devops_index import sync_story_devops_index


async def _bootstrap_storage_http_client() -> None:
    if storage.http_client is None or storage.http_client.is_closed:
        storage.http_client = httpx.AsyncClient(timeout=60)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the story backlog DevOps index.")
    parser.add_argument("--since-iso", default="", help="Optional ISO timestamp to sync from.")
    parser.add_argument("--since-days", type=int, default=30, help="Lookback window in days when no cursor exists.")
    parser.add_argument("--top", type=int, default=1200, help="Maximum number of work items to scan.")
    parser.add_argument("--no-cursor-update", action="store_true", help="Do not update the sync cursor.")
    args = parser.parse_args()

    await _bootstrap_storage_http_client()
    try:
        summary = await sync_story_devops_index(
            since_iso=str(args.since_iso or "").strip(),
            since_days=max(1, int(args.since_days or 30)),
            top=max(1, int(args.top or 1200)),
            update_cursor=not args.no_cursor_update,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        if storage.http_client and not storage.http_client.is_closed:
            await storage.http_client.aclose()
            storage.http_client = None
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
