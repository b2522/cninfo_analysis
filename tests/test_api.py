import asyncio
import sys
import tempfile
import unittest
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient
from cninfo_miner.main import SHANGHAI, create_app, daily_collection_range, manual_collection_range, run_scheduled_cycle
from cninfo_miner.repository import MemoryRepository, SQLiteRepository


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = MemoryRepository()
        self.client = TestClient(create_app(run_tasks=False, repository=self.repository))

    def test_rejects_an_invalid_collection_date_range(self) -> None:
        response = self.client.post("/api/collections", json={"start_date": "2026-08-03", "end_date": "2026-08-02"})
        self.assertEqual(response.status_code, 422)

    def test_creates_separate_collection_and_analysis_tasks(self) -> None:
        collection = self.client.post("/api/collections", json={"start_date": "2026-08-02", "end_date": "2026-08-03"})
        analysis = self.client.post("/api/analyses", json={"llm": {"base_url": "", "api_key": "", "model": ""}})

        self.assertEqual(collection.status_code, 201)
        self.assertEqual(collection.json()["type"], "collection")
        self.assertEqual(analysis.status_code, 201)
        self.assertEqual(analysis.json()["type"], "analysis")

    def test_filters_table_rows_by_opportunity_or_risk(self) -> None:
        self.repository.upsert_announcement({"announcement_id": "opportunity", "stock_code": "000001", "stock_name": "机会公司", "title": "回购公告", "announcement_time": 100, "pdf_url": None, "analysis_status": "confirmed", "labels": ["回购、增持和股权激励"]})
        self.repository.upsert_announcement({"announcement_id": "risk", "stock_code": "000002", "stock_name": "风险公司", "title": "业绩预减", "announcement_time": 99, "pdf_url": None, "analysis_status": "confirmed", "labels": ["业绩大降"]})

        self.assertEqual([item["announcement_id"] for item in self.client.get("/api/results?view=opportunity").json()], ["opportunity"])
        self.assertEqual([item["announcement_id"] for item in self.client.get("/api/results?view=risk").json()], ["risk"])

    def test_all_captured_view_includes_dismissed_announcements(self) -> None:
        self.repository.upsert_announcement({"announcement_id": "raw", "stock_code": "000001", "stock_name": "示例公司", "title": "普通公告", "announcement_time": 100, "pdf_url": None, "analysis_status": "new"})
        self.repository.upsert_announcement({"announcement_id": "dismissed", "stock_code": "000002", "stock_name": "示例公司", "title": "普通公告", "announcement_time": 99, "pdf_url": None, "analysis_status": "dismissed"})

        response = self.client.get("/api/results?view=collected")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["announcement_id"] for item in response.json()], ["raw", "dismissed"])

    def test_default_running_app_uses_a_local_sqlite_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "data" / "announcements.sqlite3"
            with patch("cninfo_miner.main.DEFAULT_DATABASE_PATH", database_path):
                app = create_app()

            repository = app.state.repository
            try:
                self.assertIsInstance(repository, SQLiteRepository)
                self.assertTrue(database_path.exists())
            finally:
                repository.close()

    def test_lists_persisted_queued_and_running_tasks_for_page_reload(self) -> None:
        queued = self.repository.create_task(task_type="collection")
        running = self.repository.create_task(task_type="analysis")
        completed = self.repository.create_task(task_type="analysis")
        self.repository.update_task(running["id"], status="running")
        self.repository.update_task(completed["id"], status="completed")

        response = self.client.get("/api/tasks/active")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([task["id"] for task in response.json()], [queued["id"], running["id"]])

    def test_creates_app_without_deprecated_event_hooks(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            create_app(run_tasks=False, repository=MemoryRepository())

        self.assertFalse(any(issubclass(item.category, DeprecationWarning) for item in caught))
    def test_daily_collection_range_covers_today_and_yesterday(self) -> None:
        self.assertEqual(daily_collection_range(date(2026, 8, 3)), ("2026-08-02", "2026-08-03"))

    def test_manual_collection_range_uses_beijing_time_cutoff(self) -> None:
        before_cutoff = datetime(2026, 8, 3, 14, 59, tzinfo=SHANGHAI)
        at_cutoff = datetime(2026, 8, 3, 15, 0, tzinfo=SHANGHAI)

        self.assertEqual(manual_collection_range(before_cutoff), ("2026-08-02", "2026-08-03"))
        self.assertEqual(manual_collection_range(at_cutoff), ("2026-08-03", "2026-08-04"))

    def test_collection_without_dates_uses_the_automatic_manual_range(self) -> None:
        with patch("cninfo_miner.main.manual_collection_range", return_value=("2026-08-03", "2026-08-04")):
            response = self.client.post("/api/collections", json={})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["start_date"], "2026-08-03")
        self.assertEqual(response.json()["end_date"], "2026-08-04")

    def test_exposes_the_automatic_collection_range_for_the_date_calendars(self) -> None:
        with patch("cninfo_miner.main.manual_collection_range", return_value=("2026-08-03", "2026-08-04")):
            response = self.client.get("/api/collections/default-range")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"start_date": "2026-08-03", "end_date": "2026-08-04"})

    def test_saves_the_model_configuration_for_background_analysis(self) -> None:
        payload = {"base_url": "https://example.test/v1", "api_key": "secret", "model": "test-model"}

        response = self.client.put("/api/settings/llm", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repository.get_llm_config(), payload)

    def test_scheduled_cycle_collects_then_analyzes_with_saved_model_settings(self) -> None:
        self.repository.save_llm_config({"base_url": "https://example.test/v1", "api_key": "secret", "model": "test-model"})
        collection = type("Collection", (), {"run": AsyncMock()})()
        analysis = type("Analysis", (), {"run": AsyncMock()})()

        with patch("cninfo_miner.main.CollectionWorker", return_value=collection), patch("cninfo_miner.main.AnalysisWorker", return_value=analysis):
            asyncio.run(run_scheduled_cycle(self.repository, datetime(2026, 8, 3, 7, 0, tzinfo=UTC)))

        tasks = list(self.repository.tasks.values())
        self.assertEqual([(task["type"], task["start_date"], task["end_date"]) for task in tasks], [("collection", "2026-08-03", "2026-08-04"), ("analysis", "", "")])
        collection.run.assert_awaited_once()
        analysis.run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

