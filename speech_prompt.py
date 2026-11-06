import json
import logging
import re
from typing import Any, Dict, List, Optional

from config import SPEECH_PROMPT_FALLBACK_SPEC, SPEECH_PROMPT_PRIMARY_SPEC
from llm_provider import get_provider_for_spec
from provider_governance import evaluate_provider_governance
from pii_shield import PIIMaskingContext, mask_messages
from prompt_shield import check_messages
from structured_schemas import SPEECH_PROMPT_NORMALIZATION_SCHEMA


logger = logging.getLogger(__name__)

_FILLER_PATTERN = re.compile(
    r"(?i)\b(?:hum+|uhm+|ahn+|hã+|ah+|eh+|mmm+)\b"
)
_MULTI_PUNCT_PATTERN = re.compile(r"([,.;:!?])(?:\s*\1)+")
_MULTI_SPACE_PATTERN = re.compile(r"\s+")


def clean_spoken_transcript(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("\r", " ").replace("\n", " ")
    value = _FILLER_PATTERN.sub(" ", value)
    value = _MULTI_PUNCT_PATTERN.sub(r"\1", value)
    value = _MULTI_SPACE_PATTERN.sub(" ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value.strip(" \t,;")


def _guess_mode(transcript: str, requested_mode: str) -> str:
    wanted = str(requested_mode or "").strip().lower()
    if wanted in {"general", "userstory"}:
        return wanted
    normalized = str(transcript or "").lower()
    if any(term in normalized for term in ("user story", "userstory", "critério de aceitação", "aceitação", "epic", "feature")):
        return "userstory"
    return "general"


def _fallback_response(cleaned: str, requested_mode: str, note: Optional[str] = None) -> Dict[str, Any]:
    notes: List[str] = []
    if note:
        notes.append(clean_spoken_transcript(note))
    elif cleaned:
        notes.append("Foi usada uma normalização conservadora porque a interpretação avançada não ficou disponível.")
    confidence = "medium" if cleaned else "low"
    governance = evaluate_provider_governance(
        provider_used="fallback",
        model_used="fallback",
        action="speech_prompt",
        mode=requested_mode,
        tools_used=["speech_prompt"],
    )
    return {
        "raw_transcript": cleaned,
        "normalized_prompt": cleaned or "Ajuda-me com este pedido.",
        "confidence": confidence,
        "inferred_mode": _guess_mode(cleaned, requested_mode),
        "auto_send_allowed": confidence in {"high", "medium"},
        "notes": notes[:5],
        "provider_used": "fallback",
        "model_used": "fallback",
        "provider_policy_mode": governance["policy_mode"],
        "provider_family": governance["provider_family"],
        "external_provider": governance["external_provider"],
        "data_sensitivity": governance["data_sensitivity"],
        "provider_policy_note": governance["policy_note"] or None,
    }


def _parse_json_payload(raw: str) -> Dict[str, Any]:
    content = str(raw or "").strip()
    if not content:
        raise ValueError("empty_response")
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(content[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("invalid_json")


def _build_prompt(cleaned: str, requested_mode: str, language: str, force_json_only: bool = False) -> str:
    json_instruction = (
        "Responde apenas em JSON válido com as chaves normalized_prompt, confidence, inferred_mode e notes."
        if force_json_only
        else "Responde apenas em JSON válido segundo o schema."
    )
    return f"""
Transforma a transcrição de fala espontânea de um colega num prompt claro e útil para um assistente interno.

Contexto:
- idioma: {language or 'pt-PT'}
- modo ativo da aplicação: {requested_mode}

Regras obrigatórias:
- Remove hesitações, repetições, começos falsos e frases partidas.
- Mantém nomes de sistemas, ficheiros, equipas, áreas, produtos e termos funcionais exatamente como aparecem.
- Não inventes requisitos, números, datas ou contexto que não estejam na transcrição.
- Se houver ambiguidade relevante, mantém o pedido útil mas regista essa ambiguidade em "notes".
- O campo "normalized_prompt" deve ficar pronto para ser enviado diretamente ao assistente.
- Se o pedido parecer ser de user stories, usa "inferred_mode" = "userstory". Caso contrário, "general".
- confidence deve ser "high", "medium" ou "low".
- {json_instruction}

Transcrição:
\"\"\"{cleaned}\"\"\"
""".strip()


async def _run_normalizer_prompt(
    spec: str,
    prompt: str,
    *,
    response_format: Optional[dict] = None,
    max_tokens: int = 350,
) -> tuple[str, str]:
    provider = get_provider_for_spec(spec)
    messages = [{"role": "user", "content": prompt}]
    pii_context = PIIMaskingContext()
    actual_messages = await mask_messages(messages, pii_context)

    shield_result = await check_messages(actual_messages)
    if shield_result.is_blocked:
        raise RuntimeError(shield_result.details or "speech_prompt_blocked")

    response = await provider.chat(
        actual_messages,
        None,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    content = response.content or ""
    if pii_context.mappings:
        content = pii_context.unmask(content)
    provider_model = getattr(provider, "deployment", "") or getattr(provider, "model", "") or spec
    return content, f"{provider.name}:{provider_model}"


def _normalize_result(
    parsed: Dict[str, Any],
    *,
    cleaned: str,
    requested_mode: str,
    provider_used: str,
) -> Dict[str, Any]:
    normalized_prompt = clean_spoken_transcript(parsed.get("normalized_prompt", "")) or cleaned
    confidence = str(parsed.get("confidence", "medium") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    inferred_mode = _guess_mode(normalized_prompt, str(parsed.get("inferred_mode", requested_mode)))
    notes = [
        clean_spoken_transcript(str(note or ""))
        for note in (parsed.get("notes") or [])
        if str(note or "").strip()
    ]

    governance = evaluate_provider_governance(
        provider_used=provider_used,
        model_used=provider_used,
        action="speech_prompt",
        mode=requested_mode,
        tools_used=["speech_prompt"],
    )
    return {
        "raw_transcript": cleaned,
        "normalized_prompt": normalized_prompt,
        "confidence": confidence,
        "inferred_mode": inferred_mode,
        "auto_send_allowed": confidence in {"high", "medium"},
        "notes": notes[:5],
        "provider_used": provider_used,
        "model_used": provider_used,
        "provider_policy_mode": governance["policy_mode"],
        "provider_family": governance["provider_family"],
        "external_provider": governance["external_provider"],
        "data_sensitivity": governance["data_sensitivity"],
        "provider_policy_note": governance["policy_note"] or None,
    }


async def normalize_spoken_prompt(
    transcript: str,
    mode: str = "general",
    language: str = "pt-PT",
) -> Dict[str, Any]:
    cleaned = clean_spoken_transcript(transcript)
    if not cleaned:
        return {
            "raw_transcript": "",
            "normalized_prompt": "",
            "confidence": "low",
            "inferred_mode": _guess_mode("", mode),
            "auto_send_allowed": False,
            "notes": ["Não foi possível recolher texto suficiente a partir da fala."],
            "provider_used": "none",
            "model_used": "none",
            "provider_policy_mode": "advisory",
            "provider_family": "none",
            "external_provider": False,
            "data_sensitivity": "elevated",
            "provider_policy_note": None,
        }

    requested_mode = _guess_mode(cleaned, mode)
    primary_prompt = _build_prompt(cleaned, requested_mode, language, force_json_only=False)
    fallback_prompt = _build_prompt(cleaned, requested_mode, language, force_json_only=True)

    primary_error: Optional[Exception] = None
    try:
        raw, provider_used = await _run_normalizer_prompt(
            SPEECH_PROMPT_PRIMARY_SPEC,
            primary_prompt,
            response_format=SPEECH_PROMPT_NORMALIZATION_SCHEMA,
        )
        parsed = _parse_json_payload(raw)
        normalized = _normalize_result(parsed, cleaned=cleaned, requested_mode=requested_mode, provider_used=provider_used)
        if normalized["confidence"] != "low":
            return normalized
        if normalized["notes"]:
            normalized["notes"].append("Foi aplicada uma segunda interpretação para reforçar a confiança.")
        else:
            normalized["notes"] = ["Foi aplicada uma segunda interpretação para reforçar a confiança."]
    except Exception as exc:
        primary_error = exc
        logger.warning("[SpeechPrompt] primary normalizer failed: %s", exc)

    if not SPEECH_PROMPT_FALLBACK_SPEC or SPEECH_PROMPT_FALLBACK_SPEC == SPEECH_PROMPT_PRIMARY_SPEC:
        return _fallback_response(
            cleaned,
            requested_mode,
            note="A interpretação principal falhou e não existe fallback configurado.",
        )

    try:
        raw, provider_used = await _run_normalizer_prompt(
            SPEECH_PROMPT_FALLBACK_SPEC,
            fallback_prompt,
            response_format=None,
        )
        parsed = _parse_json_payload(raw)
        normalized = _normalize_result(parsed, cleaned=cleaned, requested_mode=requested_mode, provider_used=provider_used)
        if primary_error:
            normalized["notes"].append("Foi usado o modelo de fallback por indisponibilidade da interpretação principal.")
        else:
            normalized["notes"].append("Foi usado o modelo de fallback para melhorar uma interpretação com baixa confiança.")
        if normalized.get("external_provider") and normalized.get("provider_policy_note"):
            normalized["notes"].append(normalized["provider_policy_note"])
        normalized["notes"] = normalized["notes"][:5]
        return normalized
    except Exception as exc:
        logger.warning("[SpeechPrompt] fallback normalizer failed: %s", exc)
        if primary_error:
            return _fallback_response(
                cleaned,
                requested_mode,
                note="A interpretação avançada falhou e foi aplicada uma normalização conservadora.",
            )
        return _fallback_response(
            cleaned,
            requested_mode,
            note="A interpretação principal ficou com baixa confiança e o fallback não ficou disponível.",
        )
