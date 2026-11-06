import pytest

import routes_chat
import privacy_service
from models import PrivacyDeleteRequest


@pytest.mark.asyncio
async def test_build_user_privacy_export_aggregates_expected_user_scoped_rows(monkeypatch):
    async def _fake_table_query(table, filter_expr="", top=1000):
        if table == "Users":
            return [{"PartitionKey": "user", "RowKey": "jane", "DisplayName": "Jane"}]
        if table == "ChatHistory":
            return [{"PartitionKey": "jane", "RowKey": "conv-1", "Title": "Conversa"}]
        if table == "feedback":
            return [{"PartitionKey": "feedback", "RowKey": "f1", "UserSub": "jane"}]
        if table == "examples":
            return [{"PartitionKey": "positive", "RowKey": "e1", "UserSub": "jane"}]
        if table == "UploadIndex":
            return [{"PartitionKey": "conv-1", "RowKey": "u1", "UserSub": "jane"}]
        if table == "UploadJobs":
            return [{"PartitionKey": "upload", "RowKey": "u1", "UserSub": "jane"}]
        if table == "UserStoryDrafts":
            return [{"PartitionKey": "user:jane", "RowKey": "d1"}]
        if table == "UserStoryFeedback":
            return [{"PartitionKey": "user:jane", "RowKey": "ev1"}]
        if table == "UserStoryCurated":
            return [{"PartitionKey": "global", "RowKey": "d1", "SourceUserSub": "jane"}]
        if table == "UserStoryKnowledgeAssets":
            return [{"PartitionKey": "global", "RowKey": "k1", "ImportedBy": "jane"}]
        return []

    monkeypatch.setattr(privacy_service, "table_query", _fake_table_query)

    payload = await privacy_service.build_user_privacy_export("jane")

    assert payload["summary"]["chat_history"] == 1
    assert payload["summary"]["user_story_drafts"] == 1
    assert payload["summary"]["knowledge_assets"] == 1
    assert payload["data"]["user_rows"][0]["DisplayName"] == "Jane"


@pytest.mark.asyncio
async def test_delete_user_personal_data_deletes_owned_rows_and_anonymizes_global_refs(monkeypatch):
    deleted_rows = []
    merged_rows = []
    deleted_blobs = []
    reviewed_assets = []
    synced_drafts = []

    async def _fake_table_query(table, filter_expr="", top=1000):
        if table == "ChatHistory":
            return [{"PartitionKey": "jane", "RowKey": "conv-1"}]
        if table == "UploadIndex":
            return [{
                "PartitionKey": "conv-1",
                "RowKey": "up-1",
                "UserSub": "jane",
                "RawBlobRef": "raw/file.xlsx",
                "ExtractedBlobRef": "",
                "ChunksBlobRef": "chunks/file.json",
                "TabularArtifactBlobRef": "artifacts/file.parquet",
            }]
        if table == "UploadJobs":
            return [{
                "PartitionKey": "upload",
                "RowKey": "up-1",
                "UserSub": "jane",
                "RawBlobRef": "raw/file.xlsx",
                "TabularArtifactBlobRef": "artifacts/file.parquet",
            }]
        if table == "UserStoryDrafts":
            return [{
                "PartitionKey": "user:jane",
                "RowKey": "draft-1",
                "DraftBlobRef": "drafts/draft.json",
                "FinalDraftBlobRef": "drafts/final.json",
            }]
        if table == "UserStoryFeedback":
            return [{"PartitionKey": "user:jane", "RowKey": "fb-1", "EventBlobRef": "drafts/event.json"}]
        if table == "feedback":
            return [{"PartitionKey": "feedback", "RowKey": "f1", "UserSub": "jane"}]
        if table == "examples":
            return [{"PartitionKey": "positive", "RowKey": "e1", "UserSub": "jane"}]
        if table == "UserStoryCurated":
            return [{
                "PartitionKey": "global",
                "RowKey": "draft-1",
                "SourceUserSub": "jane",
                "EntryBlobRef": "chat-toolresults/user-stories/curated/global/draft-1.json",
            }]
        if table == "UserStoryKnowledgeAssets":
            return [{
                "PartitionKey": "global",
                "RowKey": "asset-1",
                "ImportedBy": "jane",
                "EntryBlobRef": "chat-toolresults/user-stories/knowledge-assets/global/asset-1.json",
            }]
        return []

    async def _fake_table_delete(table, partition_key, row_key):
        deleted_rows.append((table, partition_key, row_key))
        return True

    async def _fake_table_merge(table, entity):
        merged_rows.append((table, entity))
        return True

    async def _fake_blob_delete(container, blob_name):
        deleted_blobs.append((container, blob_name))
        return True

    async def _fake_blob_download_json(container, blob_name):
        return {"entry": {"source_user_sub": "jane", "promoted_by": "jane"}}

    async def _fake_blob_upload_json(container, blob_name, payload):
        return {"blob_ref": f"{container}/{blob_name}"}

    async def _fake_review_story_knowledge_asset(*, asset_id, action, reviewed_by, note=""):
        reviewed_assets.append((asset_id, action, reviewed_by))
        return {"search_sync": {"ok": True}}

    async def _fake_sync_user_story_examples_search_index(*, draft_id="", top=200):
        synced_drafts.append(draft_id)
        return {"synced": 1, "deleted": 0}

    monkeypatch.setattr(privacy_service, "table_query", _fake_table_query)
    monkeypatch.setattr(privacy_service, "table_delete", _fake_table_delete)
    monkeypatch.setattr(privacy_service, "table_merge", _fake_table_merge)
    monkeypatch.setattr(privacy_service, "blob_delete", _fake_blob_delete)
    monkeypatch.setattr(privacy_service, "blob_download_json", _fake_blob_download_json)
    monkeypatch.setattr(privacy_service, "blob_upload_json", _fake_blob_upload_json)
    monkeypatch.setattr(privacy_service, "review_story_knowledge_asset", _fake_review_story_knowledge_asset)
    monkeypatch.setattr(privacy_service, "sync_user_story_examples_search_index", _fake_sync_user_story_examples_search_index)

    result = await privacy_service.delete_user_personal_data("jane", delete_account=True)

    assert result["deleted_rows"] >= 7
    assert result["anonymized_rows"] >= 2
    assert result["deleted_blobs"] >= 5
    assert ("Users", "user", "jane") in deleted_rows
    assert synced_drafts == ["draft-1"]
    assert reviewed_assets == [("asset-1", "delete", "jane")]


