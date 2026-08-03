import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.cninfo import Announcement
from cninfo_miner.llm import LlmConfig
from cninfo_miner.repository import MemoryRepository
from cninfo_miner.worker import AnalysisWorker, CollectionWorker


class FakeCninfoClient:
    def __init__(self, pages: dict[int, tuple[list[Announcement], int]]) -> None:
        self.pages = pages
        self.requested_pages: list[int] = []

    async def list_page(self, start_date: str, end_date: str, page_num: int):
        self.requested_pages.append(page_num)
        return self.pages[page_num]

    async def close(self) -> None:
        pass


class FakeModelClient:
    async def aclose(self) -> None:
        pass

    async def analyze(self, title: str, text: str, candidate_labels: tuple[str, ...]) -> dict:
        return {
            "labels": ["业绩增长"],
            "summary": "预计净利润增长。",
            "confidence": "high",
            "evidence": "预计净利润同比增长80%",
            "stage": "业绩预告",
            "metrics": {"净利润同比": "80%"},
        }


class EmptyModelClient:
    async def aclose(self) -> None:
        pass

    async def analyze(self, title: str, text: str, candidate_labels: tuple[str, ...]) -> dict:
        return {"labels": [], "summary": "", "confidence": "low", "evidence": "", "stage": "", "metrics": {}}


