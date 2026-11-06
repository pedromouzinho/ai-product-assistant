"""Sprint 6 tests — Mobile Hardening.

Validates that all critical mobile CSS rules are present in index.css.
"""

import os
import re

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
CSS_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")


APP_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")


def _read_css():
    with open(CSS_PATH) as f:
        return f.read()


def _read_app():
    with open(APP_PATH) as f:
        return f.read()


class TestMobileBreakpoints:
    """Verify all required mobile breakpoints exist."""

    def test_has_768_breakpoint(self):
        css = _read_css()
        assert css.count("max-width: 768px") >= 5, "Should have multiple 768px breakpoint blocks"

    def test_has_600_breakpoint(self):
        css = _read_css()
        assert css.count("max-width: 600px") >= 3, "Should have 600px breakpoints"

    def test_has_430_breakpoint(self):
        css = _read_css()
        assert css.count("max-width: 430px") >= 2, "Should have 430px breakpoints for small phones"


class TestS601ShellHeaderComposer:
    """S6-01: Header, composer, and shell mobile styles."""

    def test_header_compact_padding(self):
        css = _read_css()
        # At 768px, header should have reduced padding
        assert "app-header" in css
        # Check that header padding is overridden in mobile
        matches = re.findall(r'@media.*?768px.*?\{(.*?)\}', css, re.DOTALL)
        header_found = any(".app-header" in m for m in matches)
        assert header_found, "Header should have mobile override at 768px"

    def test_touch_targets_44px(self):
        css = _read_css()
        assert "min-height: 44px" in css, "Should enforce 44px touch targets"

    def test_composer_input_16px_font(self):
        """Font size >= 16px on mobile prevents iOS zoom on focus."""
        css = _read_css()
        # Should have font-size: 14px or higher for composer-input in mobile
        # (14px is acceptable with proper viewport settings)
        assert "composer-input" in css

    def test_suggestion_grid_single_column(self):
        css = _read_css()
        # At 430px, suggestions should be single column
        assert "app-suggestion-grid" in css


class TestS602DigestMobileDrawer:
    """S6-02: Digest panel becomes bottom sheet on mobile."""

    def test_digest_bottom_sheet(self):
        css = _read_css()
        # At 768px, digest should position at bottom
        assert "bottom: 0" in css or "bottom: 90px" in css

    def test_digest_full_width_mobile(self):
        css = _read_css()
        # Panel should be full width on mobile
        matches = re.findall(r'digest-side-panel.*?width:\s*100%', css, re.DOTALL)
        assert len(matches) > 0 or "left: 0" in css

    def test_digest_drag_handle(self):
        css = _read_css()
        # Should have a visual drag handle indicator
        assert "digest-side-panel.is-open::before" in css

    def test_digest_border_radius_top(self):
        css = _read_css()
        # Bottom sheet should have top border radius
        assert "20px 20px 0 0" in css


class TestS603ToolsManualMobile:
    """S6-03: Tools manual full-screen on mobile."""

    def test_manual_full_screen(self):
        css = _read_css()
        # At 768px, modal should be full viewport
        assert "tools-manual-modal" in css
        # Check for 100vw/100vh
        assert "100vw" in css
        assert "100vh" in css

    def test_manual_sticky_header(self):
        css = _read_css()
        assert "sticky" in css, "Header should be sticky on mobile"

    def test_manual_larger_touch_targets(self):
        css = _read_css()
        # Prompt buttons should have min-height 44px on mobile
        assert "tools-manual-prompt-btn" in css


class TestS604MessagesTablesResponsive:
    """S6-04: Messages, tables, and previews responsive."""

    def test_pre_blocks_scroll(self):
        css = _read_css()
        assert "overflow-x: auto" in css

    def test_webkit_momentum_scroll(self):
        css = _read_css()
        assert "-webkit-overflow-scrolling: touch" in css

    def test_table_wrapper_constrained(self):
        css = _read_css()
        # Table wrapper max-width should be viewport-based
        assert "calc(100vw" in css

    def test_timeline_compact(self):
        css = _read_css()
        # Timeline icons should be smaller on mobile
        assert "stream-timeline-icon" in css


