from __future__ import annotations

import asyncio
import base64
import io
import types
from datetime import datetime, timezone

import openpyxl
import pytest
from fastapi import UploadFile

import app
import tabular_loader


def _sample_csv_bytes() -> bytes:
    return (
        "Date;Category;Revenue;Margin\n"
        "2026-01-01;A;10;2\n"
        "2026-01-02;B;20;3\n"
        "2026-01-03;A;30;4\n"
    ).encode("utf-8")


def _sample_xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Category", "Revenue"])
    sheet.append(["2026-01-01", "A", 10])
    sheet.append(["2026-01-02", "B", 20])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestTabularLoader:
    def test_csv_preview_detects_columns_types_and_row_count(self):
        preview = tabular_loader.load_tabular_preview(_sample_csv_bytes(), "sample.csv")

        assert preview["columns"] == ["Date", "Category", "Revenue", "Margin"]
        assert preview["row_count"] == 3
        assert preview["delimiter"] == ";"
        assert preview["column_types"]["Revenue"] == "numeric"
        assert preview["column_types"]["Date"] == "date"
        assert "2026-01-01" in preview["data_text"]

    def test_xlsx_dataset_reads_records(self):
        dataset = tabular_loader.load_tabular_dataset(_sample_xlsx_bytes(), "sample.xlsx")

        assert dataset["row_count"] == 2
        assert dataset["rows_loaded"] == 2
        assert dataset["columns"] == ["Date", "Category", "Revenue"]
        assert dataset["records"][0]["Revenue"] == "10"

    def test_xlsb_preview_uses_reader_without_materializing_everything(self, monkeypatch):
        class _Sheet:
            def to_python(self):
                return [
                    ["Date", "Revenue"],
                    ["2026-01-01", 10],
                    ["2026-01-02", 20],
                ]

        class _Workbook:
            @classmethod
            def from_filelike(cls, fobj):
                return cls()

            def get_sheet_by_index(self, idx):
                assert idx == 0
                return _Sheet()

        monkeypatch.setattr("python_calamine.CalamineWorkbook", _Workbook)
        preview = tabular_loader.load_tabular_preview(b"PK\x03\x04fake", "sample.xlsb")

        assert preview["columns"] == ["Date", "Revenue"]
        assert preview["row_count"] == 2
        assert preview["column_types"]["Revenue"] == "numeric"


