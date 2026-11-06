"""Sprint 5 tests — file preview, streaming timeline, Pro tier suggestion."""

import os

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


# =========================================================================
# S5-01: Streaming timeline visual
# =========================================================================

class TestStreamingTimeline:
    def test_timeline_phases_in_component(self):
        """StreamingActivityPanel should include timeline phases."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "StreamingActivityPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "TIMELINE_PHASES" in content
        assert "StreamingTimeline" in content
        assert "Análise" in content or "Analise" in content
        assert "Ferramentas" in content
        assert "Resposta" in content

    def test_timeline_has_phase_states(self):
        """Timeline should have active, completed, pending, error states."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "StreamingActivityPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "is-active" in content or "'active'" in content
        assert "is-completed" in content or "'completed'" in content
        assert "is-pending" in content or "'pending'" in content

    def test_timeline_css_exists(self):
        """CSS should include streaming timeline styles."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        with open(path) as f:
            content = f.read()
        assert ".stream-timeline" in content
        assert ".stream-timeline-step" in content
        assert ".stream-timeline-connector" in content
        assert "timelinePulse" in content

    def test_timeline_renders_inside_stream_panel(self):
        """StreamingTimeline should be rendered inside the stream-panel."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "StreamingActivityPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "<StreamingTimeline" in content
        assert "currentPhase={phase}" in content
        assert "events={rawEvents}" in content

    def test_timeline_error_uses_effective_idx(self):
        """Error phase should derive effectiveIdx from event history, not PHASE_ORDER."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "StreamingActivityPanel.jsx")
        with open(path) as f:
            content = f.read()
        # effectiveIdx must be computed from events when isError
        assert "effectiveIdx" in content
        # Must not use currentIdx = -1 for error state
        assert "PHASE_ORDER[lastPhase]" in content or "PHASE_ORDER[lastPhase] ??" in content
        # Error state should mark the failing phase as 'error', not 'pending'
        assert "idx === effectiveIdx ? 'error'" in content

    def test_streaming_panel_supports_blocked_phase(self):
        """StreamingActivityPanel should expose a blocked phase for explicit request outcomes."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "StreamingActivityPanel.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "blocked:" in content
        assert "Bloqueado" in content
        assert "currentPhase === 'blocked'" in content or "|| currentPhase === 'blocked'" in content

    def test_streaming_utils_use_request_outcome_on_done(self):
        """applyStreamingTraceEvent(done) should react to request_status/request_error_kind."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "utils", "streaming.js")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "request_status" in content
        assert "request_error_kind" in content
        assert "Pedido bloqueado" in content
        assert "Pedido com erro" in content

    def test_sync_fallback_forwards_request_outcome_to_stream_trace(self):
        """App sync fallback should forward request outcome into the done trace event."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert 'applyStreamingTraceEvent(prev, "done", {' in content
        assert "request_status: data.request_status || \"ok\"" in content
        assert "request_error_kind: data.request_error_kind || \"\"" in content

    def test_tool_execution_summary_uses_explicit_tool_outcome_contract(self):
        """ToolExecutionSummary should use result_status/error_kind, not only has_error."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolExecutionSummary.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "result_status" in content
        assert "error_kind" in content
        assert "Bloqueada" in content
        assert "Erro" in content

    def test_tool_badges_title_uses_explicit_tool_outcome_contract(self):
        """ToolBadges title should reflect blocked/error from explicit tool outcome fields."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolBadges.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "result_status" in content
        assert "bloqueada" in content
        assert "erro" in content

    def test_tool_results_helpers_skip_explicit_tool_failures(self):
        """toolResults helpers should ignore blocked/tool_error results explicitly."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "utils", "toolResults.js")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "_toolResultIsExplicitFailure" in content
        assert "['blocked', 'tool_error', 'exception']" in content
        assert "if (_toolResultIsExplicitFailure(toolResult)) return false;" in content
        assert "if (_toolResultIsExplicitFailure(tr)) continue;" in content

    def test_app_builds_frontend_tool_results_with_explicit_outcome_fields(self):
        """App should persist result_status/error_kind/error_detail into tool_results entries."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "function buildFrontendToolResults" in content
        assert "result_status: summary.result_status || \"ok\"" in content
        assert "error_kind: summary.error_kind || \"\"" in content
        assert "error_detail: summary.error_detail || \"\"" in content

    def test_frontend_runtime_context_updates_from_active_conversation(self):
        """App should keep a small runtime context on window for client-error correlation."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "function updateClientRuntimeContext" in content
        assert "window.__APP_RUNTIME_CONTEXT__" in content
        assert "conversation_id" in content
        assert "agent_mode" in content
        assert "request_id" in content

    def test_client_error_reporters_include_runtime_context(self):
        """Global frontend error reporters should include request/conversation runtime context."""
        main_path = os.path.join(PROJECT_ROOT, "frontend", "src", "main.jsx")
        with open(main_path, encoding="utf-8") as f:
            main_content = f.read()
        assert "readClientRuntimeContext" in main_content
        assert "request_id: runtimeCtx.request_id" in main_content
        assert "conversation_id: runtimeCtx.conversation_id" in main_content
        assert "agent_mode: runtimeCtx.agent_mode" in main_content

        boundary_path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ErrorBoundary.jsx")
        with open(boundary_path, encoding="utf-8") as f:
            boundary_content = f.read()
        assert "readClientRuntimeContext" in boundary_content
        assert "request_id: runtimeCtx.request_id" in boundary_content
        assert "conversation_id: runtimeCtx.conversation_id" in boundary_content
        assert "agent_mode: runtimeCtx.agent_mode" in boundary_content


# =========================================================================
# S5-02: Pro tier suggestion
# =========================================================================

class TestProTierSuggestion:
    def test_should_suggest_pro_function_exists(self):
        """App.jsx should have shouldSuggestProTier function."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "shouldSuggestProTier" in content
        assert "showProSuggestion" in content

    def test_pro_suggestion_in_composer(self):
        """ChatComposer should render Pro suggestion banner."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ChatComposer.jsx")
        with open(path) as f:
            content = f.read()
        assert "showProSuggestion" in content
        assert "onSwitchToPro" in content
        assert "pro-suggestion" in content
        assert "Usar Pro" in content

    def test_pro_suggestion_css_exists(self):
        """CSS should include Pro suggestion styles."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        with open(path) as f:
            content = f.read()
        assert ".pro-suggestion-btn" in content
        assert ".app-banner.pro-suggestion" in content

    def test_pro_suggestion_has_complexity_heuristics(self):
        """shouldSuggestProTier should check for complex indicators."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        # Should have scoring system and complexity keywords
        assert "score" in content
        assert "complexIndicators" in content or "complexIndicator" in content


# =========================================================================
# S5-03: File preview before upload
# =========================================================================

class TestFilePreviewPanel:
    def test_component_exists(self):
        """FilePreviewPanel.jsx should exist."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        assert os.path.isfile(path)

    def test_component_has_file_info(self):
        """Component should show filename, size, type badge."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "file-preview-name" in content
        assert "file-preview-meta" in content
        assert "file-preview-badge" in content
        assert "formatSize" in content

    def test_component_has_row_estimate(self):
        """Component should show row estimate for tabular files."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "rowEstimate" in content
        assert "linhas" in content

    def test_component_has_confirm_cancel(self):
        """Component should have confirm and cancel buttons."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "onConfirmUpload" in content
        assert "onCancelAll" in content
        assert "onRemoveFile" in content
        assert "file-preview-confirm" in content

    def test_component_has_thumbnail_support(self):
        """Component should support image thumbnails."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "thumbnailUrl" in content
        assert "file-preview-thumbnail" in content

    def test_app_has_pending_files_state(self):
        """App.jsx should have pendingFiles state."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "pendingFiles" in content
        assert "setPendingFiles" in content
        assert "handlePendingFilesConfirm" in content
        assert "handlePendingFileRemove" in content
        assert "handlePendingFilesCancel" in content

    def test_composer_renders_preview(self):
        """ChatComposer should render FilePreviewPanel."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ChatComposer.jsx")
        with open(path) as f:
            content = f.read()
        assert "FilePreviewPanel" in content
        assert "pendingFiles" in content
        assert "onConfirmPendingFiles" in content

    def test_css_exists(self):
        """CSS should include file preview panel styles."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        with open(path) as f:
            content = f.read()
        assert ".file-preview-panel" in content
        assert ".file-preview-item" in content
        assert ".file-preview-confirm" in content

    def test_file_select_splits_from_upload(self):
        """handleFileSelect should exist separately from _doFileUpload."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "handleFileSelect" in content
        assert "_doFileUpload" in content
        # Old handleFileUpload should no longer exist
        assert "async function handleFileUpload" not in content

    def test_file_limit_counts_pending_files(self):
        """currentCount in handleFileSelect must include pendingFiles.length."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        # The file limit calculation must account for pending preview files
        assert "pendingFiles.length" in content
        # Specifically in the currentCount line
        import re
        match = re.search(r'const currentCount\s*=.*pendingFiles\.length', content)
        assert match is not None, "currentCount must include pendingFiles.length"

    def test_ext_badges_coverage(self):
        """EXT_BADGES should cover common file types."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "FilePreviewPanel.jsx")
        with open(path) as f:
            content = f.read()
        for ext in ['.csv', '.xlsx', '.pdf', '.pptx', '.txt', '.png', '.jpg']:
            assert ext in content, f"Missing badge for {ext}"


