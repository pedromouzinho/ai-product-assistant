import pytest

import tools  # noqa: F401
from tool_registry import (
    execute_tool,
    get_tool_runtime_profile,
    register_tool,
    register_tool_runtime_profile,
    validate_tool_arguments,
)


def test_validate_tool_arguments_reports_required_field():
    register_tool(
        "test_required_schema_tool",
        lambda args: {"ok": True, "args": args},
        definition={
            "type": "function",
            "function": {
                "name": "test_required_schema_tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        },
    )

    errors = validate_tool_arguments("test_required_schema_tool", {})

    assert errors
    assert "campo obrigatório em falta" in errors[0]


@pytest.mark.asyncio
async def test_execute_tool_returns_validation_error_for_invalid_args():
    register_tool(
        "test_invalid_args_tool",
        lambda args: {"ok": True, "args": args},
        definition={
            "type": "function",
            "function": {
                "name": "test_invalid_args_tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "top": {"type": "integer"},
                    },
                    "required": ["top"],
                },
            },
        },
    )

    result = await execute_tool("test_invalid_args_tool", {"top": "x"})

    assert result["error"] == "Argumentos inválidos para a tool."
    assert result["validation_errors"]


def test_register_and_get_tool_runtime_profile():
    register_tool_runtime_profile(
        "test_runtime_profile_tool",
        inject_conv_id=True,
        inject_user_sub=False,
    )

    profile = get_tool_runtime_profile("test_runtime_profile_tool")

    assert profile == {"inject_conv_id": True, "inject_user_sub": False}


def test_builtin_runtime_profile_is_registered():
    profile = get_tool_runtime_profile("generate_presentation")

    assert profile["inject_conv_id"] is True
    assert profile["inject_user_sub"] is True