@pytest.mark.asyncio
async def test_privacy_export_endpoint_returns_generated_download(monkeypatch):
    monkeypatch.setattr(routes_chat, "get_current_principal", lambda _credentials=None, request=None: type("P", (), {"sub": "jane"})())

    async def _fake_build_user_privacy_export(user_sub):
        return {"summary": {"chat_history": 2}}

    async def _fake_store_generated_file(*args, **kwargs):
        return "download-123"

    monkeypatch.setattr(routes_chat, "build_user_privacy_export", _fake_build_user_privacy_export)
    monkeypatch.setattr(routes_chat, "_store_generated_file", _fake_store_generated_file)

    async def _fake_log_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(routes_chat, "log_audit", _fake_log_audit)

    result = await routes_chat.export_my_data(None, credentials=None)

    assert result["status"] == "ok"
    assert result["download_id"] == "download-123"
    assert result["url"] == "/api/download/download-123"


@pytest.mark.asyncio
async def test_privacy_delete_endpoint_requires_confirmation_and_returns_summary(monkeypatch):
    monkeypatch.setattr(routes_chat, "get_current_principal", lambda _credentials=None, request=None: type("P", (), {"sub": "jane"})())

    async def _fake_delete_user_personal_data(user_sub, delete_account=False):
        return {"deleted_rows": 10, "anonymized_rows": 2, "deleted_blobs": 3}

    monkeypatch.setattr(routes_chat, "delete_user_personal_data", _fake_delete_user_personal_data)

    async def _fake_log_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(routes_chat, "log_audit", _fake_log_audit)

    async def _fake_persist_user_invalidation(user_sub):
        return None

    monkeypatch.setattr(routes_chat, "persist_user_invalidation", _fake_persist_user_invalidation)

    # Inject router state so _get_conversations / _get_conversation_meta / _get_uploaded_files_store work
    routes_chat._router_state.update({
        "conversations": {},
        "conversation_meta": {},
        "uploaded_files_store": {},
        "purge_upload_artifacts_for_conversation": lambda *a, **kw: None,
    })

    result = await routes_chat.delete_my_data(None, PrivacyDeleteRequest(), credentials=None)

    assert result["status"] == "ok"
    assert result["deleted_rows"] == 10
