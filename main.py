#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巨潮资讯公告聚合 —— 单文件后端 (抓取 + 存储 + API + 前端托管)

用法:
    python main.py                 启动服务 (默认 http://localhost:5000)
    python main.py serve --port 8000
    python main.py scrape          只执行一次抓取后退出 (供 GitHub Actions 调用)
    python main.py export-html     把内嵌前端导出为 index.html
    python main.py export-json     把当前库内数据导出为 data/snapshot.json (供 Vercel/KV 使用)

依赖: requests + Flask (sqlite3 为标准库)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

import requests

# --------------------------------------------------------------------------------------
# 全局配置
# --------------------------------------------------------------------------------------

CST = timezone(timedelta(hours=8))                     # 东八区
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_HOST = "https://static.cninfo.com.cn/"

PAGE_SIZE = 30
MAX_PAGES = 5                                          # 手动「重新抓取」按钮抓取页数
SCHEDULE_MAX_PAGES = 10                               # 每小时定时任务抓取页数
COLUMN = "szse"
TAB_NAME = "fulltext"

# 上班时段 (UTC+8)：08:00 <= now < 16:00 时抓取/展示 今天~明天，其余时段仅今天
WORK_START_HOUR = 8
WORK_END_HOUR = 16

SCRAPE_INTERVAL_SECONDS = 3600                         # 每小时抓取一次

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}

# 数据库位置：Vercel 等只读文件系统下自动落到 /tmp
_DEFAULT_DB = os.path.join(BASE_DIR, "announcements.db")


def resolve_db_path() -> str:
    """优先使用环境变量 DB_PATH；serverless 只读环境自动降级到 /tmp。"""
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp/announcements.db"
    return _DEFAULT_DB


DB_PATH = resolve_db_path()

# --------------------------------------------------------------------------------------
# 日期区间定义
#   明天    : 明天当天
#   今天    : 今天当天
#   近2天   : 昨天 ~ 今天   (含今天，向前推 2 个自然日)
#   近3天   : 前天 ~ 今天   (含今天，向前推 3 个自然日)
# 若想把「近N天」改成向后看，只改这里即可。
# --------------------------------------------------------------------------------------

DATE_RANGES: Dict[str, Dict[str, Any]] = {
    "tomorrow": {"label": "明天", "offset_start": 1, "offset_end": 1},
    "today": {"label": "今天", "offset_start": 0, "offset_end": 0},
    "last2": {"label": "近2天", "offset_start": -1, "offset_end": 0},
    "last3": {"label": "近3天", "offset_start": -2, "offset_end": 0},
}
DEFAULT_RANGE = "today"


def now_cst() -> datetime:
    return datetime.now(CST)


def today_cst() -> date:
    return now_cst().date()


def resolve_range(key: str) -> Tuple[str, str, str, str]:
    """返回 (规范化key, 中文标签, 起始日期, 结束日期)。"""
    key = (key or DEFAULT_RANGE).strip().lower()
    cfg = DATE_RANGES.get(key)
    if cfg is None:
        key = DEFAULT_RANGE
        cfg = DATE_RANGES[key]
    base = today_cst()
    start = base + timedelta(days=cfg["offset_start"])
    end = base + timedelta(days=cfg["offset_end"])
    return key, cfg["label"], start.isoformat(), end.isoformat()


def build_se_date(ref: datetime | None = None) -> str:
    """
    动态生成 seDate:
      UTC+8 08:00–16:00  ->  今天~明天   (例: 2026-08-05~2026-08-06)
      其余时段            ->  今天~今天   (例: 2026-08-05~2026-08-05)
    """
    ref = ref or now_cst()
    today = ref.date()
    if WORK_START_HOUR <= ref.hour < WORK_END_HOUR:
        return "{0}~{1}".format(today.isoformat(), (today + timedelta(days=1)).isoformat())
    return "{0}~{0}".format(today.isoformat())


# --------------------------------------------------------------------------------------
# 抓取模块
# --------------------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def clean_title(raw: str) -> str:
    """去掉搜索高亮标签与多余空白。"""
    if not raw:
        return ""
    text = _TAG_RE.sub("", raw)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    return " ".join(text.split())


