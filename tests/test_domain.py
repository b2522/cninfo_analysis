import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.domain import OPPORTUNITY_CATEGORIES, RISK_CATEGORIES, SUPPORTED_CATEGORIES, result_views_for_labels


class ResultClassificationTests(unittest.TestCase):
    def test_uses_the_confirmed_opportunity_and_risk_categories(self) -> None:
        self.assertEqual(len(SUPPORTED_CATEGORIES), 9)
        self.assertIn("业绩增长", OPPORTUNITY_CATEGORIES)
        self.assertIn("业绩大降", RISK_CATEGORIES)
        self.assertNotIn("大额订单和订单兑现", SUPPORTED_CATEGORIES)
        self.assertNotIn("监管问询、审计意见、诉讼和担保", SUPPORTED_CATEGORIES)

    def test_maps_all_positive_categories_to_opportunity(self) -> None:
        labels = ("业绩增长", "回购、增持和股权激励", "终止减持、未减持")
        self.assertEqual(result_views_for_labels(labels), ("opportunity",))

    def test_maps_negative_performance_to_risk(self) -> None:
        self.assertEqual(result_views_for_labels(("业绩大降",)), ("risk",))


if __name__ == "__main__":
    unittest.main()
