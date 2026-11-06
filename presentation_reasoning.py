"""Document reasoning helpers for the next-generation PPTX orchestrator.

This module is intentionally deterministic and lightweight. It does not try to
replace model reasoning; it provides cleaner source selection and evidence
grounding so the model can make better decisions later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
from typing import Iterable, List, Optional, Sequence


class SourceKind(str, Enum):
    PDF = "pdf"
    PPTX = "pptx"
    XLSX = "xlsx"
    DOCX = "docx"
    HTML = "html"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PresentationSource:
    name: str
    content: str
    kind: SourceKind = SourceKind.UNKNOWN
    mime_type: str = ""
    is_generated_artifact: bool = False
    authority_hint: float = 0.0


def classify_source_kind(name: str = "", mime_type: str = "") -> SourceKind:
    ext = os.path.splitext(str(name or "").lower())[1]
    if ext == ".pdf" or "pdf" in mime_type:
        return SourceKind.PDF
    if ext == ".pptx" or "presentation" in mime_type:
        return SourceKind.PPTX
    if ext in (".xlsx", ".xls", ".xlsb") or "spreadsheet" in mime_type or "excel" in mime_type:
        return SourceKind.XLSX
    if ext == ".docx" or "wordprocessingml" in mime_type:
        return SourceKind.DOCX
    if ext in (".html", ".htm") or "html" in mime_type:
        return SourceKind.HTML
    if ext in (".txt", ".md") or mime_type.startswith("text/"):
        return SourceKind.TEXT
    return SourceKind.UNKNOWN


def normalize_source(source: PresentationSource) -> PresentationSource:
    kind = source.kind if source.kind != SourceKind.UNKNOWN else classify_source_kind(source.name, source.mime_type)
    return PresentationSource(
        name=source.name,
        content=source.content,
        kind=kind,
        mime_type=source.mime_type,
        is_generated_artifact=source.is_generated_artifact,
        authority_hint=source.authority_hint,
    )


def authority_score(source: PresentationSource, capability: str = "general") -> float:
    source = normalize_source(source)
    base = {
        SourceKind.PDF: 1.0,
        SourceKind.XLSX: 0.98,
        SourceKind.DOCX: 0.92,
        SourceKind.HTML: 0.9,
        SourceKind.TEXT: 0.82,
        SourceKind.PPTX: 0.7,
        SourceKind.UNKNOWN: 0.5,
    }[source.kind]

    capability_bonus = 0.0
    if capability == "benchmark_executive" and source.kind == SourceKind.PDF:
        capability_bonus += 0.1
    if capability == "backlog_steering" and source.kind == SourceKind.XLSX:
        capability_bonus += 0.05

    generated_penalty = 0.0
    if source.is_generated_artifact:
        generated_penalty += 0.6
    if _looks_like_generated_deck_name(source.name):
        generated_penalty += 0.15

    return max(0.0, base + capability_bonus + source.authority_hint - generated_penalty)


def select_authoritative_sources(
    sources: Sequence[PresentationSource],
    *,
    capability: str = "general",
    max_sources: int = 3,
) -> List[PresentationSource]:
    normalized = [normalize_source(src) for src in sources if str(src.content or "").strip()]
    if not normalized:
        return []

    ranked = sorted(
        normalized,
        key=lambda src: (
            authority_score(src, capability),
            len(src.content or ""),
        ),
        reverse=True,
    )

    selected: List[PresentationSource] = []
    seen_names = set()
    for src in ranked:
        if src.name in seen_names:
            continue
        selected.append(src)
        seen_names.add(src.name)
        if len(selected) >= max_sources:
            break
    return selected


def summarize_source_selection(
    sources: Sequence[PresentationSource],
    *,
    capability: str = "general",
) -> str:
    selected = select_authoritative_sources(sources, capability=capability)
    if not selected:
        return "No authoritative sources were available."

    parts = []
    for src in selected:
        score = authority_score(src, capability)
        parts.append(f"{src.name} ({src.kind.value}, score={score:.2f})")
    return "Primary sources selected: " + "; ".join(parts)


def extract_grounded_evidence_text(
    sources: Sequence[PresentationSource],
    *,
    capability: str = "general",
    max_lines: int = 60,
) -> List[str]:
    """Extract short, high-signal evidence lines from authoritative sources."""
    selected = select_authoritative_sources(sources, capability=capability)
    lines: List[str] = []
    seen = set()
    for src in selected:
        for raw_line in src.content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if len(line) > 260:
                continue
            if _is_low_signal_line(line):
                continue
            key = re.sub(r"\s+", " ", line.lower())
            if key in seen:
                continue
            if _looks_like_evidence_line(line, capability):
                seen.add(key)
                lines.append(line)
            if len(lines) >= max_lines:
                return lines
    return lines


def _looks_like_generated_deck_name(name: str) -> bool:
    lower = str(name or "").lower()
    markers = (
        "benchmark neobank business vs acme",
        "revampfee",
        "estado do backlog",
        "pptx",
    )
    return lower.endswith(".pptx") and any(marker in lower for marker in markers)


def _is_low_signal_line(line: str) -> bool:
    lower = line.lower()
    if len(lower) < 4:
        return True
    if lower in {"produto", "obrigado", "agenda", "contexto"}:
        return True
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", lower):
        return True
    return False


def _looks_like_evidence_line(line: str, capability: str) -> bool:
    lower = line.lower()
    if re.search(r"\b\d+(?:[.,]\d+)?%|\b\d+\b", line):
        return True
    if any(token in lower for token in ("neobank", "acme", "vs ", "gap", "impacto")):
        return True
    if capability == "benchmark_executive" and any(
        token in lower
        for token in ("não", "sim", "mvp", "fase", "api", "integra", "cart", "pagament")
    ):
        return True
    if any(token in lower for token in ("bloqueio", "risco", "próximo", "decisão", "recomend")):
        return True
    return False
