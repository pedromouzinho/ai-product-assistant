from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

import app


def test_retention_expiry_helper():
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    assert app._is_retention_expired(past) is True
    assert app._is_retention_expired(future) is False
    assert app._is_retention_expired("") is False


def test_job_public_view_exposes_expected_fields():
    job = {
        "job_id": "job-1",
        "status": "queued",
        "worker_id": "worker-a",
        "conversation_id": "conv-1",
        "filename": "sample.xlsx",
        "size_bytes": 123,
        "created_at": "2026-03-27T10:00:00+00:00",
        "updated_at": "2026-03-27T10:01:00+00:00",
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": {"ok": True},
    }

    result = app._job_public_view(job)

    assert result["job_id"] == "job-1"
    assert result["worker_id"] == "worker-a"
    assert result["conversation_id"] == "conv-1"
    assert result["filename"] == "sample.xlsx"
    assert result["result"] == {"ok": True}


def test_is_job_stale_only_for_queued_or_processing():
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(seconds=app.UPLOAD_JOB_STALE_SECONDS + 5)).isoformat()
    fresh_time = (now - timedelta(seconds=5)).isoformat()

    assert app._is_job_stale({"status": "queued", "updated_at": stale_time}, now=now) is True
    assert app._is_job_stale({"status": "processing", "updated_at": stale_time}, now=now) is True
    assert app._is_job_stale({"status": "queued", "updated_at": fresh_time}, now=now) is False
    assert app._is_job_stale({"status": "completed", "updated_at": stale_time}, now=now) is False


def test_job_from_storage_row_parses_json_and_retention_fields():
    row = {
        "RowKey": "job-42",
        "Status": "completed",
        "ConversationId": "conv-1",
        "Filename": "sample.csv",
        "ContentType": "text/csv",
        "SizeBytes": 321,
        "CreatedAt": "2026-03-27T10:00:00+00:00",
        "UpdatedAt": "2026-03-27T10:01:00+00:00",
        "Error": "",
        "ResultJson": "{\"ok\": true}",
        "UserSub": "tester",
        "WorkerId": "worker-a",
        "ClaimToken": "claim-1",
        "RawBlobRef": "upload-raw/sample.csv",
        "TextBlobName": "text.txt",
        "ChunksBlobName": "chunks.json",
        "ArtifactBlobName": "artifact.parquet",
        "ArtifactBlobRef": "upload-artifacts/data.parquet",
        "ArtifactFormat": "parquet",
        "RawBlobRetentionUntil": "2026-03-28T10:00:00+00:00",
        "RawBlobPurgedAt": "",
        "RetentionUntil": "2026-03-29T10:00:00+00:00",
    }

    result = app._job_from_storage_row(row)

    assert result["job_id"] == "job-42"
    assert result["result"] == {"ok": True}
    assert result["artifact_blob_ref"] == "upload-artifacts/data.parquet"
    assert result["raw_blob_retention_until"] == "2026-03-28T10:00:00+00:00"


def test_upload_index_row_to_memory_entry_restores_polymorphic_fallback():
    row = {
        "Filename": "multi.xlsx",
        "PreviewText": "preview",
        "RowCount": 5,
        "ColNamesJson": "[\"Tipo\", \"Campo\"]",
        "ColAnalysisJson": "[{\"name\":\"Tipo\"}]",
        "Truncated": False,
        "UploadedAt": "2026-03-27T10:00:00+00:00",
        "HasChunks": "true",
        "ChunksBlobRef": "chunks/ref",
        "ExtractedBlobRef": "text/ref",
        "TabularArtifactBlobRef": "artifact/ref",
        "TabularArtifactFormat": "parquet",
        "TabularArtifactRowCount": 5,
        "PolymorphicSummary": "dataset polymorphic",
        "PivotColumn": "Tipo",
        "PivotValuesCount": 3,
    }

    result = app._upload_index_row_to_memory_entry(row)

    assert result["filename"] == "multi.xlsx"
    assert result["col_names"] == ["Tipo", "Campo"]
    assert result["has_chunks"] is True
    assert result["polymorphic_schema"]["is_polymorphic"] is True
    assert result["polymorphic_schema"]["pivot_column"] == "Tipo"


