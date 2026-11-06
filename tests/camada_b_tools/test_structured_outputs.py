"""Tests para Structured Outputs (response_format + schemas)."""

from __future__ import annotations

import pytest

from models import LLMResponse, StreamEvent
from structured_schemas import (
    SPRINT_ANALYSIS_SCHEMA,
    USER_STORY_SCHEMA,
    SCREENSHOT_USER_STORIES_SCHEMA,
    USER_STORY_LANE_DRAFT_SCHEMA,
)


def test_sprint_schema_valid():
    schema = SPRINT_ANALYSIS_SCHEMA
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    required = schema["json_schema"]["schema"]["required"]
    assert "sprint_name" in required
    assert "health" in required


def test_user_story_schema_valid():
    schema = USER_STORY_SCHEMA
    props = schema["json_schema"]["schema"]["properties"]
    assert "acceptance_criteria" in props
    assert "test_scenarios" in props
    assert props["test_scenarios"]["items"]["required"] == ["given", "when", "then"]


def test_screenshot_schema_shape():
    schema = SCREENSHOT_USER_STORIES_SCHEMA
    js = schema["json_schema"]["schema"]
    assert js["additionalProperties"] is False
    assert "stories" in js["properties"]
    story_item = js["properties"]["stories"]["items"]
    assert set(story_item["required"]) == {
        "title",
        "description",
        "provenance",
        "conditions",
        "composition_and_behavior",
        "acceptance_criteria",
        "test_scenarios",
        "test_data",
        "observations",
        "clarification_questions",
    }
    ac_item = story_item["properties"]["acceptance_criteria"]["items"]
    assert ac_item["required"] == ["id", "text"]
    scenario_item = story_item["properties"]["test_scenarios"]["items"]
    assert set(scenario_item["required"]) == {
        "id",
        "title",
        "category",
        "preconditions",
        "test_data",
        "steps",
        "covers",
    }


def test_user_story_lane_schema_shape():
    schema = USER_STORY_LANE_DRAFT_SCHEMA
    js = schema["json_schema"]["schema"]
    assert js["additionalProperties"] is False
    assert "narrative" in js["properties"]
    assert "acceptance_criteria" in js["properties"]
    assert "source_keys" in js["properties"]
    narrative = js["properties"]["narrative"]
    assert set(narrative["required"]) == {"as_a", "i_want", "so_that"}
    scenario_item = js["properties"]["test_scenarios"]["items"]
    assert set(scenario_item["required"]) == {
        "id",
        "title",
        "category",
        "preconditions",
        "test_data",
        "given",
        "when",
        "then",
        "covers",
    }


@pytest.mark.asyncio
async def test_llm_with_fallback_forwards_response_format(monkeypatch):
    import llm_provider

    captured = {}

    class DummyProvider:
        name = "dummy"

        async def chat(self, messages, tools=None, temperature=None, max_tokens=None, response_format=None, **kwargs):
            _ = messages, temperature, max_tokens, kwargs
            captured["tools"] = tools
            captured["response_format"] = response_format
            return LLMResponse(
                content='{"ok":true}',
                tool_calls=None,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                model="dummy",
                provider="dummy",
            )

    dummy = DummyProvider()
    monkeypatch.setattr(llm_provider, "get_provider", lambda tier=None: dummy)
    monkeypatch.setattr(llm_provider, "get_fallback_provider", lambda: dummy)

    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "simple",
            "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        },
    }
    result = await llm_provider.llm_with_fallback(
        messages=[{"role": "user", "content": "test"}],
        tier="standard",
        response_format=fmt,
    )
    assert captured["response_format"] == fmt
    assert result.content == '{"ok":true}'


@pytest.mark.asyncio
async def test_azure_chat_adds_response_format_without_tools(monkeypatch):
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
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "model": "gpt-4.1",
            }

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):
            _ = url, headers
            payloads.append(json or {})
            return FakeResponse()

    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_MODE", "api-key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_HEADER", "api-key")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_AUTH_VALUE", "test-key")

    provider = llm_provider.AzureOpenAIProvider(deployment="gpt-4.1")
    monkeypatch.setattr(provider, "_get_client", lambda: FakeClient())

    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "simple",
            "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        },
    }
    await provider.chat(
        messages=[{"role": "user", "content": "test"}],
        tools=None,
        response_format=fmt,
    )
    assert payloads[-1].get("response_format") == fmt

    tool_def = {
        "type": "function",
        "function": {
            "name": "demo_tool",
            "description": "demo",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    await provider.chat(
        messages=[{"role": "user", "content": "test"}],
        tools=[tool_def],
        response_format=fmt,
    )
    assert "response_format" not in payloads[-1]


@pytest.mark.asyncio
async def test_llm_stream_with_fallback_skips_unsupported_response_format_provider(monkeypatch):
    import llm_provider

    captured = {"primary_called": False, "fallback_response_format": None}

    class DummyAnthropic(llm_provider.AnthropicProvider):
        async def chat_stream(self, messages, tools=None, temperature=None, max_tokens=None, response_format=None):
            _ = messages, tools, temperature, max_tokens, response_format
            captured["primary_called"] = True
            raise AssertionError("primary unsupported provider should have been skipped")

    class DummyAzure:
        name = "azure_openai"
        deployment = "gpt-4.1"

        async def chat_stream(self, messages, tools=None, temperature=None, max_tokens=None, response_format=None):
            _ = messages, tools, temperature, max_tokens
            captured["fallback_response_format"] = response_format
            yield StreamEvent(type="token", text='{"ok":true}')
            yield StreamEvent(type="done", data={"content": '{"ok":true}'})

    primary = DummyAnthropic(model="claude-sonnet-test")
    fallback = DummyAzure()

    monkeypatch.setattr(llm_provider, "get_provider", lambda tier=None: primary)
    monkeypatch.setattr(llm_provider, "get_fallback_provider", lambda: fallback)
    monkeypatch.setattr(llm_provider, "_get_cached_provider", lambda provider_name, model: fallback)

    fmt = {
        "type": "json_schema",
        "json_schema": {
            "name": "simple",
            "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
        },
    }

    events = []
    async for event in llm_provider.llm_stream_with_fallback(
        messages=[{"role": "user", "content": "test"}],
        tier="standard",
        response_format=fmt,
    ):
        events.append(event)

    assert captured["primary_called"] is False
    assert captured["fallback_response_format"] == fmt
    assert [event.type for event in events] == ["token", "done"]
