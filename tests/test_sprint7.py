"""Sprint 7 tests — empty state and mobile/header polish."""

import os

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
APP_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "App.jsx")
EMPTY_STATE_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ChatEmptyState.jsx")
USER_MENU_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "UserMenu.jsx")
DIGEST_PANEL_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "DigestSidePanel.jsx")
COMPOSER_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "components", "ChatComposer.jsx")
CSS_PATH = os.path.join(PROJECT_ROOT, "frontend", "src", "styles", "index.css")
FRONTEND_INDEX_PATH = os.path.join(PROJECT_ROOT, "frontend", "index.html")
TOOLS_PATH = os.path.join(PROJECT_ROOT, "tools.py")
ROUTES_DIGEST_PATH = os.path.join(PROJECT_ROOT, "routes_digest.py")


def _read(path):
    with open(path) as f:
        return f.read()


class TestEmptyStateGreeting:
    def test_empty_state_uses_first_name_in_single_hero_sentence(self):
        content = _read(EMPTY_STATE_PATH)
        assert "firstName" in content
        assert 'Bom dia, {safeFirstName}. Como te posso ajudar hoje?' in content
        assert "app-empty-question" not in content

    def test_empty_state_css_uses_clamp_and_lighter_title_weight(self):
        css = _read(CSS_PATH)
        assert ".app-empty-title" in css
        assert "font-size: clamp(" in css
        assert "font-weight: 500;" in css
        assert "white-space: nowrap;" in css

    def test_empty_state_no_longer_shows_capability_grid(self):
        content = _read(EMPTY_STATE_PATH)
        assert "const CAPABILITIES" not in content
        assert "capability-grid" not in content
        assert "Manual de funcionalidades" not in content

    def test_app_passes_first_name_to_empty_state(self):
        content = _read(APP_PATH)
        assert "getFirstName" in content
        assert "firstName={firstName}" in content
        assert "app-empty-shell" in content
        assert "is-empty-state" in content


class TestHeaderManualAndPlaceholder:
    def test_manual_button_lives_in_header(self):
        content = _read(APP_PATH)
        assert "header-manual-btn" in content
        assert "BookIcon" in content

    def test_input_placeholder_is_neutral(self):
        content = _read(APP_PATH)
        assert 'Escreve a tua mensagem...' in content
        assert 'Faz uma pergunta sobre DevOps, KPIs, dados ou conhecimento interno...' not in content


class TestDigestInteraction:
    def test_digest_panel_has_backdrop_close(self):
        content = _read(DIGEST_PANEL_PATH)
        assert "digest-panel-backdrop" in content
        assert "onClose" in content

    def test_digest_toggle_is_icon_only_and_docks_to_panel(self):
        content = _read(DIGEST_PANEL_PATH)
        assert "digest-tab-label" not in content
        assert "aria-label={open ? 'Fechar resumo do dia' : 'Abrir resumo do dia'}" in content

        css = _read(CSS_PATH)
        assert ".digest-tab-toggle" in css
        assert "--digest-panel-width: min(540px, calc(100vw - 80px));" in css
        assert ".digest-tab-toggle.is-open" in css
        assert "right: calc(var(--digest-panel-width) + 24px);" in css
        assert "width: 46px;" in css
        assert "height: 46px;" in css
        assert "width: var(--digest-panel-width);" in css
        assert "top: 50%" in css
        assert "bottom: 96px" in css
        assert "right: 14px;" in css


class TestMobileHeaderAndComposer:
    def test_user_menu_uses_initials_on_mobile(self):
        content = _read(USER_MENU_PATH)
        assert "getUserInitials" in content
        assert "user-menu-initials" in content
        assert "Manual de funcionalidades" not in content

    def test_selector_has_mode_icon_variant(self):
        content = _read(APP_PATH)
        assert "selector-trigger-mode-icon" in content
        assert "selector-trigger-tier-icon" in content

    def test_composer_groups_upload_actions(self):
        content = _read(COMPOSER_PATH)
        assert "composer-upload-cluster" in content
        assert content.count("composer-upload-btn") >= 2

    def test_mobile_css_hides_header_text_and_shows_initials(self):
        css = _read(CSS_PATH)
        assert ".user-menu-initials" in css
        assert ".selector-trigger-mode-icon" in css
        assert ".header-action-text" in css


class TestBrandingAndNaming:
    def test_frontend_index_uses_official_mark_as_favicon(self):
        index_html = _read(FRONTEND_INDEX_PATH)
        assert 'rel="icon"' in index_html
        assert 'href="/static/favicon.svg"' in index_html

    def test_digest_uses_app_label_instead_of_technical_project_name(self):
        digest_routes = _read(ROUTES_DIGEST_PATH)
        assert 'DEVOPS_PROJECT_LABEL' in digest_routes
        assert '"project": DEVOPS_PROJECT_LABEL' in digest_routes

    def test_agent_prompt_uses_app_as_team_name(self):
        tools_content = _read(TOOLS_PATH)
        assert "equipa de Produto Digital" in tools_content
        assert 'usa sempre "Produto Digital"' in tools_content
