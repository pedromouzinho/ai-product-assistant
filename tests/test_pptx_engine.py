# =============================================================================
# test_pptx_engine.py — Tests for the Acme branded PPTX engine
# =============================================================================

import io
import pytest
from pptx_engine import (
    generate_presentation,
    generate_presentation_from_outline,
    BRAND_ACCENT_HEX,
    SLIDE_WIDTH_EMU,
    SLIDE_HEIGHT_EMU,
)


class TestGeneratePresentation:
    """Tests for the main generate_presentation function."""

    def test_minimal_presentation(self):
        """Single content slide produces valid PPTX."""
        slides = [{"type": "content", "title": "Test", "bullets": ["A", "B"]}]
        buf = generate_presentation("Test Title", slides)
        assert isinstance(buf, io.BytesIO)
        content = buf.getvalue()
        assert len(content) > 1000  # not trivially empty
        # PPTX is a ZIP — check magic bytes
        assert content[:2] == b"PK"

    def test_slide_dimensions_widescreen(self):
        """Presentation uses 13.333x7.5 inch widescreen layout."""
        from pptx import Presentation as PptxRead
        slides = [{"type": "content", "title": "X", "bullets": ["Y"]}]
        buf = generate_presentation("T", slides)
        prs = PptxRead(buf)
        assert prs.slide_width == SLIDE_WIDTH_EMU
        assert prs.slide_height == SLIDE_HEIGHT_EMU

    def test_auto_title_and_closing(self):
        """Auto-adds title and closing slides."""
        from pptx import Presentation as PptxRead
        slides = [{"type": "content", "title": "Body", "bullets": ["X"]}]
        buf = generate_presentation("Title", slides)
        prs = PptxRead(buf)
        # 1 title (auto) + 1 content + 1 closing (auto) = 3
        assert len(prs.slides) == 3

    def test_no_duplicate_title_slide(self):
        """If slides already start with title, don't add another."""
        from pptx import Presentation as PptxRead
        slides = [
            {"type": "title", "title": "My Title", "subtitle": "Sub"},
            {"type": "content", "title": "Body", "bullets": ["X"]},
        ]
        buf = generate_presentation("My Title", slides)
        prs = PptxRead(buf)
        # 1 title (from specs) + 1 content + 1 closing (auto) = 3
        assert len(prs.slides) == 3

    def test_no_duplicate_closing(self):
        """If slides already end with closing, don't add another."""
        from pptx import Presentation as PptxRead
        slides = [
            {"type": "content", "title": "Body", "bullets": ["X"]},
            {"type": "closing", "text": "Obrigado"},
        ]
        buf = generate_presentation("T", slides)
        prs = PptxRead(buf)
        # 1 title (auto) + 1 content + 1 closing (from specs) = 3
        assert len(prs.slides) == 3

    def test_all_slide_types(self):
        """All slide types generate without error."""
        slides = [
            {"type": "title", "title": "Capa", "subtitle": "Subtítulo"},
            {"type": "section", "title": "Secção 1"},
            {"type": "content", "title": "Conteúdo", "bullets": ["A", "B", "- Sub-bullet"]},
            {"type": "two_column", "title": "Duas Colunas", "left": ["L1", "L2"], "right": ["R1", "R2"]},
            {"type": "kpi", "title": "KPIs", "kpis": [
                {"value": "125", "label": "Total"},
                {"value": "98%", "label": "Cobertura"},
            ]},
            {"type": "table", "title": "Tabela", "headers": ["Col1", "Col2"], "rows": [["A", "B"], ["C", "D"]]},
            {"type": "agenda", "items": ["Item 1", "Item 2", "Item 3"]},
            {"type": "closing", "text": "Obrigado"},
        ]
        buf = generate_presentation("Full Test", slides, include_title_slide=False, include_closing_slide=False)
        from pptx import Presentation as PptxRead
        prs = PptxRead(buf)
        assert len(prs.slides) == 8

    def test_section_auto_numbering(self):
        """Section dividers auto-number correctly."""
        slides = [
            {"type": "section", "title": "Intro"},
            {"type": "content", "title": "Content", "bullets": ["X"]},
            {"type": "section", "title": "Results"},
            {"type": "content", "title": "More Content", "bullets": ["Y"]},
        ]
        buf = generate_presentation("T", slides)
        content = buf.getvalue()
        assert len(content) > 1000

    def test_empty_slides_returns_title_and_closing_only(self):
        """Empty slides list still creates title + closing."""
        from pptx import Presentation as PptxRead
        buf = generate_presentation("Empty", [])
        prs = PptxRead(buf)
        assert len(prs.slides) == 2  # title + closing

    def test_kpi_max_four(self):
        """KPI slide handles more than 4 kpis (only shows first 4)."""
        slides = [{"type": "kpi", "title": "Many KPIs", "kpis": [
            {"value": str(i), "label": f"KPI {i}"} for i in range(8)
        ]}]
        buf = generate_presentation("T", slides)
        assert len(buf.getvalue()) > 1000

    def test_table_row_cap(self):
        """Table slide caps at 15 rows."""
        slides = [{"type": "table", "title": "Big Table", "headers": ["A", "B"],
                   "rows": [[str(i), str(i*2)] for i in range(30)]}]
        buf = generate_presentation("T", slides)
        assert len(buf.getvalue()) > 1000

    def test_badge_text_custom(self):
        """Custom badge text is accepted."""
        slides = [{"type": "content", "title": "Test", "bullets": ["X"]}]
        buf = generate_presentation("T", slides, badge_text="CUSTOM TEAM")
        assert len(buf.getvalue()) > 1000

    def test_invalid_slide_type_fallback(self):
        """Unknown slide type falls back to content slide."""
        slides = [{"type": "unknown_type", "title": "Fallback", "bullets": ["X"]}]
        buf = generate_presentation("T", slides)
        assert len(buf.getvalue()) > 1000


