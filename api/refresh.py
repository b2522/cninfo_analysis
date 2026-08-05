# -*- coding: utf-8 -*-
"""
Vercel Serverless Function —— POST /api/refresh

在 Serverless 环境下触发一次实时回源抓取，并写入 /tmp 热缓存。
注意：/tmp 只在同一个 Lambda 实例内有效，跨实例不共享；
持久化仍以 GitHub Actions 每小时写入 Vercel KV / 提交 snapshot.json 为准。
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from announcements import cache_to_tmp, build_se_date, fetch_live, now_cst
    _IMPORT_ERROR = None
except Exception as exc:                                 # noqa: BLE001
    _IMPORT_ERROR = str(exc)


def _refresh() -> Dict[str, Any]:
    if _IMPORT_ERROR:
        return {"ok": False, "error": "helper import failed: " + _IMPORT_ERROR}
    se_date = build_se_date()
    rows = fetch_live(se_date)
    if rows:
        cache_to_tmp(rows)
    return {
        "ok": True,
        "se_date": se_date,
        "fetched": len(rows),
        "scraped_at": now_cst().isoformat(timespec="seconds"),
        "note": "Serverless /tmp 缓存已更新（非持久化，跨实例不共享）",
    }


class handler(BaseHTTPRequestHandler):                   # noqa: N801

    def _send(self, status: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:                           # noqa: N802
        try:
            self._send(200, _refresh())
        except Exception as exc:                         # noqa: BLE001
            self._send(500, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:                            # noqa: N802
        self.do_POST()

    def do_OPTIONS(self) -> None:                        # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return
