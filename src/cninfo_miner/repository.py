"""MongoDB persistence. Only the configured database is accessed."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, MongoClient

from .classification import is_routine_release_and_repledge_title, is_termination_reduction_title
from .domain import RESULT_VIEW_CATEGORIES, SUPPORTED_CATEGORIES, result_views_for_labels

_VISIBLE_STATUSES = ("new", "candidate", "analyzing", "confirmed", "failed")
_UNANALYZED_STATUSES = ("new", "candidate")
_RAW_FIELDS = ("announcement_id", "stock_code", "stock_name", "title", "announcement_time", "pdf_url")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _with_result_views(item: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(item)
    result["result_views"] = list(result_views_for_labels(result.get("labels", [])))
    return result


class MemoryRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.announcements: dict[str, dict[str, Any]] = {}
        self.llm_config: dict[str, str] | None = None

    def save_llm_config(self, config: dict[str, str]) -> None:
        self.llm_config = deepcopy(config)

    def get_llm_config(self) -> dict[str, str] | None:
        return deepcopy(self.llm_config) if self.llm_config else None

    def clear_llm_config(self) -> None:
        self.llm_config = None

    def create_task(self, start_date: str = "", end_date: str = "", *, task_type: str = "analysis") -> dict[str, Any]:
        task = {"id": uuid4().hex, "type": task_type, "start_date": start_date, "end_date": end_date, "status": "queued", "processed": 0, "candidates": 0, "failures": 0, "created_at": _now()}
        self.tasks[task["id"]] = task
        return deepcopy(task)

    def update_task(self, task_id: str, **fields: Any) -> None:
        self.tasks[task_id].update(fields)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        return deepcopy(task) if task else None

    def list_active_tasks(self) -> list[dict[str, Any]]:
        return sorted(
            (deepcopy(task) for task in self.tasks.values() if task.get("status") in {"queued", "running"}),
            key=lambda task: task.get("created_at") or "",
        )

    def requeue_dismissed_termination_notices(self) -> int:
        restored = 0
        for announcement in self.announcements.values():
            if announcement.get("analysis_status") != "dismissed" or not is_termination_reduction_title(str(announcement.get("title") or "")):
                continue
            announcement.update({
                "analysis_status": "candidate",
                "candidate_labels": [],
                "labels": [],
                "evidence": "",
                "summary": "",
                "stage": "",
                "metrics": {},
                "error": None,
            })
            restored += 1
        return restored

    def requeue_classification_corrections(self) -> int:
        """Reset historical rows affected by the refined title/PDF rules."""
        corrected = 0
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM announcements").fetchall()
            for row in rows:
                announcement = self._decode(row["payload"])
                title = str(announcement.get("title") or "")
                current_status = announcement.get("analysis_status")
                summary = str(announcement.get("summary") or "")
                if is_routine_release_and_repledge_title(title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "激励对象名单" in title or ("购买土地使用权" in title and "进展" in title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "回购" in title and "进展" in title:
                    has_expanded_progress_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：", "累计回购总金额", "最高成交价", "最低成交价")
                    )
                    if current_status not in {"dismissed", "confirmed"} or has_expanded_progress_summary:
                        continue
                    target_status = "candidate"
                elif "回购" in title and "方案" in title:
                    has_expanded_plan_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：")
                    )
                    if current_status != "confirmed" or has_expanded_plan_summary:
                        continue
                    target_status = "candidate"
                else:
                    continue
                announcement.update({
                    "analysis_status": target_status,
                    "candidate_labels": [],
                    "labels": [],
                    "evidence": "",
                    "summary": "",
                    "stage": "",
                    "metrics": {},
                    "error": None,
                })
                self._save_announcement_unlocked(announcement)
                corrected += 1
            if corrected:
                self._connection.commit()
        return corrected

    def upsert_announcement(self, announcement: dict[str, Any]) -> bool:
        key = announcement["announcement_id"]
        previous = self.announcements.get(key)
        if previous is None:
            saved = deepcopy(announcement)
            saved.setdefault("analysis_status", "new")
            saved.setdefault("labels", [])
            saved.setdefault("candidate_labels", [])
            saved["collected_at"] = _now()
        else:
            saved = deepcopy(previous)
            for field in _RAW_FIELDS:
                if field in announcement:
                    saved[field] = announcement[field]
            saved["collected_at"] = _now()
        self.announcements[key] = saved
        return previous != saved

    def update_announcement(self, announcement_id: str, **fields: Any) -> None:
        self.announcements[announcement_id].update(deepcopy(fields))

    def get_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        item = self.announcements.get(announcement_id)
        return deepcopy(item) if item else None

    def list_unanalyzed(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.announcements.values() if item.get("analysis_status") in _UNANALYZED_STATUSES]

    def upsert_result(self, result: dict[str, Any]) -> bool:
        previous = self.announcements.get(result["announcement_id"])
        self.announcements[result["announcement_id"]] = deepcopy(result)
        return previous != result

    def list_results(self, view: str = "all") -> list[dict[str, Any]]:
        items = [
            _with_result_views(item)
            for item in self.announcements.values()
            if view == "collected" or item.get("analysis_status") in _VISIBLE_STATUSES
        ]
        if view in RESULT_VIEW_CATEGORIES:
            items = [item for item in items if view in item["result_views"]]
        return sorted(items, key=lambda item: item.get("announcement_time") or 0, reverse=True)


class SQLiteRepository:
    def __init__(self, path: str | Path) -> None:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS announcements (
                    announcement_id TEXT PRIMARY KEY,
                    analysis_status TEXT NOT NULL,
                    announcement_time,
                    payload TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_announcements_status_time "
                "ON announcements (analysis_status, announcement_time)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _encode(item: dict[str, Any]) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(payload: str) -> dict[str, Any]:
        return json.loads(payload)

    def _get_announcement_unlocked(self, announcement_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM announcements WHERE announcement_id = ?",
            (announcement_id,),
        ).fetchone()
        return self._decode(row["payload"]) if row else None

    def _save_announcement_unlocked(self, announcement: dict[str, Any]) -> None:
        self._connection.execute(
            """
            INSERT INTO announcements (announcement_id, analysis_status, announcement_time, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(announcement_id) DO UPDATE SET
                analysis_status = excluded.analysis_status,
                announcement_time = excluded.announcement_time,
                payload = excluded.payload
            """,
            (
                announcement["announcement_id"],
                announcement.get("analysis_status", "new"),
                announcement.get("announcement_time"),
                self._encode(announcement),
            ),
        )

    def save_llm_config(self, config: dict[str, str]) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO settings (key, payload) VALUES ('llm_config', ?) ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
                (self._encode(config),),
            )
            self._connection.commit()

    def get_llm_config(self) -> dict[str, str] | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM settings WHERE key = 'llm_config'").fetchone()
        return self._decode(row["payload"]) if row else None

    def clear_llm_config(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM settings WHERE key = 'llm_config'")
            self._connection.commit()

    def create_task(self, start_date: str = "", end_date: str = "", *, task_type: str = "analysis") -> dict[str, Any]:
        task = {"id": uuid4().hex, "type": task_type, "start_date": start_date, "end_date": end_date, "status": "queued", "processed": 0, "candidates": 0, "failures": 0, "created_at": _now()}
        with self._lock:
            self._connection.execute("INSERT INTO tasks (id, payload) VALUES (?, ?)", (task["id"], self._encode(task)))
            self._connection.commit()
        return deepcopy(task)

    def update_task(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return
            task = self._decode(row["payload"])
            task.update(deepcopy(fields))
            self._connection.execute("UPDATE tasks SET payload = ? WHERE id = ?", (self._encode(task), task_id))
            self._connection.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return deepcopy(self._decode(row["payload"])) if row else None

    def list_active_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM tasks").fetchall()
        tasks = [self._decode(row["payload"]) for row in rows]
        return sorted(
            (task for task in tasks if task.get("status") in {"queued", "running"}),
            key=lambda task: task.get("created_at") or "",
        )

    def recover_interrupted_tasks(self) -> int:
        recovered = 0
        with self._lock:
            rows = self._connection.execute("SELECT id, payload FROM tasks").fetchall()
            interrupted_analysis = False
            for row in rows:
                task = self._decode(row["payload"])
                if task.get("status") not in {"queued", "running"}:
                    continue
                interrupted_analysis = interrupted_analysis or task.get("type") == "analysis"
                task.update({"status": "failed", "error": "服务重启，任务已中断，请重新发起。"})
                self._connection.execute(
                    "UPDATE tasks SET payload = ? WHERE id = ?", (self._encode(task), row["id"])
                )
                recovered += 1
            if interrupted_analysis:
                rows = self._connection.execute(
                    "SELECT payload FROM announcements WHERE analysis_status = ?", ("analyzing",)
                ).fetchall()
                for row in rows:
                    announcement = self._decode(row["payload"])
                    announcement.update({"analysis_status": "candidate", "error": None})
                    self._save_announcement_unlocked(announcement)
            if recovered:
                self._connection.commit()
        return recovered

    def requeue_dismissed_termination_notices(self) -> int:
        restored = 0
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM announcements WHERE analysis_status = ?", ("dismissed",)
            ).fetchall()
            for row in rows:
                announcement = self._decode(row["payload"])
                if not is_termination_reduction_title(str(announcement.get("title") or "")):
                    continue
                announcement.update({
                    "analysis_status": "candidate",
                    "candidate_labels": [],
                    "labels": [],
                    "evidence": "",
                    "summary": "",
                    "stage": "",
                    "metrics": {},
                    "error": None,
                })
                self._save_announcement_unlocked(announcement)
                restored += 1
            if restored:
                self._connection.commit()
        return restored

    def requeue_classification_corrections(self) -> int:
        """Reset historical rows affected by the refined title/PDF rules."""
        corrected = 0
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM announcements").fetchall()
            for row in rows:
                announcement = self._decode(row["payload"])
                title = str(announcement.get("title") or "")
                current_status = announcement.get("analysis_status")
                summary = str(announcement.get("summary") or "")
                if is_routine_release_and_repledge_title(title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "激励对象名单" in title or ("购买土地使用权" in title and "进展" in title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "回购" in title and "进展" in title:
                    has_expanded_progress_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：", "累计回购总金额", "最高成交价", "最低成交价")
                    )
                    if current_status not in {"dismissed", "confirmed"} or has_expanded_progress_summary:
                        continue
                    target_status = "candidate"
                elif "回购" in title and "方案" in title:
                    has_expanded_plan_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：")
                    )
                    if current_status != "confirmed" or has_expanded_plan_summary:
                        continue
                    target_status = "candidate"
                else:
                    continue
                announcement.update({
                    "analysis_status": target_status,
                    "candidate_labels": [],
                    "labels": [],
                    "evidence": "",
                    "summary": "",
                    "stage": "",
                    "metrics": {},
                    "error": None,
                })
                self._save_announcement_unlocked(announcement)
                corrected += 1
            if corrected:
                self._connection.commit()
        return corrected

    def upsert_announcement(self, announcement: dict[str, Any]) -> bool:
        key = announcement["announcement_id"]
        with self._lock:
            previous = self._get_announcement_unlocked(key)
            if previous is None:
                saved = deepcopy(announcement)
                saved.setdefault("analysis_status", "new")
                saved.setdefault("labels", [])
                saved.setdefault("candidate_labels", [])
                saved["collected_at"] = _now()
            else:
                saved = deepcopy(previous)
                for field in _RAW_FIELDS:
                    if field in announcement:
                        saved[field] = announcement[field]
                saved["collected_at"] = _now()
            self._save_announcement_unlocked(saved)
            self._connection.commit()
        return previous != saved

    def update_announcement(self, announcement_id: str, **fields: Any) -> None:
        with self._lock:
            announcement = self._get_announcement_unlocked(announcement_id)
            if announcement is None:
                return
            announcement.update(deepcopy(fields))
            self._save_announcement_unlocked(announcement)
            self._connection.commit()

    def get_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        with self._lock:
            announcement = self._get_announcement_unlocked(announcement_id)
        return deepcopy(announcement) if announcement else None

    def list_unanalyzed(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in _UNANALYZED_STATUSES)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload FROM announcements WHERE analysis_status IN ({placeholders}) ORDER BY rowid",
                _UNANALYZED_STATUSES,
            ).fetchall()
        return [deepcopy(self._decode(row["payload"])) for row in rows]

    def upsert_result(self, result: dict[str, Any]) -> bool:
        saved = deepcopy(result)
        with self._lock:
            previous = self._get_announcement_unlocked(saved["announcement_id"])
            self._save_announcement_unlocked(saved)
            self._connection.commit()
        return previous != saved

    def list_results(self, view: str = "all") -> list[dict[str, Any]]:
        if view == "collected":
            query = "SELECT payload FROM announcements ORDER BY rowid"
            parameters: tuple[str, ...] = ()
        else:
            placeholders = ", ".join("?" for _ in _VISIBLE_STATUSES)
            query = f"SELECT payload FROM announcements WHERE analysis_status IN ({placeholders}) ORDER BY rowid"
            parameters = _VISIBLE_STATUSES
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        items = [_with_result_views(self._decode(row["payload"])) for row in rows]
        if view in RESULT_VIEW_CATEGORIES:
            items = [item for item in items if view in item["result_views"]]
        return sorted(items, key=lambda item: item.get("announcement_time") or 0, reverse=True)


class MongoRepository:
    def __init__(self, uri: str, database: str) -> None:
        self._client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client[database]
        self._tasks = self._db["tasks"]
        self._results = self._db["announcement_results"]
        self._tasks.create_index([("id", ASCENDING)], unique=True)
        self._results.create_index([("announcement_id", ASCENDING)], unique=True)
        self._results.create_index([("analysis_status", ASCENDING), ("announcement_time", ASCENDING)])

    def create_task(self, start_date: str = "", end_date: str = "", *, task_type: str = "analysis") -> dict[str, Any]:
        task = {"id": uuid4().hex, "type": task_type, "start_date": start_date, "end_date": end_date, "status": "queued", "processed": 0, "candidates": 0, "failures": 0, "created_at": _now()}
        self._tasks.insert_one(task)
        return {key: value for key, value in task.items() if key != "_id"}

    def update_task(self, task_id: str, **fields: Any) -> None:
        self._tasks.update_one({"id": task_id}, {"$set": fields})

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.find_one({"id": task_id}, {"_id": 0})

    def list_active_tasks(self) -> list[dict[str, Any]]:
        return list(self._tasks.find({"status": {"$in": ["queued", "running"]}}, {"_id": 0}).sort("created_at", 1))

    def requeue_dismissed_termination_notices(self) -> int:
        restored = 0
        for announcement in self._results.find({"analysis_status": "dismissed"}, {"_id": 0}):
            if not is_termination_reduction_title(str(announcement.get("title") or "")):
                continue
            self._results.update_one(
                {"announcement_id": announcement["announcement_id"]},
                {"$set": {
                    "analysis_status": "candidate",
                    "candidate_labels": [],
                    "labels": [],
                    "evidence": "",
                    "summary": "",
                    "stage": "",
                    "metrics": {},
                    "error": None,
                }},
            )
            restored += 1
        return restored

    def requeue_classification_corrections(self) -> int:
        """Reset historical rows affected by the refined title/PDF rules."""
        corrected = 0
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM announcements").fetchall()
            for row in rows:
                announcement = self._decode(row["payload"])
                title = str(announcement.get("title") or "")
                current_status = announcement.get("analysis_status")
                summary = str(announcement.get("summary") or "")
                if is_routine_release_and_repledge_title(title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "激励对象名单" in title or ("购买土地使用权" in title and "进展" in title):
                    if current_status == "dismissed":
                        continue
                    target_status = "dismissed"
                elif "回购" in title and "进展" in title:
                    has_expanded_progress_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：", "累计回购总金额", "最高成交价", "最低成交价")
                    )
                    if current_status not in {"dismissed", "confirmed"} or has_expanded_progress_summary:
                        continue
                    target_status = "candidate"
                elif "回购" in title and "方案" in title:
                    has_expanded_plan_summary = all(
                        marker in summary for marker in ("回购计划：资金总额", "回购价格上限", "回购进展：")
                    )
                    if current_status != "confirmed" or has_expanded_plan_summary:
                        continue
                    target_status = "candidate"
                else:
                    continue
                announcement.update({
                    "analysis_status": target_status,
                    "candidate_labels": [],
                    "labels": [],
                    "evidence": "",
                    "summary": "",
                    "stage": "",
                    "metrics": {},
                    "error": None,
                })
                self._save_announcement_unlocked(announcement)
                corrected += 1
            if corrected:
                self._connection.commit()
        return corrected

    def upsert_announcement(self, announcement: dict[str, Any]) -> bool:
        key = announcement["announcement_id"]
        previous = self._results.find_one({"announcement_id": key}, {"_id": 0})
        if previous is None:
            saved = deepcopy(announcement)
            saved.setdefault("analysis_status", "new")
            saved.setdefault("labels", [])
            saved.setdefault("candidate_labels", [])
            saved["collected_at"] = _now()
            self._results.insert_one(saved)
            return True
        raw_update = {field: announcement[field] for field in _RAW_FIELDS if field in announcement}
        raw_update["collected_at"] = _now()
        self._results.update_one({"announcement_id": key}, {"$set": raw_update})
        return previous != {**previous, **raw_update}

    def update_announcement(self, announcement_id: str, **fields: Any) -> None:
        self._results.update_one({"announcement_id": announcement_id}, {"$set": fields})

    def get_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        return self._results.find_one({"announcement_id": announcement_id}, {"_id": 0})

    def list_unanalyzed(self) -> list[dict[str, Any]]:
        return list(self._results.find({"analysis_status": {"$in": list(_UNANALYZED_STATUSES)}}, {"_id": 0}).sort("announcement_time", -1))

    def upsert_result(self, result: dict[str, Any]) -> bool:
        previous = self._results.find_one({"announcement_id": result["announcement_id"]}, {"_id": 0})
        self._results.replace_one({"announcement_id": result["announcement_id"]}, result, upsert=True)
        return previous != result

    def list_results(self, view: str = "all") -> list[dict[str, Any]]:
        visible_query: dict[str, Any] = {} if view == "collected" else {"analysis_status": {"$in": list(_VISIBLE_STATUSES)}}
        if view in RESULT_VIEW_CATEGORIES:
            query: dict[str, Any] = {"$and": [visible_query, {"labels": {"$in": list(RESULT_VIEW_CATEGORIES[view])}}]}
        else:
            query = visible_query
        return [_with_result_views(item) for item in self._results.find(query, {"_id": 0}).sort("announcement_time", -1)]
