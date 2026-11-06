"""Source loading helpers for the next-generation PPTX lane."""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, Sequence

from presentation_reasoning import PresentationSource, classify_source_kind

UploadLoader = Callable[[str], Awaitable[Sequence[dict]]]
BlobTextLoader = Callable[[str], Awaitable[str]]


def build_presentation_sources(
    *,
    content: str = "",
    context: str = "",
    uploads: Sequence[dict] = (),
) -> Sequence[PresentationSource]:
    sources = []

    if str(content or "").strip():
        sources.append(
            PresentationSource(
                name="inline_request_content.txt",
                content=str(content).strip(),
                kind=classify_source_kind("inline_request_content.txt", "text/plain"),
                authority_hint=-0.15,
            )
        )

    if str(context or "").strip():
        sources.append(
            PresentationSource(
                name="request_context.txt",
                content=str(context).strip(),
                kind=classify_source_kind("request_context.txt", "text/plain"),
                authority_hint=-0.2,
            )
        )

    for upload in uploads or ():
        filename = str(upload.get("filename") or "uploaded_file").strip() or "uploaded_file"
        data_text = str(upload.get("data_text") or "").strip()
        if not data_text:
            continue
        mime_type = str(
            upload.get("content_type")
            or upload.get("mime_type")
            or ""
        ).strip()
        kind = classify_source_kind(filename, mime_type)
        is_generated_artifact = bool(upload.get("is_generated_artifact"))
        if kind.value == "pptx":
            is_generated_artifact = is_generated_artifact or _looks_like_generated_pptx(filename)

        sources.append(
            PresentationSource(
                name=filename,
                content=data_text,
                kind=kind,
                mime_type=mime_type,
                is_generated_artifact=is_generated_artifact,
                authority_hint=0.0,
            )
        )

    return tuple(sources)


async def load_conversation_presentation_sources(
    *,
    conv_id: str = "",
    content: str = "",
    context: str = "",
    upload_loader: Optional[UploadLoader] = None,
    blob_text_loader: Optional[BlobTextLoader] = None,
) -> Sequence[PresentationSource]:
    uploads = ()
    if conv_id:
        loader = upload_loader or _default_upload_loader
        try:
            uploads = await loader(conv_id)
        except Exception:
            uploads = ()
    enriched_uploads = await _hydrate_presentation_uploads(
        uploads,
        blob_text_loader=blob_text_loader or _default_blob_text_loader,
    )
    return build_presentation_sources(content=content, context=context, uploads=enriched_uploads)


async def _default_upload_loader(conv_id: str) -> Sequence[dict]:
    from agent import _get_uploaded_files_async

    return await _get_uploaded_files_async(conv_id)


async def _default_blob_text_loader(blob_ref: str) -> str:
    from storage import blob_download_bytes, parse_blob_ref

    container, blob_name = parse_blob_ref(blob_ref)
    if not container or not blob_name:
        return ""
    raw = await blob_download_bytes(container, blob_name)
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


async def _hydrate_presentation_uploads(
    uploads: Sequence[dict],
    *,
    blob_text_loader: Optional[BlobTextLoader] = None,
) -> Sequence[dict]:
    hydrated = []
    for upload in uploads or ():
        item = dict(upload or {})
        extracted_blob_ref = str(item.get("extracted_blob_ref") or "").strip()
        current_text = str(item.get("data_text") or "")
        if extracted_blob_ref and blob_text_loader is not None:
            try:
                full_text = str(await blob_text_loader(extracted_blob_ref) or "")
                if len(full_text.strip()) > len(current_text.strip()):
                    item["data_text"] = full_text[:120000]
                    item["source_text_origin"] = "extracted_blob"
            except Exception:
                pass
        hydrated.append(item)
    return tuple(hydrated)


def _looks_like_generated_pptx(filename: str) -> bool:
    lower = str(filename or "").lower()
    return lower.endswith(".pptx") and any(
        marker in lower
        for marker in (
            "benchmark neobank business vs acme",
            "revampfee",
            "estado do backlog",
            "mvp2",
        )
    )
