from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Callable
from urllib.parse import unquote

from fastapi import HTTPException, UploadFile
from utils import safe_blob_component


def build_upload_request_body_limits(
    *,
    max_upload_file_bytes: int,
    get_tabular_upload_limit_bytes: Callable[[str, int], int],
    upload_max_batch_total_bytes: int,
    multipart_overhead_bytes: int,
) -> tuple[int, int]:
    max_single_upload_request_body_bytes = (
        max(
            int(max_upload_file_bytes),
            int(get_tabular_upload_limit_bytes("sample.csv", max_upload_file_bytes)),
            int(get_tabular_upload_limit_bytes("sample.tsv", max_upload_file_bytes)),
            int(get_tabular_upload_limit_bytes("sample.xlsx", max_upload_file_bytes)),
            int(get_tabular_upload_limit_bytes("sample.xlsb", max_upload_file_bytes)),
            int(get_tabular_upload_limit_bytes("sample.xls", max_upload_file_bytes)),
        )
        + int(multipart_overhead_bytes)
    )
    max_batch_upload_request_body_bytes = int(upload_max_batch_total_bytes) + int(multipart_overhead_bytes)
    return max_single_upload_request_body_bytes, max_batch_upload_request_body_bytes


def build_upload_blob_paths(conv_id: str, job_id: str, filename: str) -> dict:
    safe_name = safe_blob_component(filename or "upload.bin", max_len=180)
    prefix = f"{safe_blob_component(conv_id, max_len=90)}/{safe_blob_component(job_id, max_len=90)}"
    return {
        "raw_blob_name": f"{prefix}/raw/{safe_name}",
        "text_blob_name": f"{prefix}/extracted/text.txt",
        "chunks_blob_name": f"{prefix}/extracted/chunks.json",
        "artifact_blob_name": f"{prefix}/artifact/data.parquet",
    }


def build_upload_auto_summary(
    store_entry: dict,
    filename: str,
    *,
    is_tabular_filename: Callable[[str], bool],
) -> str:
    parts = []
    row_count = int(store_entry.get("row_count", 0) or 0)
    col_names = store_entry.get("col_names", [])
    if not isinstance(col_names, list):
        col_names = []
    is_tabular = is_tabular_filename(filename)

    if is_tabular and row_count > 0:
        parts.append(f"📊 **{filename}** — {row_count:,} linhas × {len(col_names)} colunas")
    elif is_tabular:
        parts.append(f"📊 **{filename}** carregado")
    else:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        icon = {"pdf": "📄", "pptx": "📑", "docx": "📝", "txt": "📃"}.get(ext, "📎")
        parts.append(f"{icon} **{filename}** carregado")

    if col_names:
        if len(col_names) <= 8:
            parts.append(f"\n**Colunas:** {', '.join(col_names)}")
        else:
            parts.append(f"\n**Colunas ({len(col_names)}):** {', '.join(col_names[:6])}, …")

    full_stats = store_entry.get("full_col_stats")
    if isinstance(full_stats, list) and full_stats:
        numeric_cols = [s for s in full_stats if s.get("type") == "numeric"]
        if numeric_cols:
            highlights = []
            for s in numeric_cols[:3]:
                name = str(s.get("name", ""))
                mn, mx = s.get("min"), s.get("max")
                avg = s.get("mean")
                if mn is not None and mx is not None:
                    avg_str = f", avg {avg}" if avg is not None else ""
                    highlights.append(f"`{name}`: {mn}–{mx}{avg_str}")
            if highlights:
                parts.append(f"\n**Métricas:** {' · '.join(highlights)}")

    if isinstance(full_stats, list):
        for s in full_stats:
            if s.get("type") == "text" and s.get("first") and s.get("last"):
                name = str(s.get("name", ""))
                if any(kw in name.lower() for kw in ("data", "date", "created", "updated", "timestamp")):
                    parts.append(f"\n**Período:** `{name}` de {s['first']} a {s['last']}")
                    break

    poly = store_entry.get("polymorphic_schema")
    if isinstance(poly, dict) and poly.get("is_polymorphic"):
        pivot = poly.get("pivot_column", "")
        count = poly.get("pivot_values_count", 0)
        parts.append(f"\n⚠️ Dataset polimórfico detetado (pivô: `{pivot}`, {count} perfis)")

    if store_entry.get("has_chunks") or (isinstance(store_entry.get("chunks"), list) and store_entry.get("chunks")):
        parts.append("\n🔍 Documento indexado com pesquisa semântica disponível")

    if is_tabular:
        parts.append("\n\nPodes perguntar-me sobre análises, gráficos, filtros, ou resumos deste ficheiro.")
    else:
        parts.append("\n\nPodes perguntar-me sobre o conteúdo deste ficheiro.")

    return "".join(parts)


def retention_until_iso(*, hours: int) -> str:
    safe_hours = max(1, int(hours or 1))
    return (datetime.now(timezone.utc) + timedelta(hours=safe_hours)).isoformat()


