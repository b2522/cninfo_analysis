# -*- coding: utf-8 -*-
"""
Vercel Serverless Function —— GET /api/announcements?date_range=today

本文件为「自包含」实现：不依赖仓库根目录的 main.py，确保 Vercel 打包后一定可运行。

数据来源按优先级降级 (解决 Serverless 无持久磁盘的问题)：
  1. Vercel KV / Upstash Redis  —— 环境变量 KV_REST_API_URL + KV_REST_API_TOKEN
     GitHub Actions 每小时把快照推到 KV，函数直接读，无需重新部署。【推荐】
  2. 仓库内 data/snapshot.json —— Actions 每小时 commit，随部署一起分发（只读）。
  3. 仓库内 announcements.db   —— 同上，只读 SQLite。
  4. /tmp/announcements.db     —— 同一个 Lambda 实例内的热缓存。
  5. 实时回源抓取 cninfo       —— 兜底，前 2 页，结果写入 /tmp 缓存。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

import requests

# ---------------------------------------------------------------- 常量

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_HOST = "https://static.cninfo.com.cn/"
PAGE_SIZE = 30
MAX_PAGES = 5
WORK_START_HOUR, WORK_END_HOUR = 8, 16

SNAPSHOT_JSON = os.path.join(ROOT, "data", "snapshot.json")
BUNDLED_DB = os.path.join(ROOT, "announcements.db")
TMP_DB = "/tmp/announcements.db"
TMP_TTL_SECONDS = 900                                   # /tmp 缓存 15 分钟

KV_URL = os.environ.get("KV_REST_API_URL", "").rstrip("/")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")
KV_KEY = os.environ.get("KV_SNAPSHOT_KEY", "cninfo:snapshot")

DATE_RANGES: Dict[str, Dict[str, Any]] = {
    "tomorrow": {"label": "明天", "offset_start": 1, "offset_end": 1},
    "today": {"label": "今天", "offset_start": 0, "offset_end": 0},
    "last2": {"label": "近2天", "offset_start": -1, "offset_end": 0},
    "last3": {"label": "近3天", "offset_start": -2, "offset_end": 0},
}
DEFAULT_RANGE = "today"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}

# ---------------------------------------------------------------- 工具


def now_cst() -> datetime:
    return datetime.now(CST)


def resolve_range(key: str) -> Tuple[str, str, str, str]:
    key = (key or DEFAULT_RANGE).strip().lower()
    cfg = DATE_RANGES.get(key)
    if cfg is None:
        key, cfg = DEFAULT_RANGE, DATE_RANGES[DEFAULT_RANGE]
    base = now_cst().date()
    return (key, cfg["label"],
            (base + timedelta(days=cfg["offset_start"])).isoformat(),
            (base + timedelta(days=cfg["offset_end"])).isoformat())


def build_se_date(ref: datetime | None = None) -> str:
    ref = ref or now_cst()
    today: date = ref.date()
    if WORK_START_HOUR <= ref.hour < WORK_END_HOUR:
        return "{0}~{1}".format(today.isoformat(), (today + timedelta(days=1)).isoformat())
    return "{0}~{0}".format(today.isoformat())


_TAG_RE = re.compile(r"<[^>]+>")


def clean_title(raw: str) -> str:
    if not raw:
        return ""
    return " ".join(_TAG_RE.sub("", raw).replace("&amp;", "&").replace("&nbsp;", " ").split())


def to_iso(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, CST).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return now_cst().isoformat(timespec="seconds")


def in_range(rows: List[Dict[str, str]], start: str, end: str) -> List[Dict[str, str]]:
    out = [r for r in rows if start <= (r.get("publish_time") or "")[:10] <= end]
    out.sort(key=lambda r: (r.get("publish_time") or "", r.get("stock_code") or ""), reverse=True)
    return out


# ---------------------------------------------------------------- 数据源 1: KV


def kv_get_snapshot() -> Dict[str, Any] | None:
    if not (KV_URL and KV_TOKEN):
        return None
    try:
        resp = requests.get("{0}/get/{1}".format(KV_URL, KV_KEY),
                            headers={"Authorization": "Bearer " + KV_TOKEN}, timeout=6)
        resp.raise_for_status()
        raw = resp.json().get("result")
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:                                    # noqa: BLE001
        return None


# ---------------------------------------------------------------- 数据源 2/3: 仓库内文件


def load_snapshot_file() -> Dict[str, Any] | None:
    if not os.path.isfile(SNAPSHOT_JSON):
        return None
    try:
        with open(SNAPSHOT_JSON, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                    # noqa: BLE001
        return None


def read_sqlite(path: str, start: str, end: str) -> List[Dict[str, str]] | None:
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect("file:{0}?mode=ro".format(path), uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT stock_code, short_name, title, publish_time, pdf_url FROM announcements "
            "WHERE substr(publish_time,1,10) BETWEEN ? AND ? "
            "ORDER BY publish_time DESC, stock_code ASC", (start, end)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:                                    # noqa: BLE001
        return None


# ---------------------------------------------------------------- 数据源 5: 实时回源


def fetch_live(se_date: str | None = None) -> List[Dict[str, str]]:
    se_date = se_date if se_date is not None else ""          # 默认不限日期
    seen: set[Tuple[str, str, str]] = set()
    merged: List[Dict[str, str]] = []
    with requests.Session() as sess:
        for page in range(1, MAX_PAGES + 1):
            payload = {
                "pageNum": page, "pageSize": PAGE_SIZE, "column": "szse",
                "tabName": "fulltext", "plate": "", "stock": "", "searchkey": "",
                "secid": "", "category": "", "trade": "", "seDate": se_date,
                "sortName": "", "sortType": "", "isHLtitle": "true",
            }
            try:
                r = sess.post(CNINFO_QUERY_URL, data=payload, headers=HEADERS, timeout=15)
                r.raise_for_status()
                items = r.json().get("announcements") or []
            except Exception:                            # noqa: BLE001
                items = []
            for it in items:
                adj = (it.get("adjunctUrl") or "").lstrip("/")
                rec = {
                    "stock_code": (it.get("secCode") or "").strip(),
                    "short_name": (it.get("secName") or "").strip(),
                    "title": clean_title(it.get("announcementTitle") or ""),
                    "publish_time": to_iso(it.get("announcementTime")),
                    "pdf_url": (PDF_HOST + adj) if adj else "",
                }
                if not rec["stock_code"] or not rec["title"]:
                    continue
                key = (rec["stock_code"], rec["title"], rec["publish_time"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(rec)
    return merged


def cache_to_tmp(rows: List[Dict[str, str]]) -> None:
    try:
        conn = sqlite3.connect(TMP_DB, timeout=5)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS announcements ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT NOT NULL,"
            " short_name TEXT, title TEXT NOT NULL, publish_time TEXT NOT NULL,"
            " pdf_url TEXT, UNIQUE(stock_code, title, publish_time));"
            "CREATE INDEX IF NOT EXISTS idx_publish_time ON announcements(publish_time);")
        conn.executemany(
            "INSERT OR IGNORE INTO announcements "
            "(stock_code, short_name, title, publish_time, pdf_url) VALUES (?,?,?,?,?)",
            [(r["stock_code"], r["short_name"], r["title"], r["publish_time"], r["pdf_url"])
             for r in rows])
        conn.commit()
        conn.close()
    except Exception:                                    # noqa: BLE001
        pass


def tmp_is_fresh() -> bool:
    try:
        return os.path.isfile(TMP_DB) and (time.time() - os.path.getmtime(TMP_DB)) < TMP_TTL_SECONDS
    except OSError:
        return False


# ---------------------------------------------------------------- 编排


def build_payload(range_key: str) -> Dict[str, Any]:
    key, label, start, end = resolve_range(range_key)
    rows: List[Dict[str, str]] | None = None
    source = "none"
    last_scraped_at = None

    snap = kv_get_snapshot()
    if snap:
        source, last_scraped_at = "vercel-kv", snap.get("last_scraped_at")
        bucket = (snap.get("ranges") or {}).get(key)
        rows = bucket.get("data") if bucket else in_range(snap.get("data") or [], start, end)

    if not rows:
        snap = load_snapshot_file()
        if snap:
            bucket = (snap.get("ranges") or {}).get(key)
            candidate = bucket.get("data") if bucket else in_range(snap.get("data") or [], start, end)
            if candidate:
                rows, source = candidate, "snapshot.json"
                last_scraped_at = snap.get("last_scraped_at")

    if not rows:
        candidate = read_sqlite(BUNDLED_DB, start, end)
        if candidate:
            rows, source = candidate, "bundled-sqlite"

    if not rows and tmp_is_fresh():
        candidate = read_sqlite(TMP_DB, start, end)
        if candidate:
            rows, source = candidate, "tmp-cache"

    if not rows:
        live = fetch_live()
        if live:
            cache_to_tmp(live)
            rows, source = in_range(live, start, end), "live-cninfo"
            last_scraped_at = now_cst().isoformat(timespec="seconds")

    rows = rows or []
    return {
        "ok": True,
        "date_range": key,
        "label": label,
        "start_date": start,
        "end_date": end,
        "count": len(rows),
        "source": source,
        "last_scraped_at": last_scraped_at,
        "generated_at": now_cst().isoformat(timespec="seconds"),
        "data": rows,
    }


# ---------------------------------------------------------------- Vercel handler


class handler(BaseHTTPRequestHandler):                   # noqa: N801  (Vercel 约定名)

    def _send(self, status: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:                            # noqa: N802
        try:
            qs = parse_qs(urlparse(self.path).query)
            range_key = (qs.get("date_range") or [DEFAULT_RANGE])[0]
            self._send(200, build_payload(range_key))
        except Exception as exc:                         # noqa: BLE001
            self._send(500, {"ok": False, "error": str(exc), "data": [], "count": 0})

    def do_OPTIONS(self) -> None:                        # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return
