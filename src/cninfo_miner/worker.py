"""Background collection and analysis orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .classification import (
    has_evidence,
    increase_holding_evidence,
    repurchase_evidence,
    sale_repurchase_evidence,
    screen_categories,
    termination_reduction_evidence,
)
from .cninfo import CninfoClient
from .llm import LlmConfig, OpenAICompatibleClient
from .pdf_text import download_pdf_text


class CollectionWorker:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def run(self, task_id: str, start_date: str, end_date: str) -> None:
        self.repository.update_task(task_id, status="running")
        cninfo = CninfoClient()
        processed = failures = 0
        try:
            page = 1
            last_page_index = 0
            while page <= last_page_index + 1:
                notices, returned_total_pages = await cninfo.list_page(start_date, end_date, page)
                last_page_index = max(returned_total_pages, 0)
                for notice in notices:
                    processed += 1
                    announcement_id = notice.announcement_id or f"{notice.stock_code}-{notice.announcement_time}-{notice.title}"
                    self.repository.upsert_announcement({
                        "announcement_id": announcement_id,
                        "stock_code": notice.stock_code,
                        "stock_name": notice.stock_name,
                        "title": notice.title,
                        "announcement_time": notice.announcement_time,
                        "pdf_url": notice.pdf_url,
                    })
                self.repository.update_task(task_id, processed=processed, failures=failures)
                page += 1
            self.repository.update_task(task_id, status="completed", processed=processed, failures=failures)
        except Exception as error:
            self.repository.update_task(task_id, status="failed", error=str(error), processed=processed, failures=failures + 1)
        finally:
            await cninfo.close()


class AnalysisWorker:
    MAX_CONCURRENCY = 3
    CANDIDATE_DELAY_SECONDS = 0.35

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def run(self, task_id: str, llm_config: LlmConfig) -> None:
        self.repository.update_task(task_id, status="running")
        http_client = httpx.AsyncClient(timeout=45.0)
        model_client = OpenAICompatibleClient(llm_config)
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        progress_lock = asyncio.Lock()
        processed = candidates = failures = 0

        async def mark_candidate_completed() -> None:
            nonlocal processed
            async with progress_lock:
                processed += 1
                self.repository.update_task(task_id, processed=processed, candidates=candidates, failures=failures)

        async def analyze_candidate(
            announcement: dict[str, Any], labels: tuple[str, ...], *, retry: bool = False
        ) -> bool:
            announcement_id = announcement["announcement_id"]
            failed = False
            async with semaphore:
                try:
                    pdf_url = announcement.get("pdf_url")
                    if not pdf_url:
                        raise ValueError("公告没有可用 PDF 链接")
                    content_hash, text = await download_pdf_text(pdf_url, http_client)
                    model_result = await model_client.analyze(announcement["title"], text, labels)
                    selected = [label for label in model_result.get("labels", []) if label in labels]
                    evidence = str(model_result.get("evidence") or "")
                    termination_evidence = termination_reduction_evidence(announcement["title"], text)
                    sale_repurchase_result = sale_repurchase_evidence(announcement["title"], text)
                    repurchase_result = repurchase_evidence(announcement["title"], text)
                    increase_result = increase_holding_evidence(announcement["title"], text)
                    if termination_evidence:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="confirmed",
                            labels=["终止减持、未减持"],
                            summary="公告原文明确披露提前终止或未实施减持。",
                            confidence="high",
                            evidence=termination_evidence,
                            stage="rule-fallback",
                            metrics={},
                            content_hash=content_hash,
                            error=None,
                        )
                    elif sale_repurchase_result:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="confirmed",
                            labels=[sale_repurchase_result["label"]],
                            summary=sale_repurchase_result["summary"],
                            confidence=sale_repurchase_result["confidence"],
                            evidence=sale_repurchase_result["evidence"],
                            stage=sale_repurchase_result["stage"],
                            metrics=sale_repurchase_result["metrics"],
                            content_hash=content_hash,
                            error=None,
                        )
                    elif repurchase_result:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="confirmed",
                            labels=[repurchase_result["label"]],
                            summary=repurchase_result["summary"],
                            confidence=repurchase_result["confidence"],
                            evidence=repurchase_result["evidence"],
                            stage=repurchase_result["stage"],
                            metrics=repurchase_result["metrics"],
                            content_hash=content_hash,
                            error=None,
                        )
                    elif increase_result:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="confirmed",
                            labels=[increase_result["label"]],
                            summary=increase_result["summary"],
                            confidence=increase_result["confidence"],
                            evidence=increase_result["evidence"],
                            stage=increase_result["stage"],
                            metrics=increase_result["metrics"],
                            content_hash=content_hash,
                            error=None,
                        )
                    elif selected and has_evidence(evidence, text):
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="confirmed",
                            labels=selected,
                            summary=str(model_result.get("summary") or ""),
                            confidence=str(model_result.get("confidence") or "low"),
                            evidence=evidence,
                            stage=str(model_result.get("stage") or ""),
                            metrics=model_result.get("metrics") or {},
                            content_hash=content_hash,
                            error=None,
                        )
                    else:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="dismissed",
                            labels=[],
                            summary="",
                            confidence=str(model_result.get("confidence") or "low"),
                            evidence="",
                            stage="",
                            metrics={},
                            content_hash=content_hash,
                            error=None,
                        )
                except Exception as error:
                    failed = True
                    if retry:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="dismissed",
                            labels=[],
                            summary="",
                            confidence="low",
                            evidence="",
                            stage="",
                            metrics={},
                            error=None,
                        )
                    else:
                        self.repository.update_announcement(
                            announcement_id,
                            analysis_status="failed",
                            error=f"分析失败: {error}",
                        )
                finally:
                    await asyncio.sleep(self.CANDIDATE_DELAY_SECONDS)
            if not retry:
                await mark_candidate_completed()
            return failed

        try:
            candidate_jobs = []
            candidate_items: list[tuple[dict[str, Any], tuple[str, ...]]] = []
            for announcement in self.repository.list_unanalyzed():
                labels = screen_categories(str(announcement.get("title") or ""))
                if not labels:
                    processed += 1
                    self.repository.update_announcement(
                        announcement["announcement_id"],
                        analysis_status="dismissed",
                        candidate_labels=[],
                        labels=[],
                        error=None,
                    )
                    self.repository.update_task(task_id, processed=processed, candidates=candidates, failures=failures)
                    continue

                candidates += 1
                self.repository.update_announcement(
                    announcement["announcement_id"],
                    analysis_status="analyzing",
                    candidate_labels=list(labels),
                    error=None,
                )
                candidate_items.append((announcement, labels))
                candidate_jobs.append(analyze_candidate(announcement, labels))
            self.repository.update_task(task_id, processed=processed, candidates=candidates, failures=failures)
            first_pass_failures = await asyncio.gather(*candidate_jobs)
            for announcement, labels in (
                item for item, failed in zip(candidate_items, first_pass_failures) if failed
            ):
                if await analyze_candidate(announcement, labels, retry=True):
                    failures += 1
                    self.repository.update_task(
                        task_id, processed=processed, candidates=candidates, failures=failures
                    )
            self.repository.update_task(task_id, status="completed", processed=processed, candidates=candidates, failures=failures)
        except Exception as error:
            self.repository.update_task(task_id, status="failed", error=str(error), processed=processed, candidates=candidates, failures=failures)
        finally:
            await model_client.aclose()
            await http_client.aclose()