def test_raw_blob_retention_for_tabular_artifact_uses_shorter_window(monkeypatch):
    monkeypatch.setattr(app, "UPLOAD_TABULAR_RAW_RETENTION_HOURS", 6)
    monkeypatch.setattr(app, "UPLOAD_TABULAR_READY_RAW_RETENTION_HOURS", 1)

    retention_until = app._raw_blob_retention_until_iso(
        filename="sample.xlsx",
        artifact_blob_ref="upload-artifacts/sample.parquet",
        fallback_hours=72,
    )

    delta = datetime.fromisoformat(retention_until) - datetime.now(timezone.utc)
    assert delta.total_seconds() > 5 * 3600
    assert delta.total_seconds() < 7 * 3600


def test_raw_blob_retention_for_ready_tabular_artifact_uses_one_hour_window(monkeypatch):
    monkeypatch.setattr(app, "UPLOAD_TABULAR_RAW_RETENTION_HOURS", 6)
    monkeypatch.setattr(app, "UPLOAD_TABULAR_READY_RAW_RETENTION_HOURS", 1)

    retention_until = app._raw_blob_retention_until_iso(
        filename="sample.xlsx",
        artifact_blob_ref="upload-artifacts/sample.parquet",
        has_chunks=True,
        fallback_hours=72,
    )

    delta = datetime.fromisoformat(retention_until) - datetime.now(timezone.utc)
    assert delta.total_seconds() > 0.5 * 3600
    assert delta.total_seconds() < 1.5 * 3600


@pytest.mark.asyncio
async def test_purge_expired_upload_artifacts_removes_expired_rows_and_jobs(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    queries = []
    deleted_blobs = []
    deleted_rows = []
    deleted_jobs = []

    async def fake_table_query(table_name, filter_str="", top=0):
        queries.append((table_name, filter_str, top))
        if table_name == "UploadIndex":
            return [
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "job-expired",
                    "RetentionUntil": expired,
                    "RawBlobRef": "upload-raw/expired.xlsx",
                    "ExtractedBlobRef": "upload-text/expired.json",
                    "ChunksBlobRef": "",
                    "TabularArtifactBlobRef": "upload-artifacts/expired.parquet",
                },
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "job-active",
                    "RetentionUntil": future,
                    "RawBlobRef": "upload-raw/active.xlsx",
                    "ExtractedBlobRef": "",
                    "ChunksBlobRef": "",
                    "TabularArtifactBlobRef": "",
                },
            ]
        if table_name == "UploadJobs":
            return [
                {
                    "PartitionKey": "upload",
                    "RowKey": "job-expired",
                    "Status": "completed",
                    "RetentionUntil": expired,
                    "RawBlobRef": "upload-raw/expired.xlsx",
                    "TabularArtifactBlobRef": "upload-artifacts/expired.parquet",
                },
                {
                    "PartitionKey": "upload",
                    "RowKey": "job-failed",
                    "Status": "failed",
                    "RetentionUntil": expired,
                    "RawBlobRef": "upload-raw/failed.xlsx",
                    "TabularArtifactBlobRef": "upload-artifacts/failed.parquet",
                },
                {
                    "PartitionKey": "upload",
                    "RowKey": "job-active",
                    "Status": "completed",
                    "RetentionUntil": future,
                    "RawBlobRef": "upload-raw/active.xlsx",
                    "TabularArtifactBlobRef": "",
                },
            ]
        return []

    async def fake_blob_delete(container, blob_name):
        deleted_blobs.append(f"{container}/{blob_name}")

    async def fake_table_delete(table_name, partition_key, row_key):
        deleted_rows.append((table_name, partition_key, row_key))

    async def fake_job_delete(job_id, partition_key=None):
        deleted_jobs.append((partition_key, job_id))
        return None

    monkeypatch.setattr(app, "table_query", fake_table_query)
    monkeypatch.setattr(app, "blob_delete", fake_blob_delete)
    monkeypatch.setattr(app, "table_delete", fake_table_delete)
    monkeypatch.setattr(app.upload_jobs_store, "delete", fake_job_delete)

    result = await app._purge_expired_upload_artifacts(limit=50)

    assert ("UploadIndex", "conv-1", "job-expired") in deleted_rows
    assert ("upload", "job-expired") in deleted_jobs
    assert ("upload", "job-failed") in deleted_jobs
    assert "upload-raw/expired.xlsx" in deleted_blobs
    assert "upload-text/expired.json" in deleted_blobs
    assert "upload-artifacts/expired.parquet" in deleted_blobs
    assert "upload-raw/failed.xlsx" in deleted_blobs
    assert "upload-artifacts/failed.parquet" in deleted_blobs
    assert result["rows_deleted"] == 1
    assert result["jobs_deleted"] == 2
    assert result["blobs_deleted"] >= 3
    assert [q[0] for q in queries] == ["UploadIndex", "UploadJobs"]


