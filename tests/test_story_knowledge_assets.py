from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_create_story_knowledge_asset_from_upload_persists_and_syncs(monkeypatch):
    import story_knowledge_assets

    rows = {}
    blobs = {}
    synced = []

    async def _table_query(table_name: str, filter_str: str = "", top: int = 50):
        if table_name == "UploadIndex":
            return [
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "file-1",
                    "Filename": "site-map.md",
                    "UserSub": "jane",
                    "PreviewText": "Fluxo Pagamentos > Transferências > Recorrências com CTA primário Confirmar.",
                    "ExtractedBlobRef": "",
                    "ChunksBlobRef": "",
                }
            ]
        if table_name == "UserStoryKnowledgeAssets":
            matched = []
            for item in rows.values():
                if "RowKey eq '" in filter_str:
                    expected = filter_str.split("RowKey eq '", 1)[1].split("'", 1)[0]
                    if item.get("RowKey") != expected:
                        continue
                matched.append(dict(item))
            return matched[:top]
        return []

    async def _table_insert(table_name: str, entity: dict):
        rows[(table_name, entity["PartitionKey"], entity["RowKey"])] = dict(entity)
        return True

    async def _table_merge(table_name: str, entity: dict):
        key = (table_name, entity["PartitionKey"], entity["RowKey"])
        current = dict(rows.get(key, {}))
        current.update(entity)
        rows[key] = current

    async def _blob_upload_json(container: str, blob_name: str, payload: dict):
        ref = f"{container}/{blob_name}"
        blobs[ref] = payload
        return {"blob_ref": ref}

    async def _blob_download_json(container: str, blob_name: str):
        return blobs.get(f"{container}/{blob_name}")

    async def _upsert_story_knowledge_index_document(doc: dict):
        synced.append(doc)
        return {"ok": True, "document_id": doc["id"]}

    monkeypatch.setattr(story_knowledge_assets, "table_query", _table_query)
    monkeypatch.setattr(story_knowledge_assets, "table_insert", _table_insert)
    monkeypatch.setattr(story_knowledge_assets, "table_merge", _table_merge)
    monkeypatch.setattr(story_knowledge_assets, "blob_upload_json", _blob_upload_json)
    monkeypatch.setattr(story_knowledge_assets, "blob_download_json", _blob_download_json)
    monkeypatch.setattr(story_knowledge_assets, "upsert_story_knowledge_index_document", _upsert_story_knowledge_index_document)

    result = await story_knowledge_assets.create_story_knowledge_asset_from_upload(
        conversation_id="conv-1",
        file_id="file-1",
        imported_by="admin",
        title="Mapa do site de pagamentos",
        domain="Pagamentos",
        journey="Transferências",
        flow="Recorrências",
        note="Promovido para grounding persistente.",
    )

    assert result["created"] is True
    assert result["entry"]["status"] == "active"
    assert result["entry"]["filename"] == "site-map.md"
    assert synced
    assert synced[0]["domain"] == "Pagamentos"
    assert synced[0]["flow"] == "Recorrências"


@pytest.mark.asyncio
async def test_review_story_knowledge_asset_deactivate_deletes_from_index(monkeypatch):
    import story_knowledge_assets

    rows = {
        ("UserStoryKnowledgeAssets", "global", "upload-conv-1-file-1"): {
            "PartitionKey": "global",
            "RowKey": "upload-conv-1-file-1",
            "Status": "active",
            "Title": "Mapa do site",
            "Domain": "Pagamentos",
            "EntryBlobRef": "container/asset.json",
        }
    }

    async def _table_query(table_name: str, filter_str: str = "", top: int = 50):
        _ = top
        if table_name != "UserStoryKnowledgeAssets":
            return []
        expected = filter_str.split("RowKey eq '", 1)[1].split("'", 1)[0]
        row = rows.get((table_name, "global", expected))
        return [dict(row)] if row else []

    async def _table_merge(table_name: str, entity: dict):
        key = (table_name, entity["PartitionKey"], entity["RowKey"])
        current = dict(rows.get(key, {}))
        current.update(entity)
        rows[key] = current

    async def _delete_story_knowledge_index_document(document_id: str):
        return {"ok": True, "document_id": document_id, "deleted": True}

    monkeypatch.setattr(story_knowledge_assets, "table_query", _table_query)
    monkeypatch.setattr(story_knowledge_assets, "table_merge", _table_merge)
    monkeypatch.setattr(story_knowledge_assets, "delete_story_knowledge_index_document", _delete_story_knowledge_index_document)

    result = await story_knowledge_assets.review_story_knowledge_asset(
        asset_id="upload-conv-1-file-1",
        action="deactivate",
        reviewed_by="admin",
        note="Desativado para revisão.",
    )

    assert result["status"] == "inactive"
    assert result["search_sync"]["deleted"] is True
    stored = rows[("UserStoryKnowledgeAssets", "global", "upload-conv-1-file-1")]
    assert stored["Status"] == "inactive"


