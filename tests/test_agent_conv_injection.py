from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import agent
from models import AgentChatRequest, LLMToolCall
from request_context import reset_request_context, set_request_context


@pytest.mark.asyncio
async def test_chart_uploaded_table_receives_conv_context(monkeypatch):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"status": "ok"}

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = "conv-chart-injection"
    agent.conversations[conv_id] = [{"role": "user", "content": "faz um gráfico deste ficheiro"}]
    try:
        tool_call = LLMToolCall(id="tool-1", name="chart_uploaded_table", arguments={"query": "gráfico por categoria"})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == "chart_uploaded_table"
    assert captured["args"]["conv_id"] == conv_id
    assert captured["args"]["user_sub"] == "user-123"


@pytest.mark.asyncio
async def test_generate_presentation_receives_conv_context(monkeypatch):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"presentation_generated": True}

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = "conv-pptx-injection"
    agent.conversations[conv_id] = [{"role": "user", "content": "faz um power point com base neste pdf"}]
    try:
        tool_call = LLMToolCall(id="tool-pptx", name="generate_presentation", arguments={"title": "Deck", "content": "Resumo curto"})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == "generate_presentation"
    assert captured["args"]["conv_id"] == conv_id


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["update_data_dictionary", "get_data_dictionary"])
async def test_dictionary_tools_receive_conv_context(monkeypatch, tool_name):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"status": "ok"}

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = f"conv-{tool_name}"
    agent.conversations[conv_id] = [{"role": "user", "content": "usa o dicionário"}]
    try:
        arguments = {"table_name": "sample.csv"}
        if tool_name == "update_data_dictionary":
            arguments["mappings"] = [{"column_name": "campo_1", "mapped_name": "session_id"}]
        tool_call = LLMToolCall(id="tool-dict", name=tool_name, arguments=arguments)
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == tool_name
    assert captured["args"]["conv_id"] == conv_id
    assert captured["args"]["user_sub"] == "user-123"


@pytest.mark.asyncio
async def test_screenshot_to_us_receives_conv_context(monkeypatch):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"stories": []}

    async def fake_uploaded_files(_conv_id):
        return [{"filename": "screen.png", "image_base64": "abc"}]

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    conv_id = "conv-screenshot-injection"
    agent.conversations[conv_id] = [{"role": "user", "content": "faz-me uma user story para este ecrã"}]
    try:
        tool_call = LLMToolCall(id="tool-ss", name="screenshot_to_us", arguments={})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured["name"] == "screenshot_to_us"
    assert captured["args"]["conv_id"] == conv_id
    assert captured["args"]["user_sub"] == "user-123"


@pytest.mark.asyncio
async def test_execute_tool_calls_handles_non_dict_results(monkeypatch):
    async def fake_execute_tool(_name, _args):
        return "plain-text-result"

    async def fake_blob_upload_json(_container, _blob_name, payload):
        assert payload == "plain-text-result"
        return {"blob_ref": "blob://tool-result"}

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "blob_upload_json", fake_blob_upload_json)
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-nondict-result"
    agent.conversations[conv_id] = [{"role": "user", "content": "test"}]
    try:
        tool_call = LLMToolCall(id="tool-2", name="chart_uploaded_table", arguments={"query": "gráfico simples"})
        _, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tool_details[0]["result_summary"]["total_count"] == "N/A"
    assert tool_details[0]["result_summary"]["items_returned"] == 0
    assert tool_details[0]["result_summary"]["has_error"] is False
    assert tool_details[0]["result_summary"]["result_status"] == "ok"


@pytest.mark.asyncio
async def test_execute_tool_calls_persists_tool_error_message_when_execution_crashes(monkeypatch):
    async def fake_execute_tool(_name, _args):
        raise RuntimeError("boom")

    conv_id = "conv-tool-crash"
    agent.conversations[conv_id] = [{"role": "user", "content": "faz isso"}]
    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    try:
        tool_call = LLMToolCall(id="tool-crash", name="chart_uploaded_table", arguments={"query": "gráfico simples"})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
        persisted = agent.conversations[conv_id][-1]
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == []
    assert tool_details == []
    assert persisted["role"] == "tool"
    assert persisted["tool_call_id"] == "tool-crash"
    persisted_payload = json.loads(persisted["content"])
    assert persisted_payload["_persisted_summary"] is True
    assert "Tool execution crashed: boom" in persisted_payload["error"]


@pytest.mark.asyncio
async def test_execute_tool_calls_allows_uploaded_table_analysis_directly(monkeypatch):
    captured = {}

    async def fake_execute_tool(name, args):
        captured["name"] = name
        captured["args"] = dict(args)
        return {"summary": "ok"}

    async def fake_uploaded_files(_conv_id):
        return [{"filename": "dados.xlsx"}]

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    async def fake_blob_upload_json(*_args, **_kwargs):
        return {"blob_ref": ""}
    monkeypatch.setattr(agent, "blob_upload_json", fake_blob_upload_json)
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-direct-table-analysis"
    agent.conversations[conv_id] = [{"role": "user", "content": "analisa este ficheiro excel"}]
    try:
        tool_call = LLMToolCall(id="tool-direct-table", name="analyze_uploaded_table", arguments={"query": "analisa este ficheiro excel"})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["analyze_uploaded_table"]
    assert captured["name"] == "analyze_uploaded_table"
    assert captured["args"]["conv_id"] == conv_id
    assert tool_details[0]["result_summary"]["has_error"] is False


@pytest.mark.asyncio
async def test_execute_tool_calls_reroutes_run_code_for_exploratory_tabular_requests(monkeypatch):
    captured_calls = []

    async def fake_execute_tool(name, args):
        captured_calls.append((name, dict(args)))
        if name == "analyze_uploaded_table":
            return {"summary": "ok"}
        raise AssertionError("run_code should have been rerouted")

    async def fake_uploaded_files(_conv_id):
        return [{"filename": "dados.xlsx"}]

    async def fake_blob_upload_json(*_args, **_kwargs):
        return {"blob_ref": ""}

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    monkeypatch.setattr(agent, "blob_upload_json", fake_blob_upload_json)
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-reroute-run-code"
    agent.conversations[conv_id] = [{"role": "user", "content": "analisa bem este excel e diz-me os insights principais"}]
    try:
        tool_call = LLMToolCall(id="tool-run-code", name="run_code", arguments={})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
        persisted = agent.conversations[conv_id][-1]
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["run_code"]
    assert captured_calls[0][0] == "analyze_uploaded_table"
    assert captured_calls[0][1]["conv_id"] == conv_id
    assert tool_details[0]["result_summary"]["has_error"] is False
    payload = json.loads(persisted["content"])
    assert payload["_rerouted_from"] == "run_code"