def to_iso(ms: Any) -> str:
    """巨潮的 announcementTime 是毫秒时间戳，按东八区转 ISO 字符串。"""
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, CST).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return now_cst().isoformat(timespec="seconds")


def normalize(item: Dict[str, Any]) -> Dict[str, str]:
    adjunct = (item.get("adjunctUrl") or "").lstrip("/")
    return {
        "stock_code": (item.get("secCode") or "").strip(),
        "short_name": (item.get("secName") or "").strip(),
        "title": clean_title(item.get("announcementTitle") or ""),
        "publish_time": to_iso(item.get("announcementTime")),
        "pdf_url": (PDF_HOST + adjunct) if adjunct else "",
    }


def fetch_page(page_num: int, se_date: str, session: requests.Session,
               retries: int = 3, timeout: int = 25) -> List[Dict[str, Any]]:
    payload = {
        "pageNum": page_num,
        "pageSize": PAGE_SIZE,
        "column": COLUMN,
        "tabName": TAB_NAME,
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": se_date,
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.post(CNINFO_QUERY_URL, data=payload,
                                headers=REQUEST_HEADERS, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
            return body.get("announcements") or []
        except Exception as exc:                        # noqa: BLE001
            last_err = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    print("[warn] page {0} 抓取失败: {1}".format(page_num, last_err), file=sys.stderr)
    return []


def fetch_announcements(se_date: str | None = None,
                        pages: int | None = None) -> List[Dict[str, str]]:
    """抓取前 pages 页并按 (代码, 标题, 时间) 三元组去重。

    se_date 默认空串 -> 巨潮不限日期，直接返回最新公告；也可显式传入
    如 '2026-08-05~2026-08-06' 限定区间。
    pages 默认 MAX_PAGES（手动按钮 5 页）；定时任务传 SCHEDULE_MAX_PAGES（10 页）。
    """
    se_date = se_date if se_date is not None else ""
    pages = pages or MAX_PAGES
    seen: set[Tuple[str, str, str]] = set()
    merged: List[Dict[str, str]] = []

    with requests.Session() as session:
        for page in range(1, pages + 1):
            raw_items = fetch_page(page, se_date, session)
            for raw in raw_items:
                rec = normalize(raw)
                if not rec["stock_code"] or not rec["title"]:
                    continue
                key = (rec["stock_code"], rec["title"], rec["publish_time"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(rec)
            if page < MAX_PAGES:
                time.sleep(0.4)                          # 轻微限速，避免被风控

    merged.sort(key=lambda r: (r["publish_time"], r["stock_code"]), reverse=True)
    return merged


# --------------------------------------------------------------------------------------
# SQLite 持久化
# --------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code   TEXT NOT NULL,
    short_name   TEXT,
    title        TEXT NOT NULL,
    publish_time TEXT NOT NULL,
    pdf_url      TEXT,
    UNIQUE (stock_code, title, publish_time)
);
CREATE INDEX IF NOT EXISTS idx_publish_time ON announcements (publish_time);
CREATE INDEX IF NOT EXISTS idx_stock_code   ON announcements (stock_code);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def save_records(records: Iterable[Dict[str, str]], db_path: str | None = None) -> int:
    """写入并返回新增条数 (依赖 UNIQUE 约束做幂等去重)。"""
    rows = [(r["stock_code"], r["short_name"], r["title"], r["publish_time"], r["pdf_url"])
            for r in records]
    if not rows:
        return 0
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO announcements "
            "(stock_code, short_name, title, publish_time, pdf_url) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        after = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_scraped_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now_cst().isoformat(timespec="seconds"),),
        )
        conn.commit()
    return after - before


def query_by_date(start_date: str, end_date: str, db_path: str | None = None) -> List[Dict[str, str]]:
    sql = (
        "SELECT stock_code, short_name, title, publish_time, pdf_url "
        "FROM announcements "
        "WHERE substr(publish_time, 1, 10) BETWEEN ? AND ? "
        "ORDER BY publish_time DESC, stock_code ASC"
    )
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, (start_date, end_date)).fetchall()]