@pytest.mark.asyncio
async def test_purge_expired_upload_artifacts_can_drop_only_raw_blob_when_artifact_exists(monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    deleted_blobs = []
    merged_rows = []
    deleted_rows = []
    deleted_jobs = []
    cached_jobs = {
        "job-tabular": {
            "job_id": "job-tabular",
            "raw_blob_ref": "upload-raw/tabular.xlsx",
            "raw_blob_retention_until": expired,
            "artifact_blob_ref": "upload-artifacts/tabular.parquet",
        }
    }
    put_payloads = []

    async def fake_table_query(table_name, filter_str="", top=0):
        if table_name == "UploadIndex":
            return [
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "job-tabular",
                    "RetentionUntil": future,
                    "RawBlobRef": "upload-raw/tabular.xlsx",
                    "RawBlobRetentionUntil": expired,
                    "ExtractedBlobRef": "upload-text/tabular.txt",
                    "ChunksBlobRef": "",
                    "TabularArtifactBlobRef": "upload-artifacts/tabular.parquet",
                    "Filename": "tabular.xlsx",
                }
            ]
        if table_name == "UploadJobs":
            return [
                {
                    "PartitionKey": "upload",
                    "RowKey": "job-tabular",
                    "Status": "completed",
                    "RetentionUntil": future,
                    "RawBlobRef": "upload-raw/tabular.xlsx",
                    "RawBlobRetentionUntil": expired,
                    "ArtifactBlobRef": "upload-artifacts/tabular.parquet",
                }
            ]
        return []

    async def fake_blob_delete(container, blob_name):
        deleted_blobs.append(f"{container}/{blob_name}")

    async def fake_table_delete(table_name, partition_key, row_key):
        deleted_rows.append((table_name, partition_key, row_key))

    async def fake_table_merge(table_name, entity):
        merged_rows.append((table_name, entity))

    async def fake_job_delete(job_id, partition_key=None):
        deleted_jobs.append((partition_key, job_id))
        return None

    def fake_job_get(job_id):
        return cached_jobs.get(job_id)

    async def fake_job_put(job_id, payload):
        put_payloads.append((job_id, dict(payload)))
        cached_jobs[job_id] = dict(payload)

    monkeypatch.setattr(app, "table_query", fake_table_query)
    monkeypatch.setattr(app, "blob_delete", fake_blob_delete)
    monkeypatch.setattr(app, "table_delete", fake_table_delete)
    monkeypatch.setattr(app, "table_merge", fake_table_merge)
    monkeypatch.setattr(app.upload_jobs_store, "delete", fake_job_delete)
    monkeypatch.setattr(app.upload_jobs_store, "get", fake_job_get)
    monkeypatch.setattr(app.upload_jobs_store, "put", fake_job_put)

    result = await app._purge_expired_upload_artifacts(limit=50)

    assert deleted_blobs == ["upload-raw/tabular.xlsx"]
    assert deleted_rows == []
    assert deleted_jobs == []
    assert result["rows_deleted"] == 0
    assert result["jobs_deleted"] == 0
    assert result["blobs_deleted"] == 1
    assert any(
        table_name == "UploadIndex"
        and entity.get("RowKey") == "job-tabular"
        and entity.get("RawBlobRef") == ""
        and entity.get("RawBlobRetentionUntil") == ""
        for table_name, entity in merged_rows
    )
    assert any(
        table_name == "UploadJobs"
        and entity.get("RowKey") == "job-tabular"
        and entity.get("RawBlobRef") == ""
        and entity.get("RawBlobRetentionUntil") == ""
        for table_name, entity in merged_rows
    )
    assert put_payloads and put_payloads[0][1]["raw_blob_ref"] == ""


