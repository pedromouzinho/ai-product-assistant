from __future__ import annotations

import io

import openpyxl
import pytest

import tools
from tabular_artifacts import (
    aggregate_tabular_artifact_by_period,
    build_tabular_artifact,
    compare_tabular_artifact_periods,
    compute_tabular_artifact_numeric_metrics,
    export_tabular_artifact_as_csv_bytes,
    load_tabular_artifact_time_series,
    load_tabular_artifact_dataset,
    load_tabular_artifact_preview,
    profile_tabular_artifact_columns,
    summarize_tabular_artifact_values,
)


def _sample_xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Category", "Revenue"])
    sheet.append(["2026-01-01", "A", 10])
    sheet.append(["2026-01-02", "B", 20])
    sheet.append(["2026-01-03", "A", 30])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _large_csv_bytes(row_count: int = 6000) -> bytes:
    lines = ["Date,Category,Revenue"]
    for idx in range(1, row_count + 1):
        day = ((idx - 1) % 28) + 1
        category = "A" if idx % 2 else "B"
        lines.append(f"2026-01-{day:02d},{category},{idx}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _period_csv_bytes() -> bytes:
    lines = [
        "Date,Category,Revenue",
        "2026-01-01,A,10",
        "2026-01-15,A,20",
        "2026-02-01,B,30",
        "2026-02-10,B,40",
        "2026-03-05,C,50",
        "2027-01-03,A,60",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_build_tabular_artifact_creates_parquet_dataset():
    artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx", batch_rows=2)

    assert artifact["format"] == "parquet"
    assert artifact["row_count"] == 3
    assert artifact["columns"] == ["Date", "Category", "Revenue"]
    assert artifact["artifact_bytes"]

    dataset = load_tabular_artifact_dataset(artifact["artifact_bytes"], max_rows=10)
    assert dataset["row_count"] == 3
    assert dataset["rows_loaded"] == 3
    assert dataset["records"][0]["Revenue"] == "10"


def test_load_tabular_artifact_preview_returns_expected_shape():
    artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx", batch_rows=2)
    preview = load_tabular_artifact_preview(artifact["artifact_bytes"], preview_rows=2, preview_char_limit=1000)

    assert preview["columns"] == ["Date", "Category", "Revenue"]
    assert preview["row_count"] == 3
    assert preview["sample_records"][0]["Category"] == "A"
    assert "2026-01-01" in preview["data_text"]


def test_export_tabular_artifact_as_csv_bytes_returns_csv():
    artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx", batch_rows=2)
    csv_bytes = export_tabular_artifact_as_csv_bytes(artifact["artifact_bytes"])
    text = csv_bytes.decode("utf-8")

    assert "Date,Category,Revenue" in text
    assert "2026-01-01,A,10" in text


def test_compute_tabular_artifact_numeric_metrics_uses_full_dataset():
    artifact = build_tabular_artifact(_large_csv_bytes(row_count=10), "sample.csv")

    metrics = compute_tabular_artifact_numeric_metrics(
        artifact["artifact_bytes"],
        column="Revenue",
        requested_metrics=["count", "sum", "mean", "min", "max", "median"],
    )

    assert metrics["count"] == 10
    assert metrics["sum"] == 55.0
    assert metrics["mean"] == 5.5
    assert metrics["min"] == 1.0
    assert metrics["max"] == 10.0
    assert metrics["median"] == 5.5


def test_summarize_tabular_artifact_values_returns_distinct_counts():
    artifact = build_tabular_artifact(_large_csv_bytes(row_count=12), "sample.csv")

    summary = summarize_tabular_artifact_values(
        artifact["artifact_bytes"],
        column="Category",
        top_n=10,
        all_limit=10,
    )

    assert summary["non_empty_count"] == 12
    assert summary["empty_count"] == 0
    assert summary["distinct_count"] == 2
    assert summary["top_values"][0][0] in {"A", "B"}
    assert summary["top_values"][0][1] == 6
    assert len(summary["all_values"]) == 2


def test_aggregate_tabular_artifact_by_period_groups_all_rows():
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    grouped = aggregate_tabular_artifact_by_period(
        artifact["artifact_bytes"],
        date_column="Date",
        value_column="Revenue",
        group_mode="month",
        requested_metrics=["sum", "mean"],
    )

    assert grouped["rows_processed"] == 6
    assert grouped["numeric_points"] == 6
    assert grouped["groups"] == [
        {"group": "2026-01", "count": 2, "metrics": {"sum": 30.0, "mean": 15.0}},
        {"group": "2026-02", "count": 2, "metrics": {"sum": 70.0, "mean": 35.0}},
        {"group": "2026-03", "count": 1, "metrics": {"sum": 50.0, "mean": 50.0}},
        {"group": "2027-01", "count": 1, "metrics": {"sum": 60.0, "mean": 60.0}},
    ]


def test_compare_tabular_artifact_periods_compares_full_periods():
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    compared = compare_tabular_artifact_periods(
        artifact["artifact_bytes"],
        date_column="Date",
        value_column="Revenue",
        period1="2026-01",
        period2="2026-02",
        requested_metrics=["sum", "mean"],
    )

    assert compared == {
        "period1": {"count": 2, "numeric_count": 2, "metrics": {"sum": 30.0, "mean": 15.0}},
        "period2": {"count": 2, "numeric_count": 2, "metrics": {"sum": 70.0, "mean": 35.0}},
    }


def test_load_tabular_artifact_time_series_supports_sampling_and_full_points():
    artifact = build_tabular_artifact(_large_csv_bytes(row_count=6000), "sample.csv")

    sampled = load_tabular_artifact_time_series(
        artifact["artifact_bytes"],
        date_column="Date",
        value_column="Revenue",
        max_points=200,
        full_points=False,
    )
    full = load_tabular_artifact_time_series(
        artifact["artifact_bytes"],
        date_column="Date",
        value_column="Revenue",
        max_points=200,
        full_points=True,
    )

    assert sampled["total_points"] == 6000
    assert sampled["sampled"] is True
    assert len(sampled["points"]) <= 200
    assert full["total_points"] == 6000
    assert full["sampled"] is False
    assert len(full["points"]) == 6000
    values = [point[1] for point in full["points"]]
    assert min(values) == 1.0
    assert max(values) == 6000.0


def test_profile_tabular_artifact_columns_profiles_full_dataset():
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    profiles = profile_tabular_artifact_columns(
        artifact["artifact_bytes"],
        columns=["Date", "Category", "Revenue"],
    )
    by_name = {profile["name"]: profile for profile in profiles}

    assert by_name["Revenue"]["type"] == "numeric"
    assert by_name["Revenue"]["non_empty"] == 6
    assert by_name["Revenue"]["min"] == 10.0
    assert by_name["Revenue"]["max"] == 60.0
    assert by_name["Category"]["type"] == "text"
    assert by_name["Category"]["distinct_count"] == 3
    assert by_name["Date"]["type"] == "datetime"
    assert by_name["Date"]["non_empty"] == 6


@pytest.mark.asyncio
async def test_resolve_uploaded_tabular_source_prefers_artifact(monkeypatch):
    async def _fake_table_query(*_args, **_kwargs):
        return [
            {
                "Filename": "sample.xlsx",
                "TabularArtifactBlobRef": "upload-artifacts/sample.parquet",
                "TabularArtifactFormat": "parquet",
                "RawBlobRef": "upload-raw/sample.xlsx",
                "UploadedAt": "2026-03-13T10:00:00+00:00",
            }
        ]

    downloads = []

    async def _fake_blob_download_bytes(container, blob_name):
        downloads.append(f"{container}/{blob_name}")
        artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx")
        return artifact["artifact_bytes"]

    monkeypatch.setattr(tools, "table_query", _fake_table_query)
    monkeypatch.setattr(tools, "blob_download_bytes", _fake_blob_download_bytes)

    resolved = await tools._resolve_uploaded_tabular_source("conv-1", "jane", "sample.xlsx")

    assert resolved["source_kind"] == "artifact"
    assert resolved["artifact_format"] == "parquet"
    assert downloads == ["upload-artifacts/sample.parquet"]


@pytest.mark.asyncio
async def test_load_uploaded_files_for_code_falls_back_to_artifact(monkeypatch):
    async def _fake_table_query(*_args, **_kwargs):
        return [
            {
                "Filename": "sample.xlsx",
                "TabularArtifactBlobRef": "upload-artifacts/sample.parquet",
                "UploadedAt": "2026-03-13T10:00:00+00:00",
            }
        ]

    async def _fake_blob_download_bytes(container, blob_name):
        assert container == "upload-artifacts"
        assert blob_name == "sample.parquet"
        artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx")
        return artifact["artifact_bytes"]

    monkeypatch.setattr(tools, "table_query", _fake_table_query)
    monkeypatch.setattr(tools, "blob_download_bytes", _fake_blob_download_bytes)

    mounted = await tools._load_uploaded_files_for_code("conv-1", user_sub="jane", filename="sample.xlsx")

    assert "sample.csv" in mounted
    assert b"Date,Category,Revenue" in mounted["sample.csv"]
    # A5: Parquet also mounted alongside CSV for DuckDB cross-file JOINs
    assert "sample.parquet" in mounted


@pytest.mark.asyncio
async def test_load_uploaded_files_for_code_prefers_artifact_over_raw(monkeypatch):
    async def _fake_table_query(*_args, **_kwargs):
        return [
            {
                "Filename": "sample.xlsx",
                "TabularArtifactBlobRef": "upload-artifacts/sample.parquet",
                "RawBlobRef": "upload-raw/sample.xlsx",
                "UploadedAt": "2026-03-13T10:00:00+00:00",
            }
        ]

    downloads = []
    artifact = build_tabular_artifact(_sample_xlsx_bytes(), "sample.xlsx")

    async def _fake_blob_download_bytes(container, blob_name):
        downloads.append(f"{container}/{blob_name}")
        if container == "upload-artifacts":
            return artifact["artifact_bytes"]
        raise AssertionError("raw blob should not be used when artifact is available")

    monkeypatch.setattr(tools, "table_query", _fake_table_query)
    monkeypatch.setattr(tools, "blob_download_bytes", _fake_blob_download_bytes)

    mounted = await tools._load_uploaded_files_for_code("conv-1", user_sub="jane", filename="sample.xlsx")

    assert "sample.csv" in mounted
    assert b"Date,Category,Revenue" in mounted["sample.csv"]
    # A5: Parquet also mounted alongside CSV
    assert "sample.parquet" in mounted
    assert downloads == ["upload-artifacts/sample.parquet"]


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_full_artifact_rows_beyond_inference_sample(monkeypatch):
    artifact = build_tabular_artifact(_large_csv_bytes(), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="qual a soma da revenue",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
        value_column="Revenue",
        agg="sum",
    )

    assert result["row_count"] == 6000
    assert result["groups"] == [{"group": "overall", "value": 18003000.0, "count": 6000}]
    assert result["analysis_quality"]["rows_processed"] == 6000
    assert result["analysis_quality"]["rows_total"] == 6000
    assert result["analysis_quality"]["coverage"] == 1.0
    assert not result["analysis_quality"]["warnings"]


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_artifact_for_categorical_summary(monkeypatch):
    artifact = build_tabular_artifact(_large_csv_bytes(row_count=6000), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="quais são os valores distintos da category",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
        value_column="Category",
    )

    assert result["categorical"] is True
    assert result["distinct_count"] == 2
    assert result["non_empty_count"] == 6000
    assert result["analysis_quality"]["rows_processed"] == 6000
    assert {group["group"] for group in result["groups"]} == {"A", "B"}


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_artifact_for_month_grouping(monkeypatch):
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="mostra a soma da revenue por mês",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
        value_column="Revenue",
        date_column="Date",
        group_by="month",
        agg="sum",
    )

    assert result["group_by"] == "month"
    assert result["groups"] == [
        {"group": "2026-01", "value": 30.0, "count": 2},
        {"group": "2026-02", "value": 70.0, "count": 2},
        {"group": "2026-03", "value": 50.0, "count": 1},
        {"group": "2027-01", "value": 60.0, "count": 1},
    ]
    assert result["analysis_quality"]["rows_processed"] == 6
    assert result["analysis_quality"]["coverage"] == 1.0


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_artifact_for_period_comparison(monkeypatch):
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="compara janeiro e fevereiro de 2026 pela soma da revenue",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
        value_column="Revenue",
        date_column="Date",
        agg="sum",
        compare_periods={"period1": "2026-01", "period2": "2026-02"},
    )

    assert result["comparison"] is True
    assert result["period1"] == {"name": "2026-01", "metrics": {"sum": 30.0}, "count": 2}
    assert result["period2"] == {"name": "2026-02", "metrics": {"sum": 70.0}, "count": 2}
    assert result["delta"] == {"sum": 40.0}
    assert result["analysis_quality"]["rows_processed"] == 6
    assert result["analysis_quality"]["coverage"] == 0.6667


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_artifact_for_time_series_sampling(monkeypatch):
    artifact = build_tabular_artifact(_large_csv_bytes(row_count=6000), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="faz um gráfico de linha da revenue ao longo do tempo",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
        value_column="Revenue",
        date_column="Date",
        top=200,
    )

    assert len(result["groups"]) <= 200
    assert result["analysis_quality"]["rows_processed"] == 6000
    assert result["analysis_quality"]["coverage"] == 1.0
    assert result["analysis_quality"]["sampled"] is True
    assert any("Série temporal amostrada" in warning for warning in result["analysis_quality"]["warnings"])


@pytest.mark.asyncio
async def test_analyze_uploaded_table_uses_artifact_for_schema_profiles(monkeypatch):
    artifact = build_tabular_artifact(_period_csv_bytes(), "sample.csv")

    async def _fake_resolve_uploaded_tabular_source(_conv_id, _user_sub="", _filename=""):
        return {
            "filename": "sample.csv",
            "source_kind": "artifact",
            "artifact_bytes": artifact["artifact_bytes"],
            "artifact_format": "parquet",
        }

    monkeypatch.setattr(tools, "_resolve_uploaded_tabular_source", _fake_resolve_uploaded_tabular_source)

    result = await tools.tool_analyze_uploaded_table(
        query="qual a estrutura e schema desta tabela",
        conv_id="conv-1",
        user_sub="jane",
        filename="sample.csv",
    )

    assert result["row_count"] == 6
    assert result["analysis_quality"]["coverage"] == 1.0
    assert result["analysis_quality"]["sampled"] is False
    profiles = {profile["name"]: profile for profile in result["column_profiles"]}
    assert profiles["Revenue"]["type"] == "numeric"
    assert profiles["Category"]["type"] == "text"
    assert profiles["Date"]["type"] == "datetime"