@pytest.mark.asyncio
async def test_execute_tool_calls_blocks_devops_query_when_uploaded_file_context_exists(monkeypatch):
    async def fake_execute_tool(_name, _args):
        raise AssertionError("execute_tool should not run for blocked devops query")

    async def fake_uploaded_files(_conv_id):
        return [{"filename": "dados.csv"}]

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    monkeypatch.setattr(agent, "blob_upload_json", lambda *_args, **_kwargs: {"blob_ref": ""})
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-blocked-devops"
    agent.conversations[conv_id] = [{"role": "user", "content": "analisa este csv e mostra medias"}]
    try:
        tool_call = LLMToolCall(id="tool-blocked-devops", name="query_workitems", arguments={})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["query_workitems"]
    assert tool_details[0]["result_summary"]["has_error"] is True
    assert tool_details[0]["result_summary"]["result_status"] == "blocked"
    assert tool_details[0]["result_summary"]["error_kind"] == "blocked"
    assert "Existem ficheiros carregados" in tool_details[0]["result_summary"]["error_detail"]
    assert "query_workitems/search_workitems" in tool_details[0]["result_json"]


@pytest.mark.asyncio
async def test_execute_tool_calls_blocks_create_workitem_without_confirmation(monkeypatch):
    async def fake_execute_tool(_name, _args):
        raise AssertionError("execute_tool should not run for blocked create_workitem")

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    monkeypatch.setattr(agent, "blob_upload_json", lambda *_args, **_kwargs: {"blob_ref": ""})
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-blocked-create"
    agent.conversations[conv_id] = [{"role": "user", "content": "cria o work item"}]
    try:
        tool_call = LLMToolCall(id="tool-blocked-create", name="create_workitem", arguments={"title": "X"})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["create_workitem"]
    assert tool_details[0]["result_summary"]["has_error"] is True
    assert tool_details[0]["result_summary"]["result_status"] == "blocked"
    assert tool_details[0]["result_summary"]["error_kind"] == "blocked"
    assert "Confirmação explícita necessária" in tool_details[0]["result_json"]


@pytest.mark.asyncio
async def test_execute_tool_calls_blocks_invalid_tool_arguments_before_execution(monkeypatch):
    async def fake_execute_tool(_name, _args):
        raise AssertionError("execute_tool should not run for invalid tool args")

    async def fake_uploaded_files(_conv_id):
        return []

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    monkeypatch.setattr(agent, "blob_upload_json", lambda *_args, **_kwargs: {"blob_ref": ""})
    monkeypatch.setattr(agent, "PII_ENABLED", False)

    conv_id = "conv-invalid-tool-args"
    agent.conversations[conv_id] = [{"role": "user", "content": "procura work items"}]
    try:
        tool_call = LLMToolCall(id="tool-invalid-args", name="query_workitems", arguments={})
        tools_used, tool_details = await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["query_workitems"]
    assert tool_details[0]["result_summary"]["has_error"] is True
    assert tool_details[0]["result_summary"]["result_status"] == "tool_error"
    assert tool_details[0]["result_summary"]["error_kind"] == "invalid_tool_arguments"
    assert tool_details[0]["result_summary"]["error_detail"] == "Argumentos inválidos para a tool."
    assert "Argumentos inválidos para a tool." in tool_details[0]["result_json"]
    assert "campo obrigatório em falta" in tool_details[0]["result_json"]


def test_merge_tool_batch_results_updates_exportable_index():
    tools_used = ["query_workitems"]
    tool_details = [
        {
            "tool": "query_workitems",
            "result_summary": {"items_returned": 0},
        }
    ]

    has_exportable, export_idx = agent._merge_tool_batch_results(
        tools_used,
        tool_details,
        ["generate_file", "generate_chart"],
        [
            {"tool": "generate_file", "result_summary": {"items_returned": 2}},
            {"tool": "generate_chart", "result_summary": {"items_returned": 0}},
        ],
        has_exportable=False,
        export_idx=None,
    )

    assert tools_used == ["query_workitems", "generate_file", "generate_chart"]
    assert len(tool_details) == 3
    assert has_exportable is True
    assert export_idx == 1


@pytest.mark.asyncio
async def test_apply_forced_pretool_batches_merges_and_emits_selected_kinds():
    tools_used = []
    tool_details = []
    emitted = []

    forced_batches = [
        {
            "kind": "visual_userstory",
            "tools_used": ["screenshot_to_us"],
            "tool_details": [{"tool": "screenshot_to_us", "result_summary": {"items_returned": 0}}],
        },
        {
            "kind": "dual_hierarchy",
            "tools_used": ["query_hierarchy"],
            "tool_details": [{"tool": "query_hierarchy", "result_summary": {"items_returned": 3}}],
        },
    ]

    async def on_detail(batch_kind, detail):
        emitted.append((batch_kind, detail["tool"]))

    has_exportable, export_idx = await agent._apply_forced_pretool_batches(
        forced_batches,
        tools_used,
        tool_details,
        has_exportable=False,
        export_idx=None,
        on_detail=on_detail,
        emit_batch_kinds={"dual_hierarchy"},
    )

    assert tools_used == ["screenshot_to_us", "query_hierarchy"]
    assert len(tool_details) == 2
    assert has_exportable is True
    assert export_idx == 1
    assert emitted == [("dual_hierarchy", "query_hierarchy")]


def test_resolve_exportable_state_derives_first_export_index():
    has_exportable, export_idx = agent._resolve_exportable_state(
        [
            {"tool": "query_workitems", "result_summary": {"items_returned": 0}},
            {"tool": "generate_file", "result_summary": {"items_returned": 3}},
            {"tool": "generate_chart", "result_summary": {"items_returned": 1}},
        ]
    )

    assert has_exportable is True
    assert export_idx == 1


def test_build_agent_response_metadata_includes_export_and_suggest_fields():
    payload = agent._build_agent_response_metadata(
        "conv-meta",
        "req-meta-123",
        "blocked",
        "provider_governance_blocked",
        ["query_workitems", "generate_file"],
        [
            {"tool": "query_workitems", "result_summary": {"items_returned": 0}},
            {"tool": "generate_file", "result_summary": {"items_returned": 2}},
        ],
        {"total_tokens": 50},
        1234,
        "mock-model",
        True,
        suggest_learn_style=True,
    )

    assert payload["conversation_id"] == "conv-meta"
    assert payload["request_id"] == "req-meta-123"
    assert payload["request_status"] == "blocked"
    assert payload["request_error_kind"] == "provider_governance_blocked"
    assert set(payload["tools_used"]) == {"query_workitems", "generate_file"}
    assert payload["has_exportable_data"] is True
    assert payload["export_index"] == 1
    assert payload["tokens_used"] == {"total_tokens": 50}
    assert payload["total_time_ms"] == 1234
    assert payload["model_used"] == "mock-model"
    assert payload["is_fallback"] is True
    assert payload["suggest_learn_style"] is True


