"""FastAPI application and local-only browser API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .llm import LlmConfig
from .repository import MemoryRepository, SQLiteRepository
from .worker import AnalysisWorker, CollectionWorker

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "cninfo_announcement_mining.sqlite3"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BrowserLlmConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class CollectionRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class AnalysisRequest(BaseModel):
    llm: BrowserLlmConfig


def daily_collection_range(today: date) -> tuple[str, str]:
    return ((today - timedelta(days=1)).isoformat(), today.isoformat())


def manual_collection_range(now: datetime) -> tuple[str, str]:
    beijing_now = now.astimezone(SHANGHAI)
    today = beijing_now.date()
    if beijing_now.hour < 15:
        return daily_collection_range(today)
    return (today.isoformat(), (today + timedelta(days=1)).isoformat())


def _start_collection(repository: object, task: dict) -> None:
    thread = threading.Thread(
        target=lambda: asyncio.run(CollectionWorker(repository).run(task["id"], task["start_date"], task["end_date"])),
        daemon=True,
    )
    thread.start()


async def run_scheduled_cycle(repository: object, now: datetime) -> None:
    start_date, end_date = manual_collection_range(now)
    collection_task = repository.create_task(start_date, end_date, task_type="collection")
    await CollectionWorker(repository).run(collection_task["id"], start_date, end_date)
    config = repository.get_llm_config()
    if not config:
        return
    analysis_task = repository.create_task(task_type="analysis")
    await AnalysisWorker(repository).run(analysis_task["id"], LlmConfig(**config))


def _start_scheduled_cycle(repository: object, now: datetime) -> None:
    thread = threading.Thread(target=lambda: asyncio.run(run_scheduled_cycle(repository, now)), daemon=True)
    thread.start()


def _start_analysis(repository: object, task: dict, config: LlmConfig) -> None:
    thread = threading.Thread(
        target=lambda: asyncio.run(AnalysisWorker(repository).run(task["id"], config)),
        daemon=True,
    )
    thread.start()


def create_app(run_tasks: bool = True, repository: object | None = None) -> FastAPI:
    if repository is None:
        repository = SQLiteRepository(DEFAULT_DATABASE_PATH) if run_tasks else MemoryRepository()
        if isinstance(repository, SQLiteRepository):
            repository.recover_interrupted_tasks()
            repository.requeue_dismissed_termination_notices()
            repository.requeue_classification_corrections()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if application.state.run_tasks and not getattr(application.state, "scheduler_thread", None):
            def loop() -> None:
                last_run_slot = ""
                while not application.state.scheduler_stop.is_set():
                    now = datetime.now(UTC)
                    slot = now.strftime("%Y-%m-%dT%H")
                    if now.hour % 4 == 0 and slot != last_run_slot:
                        _start_scheduled_cycle(repository, now)
                        last_run_slot = slot
                    application.state.scheduler_stop.wait(30)

            application.state.scheduler_thread = threading.Thread(target=loop, daemon=True)
            application.state.scheduler_thread.start()
        try:
            yield
        finally:
            application.state.scheduler_stop.set()

    app = FastAPI(title="公告掘金", version="0.2.0", lifespan=lifespan)
    app.state.repository = repository
    app.state.run_tasks = run_tasks
    app.state.read_only = bool(getattr(repository, "read_only", False))
    app.state.scheduler_stop = threading.Event()
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        storage = "memory-test" if isinstance(repository, MemoryRepository) else "sqlite"
        return {"status": "ok", "storage": storage}

    def require_writable_storage() -> None:
        if app.state.read_only:
            raise HTTPException(status_code=403, detail="Vercel 部署为只读模式，请等待 GitHub Actions 更新数据")

    @app.post("/api/collections", status_code=201)
    async def create_collection(request: CollectionRequest) -> dict:
        require_writable_storage()
        if request.start_date is None and request.end_date is None:
            start_date, end_date = manual_collection_range(datetime.now(UTC))
        elif request.start_date is None or request.end_date is None:
            raise HTTPException(status_code=422, detail="开始日期和结束日期必须同时填写")
        elif request.end_date < request.start_date:
            raise HTTPException(status_code=422, detail="结束日期不能早于开始日期")
        else:
            start_date, end_date = request.start_date.isoformat(), request.end_date.isoformat()
        task = repository.create_task(start_date, end_date, task_type="collection")
        if app.state.run_tasks:
            _start_collection(repository, task)
        return task

    @app.get("/api/collections/default-range")
    async def get_default_collection_range() -> dict[str, str]:
        start_date, end_date = manual_collection_range(datetime.now(UTC))
        return {"start_date": start_date, "end_date": end_date}

    @app.put("/api/settings/llm")
    async def save_llm_settings(config: BrowserLlmConfig) -> dict[str, bool]:
        require_writable_storage()
        if not all((config.base_url, config.api_key, config.model)):
            raise HTTPException(status_code=422, detail="请完整填写 API 地址、密钥和模型名")
        repository.save_llm_config(config.model_dump())
        return {"configured": True}

    @app.delete("/api/settings/llm", status_code=204)
    async def clear_llm_settings() -> None:
        require_writable_storage()
        repository.clear_llm_config()

    @app.post("/api/analyses", status_code=201)
    async def create_analysis(request: AnalysisRequest) -> dict:
        require_writable_storage()
        task = repository.create_task(task_type="analysis")
        if app.state.run_tasks:
            _start_analysis(repository, task, LlmConfig(**request.llm.model_dump()))
        return task

    @app.get("/api/tasks/active")
    async def list_active_tasks() -> list[dict]:
        return repository.list_active_tasks()

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> dict:
        task = repository.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @app.get("/api/results")
    async def get_results(view: Literal["all", "collected", "opportunity", "risk"] = "all") -> list[dict]:
        return repository.list_results(view)

    return app


def create_vercel_app() -> FastAPI:
    repository = SQLiteRepository(DEFAULT_DATABASE_PATH, read_only=True)
    return create_app(run_tasks=False, repository=repository)


