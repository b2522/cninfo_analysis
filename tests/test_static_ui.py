import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticUiTests(unittest.TestCase):
    def test_uses_a_bright_table_with_separate_collection_and_analysis_actions(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "src" / "cninfo_miner" / "static" / "styles.css").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="collect"', html)
        self.assertIn('抓取公告', html)
        self.assertIn('id="analyze"', html)
        self.assertIn('分析公告', html)
        self.assertNotIn('分析未分析公告', html)
        self.assertIn('"正在分析…":"分析公告"', javascript)
        self.assertNotIn('分析未分析公告', javascript)
        self.assertIn('<table', html)
        self.assertIn('时间', html)
        self.assertIn('代码', html)
        self.assertIn('简称', html)
        self.assertIn('公告标题', html)
        self.assertIn('分析结果', html)
        self.assertIn('--canvas:#f7f4ee', css)
        self.assertNotIn('data-view="neutral"', html)

    def test_places_opportunity_and_risk_options_in_the_analysis_result_header(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="resultFilter"', html)
        self.assertIn('value="opportunity">机会', html)
        self.assertIn('value="risk">风险', html)

    def test_offers_all_captured_view_and_renders_new_items_as_pending_screening(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('value="collected">全部抓取（含已排除）', html)
        self.assertIn('status==="new"', javascript)
        self.assertIn('待筛选', javascript)

    def test_uses_the_announcement_title_as_the_pdf_link(self) -> None:
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('link.textContent=row.title||"—"', javascript)
        self.assertNotIn('link.textContent="原文 PDF"', javascript)

    def test_uses_a_compact_top_area_and_keeps_table_header_sticky(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "src" / "cninfo_miner" / "static" / "styles.css").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("先收录、自动去重；再只对高相关候选公告进行原文研判。", html)
        self.assertNotIn("公告掘金", html)
        self.assertNotIn("CNINFO EVENT RADAR", html)
        self.assertNotIn('class="hero"', html)
        self.assertIn('id="settings"', html)
        self.assertIn('.table-wrap{flex:1;min-height:0;max-height:none;overflow:auto}', css)
        self.assertIn("thead th{position:sticky", css)
        self.assertIn('out=$("#results");out.replaceChildren', javascript)
        self.assertNotIn('document.querySelector(\"thead\")', javascript)

    def test_uses_fixed_column_widths_when_result_filter_changes(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "src" / "cninfo_miner" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('<colgroup>', html)
        self.assertEqual(html.count('<col class="'), 5)
        self.assertIn('table-layout:fixed', css)
        self.assertIn('.column-time{width:', css)
        self.assertIn('.column-result{width:', css)

    def test_places_collection_date_calendars_in_the_compact_toolbar_and_settings_on_the_right(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "src" / "cninfo_miner" / "static" / "styles.css").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('class="collection-tools"', html)
        self.assertIn('id="startDate" type="date"', html)
        self.assertIn('id="endDate" type="date"', html)
        self.assertGreater(html.index('id="settings"'), html.index('class="collection-tools"'))
        self.assertIn('.settings-button{margin-left:auto', css)
        self.assertIn('flex-wrap:nowrap', css)
        self.assertIn('overflow-x:auto', css)
        self.assertIn('"/api/collections/default-range"', javascript)
        self.assertIn('"/api/settings/llm"', javascript)
        self.assertIn('syncStoredSettings', javascript)

    def test_enlarges_the_table_viewport_and_centers_larger_headers_with_consistent_main_type(self) -> None:
        css = (ROOT / "src" / "cninfo_miner" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('.table-wrap{flex:1;min-height:0;max-height:none;overflow:auto}', css)
        self.assertIn('th{padding:13px 16px;text-align:center', css)
        self.assertIn('font-size:14px;font-weight:800;letter-spacing:.06em', css)
        self.assertIn('justify-content:center', css)
        self.assertIn('.date-tools input{height:31px', css)
        self.assertIn('font-size:14px}.button{min-height:37px', css)
        self.assertIn('html,body{height:100%;overflow:hidden}', css)
        self.assertIn('.app-shell{width:min(1240px,calc(100% - 48px));height:100%;display:flex;flex-direction:column', css)
        self.assertIn('.table-panel{display:flex;flex:1;min-height:0;flex-direction:column;overflow:hidden', css)
        self.assertIn('.result-count{color:var(--muted);font-size:14px}', css)

    def test_time_header_defaults_to_all_dates_and_filters_only_displayed_rows(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('value="all" selected>所有日期', html)
        self.assertIn('value="tomorrow">明天', html)
        self.assertIn('value="today">今天', html)
        self.assertIn('value="2">近 2 天', html)
        self.assertIn('value="3">近 3 天', html)
        self.assertIn('rows.filter(matchesTimeRange)', javascript)
        self.assertIn('$("#timeRange").addEventListener("change",refreshResults)', javascript)
        self.assertIn('const payload=startDate?{start_date:startDate,end_date:endDate}:{}', javascript)
        self.assertIn('function iso(date)', javascript)

    def test_restores_all_active_tasks_after_a_page_reload(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="taskCard"', html)
        self.assertIn('"/api/tasks/active"', javascript)
        self.assertIn("loadActiveTasks", javascript)
        self.assertIn("activeTasks", javascript)

    def test_uses_new_endpoints_and_accessible_feedback_without_browser_alerts(self) -> None:
        html = (ROOT / "src" / "cninfo_miner" / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "src" / "cninfo_miner" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="toast"', html)
        self.assertIn('role="status"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('"/api/collections"', javascript)
        self.assertIn('"/api/analyses"', javascript)
        self.assertIn('resultFilter', javascript)
        self.assertIn('showToast', javascript)
        self.assertNotIn('alert(', javascript)


if __name__ == "__main__":
    unittest.main()