class TestGenerateFromOutline:
    """Tests for the outline parser."""

    def test_simple_outline(self):
        outline = """# Secção 1
## Slide A
- Bullet 1
- Bullet 2
# Secção 2
## Slide B
- Bullet 3
"""
        buf = generate_presentation_from_outline("Outline Test", outline)
        from pptx import Presentation as PptxRead
        prs = PptxRead(buf)
        # title(auto) + section1 + slideA + section2 + slideB + closing(auto) = 6
        assert len(prs.slides) == 6

    def test_table_in_outline(self):
        outline = """## Dados
| Nome | Valor |
| --- | --- |
| Alpha | 100 |
| Beta | 200 |
"""
        buf = generate_presentation_from_outline("Table Test", outline)
        assert len(buf.getvalue()) > 1000

    def test_empty_outline(self):
        buf = generate_presentation_from_outline("Empty", "")
        from pptx import Presentation as PptxRead
        prs = PptxRead(buf)
        assert len(prs.slides) == 2  # title + closing only


class TestValidationLayer:
    """Tests for the smart validation and auto-correction layer."""

    def test_split_overloaded_bullets(self):
        """Content slide with 15 bullets splits into 3 slides."""
        from pptx import Presentation as PptxRead
        slides = [{"type": "content", "title": "Big List",
                   "bullets": [f"Item {i}" for i in range(15)]}]
        buf = generate_presentation("T", slides,
                                    include_title_slide=False,
                                    include_closing_slide=False)
        prs = PptxRead(buf)
        # 15 bullets / 7 per slide = 3 slides (7+7+1)
        assert len(prs.slides) == 3

    def test_split_slide_adds_cont_suffix(self):
        """Split slides get '(cont.)' in title."""
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "content", "title": "Results",
                   "bullets": [f"B{i}" for i in range(10)]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 2
        assert fixed[0]["title"] == "Results"
        assert fixed[1]["title"] == "Results (cont.)"

    def test_truncate_long_bullet(self):
        """Bullets >150 chars are truncated with ellipsis."""
        from pptx_engine import _validate_and_fix_slides
        long_bullet = "A" * 200
        slides = [{"type": "content", "title": "T", "bullets": [long_bullet]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["bullets"][0]) == 150
        assert fixed[0]["bullets"][0].endswith("…")

    def test_truncate_long_title(self):
        """Titles >80 chars are truncated."""
        from pptx_engine import _validate_and_fix_slides
        long_title = "T" * 100
        slides = [{"type": "content", "title": long_title, "bullets": ["X"]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["title"]) == 80
        assert fixed[0]["title"].endswith("…")

    def test_split_kpis_over_four(self):
        """KPI slide with 6 KPIs splits into 2 slides."""
        from pptx_engine import _validate_and_fix_slides
        kpis = [{"value": str(i), "label": f"KPI{i}"} for i in range(6)]
        slides = [{"type": "kpi", "title": "Metrics", "kpis": kpis}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 2
        assert len(fixed[0]["kpis"]) == 4
        assert len(fixed[1]["kpis"]) == 2

    def test_split_large_table(self):
        """Table with 25 rows splits into multiple slides."""
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "table", "title": "Data",
                   "headers": ["A", "B"],
                   "rows": [[str(i), str(i*2)] for i in range(25)]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 3  # 12 + 12 + 1

    def test_trim_excess_columns(self):
        """Table with >8 columns is trimmed to 8."""
        from pptx_engine import _validate_and_fix_slides
        headers = [f"Col{i}" for i in range(12)]
        slides = [{"type": "table", "title": "Wide",
                   "headers": headers, "rows": [["x"] * 12]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["headers"]) == 8

    def test_remove_empty_content(self):
        """Content slides with no bullets are removed."""
        from pptx_engine import _validate_and_fix_slides
        slides = [
            {"type": "content", "title": "Empty", "bullets": []},
            {"type": "content", "title": "Full", "bullets": ["X"]},
        ]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 1
        assert fixed[0]["title"] == "Full"

    def test_remove_consecutive_sections(self):
        """Consecutive section dividers: only last is kept."""
        from pptx_engine import _validate_and_fix_slides
        slides = [
            {"type": "section", "title": "First"},
            {"type": "section", "title": "Second"},
            {"type": "content", "title": "Content", "bullets": ["X"]},
        ]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 2
        assert fixed[0]["title"] == "Second"

    def test_two_column_trimmed(self):
        """Two-column items trimmed to max 8."""
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "two_column", "title": "T",
                   "left": [f"L{i}" for i in range(15)],
                   "right": [f"R{i}" for i in range(15)]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["left"]) == 8
        assert len(fixed[0]["right"]) == 8

    def test_end_to_end_validation_generates_valid_pptx(self):
        """Full pipeline with overloaded slides still produces valid PPTX."""
        slides = [
            {"type": "content", "title": "A" * 100,
             "bullets": [f"Bullet {i}" for i in range(20)]},
            {"type": "kpi", "title": "KPIs",
             "kpis": [{"value": str(i), "label": f"K{i}"} for i in range(8)]},
            {"type": "table", "title": "Data",
             "headers": [f"C{i}" for i in range(10)],
             "rows": [[str(i)] * 10 for i in range(30)]},
        ]
        buf = generate_presentation("Stress Test", slides)
        content = buf.getvalue()
        assert content[:2] == b"PK"  # valid ZIP/PPTX
        assert len(content) > 5000


class TestOpusFallback:
    """Tests for the Opus planner fallback logic."""

    def test_fallback_from_content_creates_slides(self):
        """Fallback parser creates slides from simple text."""
        from pptx_engine import _fallback_slides_from_content
        content = """# Introdução
- Ponto 1
- Ponto 2
## Detalhes
- Sub-ponto A
- Sub-ponto B"""
        slides = _fallback_slides_from_content(content, "Test")
        assert len(slides) >= 2
        assert all(s.get("type") == "content" for s in slides)

    def test_fallback_from_empty_content(self):
        """Fallback with empty content still produces something."""
        from pptx_engine import _fallback_slides_from_content
        slides = _fallback_slides_from_content("", "Título")
        assert len(slides) >= 1

    def test_fallback_slides_generate_valid_pptx(self):
        """Fallback slides can be rendered into valid PPTX."""
        from pptx_engine import _fallback_slides_from_content, generate_presentation
        slides = _fallback_slides_from_content(
            "Dados importantes\n- KPI 1: 95%\n- KPI 2: 120\n- Análise completa",
            "Relatório"
        )
        buf = generate_presentation("Relatório", slides)
        assert buf.getvalue()[:2] == b"PK"


class TestToolIntegration:
    """Test tool_generate_presentation integration."""

    @pytest.mark.asyncio
    async def test_tool_generates_pptx(self):
        from tools_export import tool_generate_presentation
        result = await tool_generate_presentation(
            title="Integration Test",
            slides=[
                {"type": "content", "title": "Test", "bullets": ["A", "B"]},
            ],
        )
        assert result.get("presentation_generated") is True
        assert result.get("format") == "pptx"
        assert "_file_download" in result
        assert result["_file_download"]["format"] == "pptx"
        assert result["_file_download"]["size_bytes"] > 1000
        assert result.get("planning_model") == "structured_input"

    @pytest.mark.asyncio
    async def test_tool_rejects_empty_slides_and_content(self):
        from tools_export import tool_generate_presentation
        result = await tool_generate_presentation(title="Bad", slides=[], content="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_rejects_no_input(self):
        from tools_export import tool_generate_presentation
        result = await tool_generate_presentation(title="Bad", slides=None, content="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_rejects_too_many_slides(self):
        from tools_export import tool_generate_presentation
        result = await tool_generate_presentation(
            title="Too Many",
            slides=[{"type": "content", "title": f"S{i}", "bullets": ["X"]} for i in range(51)],
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_uses_content_fallback_even_when_slides_exist(self, monkeypatch):
        import pptx_engine
        from tools_export import tool_generate_presentation

        async def _boom(*args, **kwargs):
            raise RuntimeError("planner failed")

        monkeypatch.setattr(pptx_engine, "plan_slides_with_opus", _boom)
        content = (
            "Benchmark Neobank Business e comparação com o Acme\n"
            "Conta e Gestão Financeira\n"
            "o Conta empresarial multimoeda com suporte a mais de 35 moedas\n"
            "Situação Acme\n"
            "• Não existe gestão multimoeda\n"
            "Impacto para os clientes\n"
            "• Dificuldade em gerir operações internacionais\n"
            "Pagamentos e Recebimentos\n"
            "o Links de pagamento para recebimentos online\n"
            "Situação Acme\n"
            "• Tap to Pay não disponível\n"
            "Impacto para os clientes\n"
            "• Limitação na capacidade de receber pagamentos\n"
            "Cartões e Gestão de Despesas\n"
            "o Emissão de cartões virtuais\n"
            "Situação Acme\n"
            "• Não existem fluxos de aprovação\n"
            "Impacto para os clientes\n"
            "• Aumento do esforço administrativo\n"
            "2. Tabela comparativa: Neobank Business vs Nova App Acme\n"
        )
        result = await tool_generate_presentation(
            title="Benchmark Test",
            slides=[{"type": "content", "title": "Resumo curto", "bullets": ["A", "B"]}],
            content=content,
            context="Apresentação para comissão executiva",
        )
        assert result.get("presentation_generated") is True
        assert result["_file_download"]["size_bytes"] > 1000

    @pytest.mark.asyncio
    async def test_tool_can_use_vnext_presentation_lane(self, monkeypatch):
        import tools_export
        from tools_export import tool_generate_presentation

        async def _fake_vnext(**kwargs):
            return {
                "slides": [
                    {"type": "kpi", "title": "Sinais Macro", "kpis": [{"value": "36%", "label": "Readiness média"}], "notes": "Abrir com o sinal macro."},
                    {"type": "chart", "title": "Readiness por domínio", "chart_type": "bar", "categories": ["Pagamentos", "APIs"], "series": [{"name": "Acme", "values": [36, 22]}], "notes": "Comparar domínios."},
                    {"type": "comparison", "title": "Gap competitivo", "left_title": "Neobank", "left_items": ["Multimoeda"], "right_title": "Acme", "right_items": ["Cobertura limitada"], "notes": "Trade-off principal."},
                ],
                "note": "Slides planeados pelo lane novo grounded/model-first (attempts=1, sources=2).",
                "diagnostics": {
                    "status": "approved",
                    "attempts": 1,
                    "selected_source_count": 2,
                    "source_names": ["benchmark.pdf", "brief.txt"],
                    "review_codes": [],
                    "body_slide_count": 3,
                    "capability": "benchmark_executive",
                    "fallback_reason": "",
                },
            }

        monkeypatch.setattr(tools_export, "PPTX_VNEXT_ENABLED", True)
        monkeypatch.setattr(tools_export, "_plan_presentation_with_vnext", _fake_vnext)

        result = await tool_generate_presentation(
            title="Benchmark Test",
            content="Benchmark Neobank Business vs Acme",
            context="Apresentação para comissão executiva",
        )
        assert result.get("presentation_generated") is True
        assert result.get("planning_model") == "vnext:model_first"
        assert "lane novo" in result.get("planning_note", "")
        assert result["planning_diagnostics"]["vnext"]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_tool_records_fallback_reason_and_can_skip_legacy_planner(self, monkeypatch):
        import tools_export
        from tools_export import tool_generate_presentation

        async def _fake_vnext(**kwargs):
            return {
                "slides": None,
                "note": "",
                "diagnostics": {
                    "status": "critic_rejected",
                    "fallback_reason": "critic_rejected:text_only,missing_chart",
                    "attempts": 2,
                    "selected_source_count": 1,
                    "source_names": ["benchmark.pdf"],
                    "review_codes": ["text_only", "missing_chart"],
                    "capability": "benchmark_executive",
                },
            }

        monkeypatch.setattr(tools_export, "PPTX_VNEXT_ENABLED", True)
        monkeypatch.setattr(tools_export, "PPTX_LEGACY_PLANNER_ENABLED", False)
        monkeypatch.setattr(tools_export, "_plan_presentation_with_vnext", _fake_vnext)

        result = await tool_generate_presentation(
            title="Benchmark Test",
            content="Benchmark Neobank Business vs Acme",
            context="Apresentação para comissão executiva",
        )
        assert result.get("presentation_generated") is True
        assert result.get("planning_model") == "legacy_fallback"
        assert result.get("planning_fallback_reason") == "critic_rejected:text_only,missing_chart"
        assert result["planning_diagnostics"]["vnext"]["status"] == "critic_rejected"
        assert result["planning_diagnostics"]["legacy_planner"]["status"] == "disabled"
        assert result["planning_diagnostics"]["deterministic_fallback"]["status"] == "used"


class TestSprint7aNewSlideTypes:
    """Tests for Sprint 7a: chart, comparison, process, stat_chart, notes."""

    def _make(self, slides, include_title_slide=False, include_closing_slide=False, **kw):
        from pptx import Presentation as PptxRead
        buf = generate_presentation("S7a Test", slides,
                                    include_title_slide=include_title_slide,
                                    include_closing_slide=include_closing_slide, **kw)
        return PptxRead(buf)

    # -- Chart slides --

    def test_chart_column(self):
        """Column chart renders as native chart shape."""
        prs = self._make([{
            "type": "chart", "title": "Vendas", "chart_type": "column",
            "categories": ["Q1", "Q2", "Q3"],
            "series": [{"name": "Rev", "values": [10, 20, 30]}],
        }])
        assert len(prs.slides) == 1
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_chart_bar(self):
        prs = self._make([{
            "type": "chart", "title": "Bar", "chart_type": "bar",
            "categories": ["A", "B"],
            "series": [{"name": "S1", "values": [5, 10]}],
        }])
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_chart_line(self):
        prs = self._make([{
            "type": "chart", "title": "Trend", "chart_type": "line",
            "categories": ["Jan", "Feb", "Mar"],
            "series": [{"name": "Users", "values": [100, 150, 200]}],
        }])
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_chart_pie(self):
        """Pie chart renders without axis errors."""
        prs = self._make([{
            "type": "chart", "title": "Share", "chart_type": "pie",
            "categories": ["A", "B", "C"],
            "series": [{"name": "Pct", "values": [50, 30, 20]}],
        }])
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_chart_doughnut(self):
        """Doughnut chart renders without axis errors."""
        prs = self._make([{
            "type": "chart", "title": "Donut", "chart_type": "doughnut",
            "categories": ["X", "Y"],
            "series": [{"name": "V", "values": [60, 40]}],
        }])
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_chart_multi_series(self):
        """Chart with multiple series renders correctly."""
        prs = self._make([{
            "type": "chart", "title": "Multi", "chart_type": "column",
            "categories": ["Q1", "Q2"],
            "series": [
                {"name": "Rev", "values": [10, 20]},
                {"name": "Cost", "values": [5, 8]},
            ],
        }])
        chart = None
        for s in prs.slides[0].shapes:
            if s.has_chart:
                chart = s.chart
        assert chart is not None
        assert len(chart.plots[0].series) == 2

    def test_pie_multi_series_upgrades_to_column(self):
        """Pie with multiple series auto-upgrades to column (no data loss)."""
        prs = self._make([{
            "type": "chart", "title": "Pie Multi", "chart_type": "pie",
            "categories": ["A", "B", "C"],
            "series": [
                {"name": "S1", "values": [10, 20, 30]},
                {"name": "S2", "values": [5, 15, 25]},
            ],
        }])
        chart = None
        for s in prs.slides[0].shapes:
            if s.has_chart:
                chart = s.chart
        assert chart is not None
        # Should have 2 series (upgraded to column, not silently dropped)
        assert len(chart.plots[0].series) == 2

    def test_doughnut_multi_series_upgrades_to_column(self):
        """Doughnut with multiple series auto-upgrades to column."""
        prs = self._make([{
            "type": "chart", "title": "Donut Multi", "chart_type": "doughnut",
            "categories": ["X", "Y"],
            "series": [
                {"name": "A", "values": [60, 40]},
                {"name": "B", "values": [30, 70]},
            ],
        }])
        chart = None
        for s in prs.slides[0].shapes:
            if s.has_chart:
                chart = s.chart
        assert chart is not None
        assert len(chart.plots[0].series) == 2

    # -- Comparison slides --

    def test_comparison_slide(self):
        prs = self._make([{
            "type": "comparison", "title": "Antes vs Depois",
            "left_title": "Antes", "left_items": ["Manual", "Lento"],
            "right_title": "Depois", "right_items": ["Auto", "Rápido"],
        }])
        assert len(prs.slides) == 1

    # -- Process slides --

    def test_process_slide(self):
        prs = self._make([{
            "type": "process", "title": "Workflow",
            "steps": [
                {"label": "Análise", "description": "Req"},
                {"label": "Dev", "description": "Build"},
                {"label": "Deploy", "description": "Prod"},
            ],
        }])
        assert len(prs.slides) == 1

    def test_process_timeline_alias(self):
        """'timeline' alias maps to process builder."""
        prs = self._make([{
            "type": "timeline", "title": "Timeline",
            "steps": [{"label": "Step 1", "description": "Desc"}],
        }])
        assert len(prs.slides) == 1

    # -- Stat+chart slides --

    def test_stat_chart_slide(self):
        prs = self._make([{
            "type": "stat_chart", "title": "Headline",
            "stat_value": "99.5%", "stat_label": "SLA",
            "chart_type": "line",
            "categories": ["Jan", "Feb", "Mar"],
            "series": [{"name": "Uptime", "values": [99.2, 99.4, 99.5]}],
        }])
        assert len(prs.slides) == 1
        assert any(s.has_chart for s in prs.slides[0].shapes)

    def test_stat_chart_degraded_to_kpi(self):
        """stat_chart without chart data degrades to kpi."""
        prs = self._make([{
            "type": "stat_chart", "title": "No Chart",
            "stat_value": "42", "stat_label": "Count",
            "categories": [], "series": [],
        }])
        # Should render as KPI (no charts)
        assert len(prs.slides) == 1
        assert not any(s.has_chart for s in prs.slides[0].shapes)

    # -- Speaker notes --

    def test_notes_on_content_slide(self):
        prs = self._make([{
            "type": "content", "title": "T", "bullets": ["X"],
            "notes": "Speaker context here",
        }])
        slide = prs.slides[0]
        assert slide.has_notes_slide
        assert "Speaker context here" in slide.notes_slide.notes_text_frame.text

    def test_notes_on_chart_slide(self):
        prs = self._make([{
            "type": "chart", "title": "C", "chart_type": "bar",
            "categories": ["A"], "series": [{"name": "S", "values": [1]}],
            "notes": "Chart context",
        }])
        slide = prs.slides[0]
        assert slide.has_notes_slide
        assert "Chart context" in slide.notes_slide.notes_text_frame.text

    def test_notes_preserved_on_stat_chart_degradation(self):
        """Notes survive stat_chart -> kpi degradation."""
        prs = self._make([{
            "type": "stat_chart", "title": "D",
            "stat_value": "7", "stat_label": "Items",
            "categories": [], "series": [],
            "notes": "Important degraded note",
        }])
        slide = prs.slides[0]
        assert slide.has_notes_slide
        assert "Important degraded note" in slide.notes_slide.notes_text_frame.text

    def test_auto_title_closing_have_notes(self):
        """Auto-added title and closing slides get default notes."""
        prs = self._make(
            [{"type": "content", "title": "M", "bullets": ["X"]}],
            include_title_slide=True, include_closing_slide=True,
        )
        assert len(prs.slides) == 3
        # Title slide (auto) should have notes
        assert prs.slides[0].has_notes_slide
        assert len(prs.slides[0].notes_slide.notes_text_frame.text.strip()) > 0
        # Closing slide (auto) should have notes
        assert prs.slides[2].has_notes_slide
        assert len(prs.slides[2].notes_slide.notes_text_frame.text.strip()) > 0

    def test_auto_title_closing_have_notes_full(self):
        """Auto-added title and closing slides get default notes."""
        from pptx import Presentation as PptxRead
        buf = generate_presentation(
            "Notes Test",
            [{"type": "content", "title": "M", "bullets": ["X"], "notes": "Mid"}],
        )
        prs = PptxRead(buf)
        assert len(prs.slides) == 3
        for i, slide in enumerate(prs.slides):
            assert slide.has_notes_slide, f"Slide {i} missing notes_slide"
            text = slide.notes_slide.notes_text_frame.text.strip()
            assert len(text) > 0, f"Slide {i} has empty notes"

    # -- Validator for new types --

    def test_validator_trims_chart_categories(self):
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "chart", "title": "T",
                   "categories": [f"C{i}" for i in range(20)],
                   "series": [{"name": "S", "values": list(range(20))}]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["categories"]) == 12

    def test_validator_trims_process_steps(self):
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "process", "title": "T",
                   "steps": [{"label": f"S{i}", "description": ""} for i in range(10)]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["steps"]) == 5

    def test_validator_trims_comparison_items(self):
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "comparison", "title": "T",
                   "left_items": [f"L{i}" for i in range(15)],
                   "right_items": [f"R{i}" for i in range(15)]}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed[0]["left_items"]) == 8
        assert len(fixed[0]["right_items"]) == 8

    def test_validator_drops_empty_chart(self):
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "chart", "title": "T", "categories": [], "series": []}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 0

    def test_validator_drops_empty_process(self):
        from pptx_engine import _validate_and_fix_slides
        slides = [{"type": "process", "title": "T", "steps": []}]
        fixed = _validate_and_fix_slides(slides)
        assert len(fixed) == 0

    # -- All 12 types in one go --

    def test_all_twelve_types(self):
        """All 12 slide types generate without error."""
        slides = [
            {"type": "title", "title": "Capa", "subtitle": "Sub"},
            {"type": "section", "title": "Secção 1"},
            {"type": "content", "title": "Bullets", "bullets": ["A", "B"]},
            {"type": "two_column", "title": "2Col", "left": ["L"], "right": ["R"]},
            {"type": "kpi", "title": "KPIs", "kpis": [{"value": "99", "label": "Score"}]},
            {"type": "table", "title": "Tab", "headers": ["H"], "rows": [["R"]]},
            {"type": "agenda", "items": ["Item 1"]},
            {"type": "chart", "title": "Chart", "chart_type": "bar",
             "categories": ["A"], "series": [{"name": "S", "values": [1]}]},
            {"type": "comparison", "title": "Comp",
             "left_title": "A", "left_items": ["X"],
             "right_title": "B", "right_items": ["Y"]},
            {"type": "process", "title": "Proc",
             "steps": [{"label": "S1", "description": "D1"}]},
            {"type": "stat_chart", "title": "Stat", "stat_value": "42", "stat_label": "L",
             "chart_type": "pie", "categories": ["A", "B"],
             "series": [{"name": "V", "values": [60, 40]}]},
            {"type": "closing", "text": "Obrigado"},
        ]
        prs = self._make(slides)
        assert len(prs.slides) == 12


class TestSprint7cFixes:
    """Tests for Sprint 7c: no footer branding, sanitizer, improved fallback."""

    def _make(self, slides, **kw):
        buf = generate_presentation("Test", slides, **kw)
        from pptx import Presentation
        return Presentation(io.BytesIO(buf.read()))

    # -- Footer branding removal --

    def test_no_footer_branding_shapes(self):
        """Slides should have NO footer branding elements (line + label)."""
        prs = self._make([{"type": "content", "title": "Clean", "bullets": ["A", "B"]}])
        slide = prs.slides[0]  # auto-added title slide
        # No shape should be a thin bar at the very bottom
        for shape in slide.shapes:
            # Old footer was at y > 6400000 EMU (~7.0 inches)
            if hasattr(shape, 'top') and shape.top and shape.top > 6400000:
                # Should NOT have any branding shapes at bottom
                if hasattr(shape, 'text') and shape.text:
                    assert "Produto Digital" not in shape.text, "Footer branding text found"
                    assert "DIGITAL" not in shape.text, "Old branding text found"

    # -- Metadata sanitizer --

    def test_sanitize_strips_slide_metadata(self):
        """_sanitize_planner_content removes SLIDE N patterns."""
        from pptx_engine import _sanitize_planner_content
        dirty = (
            "SLIDE 1 — TITULO/CAPA\n"
            "Benchmark Neobank vs Acme\n"
            "SLIDE 5 — PAGAMENTOS\n"
            "Neobank tem mais funcionalidades\n"
            "--- Slide 3 ---\n"
            "Dados importantes aqui\n"
        )
        clean = _sanitize_planner_content(dirty)
        assert "SLIDE 1" not in clean
        assert "SLIDE 5" not in clean
        assert "--- Slide 3 ---" not in clean
        assert "Benchmark Neobank vs Acme" in clean
        assert "Neobank tem mais funcionalidades" in clean
        assert "Dados importantes aqui" in clean

    def test_sanitize_preserves_normal_content(self):
        """Sanitizer doesn't touch normal content."""
        from pptx_engine import _sanitize_planner_content
        normal = "Receita Q1: €1.2M\nCrescimento: +15%\nEquipa: 12 pessoas"
        assert _sanitize_planner_content(normal) == normal

    def test_sanitize_empty(self):
        from pptx_engine import _sanitize_planner_content
        assert _sanitize_planner_content("") == ""

    # -- Improved fallback --

    def test_fallback_max_6_bullets(self):
        """Fallback never produces more than 6 bullets per slide."""
        from pptx_engine import _fallback_slides_from_content
        content = "\n".join([f"Ponto {i+1} sobre o tema" for i in range(20)])
        slides = _fallback_slides_from_content(content, "Test")
        for s in slides:
            if s.get("type") == "content":
                assert len(s.get("bullets", [])) <= 6, \
                    f"Slide '{s.get('title')}' has {len(s.get('bullets', []))} bullets"

    def test_fallback_has_section_dividers(self):
        """Fallback adds section dividers for structured content."""
        from pptx_engine import _fallback_slides_from_content
        content = (
            "## Secção 1\n"
            "Ponto A\nPonto B\nPonto C\n"
            "## Secção 2\n"
            "Ponto D\nPonto E\nPonto F\n"
            "## Secção 3\n"
            "Ponto G\nPonto H\n"
        )
        slides = _fallback_slides_from_content(content, "Test")
        types = [s["type"] for s in slides]
        assert "section" in types, f"No section dividers in {types}"

    def test_fallback_unique_titles(self):
        """Fallback produces unique titles from headers."""
        from pptx_engine import _fallback_slides_from_content
        content = (
            "## Pagamentos\n"
            "Info sobre pagamentos\n"
            "## Segurança\n"
            "Info sobre segurança\n"
            "## Analytics\n"
            "Info sobre analytics\n"
        )
        slides = _fallback_slides_from_content(content, "Test")
        content_slides = [s for s in slides if s["type"] == "content"]
        titles = [s["title"] for s in content_slides]
        # Should have different titles, not all "(cont.)"
        unique_titles = set(titles)
        assert len(unique_titles) >= 3, f"Not enough unique titles: {titles}"

    def test_fallback_handles_markdown_tables_as_table_slides(self):
        """Fallback produces real table slides from markdown tables."""
        from pptx_engine import _fallback_slides_from_content
        content = (
            "## Comparação\n"
            "| Feature | Neobank | Acme |\n"
            "| --- | --- | --- |\n"
            "| Pagamentos | Sim | Não |\n"
            "| Analytics | Sim | Não |\n"
            "| Segurança | Sim | Sim |\n"
        )
        slides = _fallback_slides_from_content(content, "Test")
        table_slides = [s for s in slides if s["type"] == "table"]
        assert len(table_slides) >= 1, f"No table slides found. Types: {[s['type'] for s in slides]}"
        tbl = table_slides[0]
        assert tbl["headers"] == ["Feature", "Neobank", "Acme"]
        assert len(tbl["rows"]) == 3

    def test_fallback_generates_chart_from_numeric_table(self):
        """Fallback auto-generates a chart slide from tables with numeric data."""
        from pptx_engine import _fallback_slides_from_content
        content = (
            "## Resultados\n"
            "| Equipa | Q1 | Q2 | Q3 |\n"
            "| --- | --- | --- | --- |\n"
            "| Alpha | 100 | 150 | 200 |\n"
            "| Beta | 80 | 120 | 160 |\n"
            "| Gamma | 60 | 90 | 130 |\n"
        )
        slides = _fallback_slides_from_content(content, "Test")
        chart_slides = [s for s in slides if s["type"] == "chart"]
        assert len(chart_slides) >= 1, f"No chart slides found. Types: {[s['type'] for s in slides]}"
        chart = chart_slides[0]
        assert chart["categories"] == ["Alpha", "Beta", "Gamma"]
        assert len(chart["series"]) >= 1
        # Values should be numeric
        assert all(isinstance(v, float) for v in chart["series"][0]["values"])

    def test_fallback_truncates_long_bullets(self):
        """Fallback truncates bullets longer than 120 chars."""
        from pptx_engine import _fallback_slides_from_content
        long_text = "A" * 200
        content = f"## Test\n{long_text}"
        slides = _fallback_slides_from_content(content, "Test")
        content_slides = [s for s in slides if s["type"] == "content"]
        for s in content_slides:
            for b in s.get("bullets", []):
                assert len(b) <= 120, f"Bullet too long: {len(b)} chars"

    def test_fallback_adds_notes(self):
        """Fallback slides include speaker notes."""
        from pptx_engine import _fallback_slides_from_content
        content = "## Tema\nPonto 1\nPonto 2"
        slides = _fallback_slides_from_content(content, "Test")
        for s in slides:
            assert "notes" in s, f"Slide missing notes: {s}"

    # -- PT decimal number parsing --

    def test_parse_chart_number_european_decimal(self):
        """European comma decimal: 12,5 → 12.5 (not 125)."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("12,5") == 12.5
        assert _parse_chart_number("0,8") == 0.8

    def test_parse_chart_number_european_thousands(self):
        """European thousands dot + decimal comma: 1.234,56 → 1234.56."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("1.234,56") == 1234.56

    def test_parse_chart_number_english_thousands(self):
        """English thousands comma: 1,234 -> 1234.0, not 1.234."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("1,234") == 1234.0
        assert _parse_chart_number("1,234.56") == 1234.56

    def test_parse_chart_number_european_thousands_without_decimal(self):
        """European thousands dot without decimals: 1.234 -> 1234.0."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("1.234") == 1234.0

    def test_parse_chart_number_percent(self):
        """Percent symbol stripped: 85% → 85.0."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("85%") == 85.0
        assert _parse_chart_number("12,5%") == 12.5

    def test_parse_chart_number_currency(self):
        """Currency symbols stripped."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("€1200") == 1200.0
        assert _parse_chart_number("$99,50") == 99.5

    def test_parse_chart_number_text_returns_none(self):
        """Non-numeric text returns None."""
        from pptx_engine import _parse_chart_number
        assert _parse_chart_number("Sim") is None
        assert _parse_chart_number("N/A") is None
        assert _parse_chart_number("") is None

    def test_fallback_chart_european_decimals(self):
        """Chart from table with European decimals parses correctly."""
        from pptx_engine import _fallback_slides_from_content
        content = (
            "## Cobertura\n"
            "| Categoria | Percentagem |\n"
            "| --- | --- |\n"
            "| Pagamentos | 70,5% |\n"
            "| Segurança | 83,2% |\n"
            "| Analytics | 10,0% |\n"
        )
        slides = _fallback_slides_from_content(content, "Test")
        chart_slides = [s for s in slides if s["type"] == "chart"]
        assert len(chart_slides) >= 1
        values = chart_slides[0]["series"][0]["values"]
        assert values[0] == 70.5, f"Expected 70.5, got {values[0]}"
        assert values[1] == 83.2, f"Expected 83.2, got {values[1]}"
        assert values[2] == 10.0, f"Expected 10.0, got {values[2]}"


class TestExecutiveBenchmarkPipeline:
    SAMPLE_BENCHMARK = (
        "Benchmark Neobank Business e comparação com o Acme\n"
        "Conta e Gestão Financeira\n"
        "o Conta empresarial multimoeda com suporte a mais de 35 moedas\n"
        "o Gestão de saldos por moeda numa única conta\n"
        "o Visão consolidada de liquidez da empresa\n"
        "Situação Acme\n"
        "• Funcionalidades base disponíveis: histórico e exportação de movimentos (MVP 1)\n"
        "• Ausência de possibilidade de gestão de conta multimoeda\n"
        "• Não existem subcontas para segmentação de fundos\n"
        "Impacto para os clientes\n"
        "• Dificuldade em gerir operações internacionais de forma eficiente\n"
        "• Menor visibilidade consolidada da posição financeira\n"
        "Pagamentos e Recebimentos\n"
        "o Transferências SEPA e internacionais\n"
        "o Links de pagamento para recebimentos online\n"
        "o Tap to Pay em dispositivos móveis\n"
        "Situação Acme\n"
        "• Cobertura de pagamentos tradicionais\n"
        "• Transferências internacionais apenas previstas para fases posteriores\n"
        "• Tap to Pay não disponível\n"
        "Impacto para os clientes\n"
        "• Limitação na capacidade de receber pagamentos de forma simples e digital\n"
        "Cartões e Gestão de Despesas\n"
        "o Emissão de cartões físicos para colaboradores\n"
        "o Emissão de cartões virtuais\n"
        "o Fluxos de aprovação de despesas\n"
        "Situação Acme\n"
        "• Emissão de cartões físicos prevista (MVP 2)\n"
        "• Ausência de categorização automática de despesas\n"
        "• Não existem fluxos de aprovação\n"
        "Impacto para os clientes\n"
        "• Menor visibilidade e controlo sobre os gastos dos colaboradores\n"
        "2. Tabela comparativa: Neobank Business vs Nova App Acme\n"
    )

    def test_extract_benchmark_sections(self):
        from pptx_engine import _extract_benchmark_sections

        sections = _extract_benchmark_sections(self.SAMPLE_BENCHMARK)
        assert len(sections) == 3
        assert sections[0]["title"] == "Conta e Gestão Financeira"
        assert len(sections[0]["features"]) == 3
        assert len(sections[1]["status"]) == 3
        assert len(sections[2]["impact"]) == 1

    def test_build_benchmark_brief(self):
        from pptx_engine import _build_benchmark_brief

        brief = _build_benchmark_brief(
            self.SAMPLE_BENCHMARK,
            title="Benchmark Neobank vs Acme",
            context="Apresentação para comissão executiva",
        )
        assert brief is not None
        assert len(brief["domain_rows"]) == 3
        assert len(brief["summary_kpis"]) == 4
        assert len(brief["chart_categories"]) == 3
        assert "MODO EXECUTIVO BENCHMARK" in brief["briefing_text"]

    def test_extract_benchmark_api_summary(self):
        from pptx_engine import _extract_benchmark_api_summary

        content = (
            self.SAMPLE_BENCHMARK
            + "3. Tabela comparativa: Neobank API vs Acme API\n"
            + "Funcionalidade NEOBANK API ACME API\n"
            + "FX (câmbio de moeda) Ponto‑forte: obter taxa e trocar moeda com fees Não\n"
            + "Pagamentos ao Estado Não Sim: DUC e TSU\n"
        )
        summary = _extract_benchmark_api_summary(content)
        assert summary is not None
        assert len(summary["left_items"]) >= 3
        assert len(summary["right_items"]) >= 3
        assert len(summary["table_rows"]) >= 3

    def test_build_benchmark_planner_payload_is_compact_and_executive(self):
        from pptx_engine import (
            _build_benchmark_brief,
            _build_benchmark_planner_payload,
            _extract_benchmark_api_summary,
        )

        content = (
            self.SAMPLE_BENCHMARK
            + "3. Tabela comparativa: Neobank API vs Acme API\n"
            + "Funcionalidade NEOBANK API ACME API\n"
        )
        brief = _build_benchmark_brief(
            content,
            title="Benchmark Neobank vs Acme",
            context="Apresentação para comissão executiva",
        )
        payload = _build_benchmark_planner_payload(
            brief,
            _extract_benchmark_api_summary(content),
            title="Benchmark Neobank vs Acme",
            context="Comissão executiva",
        )
        assert "CONSTRAINTS VISUAIS OBRIGATÓRIOS" in payload
        assert "pelo menos 2 charts" in payload
        assert "APIs E INTEGRAÇÕES" in payload
        assert "DOMÍNIOS E GAPS:" in payload
        assert "OBJETIVO: gerar um deck executivo visual" in payload

    def test_review_injects_anchor_visual_slides(self):
        from pptx_engine import _build_benchmark_brief, _review_and_rebalance_slide_plan

        brief = _build_benchmark_brief(
            self.SAMPLE_BENCHMARK,
            title="Benchmark Neobank vs Acme",
            context="Apresentação para comissão executiva",
        )
        base_slides = [
            {"type": "content", "title": "Resumo", "bullets": ["A", "B", "C", "D", "E"], "notes": "n1"},
            {"type": "content", "title": "Pagamentos", "bullets": ["A", "B", "C", "D", "E"], "notes": "n2"},
            {"type": "content", "title": "Cartões", "bullets": ["A", "B", "C", "D", "E"], "notes": "n3"},
        ]
        reviewed = _review_and_rebalance_slide_plan(
            base_slides,
            title="Benchmark Neobank vs Acme",
            content=self.SAMPLE_BENCHMARK,
            context="Apresentação para comissão executiva",
            benchmark_brief=brief,
        )
        types = [slide["type"] for slide in reviewed]
        assert "agenda" in types
        assert any(t in ("kpi", "stat_chart") for t in types)
        assert "chart" in types
        assert "table" in types
        assert "comparison" in types
        # Dense third content slide should be upgraded to break monotony
        assert "two_column" in types

    def test_review_upgrades_generic_benchmark_content_slides(self):
        from pptx_engine import _build_benchmark_brief, _review_and_rebalance_slide_plan

        brief = _build_benchmark_brief(
            self.SAMPLE_BENCHMARK,
            title="Benchmark Neobank vs Acme",
            context="Apresentação para comissão executiva",
        )
        slides = [
            {"type": "title", "title": "Benchmark Neobank vs Acme", "subtitle": "X"},
            {"type": "content", "title": "Posicionamento Competitivo Neobank", "bullets": ["a", "b"]},
            {"type": "content", "title": "Principais Gaps Acme a Reforçar", "bullets": ["a", "b"]},
        ]
        reviewed = _review_and_rebalance_slide_plan(
            slides,
            title="Benchmark Neobank vs Acme",
            content=self.SAMPLE_BENCHMARK,
            context="Apresentação para comissão executiva",
            benchmark_brief=brief,
        )
        titles_to_type = {str(s.get("title", "")): s.get("type") for s in reviewed}
        assert titles_to_type.get("Posicionamento competitivo") == "comparison"
        assert titles_to_type.get("Top gaps com impacto cliente") == "table"

    def test_benchmark_specific_fallback_produces_executive_mix(self):
        from pptx_engine import _fallback_slides_from_content

        slides = _fallback_slides_from_content(
            self.SAMPLE_BENCHMARK + "3. Tabela comparativa: Neobank API vs Acme API\nFuncionalidade NEOBANK API ACME API\n",
            "Benchmark Neobank vs Acme",
        )
        types = [slide["type"] for slide in slides]
        assert types[:6] == ["agenda", "stat_chart", "chart", "table", "comparison", "chart"]
        assert "process" in types
        assert "comparison" in types
        content_titles = [slide["title"] for slide in slides if slide["type"] == "comparison"]
        assert "Conta e Gestão Financeira" in content_titles
        assert "Pagamentos e Recebimentos" in content_titles