def raw_blob_retention_until_iso(
    *,
    filename: str,
    artifact_blob_ref: str,
    has_chunks: bool,
    fallback_hours: int,
    upload_tabular_raw_retention_hours: int,
    upload_tabular_ready_raw_retention_hours: int,
    is_tabular_filename: Callable[[str], bool],
) -> str:
    safe_fallback_hours = max(1, int(fallback_hours or 1))
    if artifact_blob_ref and is_tabular_filename(str(filename or "")):
        raw_hours = upload_tabular_ready_raw_retention_hours if has_chunks else upload_tabular_raw_retention_hours
        return retention_until_iso(hours=max(1, int(raw_hours or 1)))
    return retention_until_iso(hours=safe_fallback_hours)


def parse_iso_dt(value: str):
    try:
        txt = str(value or "").strip()
        if not txt:
            return None
        parsed = datetime.fromisoformat(txt)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def is_retention_expired(value: str, now=None) -> bool:
    expires_at = parse_iso_dt(value)
    if not expires_at:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return now_dt >= expires_at


def effective_raw_blob_retention_until(row: dict) -> str:
    return str(row.get("RawBlobRetentionUntil", "") or row.get("RetentionUntil", "") or "").strip()


def is_job_stale(job: dict, *, stale_seconds: int, now=None) -> bool:
    status = str(job.get("status", "")).lower()
    if status not in ("queued", "processing"):
        return False
    now_dt = now or datetime.now(timezone.utc)
    ref_dt = parse_iso_dt(str(job.get("updated_at", "") or "")) or parse_iso_dt(str(job.get("created_at", "") or ""))
    if not ref_dt:
        return False
    return (now_dt - ref_dt).total_seconds() > stale_seconds


def is_export_job_stale(job: dict, *, stale_seconds: int, now=None) -> bool:
    status = str(job.get("status", "")).lower()
    if status != "processing":
        return False
    now_dt = now or datetime.now(timezone.utc)
    ref_dt = parse_iso_dt(str(job.get("updated_at", "") or "")) or parse_iso_dt(str(job.get("created_at", "") or ""))
    if not ref_dt:
        return False
    return (now_dt - ref_dt).total_seconds() > stale_seconds


def job_public_view(job: dict) -> dict:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "worker_id": job.get("worker_id", ""),
        "conversation_id": job.get("conversation_id"),
        "filename": job.get("filename"),
        "size_bytes": job.get("size_bytes", 0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "result": job.get("result"),
    }


def upload_job_from_storage_row(row: dict, *, fallback_job_id: str = "") -> dict:
    result_payload = None
    raw_result = row.get("ResultJson")
    if raw_result:
        try:
            result_payload = json.loads(raw_result)
        except Exception:
            result_payload = None
    return {
        "job_id": row.get("RowKey", fallback_job_id),
        "status": row.get("Status", "queued"),
        "conversation_id": row.get("ConversationId", ""),
        "filename": row.get("Filename", ""),
        "content_type": row.get("ContentType", ""),
        "size_bytes": int(row.get("SizeBytes", 0) or 0),
        "created_at": row.get("CreatedAt", ""),
        "updated_at": row.get("UpdatedAt", ""),
        "started_at": row.get("StartedAt", ""),
        "finished_at": row.get("FinishedAt", ""),
        "error": row.get("Error", ""),
        "result": result_payload,
        "user_sub": row.get("UserSub", ""),
        "worker_id": row.get("WorkerId", ""),
        "claim_token": row.get("ClaimToken", ""),
        "raw_blob_ref": row.get("RawBlobRef", ""),
        "text_blob_name": row.get("TextBlobName", ""),
        "chunks_blob_name": row.get("ChunksBlobName", ""),
        "artifact_blob_name": row.get("ArtifactBlobName", ""),
        "artifact_blob_ref": row.get("ArtifactBlobRef", ""),
        "artifact_format": row.get("ArtifactFormat", ""),
        "raw_blob_retention_until": row.get("RawBlobRetentionUntil", ""),
        "raw_blob_purged_at": row.get("RawBlobPurgedAt", ""),
        "retention_until": row.get("RetentionUntil", ""),
    }