@pytest.mark.asyncio
async def test_create_story_knowledge_assets_from_bundle_is_deterministic(monkeypatch):
    import story_knowledge_assets

    rows = {}
    blobs = {}
    synced = []

    async def _table_query(table_name: str, filter_str: str = "", top: int = 50):
      if table_name != "UserStoryKnowledgeAssets":
        return []
      matched = []
      for item in rows.values():
        if "RowKey eq '" in filter_str:
          expected = filter_str.split("RowKey eq '", 1)[1].split("'", 1)[0]
          if item.get("RowKey") != expected:
            continue
        matched.append(dict(item))
      return matched[:top]

    async def _table_insert(table_name: str, entity: dict):
      rows[(table_name, entity["PartitionKey"], entity["RowKey"])] = dict(entity)
      return True

    async def _table_merge(table_name: str, entity: dict):
      key = (table_name, entity["PartitionKey"], entity["RowKey"])
      current = dict(rows.get(key, {}))
      current.update(entity)
      rows[key] = current

    async def _blob_upload_json(container: str, blob_name: str, payload: dict):
      ref = f"{container}/{blob_name}"
      blobs[ref] = payload
      return {"blob_ref": ref}

    async def _blob_download_json(container: str, blob_name: str):
      return blobs.get(f"{container}/{blob_name}")

    async def _upsert_story_knowledge_index_document(doc: dict):
      synced.append(doc)
      return {"ok": True, "document_id": doc["id"]}

    monkeypatch.setattr(story_knowledge_assets, "table_query", _table_query)
    monkeypatch.setattr(story_knowledge_assets, "table_insert", _table_insert)
    monkeypatch.setattr(story_knowledge_assets, "table_merge", _table_merge)
    monkeypatch.setattr(story_knowledge_assets, "blob_upload_json", _blob_upload_json)
    monkeypatch.setattr(story_knowledge_assets, "blob_download_json", _blob_download_json)
    monkeypatch.setattr(story_knowledge_assets, "upsert_story_knowledge_index_document", _upsert_story_knowledge_index_document)

    payload = [
        {
            "asset_key": "pagamentos-sitemap",
            "title": "Sitemap Pagamentos",
            "content": "Fluxo pagamentos > transferências > recorrências.",
            "domain": "Pagamentos",
            "journey": "Transferências",
        },
        {
            "asset_key": "pagamentos-glossary",
            "title": "Glossário Pagamentos",
            "content": "Primary CTA, card, modal, dropdown.",
            "domain": "Pagamentos",
            "journey": "Glossário",
        },
    ]

    result = await story_knowledge_assets.create_story_knowledge_assets_from_bundle(
        entries=payload,
        imported_by="admin",
    )

    assert result["created_count"] == 2
    assert len(result["items"]) == 2
    assert result["items"][0]["asset_id"].startswith("bundle-")
    assert len(synced) == 2

    again = await story_knowledge_assets.create_story_knowledge_assets_from_bundle(
        entries=[payload[0]],
        imported_by="admin",
    )

    assert again["created_count"] == 1
    assert again["items"][0]["asset_id"] == result["items"][0]["asset_id"]