def test_llm_payload_text_prefers_content_then_error_detail():
    assert agent._llm_payload_text({"content": " resposta ", "error_detail": "erro"}) == "resposta"
    assert agent._llm_payload_text({"content": "", "error_detail": " erro detalhado "}) == "erro detalhado"
    assert agent._llm_payload_text(SimpleNamespace(content=None, error_detail=" detalhe ")) == "detalhe"


def test_derive_request_status_from_llm_payload_maps_blocked_and_error():
    assert agent._derive_request_status_from_llm_payload(
        "ok",
        {"is_error": True, "error_kind": "provider_governance_blocked"},
    ) == "blocked"
    assert agent._derive_request_status_from_llm_payload(
        "ok",
        SimpleNamespace(is_error=True, error_kind="all_providers_failed"),
    ) == "error"
    assert agent._derive_request_status_from_llm_payload(
        "blocked",
        {"is_error": True, "error_kind": "all_providers_failed"},
    ) == "blocked"


def test_derive_request_error_kind_from_llm_payload_prefers_first_explicit_error():
    assert agent._derive_request_error_kind_from_llm_payload(
        "",
        {"is_error": True, "error_kind": "provider_governance_blocked"},
    ) == "provider_governance_blocked"
    assert agent._derive_request_error_kind_from_llm_payload(
        "pre_model_blocked",
        {"is_error": True, "error_kind": "all_providers_failed"},
    ) == "pre_model_blocked"
    assert agent._derive_request_error_kind_from_llm_payload(
        "",
        SimpleNamespace(is_error=False, error_kind="ignored"),
    ) == ""


def test_merge_usage_totals_adds_numeric_usage_fields():
    merged = agent._merge_usage_totals(
        {"input_tokens": 10, "output_tokens": 5},
        {"input_tokens": 2, "output_tokens": 3, "cache_hit": True, "model": "x"},
    )

    assert merged == {"input_tokens": 12, "output_tokens": 8}


@pytest.mark.asyncio
async def test_run_forced_pretool_batches_returns_named_batches(monkeypatch):
    async def fake_visual(*_args, **_kwargs):
        return ["screenshot_to_us"], [{"tool": "screenshot_to_us", "result_summary": {"items_returned": 0}}]

    async def fake_dual(*_args, **_kwargs):
        return ["query_hierarchy"], [{"tool": "query_hierarchy", "result_summary": {"items_returned": 3}}]

    async def fake_followup(*_args, **_kwargs):
        return [], []

    monkeypatch.setattr(agent, "_run_forced_visual_userstory", fake_visual)
    monkeypatch.setattr(agent, "_run_forced_dual_hierarchy", fake_dual)
    monkeypatch.setattr(agent, "_run_forced_workitem_filter_followup", fake_followup)

    batches = await agent._run_forced_pretool_batches(
        "analisa isto",
        "conv-forced-batches",
        user_sub="user-123",
    )

    assert [batch["kind"] for batch in batches] == [
        "visual_userstory",
        "dual_hierarchy",
        "workitem_filter_followup",
    ]
    assert batches[0]["tools_used"] == ["screenshot_to_us"]
    assert batches[1]["tool_details"][0]["tool"] == "query_hierarchy"
    assert batches[2]["tool_details"] == []


@pytest.mark.asyncio
async def test_collect_stream_forced_pretool_payloads_returns_emitted_events(monkeypatch):
    async def fake_forced_batches(*_args, **_kwargs):
        return [
            {
                "kind": "visual_userstory",
                "tools_used": ["screenshot_to_us"],
                "tool_details": [{"tool": "screenshot_to_us", "result_summary": {"items_returned": 0}}],
            },
            {
                "kind": "dual_hierarchy",
                "tools_used": ["query_hierarchy"],
                "tool_details": [{"tool": "query_hierarchy", "result_summary": {"items_returned": 2, "result_status": "ok", "has_error": False}}],
            },
        ]

    monkeypatch.setattr(agent, "_run_forced_pretool_batches", fake_forced_batches)

    tools_used = []
    tool_details = []
    payloads, has_exportable, export_idx = await agent._collect_stream_forced_pretool_payloads(
        "pergunta",
        "conv-forced-stream",
        user_sub="user-123",
        tools_used=tools_used,
        tool_details=tool_details,
        has_exportable=False,
        export_idx=None,
    )

    assert tools_used == ["screenshot_to_us", "query_hierarchy"]
    assert len(tool_details) == 2
    assert has_exportable is True
    assert export_idx == 1
    assert [payload["type"] for payload in payloads] == ["tool_start", "tool_result"]
    assert payloads[0]["tool"] == "query_hierarchy"
    assert payloads[1]["tool"] == "query_hierarchy"


@pytest.mark.asyncio
async def test_run_tool_detection_llm_round_merges_usage_and_fallback(monkeypatch):
    captured = {}

    async def fake_build_llm_messages(conv_id, effective_question, **kwargs):
        captured["conv_id"] = conv_id
        captured["question"] = effective_question
        captured["kwargs"] = kwargs
        return [{"role": "user", "content": effective_question}]

    async def fake_llm_with_fallback(messages, **kwargs):
        captured["messages"] = messages
        captured["llm_kwargs"] = kwargs
        return SimpleNamespace(
            model="mock-model",
            usage={"input_tokens": 11, "output_tokens": 7},
            fallback_chain=[{"provider": "primary", "status": "failed"}],
            tool_calls=[],
            content="ok",
        )

    monkeypatch.setattr(agent, "_build_llm_messages", fake_build_llm_messages)
    monkeypatch.setattr(agent, "llm_with_fallback", fake_llm_with_fallback)

    response, total_usage, model_used, is_fallback = await agent._run_tool_detection_llm_round(
        "conv-llm-round",
        "pergunta",
        request=AgentChatRequest(question="pergunta"),
        tier="standard",
        tool_definitions=[{"type": "function"}],
        user_sub="user-123",
        total_usage={"input_tokens": 5},
        model_used="old-model",
        is_fallback=False,
    )

    assert response.content == "ok"
    assert total_usage == {"input_tokens": 16, "output_tokens": 7}
    assert model_used == "mock-model"
    assert is_fallback is True
    assert captured["conv_id"] == "conv-llm-round"
    assert captured["kwargs"]["tools_list"] == [{"type": "function"}]


