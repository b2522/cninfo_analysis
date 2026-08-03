import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.repository import SQLiteRepository


class SQLiteRepositoryTests(unittest.TestCase):
    def test_persists_model_configuration_for_background_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "announcements.sqlite3"
            repository = SQLiteRepository(path)
            config = {"base_url": "https://example.test/v1", "api_key": "secret", "model": "test-model"}
            try:
                repository.save_llm_config(config)
            finally:
                repository.close()

            reopened = SQLiteRepository(path)
            try:
                self.assertEqual(reopened.get_llm_config(), config)
                reopened.clear_llm_config()
                self.assertIsNone(reopened.get_llm_config())
            finally:
                reopened.close()

    def test_requeues_historical_routine_release_and_repledge_as_dismissed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "announcements.sqlite3")
            try:
                repository.upsert_announcement({
                    "announcement_id": "pledge",
                    "stock_code": "301121",
                    "stock_name": "紫建电子",
                    "title": "关于控股股东、实际控制人部分股份解除质押及质押的公告",
                    "announcement_time": 100,
                    "pdf_url": "https://static.cninfo.com.cn/pledge.pdf",
                })
                repository.update_announcement(
                    "pledge",
                    analysis_status="confirmed",
                    labels=["大股东减持、质押和股权变动"],
                    summary="已确认",
                    evidence="原文证据",
                    metrics={"本次质押数量": "1,500,000"},
                )

                self.assertEqual(repository.requeue_classification_corrections(), 1)
                saved = repository.get_announcement("pledge")
                self.assertEqual(saved["analysis_status"], "dismissed")
                self.assertEqual(saved["labels"], [])
                self.assertEqual(saved["summary"], "")
                self.assertEqual(repository.requeue_classification_corrections(), 0)
            finally:
                repository.close()


    def test_requeues_old_confirmed_repurchase_plan_for_expanded_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "announcements.sqlite3")
            try:
                repository.upsert_announcement({
                    "announcement_id": "repurchase-plan",
                    "stock_code": "300095",
                    "stock_name": "华伍股份",
                    "title": "关于回购公司股份方案的公告",
                    "announcement_time": 100,
                    "pdf_url": "https://static.cninfo.com.cn/repurchase-plan.pdf",
                })
                repository.update_announcement(
                    "repurchase-plan",
                    analysis_status="confirmed",
                    labels=["回购、增持和股权激励"],
                    summary="计划回购金额：3,000万元至5,000万元。",
                )

                self.assertEqual(repository.requeue_classification_corrections(), 1)
                saved = repository.get_announcement("repurchase-plan")
                self.assertEqual(saved["analysis_status"], "candidate")
                self.assertEqual(saved["summary"], "")
            finally:
                repository.close()

    def test_requeues_old_confirmed_repurchase_progress_for_summary_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "announcements.sqlite3")
            try:
                repository.upsert_announcement({
                    "announcement_id": "repurchase-progress",
                    "stock_code": "300095",
                    "stock_name": "华伍股份",
                    "title": "关于回购股份进展情况的公告",
                    "announcement_time": 100,
                    "pdf_url": "https://static.cninfo.com.cn/repurchase-progress.pdf",
                })
                repository.update_announcement(
                    "repurchase-progress",
                    analysis_status="confirmed",
                    labels=["回购、增持和股权激励"],
                    summary="回购进展：截至2026年7月31日暂未实施回购，累计回购0股。",
                )

                self.assertEqual(repository.requeue_classification_corrections(), 1)
                saved = repository.get_announcement("repurchase-progress")
                self.assertEqual(saved["analysis_status"], "candidate")
                self.assertEqual(saved["summary"], "")
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
