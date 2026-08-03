import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.classification import (
    has_evidence,
    increase_holding_evidence,
    repurchase_evidence,
    sale_repurchase_evidence,
    screen_categories,
    termination_reduction_evidence,
)


class ClassificationTests(unittest.TestCase):
    def test_screens_periodic_reports_for_both_performance_directions(self) -> None:
        categories = screen_categories("2026年半年度报告")
        self.assertIn("业绩增长", categories)
        self.assertIn("业绩大降", categories)

    def test_screens_explicit_positive_and_risk_titles(self) -> None:
        self.assertIn("并购重组和资产注入", screen_categories("关于筹划重大资产重组事项的提示性公告"))
        self.assertIn("大股东减持、质押和股权变动", screen_categories("控股股东股份质押及股份变动的公告"))
        self.assertIn("业绩大降", screen_categories("2026年半年度业绩预减公告"))
        categories = screen_categories("关于出售已回购股份的进展公告")
        self.assertIn("大股东减持、质押和股权变动", categories)
        self.assertNotIn("回购、增持和股权激励", categories)

    def test_screens_ambiguous_topic_and_event_title(self) -> None:
        categories = screen_categories("关于生产基地项目进展的提示性公告")
        self.assertIn("产能投产和重大项目", categories)

    def test_excludes_large_order_and_regulatory_only_titles(self) -> None:
        self.assertEqual(screen_categories("关于签订重大销售合同的公告"), ())
        self.assertEqual(screen_categories("关于收到监管问询函的公告"), ())

    def test_excludes_non_investable_grant_lists_and_land_progress(self) -> None:
        self.assertNotIn(
            "回购、增持和股权激励",
            screen_categories("2025年限制性股票激励计划预留授予激励对象名单（预留授予日）"),
        )
        self.assertNotIn(
            "产能投产和重大项目",
            screen_categories("关于购买土地使用权并投资建设项目的进展公告"),
        )
        self.assertIn("回购、增持和股权激励", screen_categories("关于限制性股票激励计划授予完成的公告"))
        self.assertIn("产能投产和重大项目", screen_categories("关于生产基地项目投产的公告"))

    def test_excludes_routine_release_and_repledge_but_preserves_escalating_pledge_risk(self) -> None:
        self.assertNotIn(
            "大股东减持、质押和股权变动",
            screen_categories("关于控股股东、实际控制人部分股份解除质押及质押的公告"),
        )
        self.assertIn(
            "大股东减持、质押和股权变动",
            screen_categories("关于控股股东股份解除质押及质押、可能导致控制权变更的提示性公告"),
        )

    def test_extracts_complete_repurchase_progress_and_increase_holding_facts(self) -> None:
        progress = repurchase_evidence(
            "关于回购股份进展情况的公告",
            "公司回购股份资金总额不低于人民币3,000万元（含）且不超过人民币5,000万元（含），回购价格不超过人民币12.34元/股。"
            "截至2026年7月31日，公司累计回购公司股份1,200,000股，占公司当前总股本的0.80%，累计回购总金额为人民币13,050,000元，成交价格区间为10.20元/股至11.50元/股。",
        )
        self.assertEqual(
            progress["summary"],
            "回购计划：资金总额3,000万元至5,000万元，回购价格上限12.34元/股；"
            "回购进展：截至2026年7月31日，累计回购1,200,000股，占总股本0.80%，累计回购总金额13,050,000元，最高成交价11.50元/股，最低成交价10.20元/股。",
        )
        self.assertEqual(progress["metrics"]["回购价格上限"], "12.34元/股")
        self.assertEqual(progress["metrics"]["累计回购总金额"], "13,050,000元")
        self.assertEqual(progress["metrics"]["最高成交价"], "11.50元/股")
        self.assertEqual(progress["metrics"]["最低成交价"], "10.20元/股")

        increase = increase_holding_evidence(
            "关于控股股东增持公司股份进展的公告",
            "截至2026年7月31日，控股股东累计增持公司股份2,000,000股，占公司总股本的1.25%，增持均价为8.60元/股。",
        )
        self.assertEqual(
            increase["summary"],
            "增持进展：累计增持2,000,000股，占总股本1.25%，成交均价8.60元/股。",
        )
        self.assertEqual(increase["metrics"]["累计增持比例"], "1.25%")

    def test_extracts_repurchase_progress_and_plan_evidence(self) -> None:
        progress = repurchase_evidence(
            "关于回购股份进展情况的公告",
            "截至2026年7月31日，公司累计回购公司股份0股，占公司当前总股本的0%。公司暂未实施本次股份回购。",
        )
        self.assertEqual(progress["label"], "回购、增持和股权激励")
        self.assertEqual(
            progress["summary"],
            "回购计划：资金总额本公告未披露，回购价格上限本公告未披露；"
            "回购进展：截至2026年7月31日，累计回购0股，占总股本0%，累计回购总金额本公告未披露，最高成交价本公告未披露，最低成交价本公告未披露。",
        )
        self.assertIn("累计回购公司股份0股", progress["evidence"])

        plan = repurchase_evidence(
            "关于回购公司股份方案的公告",
            "本次回购股份资金不低于3,000万元（含）且不超过5,000万元（含）。",
        )
        self.assertEqual(plan["summary"], "回购计划：资金总额3,000万元至5,000万元，回购价格上限本公告未披露；回购进展：本公告未披露。")
        self.assertEqual(plan["evidence"], "本次回购股份资金不低于3,000万元（含）且不超过5,000万元（含）。")

    def test_requires_pdf_evidence_to_confirm_sale_of_repurchase_shares_as_risk(self) -> None:
        title = "关于出售已回购股份的进展公告"
        result = sale_repurchase_evidence(title, "公司已完成出售已回购股份事项。")

        self.assertEqual(result["label"], "大股东减持、质押和股权变动")
        self.assertEqual(result["summary"], "出售已回购股份（相当于减持）。")
        self.assertEqual(result["evidence"], "公司已完成出售已回购股份事项。")
        self.assertIsNone(sale_repurchase_evidence(title, "公司经营情况正常。"))

    def test_finds_pdf_evidence_for_early_termination_of_a_reduction_plan(self) -> None:
        title = "关于5%以上股东提前终止减持计划暨减持股份结果的公告"

        self.assertEqual(
            termination_reduction_evidence(title, "股东决定提前终止减持计划。"),
            "提前终止减持计划",
        )
        self.assertEqual(termination_reduction_evidence(title, "股东计划减持股份。"), "")

    def test_requires_original_text_evidence_for_high_confidence(self) -> None:
        self.assertTrue(has_evidence("重大资产重组", "公司拟筹划重大资产重组事项。"))
        self.assertFalse(has_evidence("重大资产重组", "公司经营情况正常。"))


if __name__ == "__main__":
    unittest.main()
