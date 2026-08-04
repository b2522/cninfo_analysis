"""SQLite persistence for collected CNINFO announcements."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4

_RAW_FIELDS = ("announcement_id", "stock_code", "stock_name", "title", "announcement_time", "pdf_url")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.announcements: dict[str, dict[str, Any]] = {}

    def create_task(self, start_date: str = "", end_date: str = "", *, task_type: str = "collection") -> dict[str, Any]:
        task = {
            "id": uuid4().hex,
            "type": task_type,
            "start_date": start_date,
            "end_date": end_date,
            "status": "queued",
            "processed": 0,
            "failures": 0,
            "created_at": _now(),
        }
        self.tasks[task["id"]] = task
        return deepcopy(task)

    def update_task(self, task_id: str, **fields: Any) -> None:
        self.tasks[task_id].update(fields)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        return deepcopy(task) if task else None

    def list_active_tasks(self) -> list[dict[str, Any]]:
        return sorted(
            (
                deepcopy(task)
                for task in self.tasks.values()
                if task.get("type") == "collection" and task.get("status") in {"queued", "running"}
            ),
            key=lambda task: task.get("created_at") or "",
        )

    def upsert_announcement(self, announcement: dict[str, Any]) -> bool:
        key = announcement["announcement_id"]
        previous = self.announcements.get(key)
        saved = deepcopy(previous) if previous else {}
        for field in _RAW_FIELDS:
            if field in announcement:
                saved[field] = announcement[field]
        saved["collected_at"] = _now()
        self.announcements[key] = saved
        return previous != saved

    def get_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        announcement = self.announcements.get(announcement_id)
        return deepcopy(announcement) if announcement else None

    def list_results(self) -> list[dict[str, Any]]:
        return sorted(
            (deepcopy(announcement) for announcement in self.announcements.values()),
            key=lambda announcement: announcement.get("announcement_time") or 0,
            reverse=True,
        )

    def close(self) -> None:
        return None


class SQLiteRepository:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        database_path = Path(path)
        self.read_only = read_only
        if read_only:
            database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
            self._connection = sqlite3.connect(database_uri, uri=True, check_same_thread=False)
        else:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        if read_only:
            return
        with self._lock:
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
                "CREATE INDEX IF NOT EXISTS idx_announcements_time "
                "ON announcements (announcement_time)"
            )
            self._connection.commit()

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        return json.loads(value)

    def _get_announcement_unlocked(self, announcement_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload FROM announcements WHERE announcement_id = ?", (announcement_id,)
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
                str(announcement.get("analysis_status") or "collected"),
                announcement.get("announcement_time"),
                self._encode(announcement),
            ),
        )

    def create_task(self, start_date: str = "", end_date: str = "", *, task_type: str = "collection") -> dict[str, Any]:
        task = {
            "id": uuid4().hex,
            "type": task_type,
            "start_date": start_date,
            "end_date": end_date,
            "status": "queued",
            "processed": 0,
            "failures": 0,
            "created_at": _now(),
        }
        with self._lock:
            self._connection.execute("INSERT INTO tasks (id, payload) VALUES (?, ?)", (task["id"], self._encode(task)))
            self._connection.commit()
        return deepcopy(task)

    def update_task(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return
            task = self._decode(row["payload"])
            task.update(deepcopy(fields))
            self._connection.execute("UPDATE tasks SET payload = ? WHERE id = ?", (self._encode(task), task_id))
            self._connection.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._decode(row["payload"]) if row else None

    def list_active_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM tasks").fetchall()
        return sorted(
            (
                task
                for row in rows
                if (task := self._decode(row["payload"])).get("type") == "collection"
                and task.get("status") in {"queued", "running"}
            ),
            key=lambda task: task.get("created_at") or "",
        )

    def recover_interrupted_tasks(self) -> int:
        recovered = 0
        with self._lock:
            rows = self._connection.execute("SELECT id, payload FROM tasks").fetchall()
            for row in rows:
                task = self._decode(row["payload"])
                if task.get("type") != "collection" or task.get("status") != "running":
                    continue
                task["status"] = "queued"
                self._connection.execute("UPDATE tasks SET payload = ? WHERE id = ?", (self._encode(task), row["id"]))
                recovered += 1
            if recovered:
                self._connection.commit()
        return recovered

    def upsert_announcement(self, announcement: dict[str, Any]) -> bool:
        key = announcement["announcement_id"]
        with self._lock:
            previous = self._get_announcement_unlocked(key)
            saved = deepcopy(previous) if previous else {}
            for field in _RAW_FIELDS:
                if field in announcement:
                    saved[field] = announcement[field]
            saved["collected_at"] = _now()
            self._save_announcement_unlocked(saved)
            self._connection.commit()
        return previous != saved

    def get_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        with self._lock:
            announcement = self._get_announcement_unlocked(announcement_id)
        return deepcopy(announcement) if announcement else None

    def list_results(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM announcements").fetchall()
        return sorted(
            (self._decode(row["payload"]) for row in rows),
            key=lambda announcement: announcement.get("announcement_time") or 0,
            reverse=True,
        )

    def close(self) -> None:
        self._connection.close()
