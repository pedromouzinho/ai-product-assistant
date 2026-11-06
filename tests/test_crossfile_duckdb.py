"""Tests for A5 — Cross-file DuckDB JOINs."""

import textwrap

import pytest


class TestDuckDBAllowedImport:
    def test_duckdb_in_allowed_imports(self):
        from code_interpreter import ALLOWED_IMPORTS
        assert "duckdb" in ALLOWED_IMPORTS

    def test_duckdb_not_in_blocked_imports(self):
        from code_interpreter import BLOCKED_IMPORTS
        assert "duckdb" not in BLOCKED_IMPORTS


class TestDuckDBBootstrap:
    def test_runner_script_has_db_global(self):
        from code_interpreter import _runner_script
        script = _runner_script("/tmp/test_dir", "dGVzdA==")
        assert "DB" in script
        assert "DUCKDB_TABLES" in script
        assert "duckdb" in script

    def test_runner_script_registers_parquet(self):
        from code_interpreter import _runner_script
        script = _runner_script("/tmp/test_dir", "dGVzdA==")
        assert "read_parquet" in script

    def test_runner_script_registers_csv(self):
        from code_interpreter import _runner_script
        script = _runner_script("/tmp/test_dir", "dGVzdA==")
        assert "read_csv_auto" in script

    def test_runner_script_closes_db(self):
        from code_interpreter import _runner_script
        script = _runner_script("/tmp/test_dir", "dGVzdA==")
        assert "_db.close()" in script


class TestCrossFileContext:
    """Test that cross-file JOIN hint is injected for multiple tabular files."""

    @pytest.mark.asyncio
    async def test_crossfile_hint_with_two_tabular(self, monkeypatch):
        import agent

        injected_messages = []

        async def fake_get_files(conv_id):
            return [
                {"filename": "vendas.xlsx", "row_count": 100, "col_names": ["A", "B"], "data_text": "sample"},
                {"filename": "clientes.csv", "row_count": 50, "col_names": ["X", "Y"], "data_text": "sample2"},
            ]

        async def fake_get_meta(conv_id):
            return {"mode": "general"}

        async def fake_update_meta(conv_id, **kw):
            pass

        monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_get_files)
        monkeypatch.setattr(agent, "_get_conversation_meta", fake_get_meta)
        monkeypatch.setattr(agent, "_update_conversation_meta", fake_update_meta)

        messages = []
        await agent._inject_file_context("test-conv", messages)

        assert len(messages) == 1
        ctx = messages[0]["content"]
        assert "CROSS-FILE ANALYSIS" in ctx
        assert "vendas.xlsx" in ctx
        assert "clientes.csv" in ctx
        assert "JOIN" in ctx
        assert "DB" in ctx

    @pytest.mark.asyncio
    async def test_no_crossfile_hint_with_single_file(self, monkeypatch):
        import agent

        async def fake_get_files(conv_id):
            return [
                {"filename": "vendas.xlsx", "row_count": 100, "col_names": ["A"], "data_text": "sample"},
            ]

        async def fake_get_meta(conv_id):
            return {"mode": "general"}

        async def fake_update_meta(conv_id, **kw):
            pass

        monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_get_files)
        monkeypatch.setattr(agent, "_get_conversation_meta", fake_get_meta)
        monkeypatch.setattr(agent, "_update_conversation_meta", fake_update_meta)

        messages = []
        await agent._inject_file_context("test-conv", messages)

        ctx = messages[0]["content"]
        assert "CROSS-FILE ANALYSIS" not in ctx

    @pytest.mark.asyncio
    async def test_no_crossfile_hint_with_non_tabular(self, monkeypatch):
        import agent

        async def fake_get_files(conv_id):
            return [
                {"filename": "doc.pdf", "row_count": 0, "col_names": [], "data_text": "pdf text"},
                {"filename": "slides.pptx", "row_count": 0, "col_names": [], "data_text": "pptx text"},
            ]

        async def fake_get_meta(conv_id):
            return {"mode": "general"}

        async def fake_update_meta(conv_id, **kw):
            pass

        monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_get_files)
        monkeypatch.setattr(agent, "_get_conversation_meta", fake_get_meta)
        monkeypatch.setattr(agent, "_update_conversation_meta", fake_update_meta)

        messages = []
        await agent._inject_file_context("test-conv", messages)

        ctx = messages[0]["content"]
        assert "CROSS-FILE ANALYSIS" not in ctx


class TestParquetMounting:
    """Test that _load_uploaded_files_for_code mounts parquet alongside csv."""

    def test_parquet_mount_code_path(self):
        """Verify the code path exists — full integration requires Azure Storage."""
        import tools
        import inspect
        source = inspect.getsource(tools._load_uploaded_files_for_code)
        assert ".parquet" in source
        assert "artifact_bytes" in source
