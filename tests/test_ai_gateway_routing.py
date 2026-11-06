from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_azure_openai_provider_keeps_direct_mode_defaults(monkeypatch):
    import llm_provider

    class FakeResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "ok"}}],
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "model": "gpt-4.1",
            }

    captured = {}

    class FakeClient:
        async def post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_BASE_URL", "https://resource.cognitiveservices.azure.com")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_API_PREFIX", "/openai")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_MODE", "api-key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_HEADER", "api-key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_VALUE", "direct-key")
    monkeypatch.setattr(llm_provider, "EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

    provider = llm_provider.AzureOpenAIProvider(deployment="gpt-4.1")
    monkeypatch.setattr(provider, "_get_client", lambda: FakeClient())

    await provider.chat(messages=[{"role": "user", "content": "test"}])
    assert captured["url"] == (
        "https://resource.cognitiveservices.azure.com/openai/deployments/gpt-4.1/"
        "chat/completions?api-version=2024-10-21"
    )
    assert captured["headers"]["api-key"] == "direct-key"
    assert captured["headers"]["Content-Type"] == "application/json"

    await provider.embed("hello")
    assert captured["url"] == (
        "https://resource.cognitiveservices.azure.com/openai/deployments/text-embedding-3-small/"
        "embeddings?api-version=2023-05-15"
    )
    assert captured["timeout"] == 30


@pytest.mark.asyncio
async def test_azure_openai_provider_supports_gateway_subscription_key(monkeypatch):
    import llm_provider

    class FakeResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "model": "gpt-4.1",
            }

    captured = {}

    class FakeClient:
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_BASE_URL", "https://ai-gateway.example.com")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_API_PREFIX", "/ai/openai")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_MODE", "subscription-key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_HEADER", "Ocp-Apim-Subscription-Key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_VALUE", "gateway-key")

    provider = llm_provider.AzureOpenAIProvider(deployment="gpt-4.1-mini")
    monkeypatch.setattr(provider, "_get_client", lambda: FakeClient())

    await provider.chat(messages=[{"role": "user", "content": "test"}])

    assert captured["url"] == (
        "https://ai-gateway.example.com/ai/openai/deployments/gpt-4.1-mini/"
        "chat/completions?api-version=2024-10-21"
    )
    assert captured["headers"]["Ocp-Apim-Subscription-Key"] == "gateway-key"
    assert "api-key" not in captured["headers"]


def test_anthropic_provider_supports_gateway_bearer_auth(monkeypatch):
    import llm_provider

    monkeypatch.setattr(llm_provider, "ANTHROPIC_BASE_URL", "https://ai-gateway.example.com")
    monkeypatch.setattr(llm_provider, "ANTHROPIC_MESSAGES_PATH", "/anthropic/messages")
    monkeypatch.setattr(llm_provider, "ANTHROPIC_AUTH_MODE", "bearer")
    monkeypatch.setattr(llm_provider, "ANTHROPIC_AUTH_HEADER", "Authorization")
    monkeypatch.setattr(llm_provider, "ANTHROPIC_AUTH_VALUE", "gateway-token")

    provider = llm_provider.AnthropicProvider(model="claude-sonnet")

    assert provider._messages_url() == "https://ai-gateway.example.com/anthropic/messages"
    headers = provider._headers()
    assert headers["Authorization"] == "Bearer gateway-token"
    assert headers["anthropic-version"] == provider.API_VERSION
    assert headers["content-type"] == "application/json"