# =========================================================================
# Tools Manual (full manual replacing FeatureGuide)
# =========================================================================

class TestToolsManual:
    def test_component_exists(self):
        """ToolsManual.jsx should exist."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolsManual.jsx")
        assert os.path.isfile(path)

    def test_catalog_data_file_exists(self):
        """toolsCatalog.js should exist as separate data source."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "data", "toolsCatalog.js")
        assert os.path.isfile(path)

    def test_component_imports_catalog(self):
        """ToolsManual should import from toolsCatalog.js, not hardcode data."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolsManual.jsx")
        with open(path) as f:
            content = f.read()
        assert "import TOOLS_CATALOG from" in content
        assert "toolsCatalog" in content

    def test_catalog_covers_all_categories(self):
        """toolsCatalog.js should have sections for all main categories."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "data", "toolsCatalog.js")
        with open(path) as f:
            content = f.read()
        expected_sections = [
            "User Stories & DevOps",
            "Análise de Dados",
            "Geração de Ficheiros",
            "Upload & Documentos",
            "Email",
            "Pesquisa e Conhecimento",
            "Design & Colaboração",
            "Produtividade & Aprendizagem",
        ]
        for section in expected_sections:
            assert section in content, f"Missing section: {section}"

    def test_catalog_has_all_major_tools(self):
        """toolsCatalog.js should document all major tool functions."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "data", "toolsCatalog.js")
        with open(path) as f:
            content = f.read()
        key_tools = [
            "Gerar User Stories",
            "Pesquisar Work Items",
            "KPIs e Métricas",
            "Analisar Tabela",
            "Executar Código Python",
            "Exportar Apresentação",
            "Exportar Spreadsheet",
            "Classificar Emails",
            "Pesquisar Figma",
            "Perfil de Escrita",
        ]
        for tool in key_tools:
            assert tool in content, f"Missing tool: {tool}"

    def test_has_try_prompts(self):
        """Each tool should have example prompts with try buttons."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolsManual.jsx")
        with open(path) as f:
            content = f.read()
        assert "onTryPrompt" in content
        assert "Experimentar" in content or "Experimenta" in content
        assert "tools-manual-prompt-btn" in content

    def test_modal_pattern(self):
        """ToolsManual should follow overlay/modal pattern like FeedbackDashboard."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ToolsManual.jsx")
        with open(path) as f:
            content = f.read()
        assert "tools-manual-overlay" in content
        assert "tools-manual-modal" in content
        assert "onClose" in content

    def test_app_integration(self):
        """App.jsx should import and render ToolsManual."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "ToolsManual" in content
        assert "showToolsManual" in content
        assert "setShowToolsManual" in content

    def test_header_entry(self):
        """Tools manual should be accessible from the header actions."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "header-manual-btn" in content
        assert "Manual de funcionalidades" in content

    def test_css_exists(self):
        """CSS should include tools manual styles."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        with open(path) as f:
            content = f.read()
        assert ".tools-manual-overlay" in content
        assert ".tools-manual-modal" in content
        assert ".tools-manual-tool" in content
        assert ".tools-manual-prompt-btn" in content


