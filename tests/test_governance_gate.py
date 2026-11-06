"""Testes para governance gate em llm_with_fallback e llm_stream_with_fallback."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from models import LLMResponse, StreamEvent


@pytest.fixture(autouse=True)
def _mock_shields():
    """Desativar PII e Prompt Shield para isolar o teste de governance."""
    with patch("llm_provider.PII_ENABLED", False), \
         patch("llm_provider.PROMPT_SHIELD_ENABLED", False):
        yield


def _make_mock_provider(name: str):
    """Cria um provider mock com nome configurável."""
    provider = MagicMock()
    provider.name = name
    provider.chat = AsyncMock(return_value=LLMResponse(
        content="resposta ok",
        tool_calls=None,
        usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        model="test-model",
        provider=name,
    ))

    async def _mock_stream(*args, **kwargs):
        yield StreamEvent(type="token", text="chunk")
        yield StreamEvent(type="done", data={"content": "chunk", "model": "test", "provider": name})

    provider.chat_stream = _mock_stream
    return provider


MESSAGES = [{"role": "user", "content": "olá"}]


class TestGovernanceGateNonStreaming:
    """Testa governance gate no path não-streaming (llm_with_fallback)."""

    @pytest.mark.asyncio
    async def test_restrictive_blocks_external_provider(self):
        """Em modo restrictive, provider externo é filtrado."""
        from llm_provider import llm_with_fallback

        mock_anthropic = _make_mock_provider("anthropic")
        mock_azure = _make_mock_provider("azure_openai")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "restrictive"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_anthropic), \
             patch("llm_provider.get_fallback_provider", return_value=mock_azure):

            result = await llm_with_fallback(MESSAGES, tier="pro")

            # Anthropic bloqueado, Azure usado como fallback
            assert any(
                e.get("status") == "blocked" and e.get("reason") == "provider_governance_restrictive"
                for e in result.fallback_chain
            )
            # Resposta veio do Azure (não do governance_gate)
            assert result.provider == "azure_openai"

    @pytest.mark.asyncio
    async def test_restrictive_blocks_all_returns_gate_response(self):
        """Se todos os providers forem externos, retorna resposta de bloqueio."""
        from llm_provider import llm_with_fallback

        mock_anthropic1 = _make_mock_provider("anthropic")
        mock_anthropic2 = _make_mock_provider("anthropic")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "restrictive"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_anthropic1), \
             patch("llm_provider.get_fallback_provider", return_value=mock_anthropic2):

            result = await llm_with_fallback(MESSAGES, tier="pro")

            assert result.provider == "governance_gate"
            assert "bloqueado" in result.content.lower()
            assert result.is_error is True
            assert result.error_kind == "provider_governance_blocked"
            assert "bloqueado" in (result.error_detail or "").lower()

    @pytest.mark.asyncio
    async def test_advisory_allows_external_provider(self):
        """Em modo advisory, provider externo passa sem bloqueio."""
        from llm_provider import llm_with_fallback

        mock_anthropic = _make_mock_provider("anthropic")
        mock_azure = _make_mock_provider("azure_openai")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "advisory"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_anthropic), \
             patch("llm_provider.get_fallback_provider", return_value=mock_azure):

            result = await llm_with_fallback(MESSAGES, tier="pro")

            # Anthropic NÃO bloqueado
            assert result.provider == "anthropic"
            assert not any(
                e.get("status") == "blocked"
                for e in (result.fallback_chain or [])
            )


class TestGovernanceGateStreaming:
    """Testa governance gate no path streaming (llm_stream_with_fallback)."""

    @pytest.mark.asyncio
    async def test_restrictive_blocks_external_in_stream(self):
        """Em modo restrictive, streaming com provider externo é bloqueado."""
        from llm_provider import llm_stream_with_fallback

        mock_anthropic = _make_mock_provider("anthropic")
        mock_anthropic_fb = _make_mock_provider("anthropic")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "restrictive"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_anthropic), \
             patch("llm_provider.get_fallback_provider", return_value=mock_anthropic_fb):

            events = []
            async for event in llm_stream_with_fallback(MESSAGES, tier="pro"):
                events.append(event)

            # Deve ter evento done com governance_gate
            done_events = [e for e in events if e.type == "done"]
            assert len(done_events) == 1
            done_data = done_events[0].data
            assert done_data["provider"] == "governance_gate"
            assert done_data["is_error"] is True
            assert done_data["error_kind"] == "provider_governance_blocked"
            assert "bloqueado" in (done_data.get("error_detail") or "").lower()

    @pytest.mark.asyncio
    async def test_restrictive_allows_internal_in_stream(self):
        """Em modo restrictive, streaming com provider interno funciona."""
        from llm_provider import llm_stream_with_fallback

        mock_azure = _make_mock_provider("azure_openai")
        mock_azure_fb = _make_mock_provider("azure_openai")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "restrictive"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_azure), \
             patch("llm_provider.get_fallback_provider", return_value=mock_azure_fb):

            events = []
            async for event in llm_stream_with_fallback(MESSAGES, tier="fast"):
                events.append(event)

            # Deve ter tokens normais
            token_events = [e for e in events if e.type == "token"]
            assert len(token_events) >= 1
            assert token_events[0].text == "chunk"

    @pytest.mark.asyncio
    async def test_advisory_allows_external_in_stream(self):
        """Em modo advisory, streaming com provider externo passa."""
        from llm_provider import llm_stream_with_fallback

        mock_anthropic = _make_mock_provider("anthropic")
        mock_azure = _make_mock_provider("azure_openai")

        with patch("llm_provider.PROVIDER_GOVERNANCE_MODE", "advisory"), \
             patch("llm_provider.PROVIDER_EXTERNAL_MODEL_FAMILIES", ("anthropic",)), \
             patch("llm_provider.get_provider", return_value=mock_anthropic), \
             patch("llm_provider.get_fallback_provider", return_value=mock_azure):

            events = []
            async for event in llm_stream_with_fallback(MESSAGES, tier="pro"):
                events.append(event)

            # Deve ter tokens normais (anthropic não bloqueado)
            token_events = [e for e in events if e.type == "token"]
            assert len(token_events) >= 1
