import httpx
import pytest

import llm_provider
from route_deps import limiter


@pytest.mark.asyncio
async def test_llm_with_fallback_returns_precise_failure_text(monkeypatch):
    class _Primary:
        name = "anthropic"

        async def chat(self, *_args, **_kwargs):
            request = httpx.Request("POST", "https://example.com/messages")
            response = httpx.Response(529, request=request, text='{"error":"overloaded"}')
            raise httpx.HTTPStatusError("upstream overloaded", request=request, response=response)

    class _Fallback:
        name = "azure_openai"

        async def chat(self, *_args, **_kwargs):
            raise httpx.TimeoutException("took too long")

    monkeypatch.setattr(llm_provider, "get_provider", lambda tier=None: _Primary())
    monkeypatch.setattr(llm_provider, "get_fallback_provider", lambda: _Fallback())
    monkeypatch.setattr(llm_provider, "PII_ENABLED", False)
    monkeypatch.setattr(llm_provider, "PROMPT_SHIELD_ENABLED", False)

    result = await llm_provider.llm_with_fallback(
        [{"role": "user", "content": "olá"}],
        tier="standard",
    )

    assert result.is_error is True
    assert result.error_kind == "all_providers_failed"
    assert result.error_detail == result.content
    assert "Erro real detetado ao contactar os modelos:" in result.content
    assert "anthropic: HTTP 529" in result.content
    assert "azure_openai: timeout a aguardar resposta do provider" in result.content


def test_shared_limit_zero_disables_rate_limit_rule():
    @limiter.shared_limit("0/minute", scope="chat_budget")
    def _endpoint():
        return "ok"

    assert not hasattr(_endpoint, "__app_rate_limit__")


@pytest.mark.asyncio
async def test_anthropic_provider_fast_fails_on_429(monkeypatch):
    class _FakeClient:
        def __init__(self):
            self.calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            request = httpx.Request("POST", "https://example.com/messages")
            return httpx.Response(429, request=request, text='{"error":"rate_limited"}')

    provider = llm_provider.AnthropicProvider(model="claude-sonnet-test")
    fake_client = _FakeClient()
    monkeypatch.setattr(provider, "_get_client", lambda: fake_client)

    with pytest.raises(httpx.HTTPStatusError):
        await provider.chat([{"role": "user", "content": "olá"}])

    assert fake_client.calls == 1
