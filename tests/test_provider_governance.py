from provider_governance import evaluate_provider_governance, infer_data_sensitivity


def test_infer_data_sensitivity_marks_userstory_as_elevated():
    assert infer_data_sensitivity(action="agent_chat", mode="userstory", tools_used=[]) == "elevated"


def test_evaluate_provider_governance_marks_anthropic_as_external():
    result = evaluate_provider_governance(
        provider_used="anthropic:claude-sonnet-4-6",
        model_used="anthropic:claude-sonnet-4-6",
        action="speech_prompt",
        mode="general",
        tools_used=["speech_prompt"],
    )

    assert result["policy_mode"] == "advisory"
    assert result["provider_family"] == "anthropic"
    assert result["external_provider"] is True
    assert result["data_sensitivity"] == "elevated"
    assert result["policy_note"]
