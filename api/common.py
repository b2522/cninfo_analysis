# -*- coding: utf-8 -*-
"""
巨潮公告抓取 / 解析 / 缓存 —— 纯逻辑层（无 Flask / 无 HTTP）。

被 api/announcements.py 与 api/refresh.py 两个 Vercel Serverless Function 共用。
拆成独立模块是为了避免两个 function 互相 import 同一目录下的其它 function
（Vercel 对每个 function 单独打包，跨 function import 不可靠）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import requests

# ---------------------------------------------------------------- 常量

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_HOST = "https://static.cninfo.com.cn/"
PAGE_SIZE = 30
MAX_PAGES = 5                                           # Vercel 端手动刷新/兜底取前 5 页
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


def kv_set_snapshot(snapshot: Dict[str, Any]) -> bool:
    if not (KV_URL and KV_TOKEN):
        return False
    try:
        payload = json.dumps(snapshot, ensure_ascii=False)
        resp = requests.post("{0}/set/{1}".format(KV_URL, KV_KEY),
                             data=payload.encode("utf-8"),
                             headers={"Authorization": "Bearer " + KV_TOKEN}, timeout=20)
        resp.raise_for_status()
        return True
    except Exception:                                    # noqa: BLE001
        return False


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


def fetch_live(se_date: str | None = None, pages: int | None = None) -> List[Dict[str, str]]:
    se_date = se_date if se_date is not None else ""          # 默认不限日期
    pages = pages or MAX_PAGES
    seen: set[Tuple[str, str, str]] = set()
    merged: List[Dict[str, str]] = []
    with requests.Session() as sess:
        for page in range(1, pages + 1):
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


def refresh_now(se_date: str | None = None, pages: int | None = None) -> Dict[str, Any]:
    """触发一次实时回源抓取，写入 /tmp 热缓存；若配置了 KV 则同步推送。

    pages 不传时使用 MAX_PAGES(5)；Vercel 每小时 cron 传 pages=10 以匹配「定时 10 页」。
    """
    se_date = se_date if se_date is not None else ""
    rows = fetch_live(se_date, pages)
    if rows:
        cache_to_tmp(rows)
        # 用「今天 / 明天 / 近2 / 近3」四个桶重建一份完整快照推到 KV
        if KV_URL and KV_TOKEN:
            snapshot: Dict[str, Any] = {
                "last_scraped_at": now_cst().isoformat(timespec="seconds"),
                "data": rows,
                "ranges": {},
            }
            for rk in DATE_RANGES:
                _, _, s, e = resolve_range(rk)
                snapshot["ranges"][rk] = {
                    "label": DATE_RANGES[rk]["label"],
                    "start_date": s, "end_date": e,
                    "count": len(in_range(rows, s, e)),
                    "data": in_range(rows, s, e),
                }
            kv_set_snapshot(snapshot)
    return {
        "ok": True,
        "se_date": se_date,
        "fetched": len(rows),
        "scraped_at": now_cst().isoformat(timespec="seconds"),
        "kv_pushed": bool(KV_URL and KV_TOKEN and rows),
        "note": "Serverless /tmp 缓存已更新（非持久化，跨实例不共享）；KV 已配置时同步推送。",
    }