class TestUploadLimitsAndExtraction:
    def test_tabular_upload_limits_are_extension_specific(self):
        csv_limit = app._max_upload_bytes_for_file("emails.csv")
        xlsx_limit = app._max_upload_bytes_for_file("report.xlsx")
        xlsb_limit = app._max_upload_bytes_for_file("report.xlsb")
        xls_limit = app._max_upload_bytes_for_file("report.xls")

        assert csv_limit > app.MAX_UPLOAD_FILE_BYTES
        assert xlsx_limit > app.MAX_UPLOAD_FILE_BYTES
        assert xlsb_limit >= xlsx_limit
        assert xls_limit == 100 * 1024 * 1024
        assert csv_limit == 200 * 1024 * 1024
        assert xlsx_limit == 200 * 1024 * 1024
        assert xlsb_limit == 200 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_extract_upload_entry_accepts_xlsb(self, monkeypatch):
        monkeypatch.setattr(
            app,
            "load_tabular_preview",
            lambda _content, _filename: {
                "columns": ["Date", "Revenue"],
                "row_count": 2,
                "data_text": "Date\tRevenue\n2026-01-01\t10",
                "delimiter": "\t",
                "col_analysis": [{"name": "Revenue", "type": "numeric", "sample": ["10"]}],
                "truncated": False,
            },
        )

        store_entry, result_payload = await app._extract_upload_entry(
            "sample.xlsb",
            b"PK\x03\x04fake-content",
            "application/vnd.ms-excel.sheet.binary.macroenabled.12",
        )

        assert store_entry["col_names"] == ["Date", "Revenue"]
        assert store_entry["row_count"] == 2
        assert result_payload["rows"] == 2

    @pytest.mark.asyncio
    async def test_extract_upload_entry_deep_ingest_produces_full_text(self, monkeypatch):
        """When deep ingest is enabled, full_text should be built from
        load_tabular_dataset (not just the preview).  Semantic chunk
        building is now deferred to background, so we verify via the
        tabular_ingest_mode flag instead."""
        preview_rows = [f"row-{idx}" for idx in range(200)]
        full_records = [{"Body": f"line-{idx}-" + ("x" * 260)} for idx in range(300)]
        monkeypatch.setattr(app, "UPLOAD_TABULAR_DEEP_INGEST_MAX_BYTES", 1024 * 1024)
        monkeypatch.setattr(app, "UPLOAD_TABULAR_DEEP_INGEST_MAX_ROWS", 1000)

        monkeypatch.setattr(
            app,
            "load_tabular_preview",
            lambda _content, _filename: {
                "columns": ["Body"],
                "row_count": 300,
                "data_text": "Body\n" + "\n".join(preview_rows),
                "delimiter": "\t",
                "col_analysis": [{"name": "Body", "type": "text", "sample": ["row-0"]}],
                "sample_records": [{"Body": f"row-{idx}"} for idx in range(200)],
                "column_types": {"Body": "text"},
                "truncated": True,
            },
        )
        monkeypatch.setattr(
            app,
            "load_tabular_dataset",
            lambda _content, _filename, *_args, **_kwargs: {
                "columns": ["Body"],
                "records": full_records,
                "row_count": 300,
            },
        )

        store_entry, resp = await app._extract_upload_entry("sample.csv", b"Body\nx\n", "text/csv")

        assert store_entry.get("tabular_ingest_mode") == "deep"
        assert resp.get("tabular_ingest_mode") == "deep"

    @pytest.mark.asyncio
    async def test_extract_upload_entry_uses_preview_only_mode_for_large_tabular_files(self, monkeypatch):
        fake_xlsx = b"PK\x03\x04" + (b"x" * app.UPLOAD_TABULAR_DEEP_INGEST_MAX_BYTES)
        monkeypatch.setattr(
            app,
            "load_tabular_preview",
            lambda _content, _filename: {
                "columns": ["Body"],
                "row_count": 25000,
                "data_text": "Body\nlinha 1\nlinha 2\n",
                "delimiter": "\t",
                "col_analysis": [],
                "sample_records": [{"Body": "linha 1"}, {"Body": "linha 2"}],
                "column_types": {"Body": "text"},
                "truncated": False,
            },
        )

        def _should_not_load_dataset(*_args, **_kwargs):
            raise AssertionError("large tabular files should not trigger deep dataset loading")

        async def _should_not_build_chunks(*_args, **_kwargs):
            raise AssertionError("preview-only ingest should not build semantic chunks")

        monkeypatch.setattr(app, "load_tabular_dataset", _should_not_load_dataset)
        monkeypatch.setattr(app, "_build_semantic_chunks", _should_not_build_chunks)

        store_entry, result_payload = await app._extract_upload_entry(
            "large.xlsx",
            fake_xlsx,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        assert store_entry["tabular_ingest_mode"] == "preview_only"
        assert result_payload["tabular_ingest_mode"] == "preview_only"
        assert store_entry["truncated"] is True
        assert "chunks" not in store_entry

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route_name", ["upload_file", "upload_file_async"])
    async def test_single_upload_routes_use_per_extension_limit(self, monkeypatch, route_name):
        captured = {}

        monkeypatch.setattr(app, "get_current_user", lambda _credentials=None: {"sub": "tester"})
        monkeypatch.setattr(app, "_max_upload_bytes_for_file", lambda filename: 123 if filename.endswith(".csv") else 50)

        async def _fake_read_upload_with_limit(upload, max_bytes):
            captured["filename"] = upload.filename
            captured["max_bytes"] = max_bytes
            return b"csv-bytes"

        async def _fake_count_reserved_slots(*_args, **_kwargs):
            return 0

        async def _fake_count_pending_jobs_for_user(*_args, **_kwargs):
            return 0

        async def _fake_queue_upload_job(conv_id, user_sub, filename, content, content_type):
            return {
                "job_id": "job-1",
                "conversation_id": conv_id,
                "filename": filename,
                "size_bytes": len(content),
                "content_type": content_type,
                "user_sub": user_sub,
            }

        monkeypatch.setattr(app, "_read_upload_with_limit", _fake_read_upload_with_limit)
        monkeypatch.setattr(app, "_count_reserved_slots_for_conversation", _fake_count_reserved_slots)
        monkeypatch.setattr(app, "_count_pending_jobs_for_user", _fake_count_pending_jobs_for_user)
        monkeypatch.setattr(app, "_queue_upload_job", _fake_queue_upload_job)
        monkeypatch.setattr(app, "_cleanup_upload_jobs", lambda: None)

        upload = UploadFile(filename="emails.csv", file=io.BytesIO(b"csv"))
        route = getattr(app, route_name)
        result = await route(None, upload, None, None)

        assert result["status"] == "queued"
        assert captured["filename"] == "emails.csv"
        assert captured["max_bytes"] == 123

    @pytest.mark.asyncio
    async def test_stream_upload_route_queues_large_tabular_file(self, monkeypatch):
        captured = {}

        class _FakeRequest:
            def __init__(self):
                self.headers = {
                    "x-upload-filename": "sample.xlsx",
                    "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "content-length": str(len(b"chunk-a") + len(b"chunk-b")),
                }

            async def stream(self):
                yield b"chunk-a"
                yield b"chunk-b"

        monkeypatch.setattr(app, "get_current_user", lambda _credentials=None: {"sub": "tester"})
        monkeypatch.setattr(app, "_cleanup_upload_jobs", lambda: None)
        monkeypatch.setattr(app, "_max_upload_bytes_for_file", lambda _filename: 999)

        async def _fake_count_reserved_slots(*_args, **_kwargs):
            return 0

        async def _fake_count_pending_jobs_for_user(*_args, **_kwargs):
            return 0

        async def _fake_blob_upload_stream(container, blob_name, chunk_iter, **kwargs):
            data = bytearray()
            async for chunk in chunk_iter:
                data.extend(chunk)
            captured["container"] = container
            captured["blob_name"] = blob_name
            captured["content"] = bytes(data)
            captured["max_bytes"] = kwargs.get("max_bytes")
            return {
                "blob_ref": f"{container}/{blob_name}",
                "size_bytes": len(data),
            }

        async def _fake_queue_upload_job_from_blob(conv_id, user_sub, filename, raw_blob_ref, size_bytes, content_type="", **kwargs):
            captured["queued"] = {
                "conv_id": conv_id,
                "user_sub": user_sub,
                "filename": filename,
                "raw_blob_ref": raw_blob_ref,
                "size_bytes": size_bytes,
                "content_type": content_type,
                "job_id": kwargs.get("job_id"),
            }
            return {
                "job_id": kwargs.get("job_id") or "job-1",
                "conversation_id": conv_id,
            }

        monkeypatch.setattr(app, "_count_reserved_slots_for_conversation", _fake_count_reserved_slots)
        monkeypatch.setattr(app, "_count_pending_jobs_for_user", _fake_count_pending_jobs_for_user)
        monkeypatch.setattr(app, "blob_upload_stream", _fake_blob_upload_stream)
        monkeypatch.setattr(app, "_queue_upload_job_from_blob", _fake_queue_upload_job_from_blob)

        result = await app.upload_file_stream_async(_FakeRequest(), None, None)

        assert result["status"] == "queued"
        assert result["upload_mode"] == "stream"
        assert result["size_bytes"] == len(b"chunk-achunk-b")
        assert captured["content"] == b"chunk-achunk-b"
        assert captured["max_bytes"] == 999
        assert captured["queued"]["filename"] == "sample.xlsx"
        assert captured["queued"]["size_bytes"] == len(b"chunk-achunk-b")

    @pytest.mark.asyncio
    async def test_build_tabular_semantic_chunks_from_artifact_uses_full_artifact(self, monkeypatch):
        artifact = app.build_tabular_artifact(_sample_csv_bytes(), "sample.csv")

        async def _fake_embedding(text):
            return [float(len(text))]

        monkeypatch.setattr(app, "get_embedding", _fake_embedding)

        chunks = await app._build_tabular_semantic_chunks_from_artifact(
            artifact["artifact_bytes"],
            columns=["Date", "Category", "Revenue", "Margin"],
            rows_per_chunk=2,
            max_chunk_chars=60,
            max_chunks=10,
        )

        assert chunks
        assert len(chunks) >= 2
        assert "Date\tCategory\tRevenue\tMargin" in chunks[0]["text"]
        assert "2026-01-03\tA\t30\t4" in chunks[-1]["text"]

    @pytest.mark.asyncio
    async def test_process_upload_job_generates_tabular_chunks_from_artifact_when_preview_only(self, monkeypatch):
        job = {
            "job_id": "job-1",
            "conversation_id": "conv-1",
            "user_sub": "tester",
            "filename": "sample.csv",
            "content_type": "text/csv",
            "raw_blob_ref": "upload-raw/conv-1/job-1/raw/sample.csv",
            "artifact_blob_name": "conv-1/job-1/artifact/data.parquet",
            "text_blob_name": "conv-1/job-1/extracted/text.txt",
            "chunks_blob_name": "conv-1/job-1/extracted/chunks.json",
            "size_bytes": len(_sample_csv_bytes()),
            "retention_until": app._retention_until_iso(hours=24),
        }
        uploaded_json = {}

        async def _fake_blob_download_bytes(_container, _blob_name):
            return _sample_csv_bytes()

        async def _fake_blob_upload_bytes(container, blob_name, payload, **kwargs):
            _ = kwargs
            return {"blob_ref": f"{container}/{blob_name}", "size_bytes": len(payload)}

        async def _fake_blob_upload_json(container, blob_name, payload):
            uploaded_json["container"] = container
            uploaded_json["blob_name"] = blob_name
            uploaded_json["payload"] = payload
            return {"blob_ref": f"{container}/{blob_name}"}

        async def _fake_extract_upload_entry(_filename, _raw_bytes, _content_type, **_kwargs):
            return (
                {
                    "filename": "sample.csv",
                    "data_text": "Date\tCategory\tRevenue\tMargin\n2026-01-01\tA\t10\t2",
                    "row_count": 3,
                    "col_names": ["Date", "Category", "Revenue", "Margin"],
                    "col_analysis": [],
                    "truncated": True,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "tabular_ingest_mode": "preview_only",
                },
                {"tabular_ingest_mode": "preview_only"},
            )

        async def _fake_build_tabular_chunks(artifact_bytes, **kwargs):
            _ = (artifact_bytes, kwargs)
            return [{"index": 0, "start": 0, "end": 3, "text": "chunk", "embedding": [1.0]}]

        async def _fake_append_uploaded_entry_safe(_conv_id, store_entry):
            uploaded_json["store_entry"] = store_entry
            return [{"filename": store_entry["filename"], "rows": store_entry["row_count"]}]

        async def _fake_upsert_upload_index(entity):
            uploaded_json["index_entity"] = entity

        monkeypatch.setattr(app, "blob_download_bytes", _fake_blob_download_bytes)
        monkeypatch.setattr(app, "blob_upload_bytes", _fake_blob_upload_bytes)
        monkeypatch.setattr(app, "blob_upload_json", _fake_blob_upload_json)
        monkeypatch.setattr(app, "_extract_upload_entry", _fake_extract_upload_entry)
        monkeypatch.setattr(app, "_build_tabular_semantic_chunks_from_artifact", _fake_build_tabular_chunks)
        monkeypatch.setattr(app, "_append_uploaded_entry_safe", _fake_append_uploaded_entry_safe)
        monkeypatch.setattr(app, "_upsert_upload_index", _fake_upsert_upload_index)
        monkeypatch.setattr(app, "_count_files_for_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result=0))

        await app._process_upload_job(job)

        # Semantic chunks are now deferred to background / first-query,
        # so they are NOT uploaded inline during _process_upload_job.
        # Instead, needs_artifact_semantic_chunks is flagged in store_entry.
        assert uploaded_json["store_entry"]["needs_artifact_semantic_chunks"] is True
        assert uploaded_json["store_entry"]["tabular_artifact_blob_ref"] != ""
        assert job["status"] == "completed"

    def test_request_body_limit_bytes_uses_stream_limit_for_tabular_uploads(self, monkeypatch):
        monkeypatch.setattr(app, "_max_upload_bytes_for_file", lambda _filename: 60 * 1024 * 1024)
        request = types.SimpleNamespace(
            url=types.SimpleNamespace(path="/upload/stream/async"),
            headers={"x-upload-filename": "sample.xlsx"},
        )

        assert app._request_body_limit_bytes(request) == 60 * 1024 * 1024

    def test_request_body_limit_bytes_uses_batch_limit_for_batch_uploads(self):
        request = types.SimpleNamespace(
            url=types.SimpleNamespace(path="/upload/batch/async"),
            headers={},
        )

        assert app._request_body_limit_bytes(request) == app.MAX_BATCH_UPLOAD_REQUEST_BODY_BYTES


class TestUploadedTableCharting:
    def test_chart_tool_is_registered(self):
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("chart_uploaded_table")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "chart_type" in params
        assert "x_column" in params
        assert "y_column" in params

    @pytest.mark.asyncio
    async def test_chart_uploaded_table_generates_artifacts(self, monkeypatch):
        import tools

        sample_bytes = _sample_csv_bytes()

        async def _fake_table_query(*_args, **_kwargs):
            return [
                {
                    "Filename": "sample.csv",
                    "RawBlobRef": "raw/sample.csv",
                    "UploadedAt": "2026-03-08T10:00:00+00:00",
                }
            ]

        async def _fake_blob_download_bytes(_container, _blob_name):
            return sample_bytes

        monkeypatch.setattr(tools, "table_query", _fake_table_query)
        monkeypatch.setattr(tools, "blob_download_bytes", _fake_blob_download_bytes)

        result = await tools.tool_chart_uploaded_table(
            query="faz um gráfico de barras da revenue por category",
            conv_id="conv-1",
            chart_type="bar",
            x_column="Category",
            y_column="Revenue",
            agg="sum",
        )

        assert result["success"] is True
        assert result["source"] == "uploaded_table_chart"
        artifact_names = {item["filename"] for item in result["generated_artifacts"]}
        assert "uploaded_table_chart.html" in artifact_names
        assert "uploaded_table_chart.svg" in artifact_names
        assert "uploaded_table_chart_data.csv" in artifact_names
        assert result["chart_spec"]["x_column"] == "Category"
        assert result["chart_spec"]["y_column"] == "Revenue"

    def test_chart_spec_clears_nonexistent_columns(self):
        from tools import _build_uploaded_table_chart_spec

        preview = {
            "columns": ["Date", "Revenue"],
            "sample_records": [{"Date": "2026-01-01", "Revenue": "10"}],
            "column_types": {"Date": "date", "Revenue": "numeric"},
            "row_count": 1,
        }
        spec = _build_uploaded_table_chart_spec(
            "gráfico de barras",
            preview,
            chart_type="bar",
            x_column="ColunaNaoExiste",
            y_column="OutraInexistente",
        )

        assert spec["x_column"] != "ColunaNaoExiste"
        assert spec["y_column"] != "OutraInexistente"

    def test_chart_code_template_produces_valid_python(self):
        from tools import _build_uploaded_table_chart_code

        spec = {
            "chart_type": "bar",
            "x_column": "Category",
            "y_column": "Revenue",
            "series_column": "",
            "agg": "sum",
            "top_n": 20,
            "max_points": 2000,
            "x_kind": "",
        }
        code = _build_uploaded_table_chart_code("sample.csv", spec, "test query")

        compile(code, "<chart_template>", "exec")
        assert "sample.csv" in code

    def test_chart_template_is_not_fstring(self):
        from tools import _CHART_CODE_TEMPLATE

        assert "{{" not in _CHART_CODE_TEMPLATE
        assert "}}" not in _CHART_CODE_TEMPLATE

    def test_code_interpreter_knows_xlsb_mime(self):
        from code_interpreter import _guess_mime

        assert _guess_mime("sample.xlsb") == "application/vnd.ms-excel.sheet.binary.macroenabled.12"

    @pytest.mark.asyncio
    async def test_run_code_exposes_generated_files_as_download_buttons(self, monkeypatch):
        import code_interpreter
        import tools

        async def fake_execute_code(*_args, **_kwargs):
            return {
                "success": True,
                "stdout": "",
                "stderr": "",
                "error": "",
                "files": [
                    {
                        "filename": "uploaded_table_chart.html",
                        "mime_type": "text/html",
                        "data": base64.b64encode(b"<html></html>").decode("ascii"),
                        "size": 13,
                    },
                    {
                        "filename": "uploaded_table_chart.svg",
                        "mime_type": "image/svg+xml",
                        "data": base64.b64encode(b"<svg></svg>").decode("ascii"),
                        "size": 11,
                    },
                    {
                        "filename": "uploaded_table_chart_data.csv",
                        "mime_type": "text/csv",
                        "data": base64.b64encode(b"a,b\n1,2\n").decode("ascii"),
                        "size": 8,
                    },
                ],
            }

        stored = []

        async def fake_store_generated_file(_content, _mime_type, filename, _fmt, **_kwargs):
            stored.append(filename)
            return f"download-{len(stored)}"

        monkeypatch.setattr(code_interpreter, "execute_code", fake_execute_code)
        monkeypatch.setattr(tools, "_store_generated_file", fake_store_generated_file)

        result = await tools.tool_run_code(code="print('ok')", description="gerar chart")

        assert len(result["_auto_file_downloads"]) == 3
        primary = next(item for item in result["_auto_file_downloads"] if item["primary"])
        assert primary["filename"] == "uploaded_table_chart.html"
        assert primary["endpoint"] == "/api/download/download-1"
        assert "[Abrir gráfico interativo (.html)](/api/download/download-1)" in result["output_text"]