class ConcurrentFakeModelClient(FakeModelClient):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def analyze(self, title: str, text: str, candidate_labels: tuple[str, ...]) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().analyze(title, text, candidate_labels)
        finally:
            self.active -= 1


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_collection_fetches_every_page_and_persists_raw_notices(self) -> None:
        notice = Announcement("one", "000001", "示例公司", "2026年半年度业绩预告", 100, "https://static.cninfo.com.cn/one.pdf")
        cninfo = FakeCninfoClient({1: ([notice], 1), 2: ([], 1)})
        repository = MemoryRepository()
        task = repository.create_task("2026-08-02", "2026-08-03", task_type="collection")

        with patch("cninfo_miner.worker.CninfoClient", return_value=cninfo):
            await CollectionWorker(repository).run(task["id"], "2026-08-02", "2026-08-03")

        self.assertEqual(cninfo.requested_pages, [1, 2])
        self.assertEqual(repository.get_announcement("one")["analysis_status"], "new")
        self.assertEqual(repository.get_task(task["id"])["processed"], 1)

    async def test_analysis_only_calls_model_for_unanalyzed_title_candidates(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({"announcement_id": "candidate", "stock_code": "000001", "stock_name": "示例公司", "title": "2026年半年度业绩预告", "announcement_time": 100, "pdf_url": "https://static.cninfo.com.cn/candidate.pdf"})
        repository.upsert_announcement({"announcement_id": "ignored", "stock_code": "000002", "stock_name": "普通公司", "title": "关于召开股东大会的通知", "announcement_time": 99, "pdf_url": "https://static.cninfo.com.cn/ignored.pdf"})
        repository.upsert_announcement({"announcement_id": "done", "stock_code": "000003", "stock_name": "已分析公司", "title": "业绩预告", "announcement_time": 98, "pdf_url": "https://static.cninfo.com.cn/done.pdf", "analysis_status": "confirmed", "labels": ["业绩增长"]})
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(return_value=("hash", "预计净利润同比增长80%"))), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=FakeModelClient()) as model:
            await AnalysisWorker(repository).run(task["id"], config)

        self.assertEqual(model.call_count, 1)
        self.assertEqual(repository.get_announcement("candidate")["analysis_status"], "confirmed")
        self.assertEqual(repository.get_announcement("candidate")["labels"], ["业绩增长"])
        self.assertEqual(repository.get_announcement("ignored")["analysis_status"], "dismissed")
        self.assertEqual(repository.get_announcement("done")["analysis_status"], "confirmed")
        self.assertEqual(repository.get_task(task["id"])["candidates"], 1)

    async def test_analysis_confirms_repurchase_progress_and_plan_with_pdf_evidence_when_model_omits_labels(self) -> None:
        repository = MemoryRepository()
        for announcement_id, title in (
            ("progress", "关于回购股份进展情况的公告"),
            ("plan", "关于回购公司股份方案的公告"),
        ):
            repository.upsert_announcement({
                "announcement_id": announcement_id,
                "stock_code": "300001",
                "stock_name": "示例公司",
                "title": title,
                "announcement_time": 100,
                "pdf_url": f"https://static.cninfo.com.cn/{announcement_id}.pdf",
            })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")
        async def pdf_text(url, _client):
            if "progress" in url:
                return "progress-hash", "截至2026年7月31日，公司累计回购公司股份0股。公司暂未实施本次股份回购。"
            return "plan-hash", "本次回购股份资金不低于3,000万元（含）且不超过5,000万元（含）。"

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(side_effect=pdf_text)), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=EmptyModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        progress = repository.get_announcement("progress")
        self.assertEqual(progress["analysis_status"], "confirmed")
        self.assertEqual(progress["labels"], ["回购、增持和股权激励"])
        self.assertTrue(progress["summary"].startswith("回购计划："))
        plan = repository.get_announcement("plan")
        self.assertEqual(plan["analysis_status"], "confirmed")
        self.assertEqual(plan["summary"], "回购计划：资金总额3,000万元至5,000万元，回购价格上限本公告未披露；回购进展：本公告未披露。")
    async def test_analysis_confirms_early_terminated_reduction_with_pdf_evidence_when_model_omits_labels(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({
            "announcement_id": "termination",
            "stock_code": "301160",
            "stock_name": "荣信文化",
            "title": "关于5%以上股东、董事、高级管理人员提前终止减持计划暨减持股份结果的公告",
            "announcement_time": 100,
            "pdf_url": "https://static.cninfo.com.cn/termination.pdf",
        })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(return_value=("hash", "相关股东决定提前终止减持计划。"))), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=EmptyModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        saved = repository.get_announcement("termination")
        self.assertEqual(saved["analysis_status"], "confirmed")
        self.assertEqual(saved["labels"], ["终止减持、未减持"])
        self.assertEqual(saved["evidence"], "提前终止减持计划")

    async def test_analysis_confirms_increase_holding_facts_from_pdf_when_model_omits_labels(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({
            "announcement_id": "increase",
            "stock_code": "300001",
            "stock_name": "示例公司",
            "title": "关于控股股东增持公司股份进展的公告",
            "announcement_time": 100,
            "pdf_url": "https://static.cninfo.com.cn/increase.pdf",
        })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")
        text = "截至2026年7月31日，控股股东累计增持公司股份2,000,000股，占公司总股本的1.25%，增持均价为8.60元/股。"

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(return_value=("hash", text))), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=EmptyModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        saved = repository.get_announcement("increase")
        self.assertEqual(saved["analysis_status"], "confirmed")
        self.assertEqual(saved["summary"], "增持进展：累计增持2,000,000股，占总股本1.25%，成交均价8.60元/股。")

    async def test_analysis_confirms_sale_of_repurchase_shares_as_a_risk_with_pdf_evidence(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({
            "announcement_id": "sale-repurchase",
            "stock_code": "300001",
            "stock_name": "示例公司",
            "title": "关于出售已回购股份的进展公告",
            "announcement_time": 100,
            "pdf_url": "https://static.cninfo.com.cn/sale-repurchase.pdf",
        })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(return_value=("hash", "公司已完成出售已回购股份事项。"))), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=EmptyModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        saved = repository.get_announcement("sale-repurchase")
        self.assertEqual(saved["analysis_status"], "confirmed")
        self.assertEqual(saved["labels"], ["大股东减持、质押和股权变动"])
        self.assertEqual(saved["summary"], "出售已回购股份（相当于减持）。")

    async def test_analysis_retries_a_technical_failure_once_before_confirming(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({
            "announcement_id": "retry-success",
            "stock_code": "000001",
            "stock_name": "示例公司",
            "title": "2026年半年度业绩预告",
            "announcement_time": 100,
            "pdf_url": "https://static.cninfo.com.cn/retry-success.pdf",
        })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")
        download = AsyncMock(side_effect=[RuntimeError("temporary failure"), ("hash", "预计净利润同比增长80%")])

        with patch("cninfo_miner.worker.download_pdf_text", new=download), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=FakeModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        self.assertEqual(download.await_count, 2)
        self.assertEqual(repository.get_announcement("retry-success")["analysis_status"], "confirmed")
        self.assertEqual(repository.get_task(task["id"])["failures"], 0)

    async def test_analysis_dismisses_after_a_second_technical_failure(self) -> None:
        repository = MemoryRepository()
        repository.upsert_announcement({
            "announcement_id": "retry-dismissed",
            "stock_code": "000001",
            "stock_name": "示例公司",
            "title": "2026年半年度业绩预告",
            "announcement_time": 100,
            "pdf_url": "https://static.cninfo.com.cn/retry-dismissed.pdf",
        })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")
        download = AsyncMock(side_effect=[RuntimeError("first failure"), RuntimeError("second failure")])

        with patch("cninfo_miner.worker.download_pdf_text", new=download), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=FakeModelClient()), patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        saved = repository.get_announcement("retry-dismissed")
        self.assertEqual(download.await_count, 2)
        self.assertEqual(saved["analysis_status"], "dismissed")
        self.assertIsNone(saved["error"])
        self.assertEqual(repository.get_task(task["id"])["status"], "completed")
        self.assertEqual(repository.get_task(task["id"])["failures"], 1)

    async def test_analysis_limits_candidates_to_three_and_reuses_one_model_client(self) -> None:
        repository = MemoryRepository()
        for index in range(4):
            repository.upsert_announcement({
                "announcement_id": f"candidate-{index}",
                "stock_code": f"00000{index}",
                "stock_name": "示例公司",
                "title": "2026年半年度业绩预告",
                "announcement_time": 100 - index,
                "pdf_url": f"https://static.cninfo.com.cn/candidate-{index}.pdf",
            })
        task = repository.create_task(task_type="analysis")
        config = LlmConfig(base_url="http://example.invalid", api_key="unused", model="unused")
        model_client = ConcurrentFakeModelClient()

        with patch("cninfo_miner.worker.download_pdf_text", new=AsyncMock(return_value=("hash", "预计净利润同比增长80%"))), patch("cninfo_miner.worker.OpenAICompatibleClient", return_value=model_client) as model, patch.object(AnalysisWorker, "CANDIDATE_DELAY_SECONDS", 0, create=True):
            await AnalysisWorker(repository).run(task["id"], config)

        self.assertEqual(model.call_count, 1)
        self.assertEqual(model_client.max_active, 3)
        self.assertTrue(all(repository.get_announcement(f"candidate-{index}")["analysis_status"] == "confirmed" for index in range(4)))


if __name__ == "__main__":
    unittest.main()


