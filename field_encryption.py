# =============================================================================
# field_encryption.py — Field-level encryption for sensitive AuditLog fields
# =============================================================================
# Uses Fernet (AES-128-CBC + HMAC-SHA256) for symmetric encryption.
# Key source: AUDIT_ENCRYPTION_KEY env var (base64-url-safe 32 bytes),
# or derived from JWT_SECRET as fallback.
# =============================================================================

import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY: Optional[bytes] = None
_FERNET = None


def _get_fernet():
    """Lazy-init Fernet instance."""
    global _ENCRYPTION_KEY, _FERNET
    if _FERNET is not None:
        return _FERNET

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "[FieldEncryption] cryptography package not installed. "
            "Field encryption disabled. Install with: pip install cryptography"
        )
        return None

    raw_key = os.environ.get("AUDIT_ENCRYPTION_KEY", "").strip()
    if raw_key:
        # Expect base64-url-safe encoded 32-byte key
        try:
            _ENCRYPTION_KEY = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
            _FERNET = Fernet(_ENCRYPTION_KEY)
            logger.info("[FieldEncryption] Using AUDIT_ENCRYPTION_KEY.")
            return _FERNET
        except Exception as e:
            logger.error("[FieldEncryption] Invalid AUDIT_ENCRYPTION_KEY: %s", e)

    # Fallback: derive from JWT_SECRET
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        from config import JWT_SECRET as _jwt_secret
        jwt_secret = _jwt_secret

    if jwt_secret:
        derived = hashlib.sha256(f"audit-encrypt::{jwt_secret}".encode("utf-8")).digest()
        _ENCRYPTION_KEY = base64.urlsafe_b64encode(derived)
        _FERNET = Fernet(_ENCRYPTION_KEY)
        logger.info("[FieldEncryption] Key derived from JWT_SECRET.")
        return _FERNET

    logger.warning("[FieldEncryption] No encryption key available. Field encryption disabled.")
    return None


def encrypt_field(value: str) -> str:
    """Encrypt a string field. Returns 'ENC::' prefixed ciphertext, or plaintext if encryption unavailable."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        ct = f.encrypt(value.encode("utf-8"))
        return f"ENC::{ct.decode('utf-8')}"
    except Exception as e:
        logger.error("[FieldEncryption] encrypt failed: %s", e)
        return value


def decrypt_field(value: str) -> str:
    """Decrypt a field. If not encrypted (no 'ENC::' prefix), returns as-is."""
    if not value or not value.startswith("ENC::"):
        return value
    f = _get_fernet()
    if f is None:
        logger.warning("[FieldEncryption] Cannot decrypt — no key available.")
        return "[encrypted]"
    try:
        ct = value[5:].encode("utf-8")
        return f.decrypt(ct).decode("utf-8")
    except Exception as e:
        logger.error("[FieldEncryption] decrypt failed: %s", e)
        return "[encrypted]"


# Convenience: list of AuditLog fields to encrypt
AUDIT_SENSITIVE_FIELDS = ("Question", "MetadataJson")
