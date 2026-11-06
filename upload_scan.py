# =============================================================================
# upload_scan.py — Upload content scanning gate
# =============================================================================
"""
Gate de scanning de ficheiros carregados.

Backends configuráveis via UPLOAD_SCAN_BACKEND:
  - "clamd"    → ClamAV daemon via socket/TCP — fail-closed (scanner
                 indisponível = upload bloqueado)
  - "defender" → Microsoft Defender for Storage — scan assíncrono a nível
                 de Blob Storage, não bloqueia inline. Este backend é um
                 passthrough: regista que o Defender cobre o storage, mas
                 NÃO dá garantia síncrona no momento do upload.
  - "none"     → scanning desativado (default em dev)

Nota de segurança:
  - "clamd" é o único backend que efectivamente bloqueia uploads maliciosos
    de forma síncrona. Se o daemon estiver indisponível, o upload é REJEITADO
    (fail-closed).
  - "defender" NÃO bloqueia no momento do upload — a proteção acontece
    depois, no Azure Storage. Não se deve apresentar como "antivírus inline".
"""

import logging
import os

from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

UPLOAD_SCAN_BACKEND = os.environ.get("UPLOAD_SCAN_BACKEND", "none").strip().lower()
CLAMD_HOST = os.environ.get("CLAMD_HOST", "127.0.0.1")
CLAMD_PORT = int(os.environ.get("CLAMD_PORT", "3310"))
CLAMD_TIMEOUT_SECONDS = int(os.environ.get("CLAMD_TIMEOUT_SECONDS", "30"))

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanResult:
    """Resultado do scan de um ficheiro."""
    is_clean: bool
    threat_name: Optional[str] = None
    backend: str = "none"
    error: Optional[str] = None


CLEAN = ScanResult(is_clean=True, backend="skip")

# ---------------------------------------------------------------------------
# ClamAV scanning — fail-closed
# ---------------------------------------------------------------------------

def _scan_with_clamd(content: bytes, filename: str) -> ScanResult:
    """Envia conteúdo ao ClamAV daemon para scanning.

    Fail-closed: se o daemon não responder ou devolver erro,
    o ficheiro é REJEITADO (is_clean=False).
    """
    try:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(CLAMD_TIMEOUT_SECONDS)
            sock.connect((CLAMD_HOST, CLAMD_PORT))

            # INSTREAM protocol: send chunks prefixed by 4-byte big-endian length, end with 0-length
            sock.sendall(b"zINSTREAM\0")

            chunk_size = 8192
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))

            response = b""
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                response += data
        finally:
            sock.close()

        result_str = response.decode("utf-8", errors="replace").strip()
        # Format: "stream: OK" or "stream: <threat> FOUND"
        if "FOUND" in result_str:
            threat = result_str.replace("stream:", "").replace("FOUND", "").strip()
            logger.warning("[UploadScan] ClamAV detected threat in %s: %s", filename, threat)
            return ScanResult(is_clean=False, threat_name=threat, backend="clamd")
        if "OK" in result_str:
            return ScanResult(is_clean=True, backend="clamd")

        # Resposta inesperada — fail-closed
        logger.error("[UploadScan] ClamAV unexpected response for %s: %s", filename, result_str)
        return ScanResult(
            is_clean=False, backend="clamd",
            threat_name="scan_inconclusive",
            error=f"Unexpected response: {result_str}",
        )

    except Exception as e:
        # Fail-closed: scanner indisponível = upload bloqueado
        logger.error("[UploadScan] ClamAV unavailable (fail-closed) for %s: %s", filename, e)
        return ScanResult(
            is_clean=False, backend="clamd",
            threat_name="scanner_unavailable",
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_upload_content(content: bytes, filename: str) -> ScanResult:
    """
    Faz scan do conteúdo de um ficheiro carregado.

    Retorna ScanResult com is_clean=True se seguro, False se ameaça detectada.
    Se o backend estiver desativado ('none'), retorna sempre clean (sem gate).
    """
    if UPLOAD_SCAN_BACKEND == "none":
        return CLEAN

    if UPLOAD_SCAN_BACKEND == "clamd":
        return _scan_with_clamd(content, filename)

    if UPLOAD_SCAN_BACKEND == "defender":
        # Microsoft Defender for Storage actua a nível de Blob Storage,
        # não bloqueia inline neste ponto. Logging apenas para auditoria.
        # O upload passa — a proteção é assíncrona no Azure.
        logger.debug(
            "[UploadScan] Defender mode — passthrough (scan assíncrono no Azure Storage). "
            "Ficheiro: %s", filename,
        )
        return ScanResult(is_clean=True, backend="defender")

    logger.warning("[UploadScan] Backend desconhecido: %s — upload bloqueado por precaução.", UPLOAD_SCAN_BACKEND)
    return ScanResult(
        is_clean=False, backend=UPLOAD_SCAN_BACKEND,
        threat_name="unknown_backend",
        error=f"Backend de scan '{UPLOAD_SCAN_BACKEND}' não reconhecido",
    )