@pytest.mark.asyncio
async def test_run_tool_call_iterations_executes_tools_and_returns_final_response(monkeypatch):
    conv_id = "conv-tool-iterations"
    start_tools = []
    result_tools = []
    next_round_markers = []

    async def fake_execute_tool_calls(tool_calls, _conv_id, user_sub=""):
        assert _conv_id == conv_id
        assert user_sub == "user-123"
        assert [tc.name for tc in tool_calls] == ["generate_file"]
        return (
            ["generate_file"],
            [
                {
                    "tool": "generate_file",
                    "result_summary": {"items_returned": 2},
                    "duration_ms": 12,
                }
            ],
        )

    async def fake_round(*_args, **_kwargs):
        return (
            SimpleNamespace(tool_calls=[], content="resposta final"),
            {"total_tokens": 9},
            "mock-model",
            False,
        )

    initial_response = SimpleNamespace(
        tool_calls=[LLMToolCall(id="tc-1", name="generate_file", arguments={"format": "xlsx"})],
        content=None,
        model="mock-model",
    )

    monkeypatch.setattr(agent, "_execute_tool_calls", fake_execute_tool_calls)
    monkeypatch.setattr(agent, "_run_tool_detection_llm_round", fake_round)

    agent.conversations[conv_id] = []
    try:
        response, total_usage, model_used, is_fallback, has_exportable, export_idx, exhausted = await agent._run_tool_call_iterations(
            initial_response,
            conv_id,
            "pergunta",
            request=AgentChatRequest(question="pergunta"),
            tier="standard",
            tool_definitions=[{"type": "function"}],
            user_sub="user-123",
            tools_used=[],
            tool_details=[],
            total_usage={},
            model_used="old-model",
            is_fallback=False,
            has_exportable=False,
            export_idx=None,
            on_tool_start=lambda tc: start_tools.append(tc.name),
            on_tool_result=lambda detail: result_tools.append(detail["tool"]),
            on_before_next_round=lambda iteration: next_round_markers.append(iteration),
        )
    finally:
        agent.conversations.pop(conv_id, None)

    assert response.content == "resposta final"
    assert total_usage == {"total_tokens": 9}
    assert model_used == "mock-model"
    assert is_fallback is False
    assert has_exportable is True
    assert export_idx == 0
    assert exhausted is False
    assert start_tools == ["generate_file"]
    assert result_tools == ["generate_file"]
    assert next_round_markers == [1]


@pytest.mark.asyncio
async def test_stream_final_response_payloads_updates_state_and_persists(monkeypatch):
    captured = {"append": None}

    async def fake_build_llm_messages(*_args, **_kwargs):
        return [{"role": "system", "content": "system"}]

    async def fake_llm_stream_with_fallback(*_args, **_kwargs):
        yield SimpleNamespace(type="token", text="ola ", data=None)
        yield SimpleNamespace(
            type="done",
            text=None,
            data={
                "usage": {"output_tokens": 3},
                "model": "stream-model",
                "fallback_chain": [{"provider": "primary", "status": "failed"}],
            },
        )

    async def fake_append(conv_id, content, **kwargs):
        captured["append"] = (conv_id, content, kwargs)

    monkeypatch.setattr(agent, "_build_llm_messages", fake_build_llm_messages)
    monkeypatch.setattr(agent, "llm_stream_with_fallback", fake_llm_stream_with_fallback)
    monkeypatch.setattr(agent, "_append_assistant_text", fake_append)

    state = {
        "request_status": "ok",
        "request_error_kind": "",
        "total_usage": {"input_tokens": 2},
        "model_used": "initial-model",
        "is_fallback": False,
        "should_persist": False,
    }

    payloads = []
    async for payload in agent._stream_final_response_payloads(
        SimpleNamespace(content="fallback static", is_error=False, error_kind=""),
        "conv-stream-final",
        "pergunta",
        request=AgentChatRequest(question="pergunta"),
        tier="standard",
        user_sub="user-123",
        prefix="",
        state=state,
    ):
        payloads.append(payload)

    assert payloads == [{"type": "token", "text": "ola "}]
    assert captured["append"] == (
        "conv-stream-final",
        "ola ",
        {"effective_question": "pergunta", "refresh_polymorphic": True},
    )
    assert state["request_status"] == "ok"
    assert state["request_error_kind"] == ""
    assert state["total_usage"] == {"input_tokens": 2, "output_tokens": 3}
    assert state["model_used"] == "stream-model"
    assert state["is_fallback"] is True
    assert state["should_persist"] is True


