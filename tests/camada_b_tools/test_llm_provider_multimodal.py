from __future__ import annotations

import base64

from llm_provider import _openai_messages_to_anthropic, _sanitize_openai_tool_messages


def test_openai_messages_to_anthropic_converts_data_url_images():
    payload = base64.b64encode(b"fake-png").decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analisa este ecrã."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{payload}"}},
            ],
        }
    ]

    system_prompt, anthropic_messages = _openai_messages_to_anthropic(messages)

    assert system_prompt == ""
    assert anthropic_messages[0]["role"] == "user"
    content = anthropic_messages[0]["content"]
    assert content[0] == {"type": "text", "text": "Analisa este ecrã."}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": payload,
        },
    }


def test_openai_messages_to_anthropic_replaces_unsupported_image_formats_with_text_notice():
    payload = base64.b64encode(b"<svg></svg>").decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analisa este SVG."},
                {"type": "image_url", "image_url": {"url": f"data:image/svg+xml;base64,{payload}"}},
            ],
        }
    ]

    _, anthropic_messages = _openai_messages_to_anthropic(messages)
    content = anthropic_messages[0]["content"]

    assert content[0] == {"type": "text", "text": "Analisa este SVG."}
    assert content[1]["type"] == "text"
    assert "formato não suportado" in content[1]["text"]


def test_openai_messages_to_anthropic_drops_orphaned_tool_results():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "tool", "tool_call_id": "missing-call", "content": '{"ok": true}'},
        {"role": "user", "content": "olá"},
    ]

    system_prompt, anthropic_messages = _openai_messages_to_anthropic(messages)

    assert system_prompt == "system"
    assert anthropic_messages == [{"role": "user", "content": "olá"}]


def test_sanitize_openai_tool_messages_drops_orphaned_tool_message():
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "missing-call", "content": '{"error": true}'},
    ]

    sanitized = _sanitize_openai_tool_messages(messages)

    assert sanitized == messages[:2]


def test_sanitize_openai_tool_messages_drops_orphaned_assistant_tool_call():
    messages = [
        {"role": "user", "content": "analisa"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "user", "content": "continua"},
    ]

    sanitized = _sanitize_openai_tool_messages(messages)

    assert sanitized == [
        {"role": "user", "content": "analisa"},
        {"role": "user", "content": "continua"},
    ]


def test_openai_messages_to_anthropic_drops_orphaned_assistant_tool_use_sequence():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "analisa"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "user", "content": "continua"},
    ]

    system_prompt, anthropic_messages = _openai_messages_to_anthropic(messages)

    assert system_prompt == "system"
    assert anthropic_messages == [
        {"role": "user", "content": "analisa"},
        {"role": "user", "content": "continua"},
    ]