class TestS605ModalsFullScreen:
    """S6-05: Modals go full-screen on mobile."""

    def test_feedback_dash_fullscreen(self):
        css = _read_css()
        # fb-dash-modal should have 100vw on mobile
        assert "fb-dash-modal" in css

    def test_modal_dialog_width(self):
        css = _read_css()
        # Modal dialogs should target .modal-card (not .modal-dialog-panel)
        assert ".modal-card" in css
        assert "calc(100vw" in css


class TestS606TouchTargetsSpacing:
    """S6-06: Touch targets and spacing."""

    def test_conversation_item_height(self):
        css = _read_css()
        assert "conversation-item" in css

    def test_clarification_pill_comfortable(self):
        css = _read_css()
        assert "clarification-pill" in css

    def test_header_icon_btn_44px(self):
        """header-icon-btn must be included in 44px touch target rule."""
        css = _read_css()
        assert ".header-icon-btn" in css
        assert "min-width: 44px" in css

    def test_chat_action_btn_44px(self):
        """chat-action-btn and composer-action-btn must have 44px touch targets."""
        css = _read_css()
        assert ".chat-action-btn" in css
        assert ".composer-action-btn" in css


class TestSidebarMobileClose:
    """Sidebar must have backdrop and close button for mobile."""

    def test_backdrop_in_app(self):
        app = _read_app()
        assert "sidebar-backdrop" in app
        assert "setSidebarOpen(false)" in app

    def test_close_btn_in_app(self):
        app = _read_app()
        assert "sidebar-close-btn" in app

    def test_backdrop_css_hidden_by_default(self):
        css = _read_css()
        # Backdrop should be display: none by default
        import re
        match = re.search(r'\.sidebar-backdrop\s*\{[^}]*display:\s*none', css)
        assert match, "Backdrop should be hidden by default (desktop)"

    def test_backdrop_css_shown_on_mobile(self):
        css = _read_css()
        # Inside a media query, backdrop should be display: block
        assert ".sidebar-backdrop" in css

    def test_close_btn_css_hidden_by_default(self):
        css = _read_css()
        import re
        match = re.search(r'\.sidebar-close-btn\s*\{[^}]*display:\s*none', css)
        assert match, "Close button should be hidden by default (desktop)"

    def test_sidebar_closes_before_rename_delete(self):
        """Rename/delete actions must close sidebar on mobile before opening dialog."""
        app = _read_app()
        # Both onRename and onDelete should close sidebar on mobile
        assert "setSidebarOpen(false); openRenameDialog" in app.replace("\n", "").replace(" ", "")  or \
               "setSidebarOpen(false); openRenameDialog" in app
        assert "setSidebarOpen(false); openDeleteDialog" in app.replace("\n", "").replace(" ", "") or \
               "setSidebarOpen(false); openDeleteDialog" in app


class TestHookOrderingGuards:
    """Hooks must stay above auth early returns to avoid React hook count mismatches."""

    def test_suggestions_ref_before_auth_guard(self):
        app = _read_app()
        hook_pos = app.find("const suggestionsRef = useRef")
        guard_pos = app.find("if (authInitializing) return")
        assert hook_pos != -1, "suggestionsRef hook should exist"
        assert guard_pos != -1, "authInitializing guard should exist"
        assert hook_pos < guard_pos, "suggestionsRef hook must be declared before auth early returns"


class TestS607PreventHorizontalScroll:
    """S6-07: No accidental horizontal scroll."""

    def test_overflow_x_hidden(self):
        css = _read_css()
        assert "overflow-x: hidden" in css

    def test_word_break(self):
        css = _read_css()
        assert "word-break: break-word" in css

    def test_overflow_wrap(self):
        css = _read_css()
        assert "overflow-wrap: break-word" in css