@pytest.mark.asyncio
async def test_stream_tool_loop_payloads_updates_state_and_emits_sequence(monkeypatch):
    conv_id = "conv-stream-tool-loop"
    captured = {"tool_calls": None}

    responses = iter(
        [
            (
                SimpleNamespace(
                    tool_calls=[LLMToolCall(id="tc-1", name="generate_file", arguments={"format": "xlsx"})],
                    content=None,
                ),
                {"input_tokens": 5},
                "model-a",
                False,
            ),
            (
                SimpleNamespace(tool_calls=[], content="resposta final"),
                {"output_tokens": 7},
                "model-b",
                True,
            ),
        ]
    )

    async def fake_round(*_args, **kwargs):
        response, usage, model_used, is_fallback = next(responses)
        return (
            response,
            agent._merge_usage_totals(kwargs.get("total_usage", {}), usage),
            model_used,
            is_fallback,
        )

    async def fake_execute_tool_calls(tool_calls, _conv_id, user_sub=""):
        captured["tool_calls"] = [tc.name for tc in tool_calls]
        assert _conv_id == conv_id
        assert user_sub == "user-123"
        return (
            ["generate_file"],
            [
                {
                    "tool": "generate_file",
                    "result_summary": {"items_returned": 1, "result_status": "ok", "has_error": False},
                    "duration_ms": 11,
                }
            ],
        )

    monkeypatch.setattr(agent, "_run_tool_detection_llm_round", fake_round)
    monkeypatch.setattr(agent, "_execute_tool_calls", fake_execute_tool_calls)

    agent.conversations[conv_id] = []
    try:
        state = {
            "total_usage": {},
            "model_used": "initial",
            "is_fallback": False,
            "has_exportable": False,
            "export_idx": None,
        }
        payloads = []
        async for payload in agent._stream_tool_loop_payloads(
            conv_id,
            "pergunta",
            request=AgentChatRequest(question="pergunta"),
            tier="standard",
            tool_definitions=[{"type": "function"}],
            user_sub="user-123",
            tools_used=[],
            tool_details=[],
            state=state,
        ):
            payloads.append(payload)
    finally:
        agent.conversations.pop(conv_id, None)

    assert [payload["type"] for payload in payloads] == [
        "thinking",
        "tool_start",
        "tool_result",
        "thinking",
    ]
    assert captured["tool_calls"] == ["generate_file"]
    assert state["response"].content == "resposta final"
    assert state["total_usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert state["model_used"] == "model-b"
    assert state["is_fallback"] is True
    assert state["has_exportable"] is True
    assert state["export_idx"] == 0
    assert state["exhausted_tool_loop"] is False


@pytest.mark.asyncio
async def test_stream_tool_loop_payloads_delegates_to_shared_iterations_helper(monkeypatch):
    initial_response = SimpleNamespace(
        tool_calls=[LLMToolCall(id="tc-1", name="generate_file", arguments={"format": "xlsx"})],
        content=None,
    )
    captured = {}

    async def fake_round(*_args, **_kwargs):
        return (initial_response, {"input_tokens": 2}, "model-a", False)

    async def fake_run_tool_call_iterations(
        passed_initial_response,
        _conv_id,
        _effective_question,
        **kwargs,
    ):
        captured["initial_response"] = passed_initial_response
        captured["conv_id"] = _conv_id
        await kwargs["on_tool_start"](LLMToolCall(id="tc-1", name="generate_file", arguments={}))
        await kwargs["on_tool_result"](
            {
                "tool": "generate_file",
                "result_summary": {"items_returned": 1, "result_status": "ok", "has_error": False},
                "duration_ms": 9,
            }
        )
        await kwargs["on_before_next_round"](1)
        return (
            SimpleNamespace(tool_calls=[], content="final"),
            {"input_tokens": 2, "output_tokens": 4},
            "model-b",
            True,
            True,
            0,
            False,
        )

    monkeypatch.setattr(agent, "_run_tool_detection_llm_round", fake_round)
    monkeypatch.setattr(agent, "_run_tool_call_iterations", fake_run_tool_call_iterations)

    state = {
        "total_usage": {},
        "model_used": "initial",
        "is_fallback": False,
        "has_exportable": False,
        "export_idx": None,
    }

    payloads = []
    async for payload in agent._stream_tool_loop_payloads(
        "conv-stream-shared-helper",
        "pergunta",
        request=AgentChatRequest(question="pergunta"),
        tier="standard",
        tool_definitions=[{"type": "function"}],
        user_sub="user-123",
        tools_used=[],
        tool_details=[],
        state=state,
    ):
        payloads.append(payload)

    assert captured["initial_response"] is initial_response
    assert captured["conv_id"] == "conv-stream-shared-helper"
    assert [payload["type"] for payload in payloads] == [
        "thinking",
        "tool_start",
        "tool_result",
        "thinking",
    ]
    assert state["response"].content == "final"
    assert state["total_usage"] == {"input_tokens": 2, "output_tokens": 4}
    assert state["model_used"] == "model-b"
    assert state["is_fallback"] is True
    assert state["has_exportable"] is True
    assert state["export_idx"] == 0
    assert state["exhausted_tool_loop"] is False


def test_build_stream_tool_result_event_exposes_error_envelope():
    payload = agent._build_stream_tool_result_event(
        {
            "tool": "generate_presentation",
            "duration_ms": 27,
            "result_summary": {
                "has_error": True,
                "result_status": "tool_error",
                "error_kind": "planner_failed",
                "error_detail": "Planner devolveu schema inválido.",
            },
        }
    )

    assert payload["type"] == "tool_result"
    assert payload["tool"] == "generate_presentation"
    assert payload["text"] == "⚠️ generate_presentation: erro"
    assert payload["duration_ms"] == 27
    assert payload["result_status"] == "tool_error"
    assert payload["error_kind"] == "planner_failed"
    assert payload["error_detail"] == "Planner devolveu schema inválido."
    assert payload["has_error"] is True


@pytest.mark.asyncio
async def test_check_pre_model_blockers_prefers_quota_reason(monkeypatch):
    async def fake_check_token_quota(_tier, _conv_id):
        return "quota hit"

    async def fake_check_user_daily_limit(_user_sub):
        raise AssertionError("daily limit should not run when quota already blocked")

    monkeypatch.setattr(agent, "_check_token_quota", fake_check_token_quota)
    monkeypatch.setattr(agent, "_check_user_daily_limit", fake_check_user_daily_limit)

    result = await agent._check_pre_model_blockers("standard", "conv-1", "user-1")

    assert result == "quota hit"


@pytest.mark.asyncio
async def test_append_assistant_text_refreshes_pending_state_when_requested(monkeypatch):
    captured = {}

    async def fake_refresh(conv_id, effective_question, answer):
        captured["conv_id"] = conv_id
        captured["effective_question"] = effective_question
        captured["answer"] = answer

    monkeypatch.setattr(agent, "_refresh_polymorphic_pending_state", fake_refresh)

    conv_id = "conv-append-assistant"
    agent.conversations[conv_id] = [{"role": "system", "content": "sistema"}]
    try:
        await agent._append_assistant_text(
            conv_id,
            "resposta final",
            effective_question="pergunta base",
            refresh_polymorphic=True,
        )
    finally:
        agent.conversations.pop(conv_id, None)

    assert captured == {
        "conv_id": "conv-append-assistant",
        "effective_question": "pergunta base",
        "answer": "resposta final",
    }


@pytest.mark.asyncio
async def test_bootstrap_agent_turn_prepares_history_and_returns_pending_reply(monkeypatch):
    async def fake_ensure_conversation(*_args, **_kwargs):
        return None

    async def fake_ensure_uploaded_files_loaded(*_args, **_kwargs):
        return None

    async def fake_inject_file_context(_conv_id, messages):
        messages.append({"role": "system", "content": "ficheiro anexado"})

    async def fake_prepare_followup(_conv_id, _question):
        return "pergunta transformada", "resposta pendente"

    monkeypatch.setattr(agent, "_ensure_conversation", fake_ensure_conversation)
    monkeypatch.setattr(agent, "_ensure_uploaded_files_loaded", fake_ensure_uploaded_files_loaded)
    monkeypatch.setattr(agent, "_inject_file_context", fake_inject_file_context)
    monkeypatch.setattr(agent, "_prepare_polymorphic_followup_question", fake_prepare_followup)
    monkeypatch.setattr(agent, "_trim_history", lambda messages: messages + [{"role": "system", "content": "trimmed"}])

    conv_id = "conv-bootstrap-turn"
    agent.conversations[conv_id] = [{"role": "system", "content": "base"}]
    try:
        effective_question, pending_reply = await agent._bootstrap_agent_turn(
            conv_id,
            "general",
            "pk-user",
            "pergunta original",
            user_sub="user-123",
        )
        history = agent.conversations[conv_id]
    finally:
        agent.conversations.pop(conv_id, None)

    assert effective_question == "pergunta transformada"
    assert pending_reply == "resposta pendente"
    assert history[-2] == {"role": "user", "content": "pergunta original"}
    assert history[-1] == {"role": "system", "content": "trimmed"}


@pytest.mark.asyncio
async def test_finalize_agent_request_persists_and_records_usage(monkeypatch):
    captured = {}

    async def fake_persist(conv_id, partition_key):
        captured["persist"] = (conv_id, partition_key)

    async def fake_record_token_quota(tier, usage):
        captured["quota"] = (tier, dict(usage))

    async def fake_record_daily_spend(user_sub, tokens):
        captured["spend"] = (user_sub, tokens)

    monkeypatch.setattr(agent, "_persist_conversation", fake_persist)
    monkeypatch.setattr(agent, "_record_token_quota", fake_record_token_quota)
    monkeypatch.setattr(agent, "_record_user_daily_spend", fake_record_daily_spend)
    monkeypatch.setattr(
        agent.agent_metrics,
        "record",
        lambda route_name, duration_ms, **kwargs: captured.setdefault(
            "metrics",
            (route_name, duration_ms, kwargs),
        ),
    )
    ctx_tokens = set_request_context("req-finalize-123", "conv-finalize")
    try:
        await agent._finalize_agent_request(
            "chat",
            "conv-finalize",
            "pk-finalize",
            "standard",
            {"total_tokens": 42},
            user_sub="user-123",
            should_persist=True,
            total_time_ms=1234,
            tool_count=2,
            is_fallback=True,
            request_status="ok",
            request_error_kind="",
            persist_task_label="persist_task",
            model_used="mock-model",
        )
    finally:
        reset_request_context(ctx_tokens)

    assert captured["persist"] == ("conv-finalize", "pk-finalize")
    assert captured["quota"] == ("standard", {"total_tokens": 42})
    assert captured["spend"] == ("user-123", 42)
    assert captured["metrics"] == (
        "chat",
        1234,
        {
            "tool_count": 2,
            "fallback": True,
            "status": "ok",
            "request_id": "req-finalize-123",
            "conversation_id": "conv-finalize",
            "model_used": "mock-model",
            "error_kind": "",
        },
    )


@pytest.mark.asyncio
async def test_agent_chat_stream_emits_request_id_in_sse_events(monkeypatch):
    async def fake_bootstrap(*_args, **_kwargs):
        return "pergunta", "resposta pendente"

    async def fake_append(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent, "_bootstrap_agent_turn", fake_bootstrap)
    monkeypatch.setattr(agent, "_append_assistant_text", fake_append)
    monkeypatch.setattr(agent.agent_metrics, "record", lambda *_args, **_kwargs: None)

    stream = agent.agent_chat_stream(
        AgentChatRequest(question="olá", conversation_id="conv-stream-ctx"),
        {"sub": "user-123"},
    )

    events = []
    async for chunk in stream:
        assert chunk.startswith("data: ")
        events.append(json.loads(chunk[len("data: "):].strip()))

    assert [event["type"] for event in events] == ["init", "token", "done"]
    assert events[0]["request_id"]
    assert events[1]["request_id"] == events[0]["request_id"]
    assert events[2]["request_id"] == events[0]["request_id"]
    assert events[2]["conversation_id"] == "conv-stream-ctx"


@pytest.mark.asyncio
async def test_agent_chat_stream_pending_reply_uses_finalize_path(monkeypatch):
    captured = {}

    async def fake_bootstrap(*_args, **_kwargs):
        return "pergunta", "resposta pendente"

    async def fake_append(*_args, **_kwargs):
        return None

    async def fake_finalize(*_args, **kwargs):
        captured["should_persist"] = kwargs["should_persist"]
        captured["request_status"] = kwargs["request_status"]
        captured["request_error_kind"] = kwargs["request_error_kind"]

    monkeypatch.setattr(agent, "_bootstrap_agent_turn", fake_bootstrap)
    monkeypatch.setattr(agent, "_append_assistant_text", fake_append)
    monkeypatch.setattr(agent, "_finalize_agent_request", fake_finalize)

    stream = agent.agent_chat_stream(
        AgentChatRequest(question="olá", conversation_id="conv-stream-pending"),
        {"sub": "user-123"},
    )

    events = []
    async for chunk in stream:
        events.append(json.loads(chunk[len("data: "):].strip()))

    assert [event["type"] for event in events] == ["init", "token", "done"]
    assert captured == {
        "should_persist": True,
        "request_status": "ok",
        "request_error_kind": "",
    }


@pytest.mark.asyncio
async def test_agent_chat_stream_blocked_before_model_uses_finalize_path(monkeypatch):
    captured = {}

    async def fake_bootstrap(*_args, **_kwargs):
        return "pergunta", ""

    async def fake_check_pre_model_blockers(*_args, **_kwargs):
        return "quota hit"

    async def fake_finalize(*_args, **kwargs):
        captured["should_persist"] = kwargs["should_persist"]
        captured["request_status"] = kwargs["request_status"]
        captured["request_error_kind"] = kwargs["request_error_kind"]

    monkeypatch.setattr(agent, "_bootstrap_agent_turn", fake_bootstrap)
    monkeypatch.setattr(agent, "_check_pre_model_blockers", fake_check_pre_model_blockers)
    monkeypatch.setattr(agent, "_finalize_agent_request", fake_finalize)

    stream = agent.agent_chat_stream(
        AgentChatRequest(question="olá", conversation_id="conv-stream-blocked"),
        {"sub": "user-123"},
    )

    events = []
    async for chunk in stream:
        events.append(json.loads(chunk[len("data: "):].strip()))

    assert [event["type"] for event in events] == ["init", "error", "done"]
    assert captured == {
        "should_persist": False,
        "request_status": "blocked",
        "request_error_kind": "pre_model_blocked",
    }


@pytest.mark.asyncio
async def test_execute_tool_calls_logs_request_id_into_tool_metrics(monkeypatch):
    async def fake_execute_tool(_name, _args):
        return {"status": "ok"}

    async def fake_uploaded_files(_conv_id):
        return []

    captured = {}

    def fake_record(tool_name, duration_ms, status="ok", **kwargs):
        captured["tool_name"] = tool_name
        captured["status"] = status
        captured["request_id"] = kwargs.get("request_id")
        captured["conversation_id"] = kwargs.get("conversation_id")

    monkeypatch.setattr(agent, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)
    monkeypatch.setattr(agent, "blob_upload_json", lambda *_args, **_kwargs: {"blob_ref": ""})
    monkeypatch.setattr(agent, "PII_ENABLED", False)
    monkeypatch.setattr(agent.tool_metrics, "record", fake_record)

    conv_id = "conv-tool-metrics-ctx"
    ctx_tokens = set_request_context("req-tool-123", conv_id)
    agent.conversations[conv_id] = [{"role": "user", "content": "faz isso"}]
    try:
        tool_call = LLMToolCall(id="tool-metrics", name="chart_uploaded_table", arguments={"query": "grafico"})
        await agent._execute_tool_calls([tool_call], conv_id=conv_id, user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)
        reset_request_context(ctx_tokens)

    assert captured["tool_name"] == "chart_uploaded_table"
    assert captured["status"] == "ok"
    assert captured["request_id"] == "req-tool-123"
    assert captured["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_build_llm_messages_trims_tool_call_and_tool_result_as_atomic_group(monkeypatch):
    conv_id = "conv-trim-tool-pair"
    agent.conversations[conv_id] = [
        {"role": "system", "content": "sistema"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search_workitems", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok": true}'},
        {"role": "user", "content": "continua"},
    ]

    async def fake_learned_rules():
        return ""

    async def fake_fewshot(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(agent, "get_learned_rules", fake_learned_rules)
    monkeypatch.setattr(agent, "get_few_shot_examples", fake_fewshot)
    monkeypatch.setattr(agent, "count_tools_tokens", lambda _tools: 0)
    monkeypatch.setattr(agent, "count_messages_tokens", lambda _messages: 5000)
    monkeypatch.setattr(agent, "count_single_message_tokens", lambda _msg: 1000)
    monkeypatch.setattr(agent, "resolve_context_window", lambda _model: 3000)
    monkeypatch.setattr(agent, "RESPONSE_RESERVE_TOKENS", 500)
    monkeypatch.setattr(agent, "get_provider", lambda _tier=None: SimpleNamespace(deployment="gpt-4.1"))

    try:
        result = await agent._build_llm_messages(conv_id, "continua", request=None, tier="standard", tools_list=[], user_sub="user-123")
    finally:
        agent.conversations.pop(conv_id, None)

    assert result == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "continua"},
    ]


@pytest.mark.asyncio
async def test_persist_conversation_handles_large_assistant_tool_calls(monkeypatch):
    captured = {}

    async def fake_table_insert(_table, entity):
        captured["entity"] = entity
        return True

    monkeypatch.setattr(agent, "table_insert", fake_table_insert)
    monkeypatch.setattr(agent, "table_merge", lambda *_args, **_kwargs: True)

    conv_id = "conv-persist-large"
    huge_arguments = json.dumps({"payload": "x" * 70000})
    agent.conversations[conv_id] = [
        {"role": "system", "content": "sistema"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_code", "arguments": huge_arguments},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": json.dumps({"rows": list(range(5000))})},
        {"role": "user", "content": "analisa tudo " + ("muito " * 5000)},
    ]
    agent.conversation_meta[conv_id] = {
        "partition_key": "pk",
        "created_at": "2026-03-26T10:00:00+00:00",
        "mode": "general",
    }
    try:
        await agent._persist_conversation(conv_id, "pk")
    finally:
        agent.conversations.pop(conv_id, None)
        agent.conversation_meta.pop(conv_id, None)

    entity = captured["entity"]
    assert len(entity["Messages"].encode("utf-8")) <= 60000


@pytest.mark.asyncio
async def test_mask_pii_structured_preserves_json_shape(monkeypatch):
    async def fake_mask_pii(text, _ctx):
        return text.replace("João Silva", "[MASKED_PERSON]")

    monkeypatch.setattr(agent, "mask_pii", fake_mask_pii)

    masked = await agent._mask_pii_structured(
        {"items": [{"name": "João Silva", "count": 42}], "ok": True},
        agent.PIIMaskingContext(),
    )

    assert masked == {"items": [{"name": "[MASKED_PERSON]", "count": 42}], "ok": True}


@pytest.mark.asyncio
async def test_switch_conversation_mode_waits_for_conversation_lock():
    conv_id = "conv-switch-lock"
    agent.conversations[conv_id] = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "hello"},
    ]
    agent.conversation_meta[conv_id] = {"mode": "general"}
    lock = await agent._get_conversation_lock(conv_id)

    try:
        async with lock:
            switch_task = asyncio.create_task(agent.switch_conversation_mode(conv_id, "userstory"))
            await asyncio.sleep(0)
            assert not switch_task.done()

        assert await switch_task is True
        assert agent.conversation_meta[conv_id]["mode"] == "userstory"
        assert agent.conversations[conv_id][0]["role"] == "system"
        assert agent.conversations[conv_id][0]["content"] == agent.get_userstory_system_prompt()
    finally:
        agent.conversations.pop(conv_id, None)
        agent.conversation_meta.pop(conv_id, None)
        agent._conversation_locks.pop(conv_id, None)