def upload_index_row_to_memory_entry(row: dict) -> dict:
    col_names = []
    col_analysis = []
    polymorphic_schema = None
    try:
        col_names = json.loads(row.get("ColNamesJson", "[]") or "[]")
    except Exception:
        col_names = []
    try:
        col_analysis = json.loads(row.get("ColAnalysisJson", "[]") or "[]")
    except Exception:
        col_analysis = []
    try:
        parsed_schema = json.loads(row.get("PolymorphicSchemaJson", "") or "")
        if isinstance(parsed_schema, dict) and parsed_schema.get("is_polymorphic"):
            polymorphic_schema = parsed_schema
    except Exception:
        polymorphic_schema = None
    if polymorphic_schema is None:
        poly_summary = str(row.get("PolymorphicSummary", "") or "").strip()
        pivot_column = str(row.get("PivotColumn", "") or "").strip()
        if poly_summary:
            polymorphic_schema = {
                "is_polymorphic": True,
                "summary_text": poly_summary,
                "pivot_column": pivot_column,
                "pivot_values_count": int(row.get("PivotValuesCount", 0) or 0),
            }
    return {
        "filename": row.get("Filename", ""),
        "data_text": row.get("PreviewText", ""),
        "row_count": int(row.get("RowCount", 0) or 0),
        "col_names": col_names if isinstance(col_names, list) else [],
        "col_analysis": col_analysis if isinstance(col_analysis, list) else [],
        "truncated": bool(row.get("Truncated", False)),
        "uploaded_at": row.get("UploadedAt", ""),
        "has_chunks": str(row.get("HasChunks", "")).lower() in ("true", "1"),
        "chunks_blob_ref": row.get("ChunksBlobRef", ""),
        "extracted_blob_ref": row.get("ExtractedBlobRef", ""),
        "tabular_artifact_blob_ref": row.get("TabularArtifactBlobRef", ""),
        "tabular_artifact_format": row.get("TabularArtifactFormat", ""),
        "tabular_artifact_row_count": int(row.get("TabularArtifactRowCount", 0) or 0),
        "polymorphic_schema": polymorphic_schema,
    }


def build_queued_upload_job(
    *,
    job_id: str,
    conversation_id: str,
    user_sub: str,
    filename: str,
    size_bytes: int,
    content_type: str,
    raw_blob_ref: str,
    blob_paths: dict,
    retention_until: str,
    raw_blob_retention_until: str,
    result=None,
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "job_id": job_id,
        "status": "queued",
        "conversation_id": conversation_id,
        "filename": filename,
        "size_bytes": int(size_bytes or 0),
        "created_at": now_iso,
        "updated_at": now_iso,
        "started_at": "",
        "finished_at": "",
        "error": "",
        "result": result,
        "user_sub": user_sub,
        "content_type": (content_type or "")[:120],
        "raw_blob_ref": raw_blob_ref,
        "text_blob_name": str((blob_paths or {}).get("text_blob_name", "") or ""),
        "chunks_blob_name": str((blob_paths or {}).get("chunks_blob_name", "") or ""),
        "artifact_blob_name": str((blob_paths or {}).get("artifact_blob_name", "") or ""),
        "artifact_blob_ref": "",
        "artifact_format": "",
        "raw_blob_retention_until": raw_blob_retention_until,
        "raw_blob_purged_at": "",
        "retention_until": retention_until,
    }


def normalize_uploaded_conv_entry(current_entry: dict | None, *, uploaded_at_iso: str) -> dict:
    current = current_entry
    if isinstance(current, dict) and isinstance(current.get("files"), list):
        return current

    files = []
    if isinstance(current, dict) and current:
        legacy = dict(current)
        legacy.pop("files", None)
        files = [legacy]

    return {
        "files": files,
        "uploaded_at": uploaded_at_iso,
    }


def file_entry_summary(entry: dict) -> dict:
    polymorphic_schema = entry.get("polymorphic_schema")
    is_polymorphic = bool(isinstance(polymorphic_schema, dict) and polymorphic_schema.get("is_polymorphic"))
    return {
        "filename": entry.get("filename", ""),
        "rows": entry.get("row_count", 0),
        "columns": entry.get("col_names", []),
        "truncated": bool(entry.get("truncated")),
        "uploaded_at": entry.get("uploaded_at", ""),
        "has_chunks": isinstance(entry.get("chunks"), list) and len(entry.get("chunks", [])) > 0,
        "is_image": bool(entry.get("image_base64")),
        "polymorphic": is_polymorphic,
        "pivot_column": polymorphic_schema.get("pivot_column", "") if is_polymorphic else "",
        "pivot_values_count": int(polymorphic_schema.get("pivot_values_count", 0) or 0) if is_polymorphic else 0,
    }


async def read_upload_with_limit(upload: UploadFile, max_bytes: int) -> bytes:
    total = 0
    chunks = []
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"Ficheiro excede limite máximo de {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def max_upload_bytes_for_file(
    filename: str,
    *,
    get_tabular_upload_limit_bytes: Callable[[str, int], int],
    max_upload_file_bytes: int,
) -> int:
    return int(get_tabular_upload_limit_bytes(filename, max_upload_file_bytes))


def request_body_limit_bytes(
    request,
    *,
    max_request_body_bytes: int,
    max_single_upload_request_body_bytes: int,
    max_batch_upload_request_body_bytes: int,
    max_upload_bytes_for_file: Callable[[str], int],
) -> int:
    req_path = str(getattr(getattr(request, "url", None), "path", "") or "").strip()
    if req_path == "/upload/stream/async":
        encoded_filename = str(request.headers.get("x-upload-filename", "") or "").strip()
        filename = unquote(encoded_filename) if encoded_filename else ""
        if filename:
            return max(max_request_body_bytes, max_upload_bytes_for_file(filename))
        return max_request_body_bytes
    if req_path in {"/upload", "/upload/async"}:
        return max(max_request_body_bytes, max_single_upload_request_body_bytes)
    if req_path == "/upload/batch/async":
        return max(max_request_body_bytes, max_batch_upload_request_body_bytes)
    return max_request_body_bytes