# =========================================================================
# Digest Side Panel
# =========================================================================

class TestDigestSidePanel:
    def test_component_exists(self):
        """DigestSidePanel.jsx should exist."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "DigestSidePanel.jsx")
        assert os.path.isfile(path)

    def test_has_toggle_and_panel(self):
        """DigestSidePanel should have toggle button and sliding panel."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "DigestSidePanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "digest-tab-toggle" in content
        assert "digest-side-panel" in content
        assert "onToggle" in content
        assert "onClose" in content
        assert "digest-panel-backdrop" in content
        assert "is-open" in content

    def test_renders_digest_card(self):
        """DigestSidePanel should render DigestCard inside."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "DigestSidePanel.jsx")
        with open(path) as f:
            content = f.read()
        assert "DigestCard" in content
        assert "import DigestCard" in content

    def test_app_integration(self):
        """App.jsx should render DigestSidePanel with state."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "DigestSidePanel" in content
        assert "digestPanelOpen" in content
        assert "setDigestPanelOpen" in content

    def test_digest_not_in_empty_state(self):
        """ChatEmptyState should NOT render DigestCard directly anymore."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ChatEmptyState.jsx")
        with open(path) as f:
            content = f.read()
        assert "DigestCard" not in content
        assert "digestLoading" not in content

    def test_tab_always_visible(self):
        """Digest tab should always render (no early return hiding it)."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "DigestSidePanel.jsx")
        with open(path) as f:
            content = f.read()
        # Should NOT have conditional that hides the whole component
        assert "if (!hasData && !open) return null" not in content

    def test_css_exists(self):
        """CSS should include digest side panel styles."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
        with open(path) as f:
            content = f.read()
        assert ".digest-side-panel" in content
        assert ".digest-tab-toggle" in content


# =========================================================================
# Suggestion Stability
# =========================================================================

class TestSuggestionStability:
    def test_suggestions_use_ref_for_stability(self):
        """Suggestions should be memoized via useRef to avoid constant recalculation."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        assert "suggestionsRef" in content
        assert "suggestionsRef.current.seed" in content
        assert "suggestionsRef.current.result" in content

    def test_suggestions_recalculate_on_context_changes(self):
        """Suggestions should recalculate on seed, mode, conversation, and file changes."""
        path = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
        with open(path) as f:
            content = f.read()
        # Should check seed, mode, conversation index, and file count
        assert "suggestionsRef.current.seed !== suggestionSeed" in content
        assert "suggestionsRef.current.mode !== agentMode" in content
        assert "suggestionsRef.current.convIdx !== activeIdx" in content
        assert "suggestionsRef.current.files !== fileCount" in content