@pytest.mark.asyncio
async def test_agent_chat_returns_precise_timeout_error_without_secret(monkeypatch):
    async def fake_ensure_conversation(conv_id, _mode, _partition_key, user_sub=""):
        _ = user_sub
        agent.conversations[conv_id] = [{"role": "system", "content": "system"}]
        return conv_id

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_build_llm_messages(*_args, **_kwargs):
        return [{"role": "system", "content": "system"}]

    async def fake_llm_with_fallback(*_args, **_kwargs):
        raise asyncio.TimeoutError("sensitive/path/secret")

    monkeypatch.setattr(agent, "_ensure_conversation", fake_ensure_conversation)
    monkeypatch.setattr(agent, "_ensure_uploaded_files_loaded", fake_noop)
    monkeypatch.setattr(agent, "_inject_file_context", fake_noop)
    monkeypatch.setattr(agent, "_build_llm_messages", fake_build_llm_messages)
    monkeypatch.setattr(agent, "llm_with_fallback", fake_llm_with_fallback)
    monkeypatch.setattr(agent, "get_all_tool_definitions", lambda: [])

    request = AgentChatRequest(question="olá", conversation_id="conv-timeout")
    try:
        response = await agent.agent_chat(request, {"sub": "tester"})
    finally:
        agent.conversations.pop("conv-timeout", None)
        agent.conversation_meta.pop("conv-timeout", None)
        agent._conversation_locks.pop("conv-timeout", None)

    assert response.answer == "Erro real detetado ao contactar o modelo: timeout a aguardar resposta do provider"
    assert "sensitive/path/secret" not in response.answer


