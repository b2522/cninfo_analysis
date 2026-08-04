"""CNINFO announcement collection orchestration."""

from __future__ import annotations

from typing import Any

from .cninfo import CninfoClient


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
