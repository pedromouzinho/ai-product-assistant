from __future__ import annotations

import pytest

import speech_prompt


def test_clean_spoken_transcript_removes_fillers_and_duplicate_punctuation():
    cleaned = speech_prompt.clean_spoken_transcript("Hum ahn preciso, , comparar este ficheiro... eeh com o outro!!")

    assert cleaned == "preciso, comparar este ficheiro. eeh com o outro!"


@pytest.mark.asyncio
async def test_normalize_spoken_prompt_falls_back_when_both_models_fail(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(speech_prompt, "_run_normalizer_prompt", _boom)

    result = await speech_prompt.normalize_spoken_prompt(
        "Hum preciso de perceber os bugs mais críticos do BackOffice",
        mode="general",
    )

    assert result["raw_transcript"] == "preciso de perceber os bugs mais críticos do BackOffice"
    assert result["normalized_prompt"] == "preciso de perceber os bugs mais críticos do BackOffice"
    assert result["confidence"] == "medium"
    assert result["inferred_mode"] == "general"
    assert result["auto_send_allowed"] is True


@pytest.mark.asyncio
async def test_normalize_spoken_prompt_uses_primary_structured_output(monkeypatch):
    async def _mock_normalizer(spec, prompt, **kwargs):
        assert spec == speech_prompt.SPEECH_PROMPT_PRIMARY_SPEC
        return (
            """
            {
              "normalized_prompt": "Cria uma user story para o step 2 da Via Verde com foco no resumo e confirmação.",
              "confidence": "high",
              "inferred_mode": "userstory",
              "notes": ["O pedido referia Via Verde e um step específico."]
            }
            """,
            "azure_openai:gpt-4-1-mini-dz",
        )

    monkeypatch.setattr(speech_prompt, "_run_normalizer_prompt", _mock_normalizer)

    result = await speech_prompt.normalize_spoken_prompt(
        "olha preciso de uma user story para o step 2 da via verde resum resumo e confirmação",
        mode="general",
    )

    assert result["normalized_prompt"].startswith("Cria uma user story")
    assert result["confidence"] == "high"
    assert result["inferred_mode"] == "userstory"
    assert result["auto_send_allowed"] is True
    assert result["notes"] == ["O pedido referia Via Verde e um step específico."]
    assert result["external_provider"] is False
    assert result["provider_family"] == "azure_openai"


@pytest.mark.asyncio
async def test_normalize_spoken_prompt_uses_fallback_on_low_confidence(monkeypatch):
    calls = []

    async def _mock_normalizer(spec, prompt, **kwargs):
        calls.append(spec)
        if spec == speech_prompt.SPEECH_PROMPT_PRIMARY_SPEC:
            return (
                """
                {
                  "normalized_prompt": "ajuda-me com isto da via verde",
                  "confidence": "low",
                  "inferred_mode": "general",
                  "notes": ["Pedido pouco específico."]
                }
                """,
                "azure_openai:gpt-4-1-mini-dz",
            )
        return (
            """
            {
              "normalized_prompt": "Explica o que mudou no fluxo Via Verde e identifica o melhor encaixe funcional.",
              "confidence": "medium",
              "inferred_mode": "general",
              "notes": ["Foi clarificado o objetivo principal do pedido."]
            }
            """,
            "anthropic:claude-sonnet-4-6",
        )

    monkeypatch.setattr(speech_prompt, "_run_normalizer_prompt", _mock_normalizer)

    result = await speech_prompt.normalize_spoken_prompt(
        "olha isto da via verde preciso de perceber melhor",
        mode="general",
    )

    assert calls == [speech_prompt.SPEECH_PROMPT_PRIMARY_SPEC, speech_prompt.SPEECH_PROMPT_FALLBACK_SPEC]
    assert result["confidence"] == "medium"
    assert result["auto_send_allowed"] is True
    assert any("fallback" in note.lower() for note in result["notes"])
    assert result["external_provider"] is True
    assert result["provider_family"] == "anthropic"
    assert result["provider_policy_note"]