@pytest.mark.asyncio
async def test_process_upload_job_shortens_raw_retention_once_tabular_chunks_exist(monkeypatch):
    job = {
        "job_id": "job-retention",
        "conversation_id": "conv-1",
        "user_sub": "tester",
        "filename": "sample.csv",
        "content_type": "text/csv",
        "raw_blob_ref": "upload-raw/conv-1/job-retention/raw/sample.csv",
        "artifact_blob_name": "conv-1/job-retention/artifact/data.parquet",
        "text_blob_name": "conv-1/job-retention/extracted/text.txt",
        "chunks_blob_name": "conv-1/job-retention/extracted/chunks.json",
        "size_bytes": 32,
        "retention_until": app._retention_until_iso(hours=24),
    }
    uploaded = {}
    monkeypatch.setattr(app, "UPLOAD_TABULAR_RAW_RETENTION_HOURS", 6)
    monkeypatch.setattr(app, "UPLOAD_TABULAR_READY_RAW_RETENTION_HOURS", 1)

    async def _fake_blob_download_bytes(_container, _blob_name):
        return b"Date,Value\n2026-01-01,10\n"

    async def _fake_blob_upload_bytes(container, blob_name, payload, **kwargs):
        _ = kwargs
        return {"blob_ref": f"{container}/{blob_name}", "size_bytes": len(payload)}

    async def _fake_blob_upload_json(container, blob_name, payload):
        uploaded["chunks_payload"] = payload
        return {"blob_ref": f"{container}/{blob_name}"}

    async def _fake_extract_upload_entry(_filename, _raw_bytes, _content_type, **_kwargs):
        return (
            {
                "filename": "sample.csv",
                "data_text": "Date\tValue\n2026-01-01\t10",
                "row_count": 1,
                "col_names": ["Date", "Value"],
                "col_analysis": [],
                "truncated": True,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "tabular_ingest_mode": "preview_only",
            },
            {"tabular_ingest_mode": "preview_only"},
        )

    async def _fake_append_uploaded_entry_safe(_conv_id, store_entry):
        uploaded["store_entry"] = dict(store_entry)
        return [{"filename": store_entry["filename"], "rows": store_entry["row_count"]}]

    async def _fake_upsert_upload_index(entity):
        uploaded["index_entity"] = dict(entity)

    monkeypatch.setattr(app, "blob_download_bytes", _fake_blob_download_bytes)
    monkeypatch.setattr(app, "blob_upload_bytes", _fake_blob_upload_bytes)
    monkeypatch.setattr(app, "blob_upload_json", _fake_blob_upload_json)
    monkeypatch.setattr(app, "_extract_upload_entry", _fake_extract_upload_entry)
    monkeypatch.setattr(
        app,
        "_build_tabular_semantic_chunks_from_artifact",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"index": 0, "start": 0, "end": 1, "text": "chunk", "embedding": [1.0]}],
        ),
    )
    monkeypatch.setattr(app, "_append_uploaded_entry_safe", _fake_append_uploaded_entry_safe)
    monkeypatch.setattr(app, "_upsert_upload_index", _fake_upsert_upload_index)
    monkeypatch.setattr(app, "_count_files_for_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result=0))

    await app._process_upload_job(job)

    retention_until = datetime.fromisoformat(uploaded["index_entity"]["RawBlobRetentionUntil"])
    delta = retention_until - datetime.now(timezone.utc)
    # With deferred semantic chunks, raw retention uses the longer
    # UPLOAD_TABULAR_RAW_RETENTION_HOURS (6h) instead of the short 1h.
    assert delta.total_seconds() > 0.5 * 3600
    assert delta.total_seconds() < 7 * 3600
    # Chunks are now deferred — not built inline during _process_upload_job.
    assert uploaded["store_entry"].get("needs_artifact_semantic_chunks") is True


@pytest.mark.asyncio
async def test_backfill_tabular_artifact_chunks_completes_historical_row(monkeypatch):
    artifact = app.build_tabular_artifact(
        b"Date,Value\n2026-01-01,10\n2026-01-02,20\n",
        "historical.csv",
    )
    merged_rows = []
    uploaded_chunks = {}
    monkeypatch.setattr(app, "UPLOAD_TABULAR_RAW_RETENTION_HOURS", 6)
    monkeypatch.setattr(app, "UPLOAD_TABULAR_READY_RAW_RETENTION_HOURS", 1)

    async def _fake_table_query(table_name, filter_str="", top=0):
        _ = (filter_str, top)
        if table_name == "UploadIndex":
            return [
                {
                    "PartitionKey": "conv-1",
                    "RowKey": "job-1",
                    "Filename": "historical.csv",
                    "TabularArtifactBlobRef": "upload-artifacts/conv-1/job-1/artifact/data.parquet",
                    "ChunksBlobRef": "",
                    "HasChunks": False,
                    "ColNamesJson": '["Date","Value"]',
                }
            ]
        return []

    async def _fake_blob_download_bytes(_container, _blob_name):
        return artifact["artifact_bytes"]

    async def _fake_blob_upload_json(container, blob_name, payload):
        uploaded_chunks["container"] = container
        uploaded_chunks["blob_name"] = blob_name
        uploaded_chunks["payload"] = payload
        return {"blob_ref": f"{container}/{blob_name}"}

    async def _fake_table_merge(table_name, entity):
        merged_rows.append((table_name, dict(entity)))

    async def _fake_get_embedding(_text):
        return [1.0]

    monkeypatch.setattr(app, "table_query", _fake_table_query)
    monkeypatch.setattr(app, "blob_download_bytes", _fake_blob_download_bytes)
    monkeypatch.setattr(app, "blob_upload_json", _fake_blob_upload_json)
    monkeypatch.setattr(app, "table_merge", _fake_table_merge)
    monkeypatch.setattr(app, "get_embedding", _fake_get_embedding)
    monkeypatch.setattr(app.upload_jobs_store, "get", lambda *_args, **_kwargs: None)

    result = await app._backfill_tabular_artifact_chunks(limit=5)

    assert result["completed"] == 1
    assert uploaded_chunks["container"] == app.UPLOAD_BLOB_CONTAINER_CHUNKS
    assert uploaded_chunks["payload"]["chunks"]
    assert any(
        table_name == "UploadIndex"
        and entity.get("HasChunks") is True
        and entity.get("ChunksBlobRef")
        for table_name, entity in merged_rows
    )
    upload_jobs_merge = next(entity for table_name, entity in merged_rows if table_name == "UploadJobs")
    retention_until = datetime.fromisoformat(upload_jobs_merge["RawBlobRetentionUntil"])
    delta = retention_until - datetime.now(timezone.utc)
    assert delta.total_seconds() > 0.5 * 3600
    assert delta.total_seconds() < 1.5 * 3600