@pytest.mark.asyncio
async def test_agent_chat_uses_structured_llm_error_to_mark_blocked_status(monkeypatch):
    captured = {}

    async def fake_bootstrap(*_args, **_kwargs):
        return "pergunta", ""

    async def fake_noop(*_args, **_kwargs):
        return None

    async def fake_forced_batches(*_args, **_kwargs):
        return []

    async def fake_round(*_args, **_kwargs):
        return (
            SimpleNamespace(
                content="Pedido bloqueado: nenhum provider interno disponível neste modo de governação.",
                error_detail="Pedido bloqueado: nenhum provider interno disponível neste modo de governação.",
                is_error=True,
                error_kind="provider_governance_blocked",
                tool_calls=[],
                usage={},
                model="governance_gate",
                fallback_chain=[],
            ),
            {},
            "governance_gate",
            False,
        )

    async def fake_finalize(*_args, **kwargs):
        captured["request_status"] = kwargs["request_status"]
        captured["model_used"] = kwargs["model_used"]

    async def fake_refresh(*_args, **_kwargs):
        return None

    conv_id = "conv-structured-llm-blocked"
    agent.conversations[conv_id] = []
    monkeypatch.setattr(agent, "_bootstrap_agent_turn", fake_bootstrap)
    monkeypatch.setattr(agent, "_check_pre_model_blockers", fake_noop)
    monkeypatch.setattr(agent, "_run_forced_pretool_batches", fake_forced_batches)
    monkeypatch.setattr(agent, "_run_tool_detection_llm_round", fake_round)
    monkeypatch.setattr(agent, "_finalize_agent_request", fake_finalize)
    monkeypatch.setattr(agent, "_refresh_polymorphic_pending_state", fake_refresh)
    monkeypatch.setattr(agent, "get_all_tool_definitions", lambda: [])

    try:
        response = await agent.agent_chat(
            AgentChatRequest(question="olá", conversation_id=conv_id),
            {"sub": "tester"},
        )
    finally:
        agent.conversations.pop(conv_id, None)
        agent.conversation_meta.pop(conv_id, None)
        agent._conversation_locks.pop(conv_id, None)

    assert response.answer.startswith("Pedido bloqueado:")
    assert response.request_id
    assert response.request_status == "blocked"
    assert response.request_error_kind == "provider_governance_blocked"
    assert captured["request_status"] == "blocked"
    assert captured["model_used"] == "governance_gate"