def get_meta(key: str, db_path: str | None = None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def prune_old(keep_days: int = 30, db_path: str | None = None) -> int:
    """删除 keep_days 之前的记录，避免库文件无限增长。返回删除条数。"""
    if keep_days <= 0:
        return 0
    cutoff = (today_cst() - timedelta(days=keep_days)).isoformat()
    with connect(db_path) as conn:
        cur = conn.execute("DELETE FROM announcements WHERE substr(publish_time,1,10) < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount or 0
    return deleted


def run_scrape(db_path: str | None = None, verbose: bool = True,
               keep_days: int = 0, pages: int | None = None) -> Dict[str, Any]:
    init_db(db_path)
    se_date = ""                              # 不限日期，直接取巨潮最新公告
    pages = pages if pages is not None else SCHEDULE_MAX_PAGES
    records = fetch_announcements(se_date, pages=pages)
    inserted = save_records(records, db_path)
    pruned = prune_old(keep_days, db_path) if keep_days else 0
    result = {
        "se_date": se_date,
        "fetched": len(records),
        "inserted": inserted,
        "pruned": pruned,
        "scraped_at": now_cst().isoformat(timespec="seconds"),
        "db_path": db_path or DB_PATH,
    }
    if verbose:
        print("[scrape] seDate={se_date} 抓取={fetched} 新增={inserted} 清理={pruned} 时间={scraped_at}"
              .format(**result))
    return result


# --------------------------------------------------------------------------------------
# 前端页面 (内联 HTML/CSS/JS，无任何框架依赖)
# --------------------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>巨潮资讯 · 公告聚合</title>
<style>
  :root {
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Courier New", monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
            "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --pad-x: 12px;
    --border: #e3e6ea;
    --head-bg: #f5f5f5;
    --link: #1a5fb4;
    --text: #24292f;
    --muted: #6b7280;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    font-size: 14px;
    color: var(--text);
    background: #fafbfc;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 24px 20px 48px; }

  header.top { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
  header.top h1 { font-size: 20px; margin: 0; font-weight: 700; letter-spacing: .5px; }
  header.top .sub { font-size: 12px; color: var(--muted); }

  .toolbar {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    margin: 16px 0 12px; padding: 12px 14px;
    background: #fff; border: 1px solid var(--border); border-radius: 8px;
  }
  .toolbar label { font-weight: 600; font-size: 13px; }
  .toolbar select {
    font-family: var(--sans); font-size: 14px; padding: 6px 30px 6px 10px;
    border: 1px solid #ccd1d7; border-radius: 6px; background: #fff; color: var(--text);
    cursor: pointer; appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path d='M2 4l4 4 4-4' fill='none' stroke='%23555' stroke-width='1.6'/></svg>");
    background-repeat: no-repeat; background-position: right 9px center;
  }
  .toolbar select:focus { outline: 2px solid #cfe0f5; border-color: var(--link); }
  .toolbar button {
    font-family: var(--sans); font-size: 13px; padding: 6px 14px; cursor: pointer;
    border: 1px solid #ccd1d7; border-radius: 6px; background: #fff; color: var(--text);
  }
  .toolbar button:hover:not(:disabled) { background: #f0f3f6; border-color: #b6bcc4; }
  .toolbar button:disabled { opacity: .55; cursor: not-allowed; }
  .toolbar .spacer { flex: 1 1 auto; }
  .toolbar .meta { font-size: 12px; color: var(--muted); font-family: var(--mono); }

  .table-shell {
    background: #fff; border: 1px solid var(--border); border-radius: 8px;
    overflow-x: auto;
  }
  table.grid {
    width: 100%; min-width: 760px;
    border-collapse: collapse; table-layout: fixed;
  }
  /* 固定列宽：切换筛选时列宽不变 */
  col.c-code  { font-family: var(--mono); width: calc(8ch  + var(--pad-x) * 2); }
  col.c-name  {                           width: calc(6em  + var(--pad-x) * 2); }
  col.c-title {                           width: auto; }
  col.c-time  { font-family: var(--mono); width: calc(16ch + var(--pad-x) * 2); }

  table.grid th, table.grid td {
    padding: 9px var(--pad-x);
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  table.grid thead th {
    position: sticky; top: 0; z-index: 2;
    text-align: center; font-weight: 700; background: var(--head-bg);
    border-bottom: 1px solid #dcdfe3; white-space: nowrap;
  }
  table.grid tbody tr:nth-child(even) { background: #fcfcfd; }
  table.grid tbody tr:hover { background: #f2f7fd; }
  table.grid tbody tr:last-child td { border-bottom: none; }

  td.code { font-family: var(--mono); text-align: center; letter-spacing: .3px; }
  td.name { text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  td.title { text-align: left; word-break: break-word; line-height: 1.5; }
  td.time { font-family: var(--mono); text-align: center; color: #4b5563; white-space: nowrap; }

  td.title a { color: var(--link); text-decoration: none; }
  td.title a:hover { text-decoration: underline; }
  td.title a:visited { color: #6b4fa0; }

  .state { padding: 40px 16px; text-align: center; color: var(--muted); font-size: 14px; }
  .state.err { color: #b3261e; }
  footer.tip { margin-top: 14px; font-size: 12px; color: var(--muted); line-height: 1.7; }

  @media (max-width: 720px) {
    .wrap { padding: 16px 12px 32px; }
    .toolbar { gap: 8px; }
    .toolbar .spacer { flex-basis: 100%; height: 0; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <h1>巨潮资讯 · 公告聚合</h1>
    <span class="sub">数据来源 cninfo.com.cn ｜ 每小时自动更新</span>
  </header>

  <div class="toolbar">
    <label for="range">日期筛选</label>
    <select id="range">
      <option value="tomorrow">明天</option>
      <option value="today">今天</option>
      <option value="last2">近2天</option>
      <option value="last3">近3天</option>
    </select>
    <span class="meta" id="rangeHint">—</span>
    <span class="spacer"></span>
    <span class="meta" id="updatedAt">—</span>
    <button id="refreshBtn" type="button" title="重新抓取巨潮最新公告">重新抓取</button>
  </div>

  <div class="table-shell">
    <table class="grid">
      <colgroup>
        <col class="c-code">
        <col class="c-name">
        <col class="c-title">
        <col class="c-time">
      </colgroup>
      <thead>
        <tr>
          <th>代码</th>
          <th>简称</th>
          <th>公告标题</th>
          <th>公告时间</th>
        </tr>
      </thead>
      <tbody id="tbody">
        <tr><td colspan="4"><div class="state">加载中…</div></td></tr>
      </tbody>
    </table>
  </div>

  <footer class="tip">
    「近2天 / 近3天」= 含今天在内向前推算的自然日；「明天」用于查看提前披露的次日公告。<br>
    公告标题为原文 PDF 链接，点击在新标签页打开。
  </footer>

</div>

<script>
(function () {
  "use strict";

  var API = "/api/announcements";
  var sel = document.getElementById("range");
  var tbody = document.getElementById("tbody");
  var rangeHint = document.getElementById("rangeHint");
  var updatedAt = document.getElementById("updatedAt");
  var refreshBtn = document.getElementById("refreshBtn");
  var inflight = 0;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // "2026-08-05T11:42:12+08:00" -> "2026-08-05 11:42"
  function fmtTime(iso) {
    if (!iso) return "";
    var m = /^(\\d{4}-\\d{2}-\\d{2})[T ](\\d{2}:\\d{2})/.exec(iso);
    return m ? (m[1] + " " + m[2]) : String(iso).slice(0, 16).replace("T", " ");
  }

  function showState(msg, isErr) {
    tbody.innerHTML = '<tr><td colspan="4"><div class="state' +
      (isErr ? " err" : "") + '">' + esc(msg) + "</div></td></tr>";
  }

  function render(rows) {
    if (!rows || !rows.length) { showState("该时间段暂无公告数据", false); return; }
    var html = "";
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var title = esc(r.title);
      var cell = r.pdf_url
        ? '<a href="' + esc(r.pdf_url) + '" target="_blank" rel="noopener noreferrer"' +
          ' title="' + title + '">' + title + "</a>"
        : title;
      html += "<tr>" +
        '<td class="code">' + esc(r.stock_code) + "</td>" +
        '<td class="name" title="' + esc(r.short_name) + '">' + esc(r.short_name) + "</td>" +
        '<td class="title">' + cell + "</td>" +
        '<td class="time">' + esc(fmtTime(r.publish_time)) + "</td>" +
      "</tr>";
    }
    tbody.innerHTML = html;   // 只替换 tbody，colgroup 不动 -> 列宽保持不变
  }

  function load() {
    var token = ++inflight;
    var key = sel.value;
    showState("加载中…", false);
    fetch(API + "?date_range=" + encodeURIComponent(key), {
      headers: { "Accept": "application/json" }, cache: "no-store"
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (token !== inflight) return;               // 丢弃过期响应
        rangeHint.textContent = data.start_date === data.end_date
          ? data.start_date + " · " + data.count + " 条"
          : data.start_date + " ~ " + data.end_date + " · " + data.count + " 条";
        updatedAt.textContent = data.last_scraped_at
          ? "更新于 " + fmtTime(data.last_scraped_at) : "";
        render(data.data);
      })
      .catch(function (err) {
        if (token !== inflight) return;
        showState("加载失败：" + err.message + "（请确认通过 http://localhost:5000 访问本页）", true);
      });
  }

  sel.addEventListener("change", load);

  refreshBtn.addEventListener("click", function () {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "抓取中…";
    fetch("/api/refresh", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        refreshBtn.disabled = false;
        if (j && j.ok) {
          refreshBtn.textContent = "重新抓取";
          load();
        } else {
          refreshBtn.textContent = "抓取失败";
          setTimeout(function () { refreshBtn.textContent = "重新抓取"; }, 2500);
          showState("抓取失败：" + ((j && j.error) || "未知错误"), true);
        }
      })
      .catch(function () {
        refreshBtn.disabled = false;
        refreshBtn.textContent = "抓取失败";
        setTimeout(function () { refreshBtn.textContent = "重新抓取"; }, 2500);
        showState("抓取失败：无法连接后端。请确认本页是通过 http://localhost:5000 访问" +
                  "（或已部署到 Vercel），而非直接打开 html 文件 / 预览面板。", true);
      });
  });

  // 初始默认：明天有数据则显示明天，否则显示今天
  function pickDefault() {
    fetch(API + "?date_range=tomorrow", {
      headers: { "Accept": "application/json" }, cache: "no-store"
    })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        sel.value = (data && data.count > 0) ? "tomorrow" : "today";
        load();
      })
      .catch(function () {
        sel.value = "today";
        load();
      });
  }

  pickDefault();
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------------------
# Flask 应用
# --------------------------------------------------------------------------------------

def create_app() -> "Flask":                             # type: ignore[name-defined]
    from flask import Flask, jsonify, request, Response

    app = Flask(__name__, static_folder=None)
    init_db()

    @app.after_request
    def _no_cache(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.get("/")
    def index() -> "Response":
        disk = os.path.join(BASE_DIR, "index.html")
        if os.path.isfile(disk):
            with open(disk, "r", encoding="utf-8") as fh:
                html = fh.read()
        else:
            html = INDEX_HTML                             # 单文件运行时的内嵌回退
        return Response(html, mimetype="text/html; charset=utf-8")

    @app.get("/api/announcements")
    def api_announcements():
        key, label, start, end = resolve_range(request.args.get("date_range", DEFAULT_RANGE))
        rows = query_by_date(start, end)
        return jsonify({
            "ok": True,
            "date_range": key,
            "label": label,
            "start_date": start,
            "end_date": end,
            "count": len(rows),
            "last_scraped_at": get_meta("last_scraped_at"),
            "generated_at": now_cst().isoformat(timespec="seconds"),
            "source": "sqlite",
            "data": rows,
        })

    @app.route("/api/refresh", methods=["POST", "GET"])
    def api_refresh():
        try:
            # 手动「重新抓取」按钮：抓 MAX_PAGES(5) 页
            return jsonify({"ok": True, **run_scrape(verbose=False, pages=MAX_PAGES)})
        except Exception as exc:                          # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/health")
    def api_health():
        with connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        return jsonify({
            "ok": True,
            "db_path": DB_PATH,
            "total": total,
            "se_date_now": "",
            "server_time_cst": now_cst().isoformat(timespec="seconds"),
            "last_scraped_at": get_meta("last_scraped_at"),
        })

    return app


def start_scheduler(interval: int = SCRAPE_INTERVAL_SECONDS) -> threading.Thread:
    """后台线程：启动即抓一次，随后每 interval 秒抓一次。"""
    def loop() -> None:
        while True:
            try:
                run_scrape()
            except Exception as exc:                      # noqa: BLE001
                print("[scheduler] 抓取异常: {0}".format(exc), file=sys.stderr)
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="cninfo-scraper", daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    app = create_app()
    if not args.no_scheduler and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        start_scheduler(args.interval)
    print("=" * 62)
    print(" 巨潮公告聚合服务已启动  ->  http://{0}:{1}".format(
        "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host, args.port))
    print(" 数据库: {0}".format(DB_PATH))
    print(" 抓取范围: 不限日期（seDate 为空），取巨潮最新公告")
    print("=" * 62)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)
    return 0


def cmd_scrape(args: argparse.Namespace) -> int:
    run_scrape(db_path=args.db, keep_days=args.keep_days)
    if args.json_out:
        export_snapshot(args.json_out, db_path=args.db)
    return 0                                             # 非交易日无数据也不算失败


def export_snapshot(path: str, db_path: str | None = None) -> Dict[str, Any]:
    """导出全部区间快照，供 Vercel KV / 静态托管使用。"""
    payload: Dict[str, Any] = {
        "generated_at": now_cst().isoformat(timespec="seconds"),
        "last_scraped_at": get_meta("last_scraped_at", db_path),
        "ranges": {},
    }
    for key in DATE_RANGES:
        k, label, start, end = resolve_range(key)
        rows = query_by_date(start, end, db_path)
        payload["ranges"][k] = {
            "label": label, "start_date": start, "end_date": end,
            "count": len(rows), "data": rows,
        }
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("[export] 快照已写入 {0}".format(path))
    return payload


def cmd_export_html(args: argparse.Namespace) -> int:
    out = args.out or os.path.join(BASE_DIR, "index.html")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(INDEX_HTML)
    print("[export] 前端页面已写入 {0}".format(out))
    return 0


def cmd_export_json(args: argparse.Namespace) -> int:
    export_snapshot(args.out or os.path.join(BASE_DIR, "data", "snapshot.json"), db_path=args.db)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="巨潮资讯公告聚合 (抓取 + API + 前端)")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动 Web 服务 (默认命令)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    p_serve.add_argument("--debug", action="store_true")
    p_serve.add_argument("--no-scheduler", action="store_true", help="不启动小时级定时抓取")
    p_serve.add_argument("--interval", type=int, default=SCRAPE_INTERVAL_SECONDS)
    p_serve.set_defaults(func=cmd_serve)

    p_scrape = sub.add_parser("scrape", help="执行一次抓取后退出")
    p_scrape.add_argument("--db", default=None)
    p_scrape.add_argument("--json-out", default=None, help="同时导出 JSON 快照")
    p_scrape.add_argument("--keep-days", type=int, default=0,
                          help="只保留最近 N 天记录，0=不清理")
    p_scrape.set_defaults(func=cmd_scrape)

    p_html = sub.add_parser("export-html", help="导出内嵌前端为 index.html")
    p_html.add_argument("--out", default=None)
    p_html.set_defaults(func=cmd_export_html)

    p_json = sub.add_parser("export-json", help="导出 JSON 快照")
    p_json.add_argument("--out", default=None)
    p_json.add_argument("--db", default=None)
    p_json.set_defaults(func=cmd_export_json)

    return parser


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["serve"] + argv                          # 裸跑 python main.py -> serve
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
