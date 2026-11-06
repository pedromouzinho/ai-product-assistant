# =============================================================================
# pii_column_scanner.py — PII detection & masking in tabular uploads (S4-09)
# =============================================================================
# Scans column names and sample values for PII patterns (email, phone, NIF,
# IBAN, credit card, name heuristics). Masks detected PII before indexing.
# =============================================================================

import logging
import re
from typing import Dict, List, Optional, Tuple

from config import PII_COLUMN_SCAN_ENABLED, PII_COLUMN_SCAN_SAMPLE_ROWS

logger = logging.getLogger(__name__)

# ── PII detection patterns ─────────────────────────────────────

# Column name hints (case-insensitive partial match)
_NAME_HINTS = {
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    "correio": "email",
    "telefone": "phone",
    "phone": "phone",
    "telemovel": "phone",
    "telemóvel": "phone",
    "mobile": "phone",
    "celular": "phone",
    "nif": "nif",
    "contribuinte": "nif",
    "tax_id": "nif",
    "iban": "iban",
    "conta_bancaria": "iban",
    "bank_account": "iban",
    "cc_number": "credit_card",
    "credit_card": "credit_card",
    "cartao_credito": "credit_card",
    "cartão": "credit_card",
    "niss": "niss",
    "seg_social": "niss",
    "social_security": "niss",
    "ssn": "niss",
    "bi_number": "id_doc",
    "cc_cidadao": "id_doc",
    "passport": "id_doc",
    "passaporte": "id_doc",
    "morada": "address",
    "address": "address",
    "endereco": "address",
    "endereço": "address",
    "nome_completo": "person_name",
    "full_name": "person_name",
    "nome": "person_name",
    "first_name": "person_name",
    "last_name": "person_name",
    "apelido": "person_name",
}

# Value-level regex patterns
_VALUE_PATTERNS = {
    "email": re.compile(
        r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$"
    ),
    "phone": re.compile(
        r"^(?:\+351\s?)?(?:9[1-9]\d{7}|2\d{8})$"
    ),
    "nif": re.compile(
        r"^[12356789]\d{8}$"
    ),
    "iban": re.compile(
        r"^[A-Z]{2}\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{0,7}$",
        re.IGNORECASE,
    ),
    "credit_card": re.compile(
        r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}$"
    ),
    "niss": re.compile(
        r"^\d{11}$"
    ),
}

# Masking templates per PII type
_MASK_TEMPLATES = {
    "email": lambda v: _mask_email(v),
    "phone": lambda v: v[:4] + "***" + v[-2:] if len(v) > 6 else "***",
    "nif": lambda v: v[:3] + "***" + v[-3:] if len(v) == 9 else "***",
    "iban": lambda v: v[:4] + " **** **** " + v[-4:] if len(v) > 8 else "***",
    "credit_card": lambda v: "****-****-****-" + v[-4:] if len(v) >= 4 else "***",
    "niss": lambda v: v[:3] + "*****" + v[-3:] if len(v) == 11 else "***",
    "id_doc": lambda v: v[:2] + "***" + v[-2:] if len(v) > 4 else "***",
    "person_name": lambda v: _mask_name(v),
    "address": lambda v: v[:8] + "..." if len(v) > 8 else "***",
}


def _mask_email(v: str) -> str:
    """user@example.com → u***@d***.com"""
    parts = v.split("@")
    if len(parts) != 2:
        return "***@***.***"
    local = parts[0]
    domain = parts[1]
    masked_local = local[0] + "***" if local else "***"
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        masked_domain = domain_parts[0][0] + "***." + domain_parts[-1]
    else:
        masked_domain = "***"
    return f"{masked_local}@{masked_domain}"


def _mask_name(v: str) -> str:
    """Jane Doe → P. M."""
    parts = v.strip().split()
    if not parts:
        return "***"
    return " ".join(p[0] + "." for p in parts if p)


# ── Public API ──────────────────────────────────────────────────

class PIIColumnResult:
    """Result of scanning a single column for PII."""
    __slots__ = ("column_name", "pii_type", "confidence", "sample_count", "match_count")

    def __init__(self, column_name: str, pii_type: str, confidence: float,
                 sample_count: int, match_count: int):
        self.column_name = column_name
        self.pii_type = pii_type
        self.confidence = confidence
        self.sample_count = sample_count
        self.match_count = match_count

    def to_dict(self) -> dict:
        return {
            "column": self.column_name,
            "pii_type": self.pii_type,
            "confidence": round(self.confidence, 2),
            "sample_count": self.sample_count,
            "match_count": self.match_count,
        }