@pytest.mark.asyncio
async def test_check_token_quota_is_fail_open_when_enforcement_disabled(monkeypatch):
    class _DenyManager:
        async def check(self, _tier):
            return False, "daily quota exceeded"

    monkeypatch.setattr(agent, "TOKEN_QUOTA_ENFORCEMENT_ENABLED", False)
    monkeypatch.setattr(agent._tq_module, "token_quota_manager", _DenyManager())

    assert await agent._check_token_quota("standard", "conv-quota") == ""


@pytest.mark.asyncio
async def test_refresh_polymorphic_pending_state_sets_pending_selection(monkeypatch):
    conv_id = "conv-poly-pending"

    async def fake_uploaded_files(_conv_id):
        return [
            {
                "filename": "Tbl_Contact_Detail.xlsx",
                "polymorphic_schema": {
                    "is_polymorphic": True,
                    "pivot_column": "transaction_Id",
                    "pivot_profiles": {
                        "871": {"row_count": 2},
                        "872": {"row_count": 2},
                        "873": {"row_count": 2},
                    },
                },
            }
        ]

    monkeypatch.setattr(agent, "_get_uploaded_files_async", fake_uploaded_files)

    try:
        await agent._refresh_polymorphic_pending_state(
            conv_id,
            "Analisa novamente os dados deste dataset",
            "Indica qual transaction_Id queres analisar: 871, 872, 873.",
        )
        meta = await agent._get_conversation_meta(conv_id)
    finally:
        agent.conversation_meta.pop(conv_id, None)

    pending = meta.get(agent._PENDING_POLYMORPHIC_SELECTION_KEY)
    assert pending["pivot_column"] == "transaction_Id"
    assert pending["table_name"] == "Tbl_Contact_Detail.xlsx"
    assert pending["available_values"] == ["871", "872", "873"]


@pytest.mark.asyncio
async def test_prepare_polymorphic_followup_question_rewrites_short_reply():
    conv_id = "conv-poly-followup"
    await agent._set_conversation_meta(
        conv_id,
        {
            agent._PENDING_POLYMORPHIC_SELECTION_KEY: {
                "table_name": "Tbl_Contact_Detail.xlsx",
                "pivot_column": "transaction_Id",
                "available_values": ["871", "872", "873"],
                "original_question": "Analisa novamente os dados do dataset",
            }
        },
    )

    try:
        rewritten, clarification = await agent._prepare_polymorphic_followup_question(conv_id, "871")
        meta = await agent._get_conversation_meta(conv_id)
    finally:
        agent.conversation_meta.pop(conv_id, None)

    assert clarification == ""
    assert "transaction_Id=871" in rewritten
    assert meta.get(agent._PENDING_POLYMORPHIC_SELECTION_KEY) is None


@pytest.mark.asyncio
async def test_run_forced_workitem_filter_followup_refreshes_created_by_query(monkeypatch):
    captured = {}

    async def fake_execute_tool_calls(tool_calls, conv_id, user_sub=""):
        captured["tool_calls"] = tool_calls
        captured["conv_id"] = conv_id
        captured["user_sub"] = user_sub
        return ["query_workitems"], [
            {
                "tool": "query_workitems",
                "arguments": tool_calls[0].arguments,
                "result_summary": {"total_count": 6, "items_returned": 6, "has_error": False},
            }
        ]

    monkeypatch.setattr(agent, "_execute_tool_calls", fake_execute_tool_calls)

    conv_id = "conv-workitem-followup"
    agent.conversations[conv_id] = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-prev",
                    "type": "function",
                    "function": {
                        "name": "query_workitems",
                        "arguments": json.dumps(
                            {
                                "wiql_where": "[System.WorkItemType] = 'User Story' AND [System.CreatedDate] >= '2026-03-09' AND [System.CreatedBy] CONTAINS 'John Carter'",
                                "fields": ["System.Id", "System.Title", "System.CreatedBy"],
                                "top": 200,
                            }
                        ),
                    },
                }
            ],
        }
    ]
    try:
        tools_used, tool_details = await agent._run_forced_workitem_filter_followup(
            "Só as do Jane Doe e para john.smith@example.com",
            conv_id,
            user_sub="user-123",
        )
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == ["query_workitems"]
    assert tool_details[0]["arguments"]["top"] == 200
    updated_where = tool_details[0]["arguments"]["wiql_where"]
    assert "Jane Doe" in updated_where
    assert "John Carter" not in updated_where
    assert "[System.CreatedBy] CONTAINS 'Jane Doe'" in updated_where
    assert captured["conv_id"] == conv_id
    assert captured["user_sub"] == "user-123"


@pytest.mark.asyncio
async def test_run_forced_workitem_filter_followup_skips_without_prior_query(monkeypatch):
    async def fake_execute_tool_calls(*_args, **_kwargs):
        raise AssertionError("Should not execute tool calls without prior query context")

    monkeypatch.setattr(agent, "_execute_tool_calls", fake_execute_tool_calls)

    conv_id = "conv-workitem-no-context"
    agent.conversations[conv_id] = [{"role": "user", "content": "olá"}]
    try:
        tools_used, tool_details = await agent._run_forced_workitem_filter_followup(
            "Só as do Jane Doe",
            conv_id,
            user_sub="user-123",
        )
    finally:
        agent.conversations.pop(conv_id, None)

    assert tools_used == []
    assert tool_details == []


@pytest.mark.asyncio
async def test_prepare_polymorphic_followup_question_reprompts_invalid_short_reply():
    conv_id = "conv-poly-invalid"
    await agent._set_conversation_meta(
        conv_id,
        {
            agent._PENDING_POLYMORPHIC_SELECTION_KEY: {
                "table_name": "Tbl_Contact_Detail.xlsx",
                "pivot_column": "transaction_Id",
                "available_values": ["871", "872"],
                "original_question": "Analisa novamente os dados do dataset",
            }
        },
    )

    try:
        rewritten, clarification = await agent._prepare_polymorphic_followup_question(conv_id, "999")
    finally:
        agent.conversation_meta.pop(conv_id, None)

    assert rewritten == ""
    assert "transaction_Id" in clarification
    assert "871" in clarification
