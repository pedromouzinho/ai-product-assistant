from __future__ import annotations

from typing import Any, Iterable

from config import (
    PROVIDER_EXTERNAL_MODEL_FAMILIES,
    PROVIDER_GOVERNANCE_EXPERIMENTAL_ALLOW_EXTERNAL,
    PROVIDER_GOVERNANCE_MODE,
    PROVIDER_GOVERNANCE_NOTE,
)


def _normalize_provider_family(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if ":" in raw:
        return raw.split(":", 1)[0].strip()
    return raw


def infer_data_sensitivity(
    *,
    action: str = "",
    mode: str = "",
    tools_used: Iterable[str] | None = None,
) -> str:
    normalized_action = str(action or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    normalized_tools = {str(item or "").strip().lower() for item in (tools_used or []) if str(item or "").strip()}

    elevated_actions = {
        "speech_prompt",
        "user_story_context_preview",
        "user_story_generate",
        "user_story_validate",
        "user_story_publish",
        "chat_file",
        "agent_chat_file",
    }
    elevated_tools = {
        "speech_prompt",
        "tool_generate_user_stories",
        "tool_search_devops",
        "tool_search_figma",
    }

    if normalized_mode == "userstory":
        return "elevated"
    if normalized_action in elevated_actions:
        return "elevated"
    if normalized_tools.intersection(elevated_tools):
        return "elevated"
    return "standard"


def evaluate_provider_governance(
    *,
    provider_used: str = "",
    model_used: str = "",
    action: str = "",
    mode: str = "",
    tools_used: Iterable[str] | None = None,
) -> dict[str, Any]:
    provider_family = _normalize_provider_family(provider_used or model_used)
    external_provider = provider_family in PROVIDER_EXTERNAL_MODEL_FAMILIES
    sensitivity = infer_data_sensitivity(action=action, mode=mode, tools_used=tools_used)
    policy_note = ""

    if external_provider and PROVIDER_GOVERNANCE_EXPERIMENTAL_ALLOW_EXTERNAL:
        policy_note = PROVIDER_GOVERNANCE_NOTE
    elif external_provider:
        policy_note = "Provider externo ativo fora do âmbito preferencial Azure."

    recommended_scope = "azure_preferred" if sensitivity == "elevated" else "hybrid_allowed"

    return {
        "policy_mode": PROVIDER_GOVERNANCE_MODE,
        "provider_family": provider_family or "unknown",
        "external_provider": bool(external_provider),
        "data_sensitivity": sensitivity,
        "recommended_scope": recommended_scope,
        "experimental_allow_external": bool(PROVIDER_GOVERNANCE_EXPERIMENTAL_ALLOW_EXTERNAL),
        "policy_note": policy_note,
    }