def scan_columns_for_pii(
    columns: List[str],
    sample_rows: Optional[List[Dict[str, str]]] = None,
) -> List[PIIColumnResult]:
    """Scan column names and optional sample data for PII patterns.

    Args:
        columns: List of column names from the tabular file.
        sample_rows: Optional list of dicts (column→value) for value-level scanning.

    Returns:
        List of PIIColumnResult for columns with detected PII.
    """
    if not PII_COLUMN_SCAN_ENABLED or not columns:
        return []

    results: List[PIIColumnResult] = []
    rows = (sample_rows or [])[:PII_COLUMN_SCAN_SAMPLE_ROWS]

    for col in columns:
        col_lower = col.strip().lower().replace(" ", "_")

        # 1. Check column name hints
        name_pii_type = None
        for hint, pii_type in _NAME_HINTS.items():
            if hint in col_lower:
                name_pii_type = pii_type
                break

        # 2. Check value patterns on sample rows
        value_pii_type = None
        match_count = 0
        sample_count = 0

        if rows:
            for row in rows:
                val = str(row.get(col, "") or "").strip()
                if not val:
                    continue
                sample_count += 1
                for ptype, pattern in _VALUE_PATTERNS.items():
                    if pattern.match(val):
                        if value_pii_type is None:
                            value_pii_type = ptype
                        if ptype == value_pii_type:
                            match_count += 1
                        break

        # 3. Determine final PII type and confidence
        pii_type = None
        confidence = 0.0

        if name_pii_type and value_pii_type:
            # Both name and values agree
            pii_type = value_pii_type
            confidence = min(0.95, 0.6 + (match_count / max(1, sample_count)) * 0.35)
        elif name_pii_type and not rows:
            # Name hint only, no data to verify
            pii_type = name_pii_type
            confidence = 0.6
        elif name_pii_type and rows and sample_count == 0:
            # Name hint, but column is empty
            pii_type = name_pii_type
            confidence = 0.5
        elif value_pii_type and sample_count > 0:
            # Values match PII pattern even without name hint
            ratio = match_count / max(1, sample_count)
            if ratio >= 0.5:
                pii_type = value_pii_type
                confidence = min(0.85, 0.4 + ratio * 0.45)

        if pii_type and confidence >= 0.4:
            results.append(PIIColumnResult(
                column_name=col,
                pii_type=pii_type,
                confidence=confidence,
                sample_count=sample_count,
                match_count=match_count,
            ))

    if results:
        logger.info(
            "[PIIScanner] Detected %d PII column(s): %s",
            len(results),
            ", ".join(f"{r.column_name}({r.pii_type})" for r in results),
        )
    return results


def mask_pii_in_rows(
    rows: List[Dict[str, str]],
    pii_columns: List[PIIColumnResult],
) -> List[Dict[str, str]]:
    """Mask PII values in-place for detected columns.

    Args:
        rows: List of dicts (column→value) — data rows.
        pii_columns: Results from scan_columns_for_pii.

    Returns:
        Same rows list with PII values masked.
    """
    if not pii_columns or not rows:
        return rows

    col_mask_map: Dict[str, str] = {r.column_name: r.pii_type for r in pii_columns}

    for row in rows:
        for col, pii_type in col_mask_map.items():
            val = row.get(col)
            if val is None:
                continue
            val_str = str(val).strip()
            if not val_str:
                continue
            mask_fn = _MASK_TEMPLATES.get(pii_type)
            if mask_fn:
                try:
                    row[col] = mask_fn(val_str)
                except Exception:
                    row[col] = "***"
            else:
                row[col] = "***"

    return rows


def mask_pii_in_text(
    text: str,
    columns: List[str],
    pii_columns: List[PIIColumnResult],
    delimiter: str = "\t",
) -> str:
    """Mask PII values in a delimited text representation.

    The first line is treated as a header row. Subsequent lines have
    PII columns replaced with masked values.

    Args:
        text: Delimited text (header + data rows).
        columns: Column names matching the header.
        pii_columns: Results from scan_columns_for_pii.
        delimiter: Column delimiter used in the text.

    Returns:
        Text with PII values masked.
    """
    if not pii_columns or not text or not columns:
        return text

    col_indices: Dict[int, str] = {}
    for r in pii_columns:
        try:
            idx = columns.index(r.column_name)
            col_indices[idx] = r.pii_type
        except ValueError:
            continue

    if not col_indices:
        return text

    lines = text.split("\n")
    if len(lines) <= 1:
        return text

    masked_lines = [lines[0]]  # keep header
    for line in lines[1:]:
        if not line.strip():
            masked_lines.append(line)
            continue
        parts = line.split(delimiter)
        for idx, pii_type in col_indices.items():
            if idx < len(parts):
                val = parts[idx].strip()
                if val:
                    mask_fn = _MASK_TEMPLATES.get(pii_type)
                    if mask_fn:
                        try:
                            parts[idx] = mask_fn(val)
                        except Exception:
                            parts[idx] = "***"
                    else:
                        parts[idx] = "***"
        masked_lines.append(delimiter.join(parts))

    return "\n".join(masked_lines)


def format_pii_report(pii_columns: List[PIIColumnResult]) -> str:
    """Format PII scan results as a human-readable summary for the user."""
    if not pii_columns:
        return ""
    lines = ["⚠️ **PII detetado** — as seguintes colunas contêm dados pessoais e foram mascaradas:"]
    for r in pii_columns:
        type_labels = {
            "email": "Email",
            "phone": "Telefone",
            "nif": "NIF",
            "iban": "IBAN",
            "credit_card": "Cartão de crédito",
            "niss": "Nº Seg. Social",
            "id_doc": "Documento de identificação",
            "person_name": "Nome pessoal",
            "address": "Morada",
        }
        label = type_labels.get(r.pii_type, r.pii_type)
        conf_label = "alta" if r.confidence >= 0.8 else ("média" if r.confidence >= 0.6 else "baixa")
        lines.append(f"- **{r.column_name}** → {label} (confiança {conf_label})")
    return "\n".join(lines)
