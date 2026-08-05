# -*- coding: utf-8 -*-
"""
Vercel Serverless Function —— GET /api/announcements?date_range=today

Vercel 当前 Python 运行时要求暴露**顶层** `app`（WSGI/ASGI），
旧的 `class handler(BaseHTTPRequestHandler)` 写法已废弃，会导致
"does not define a top-level 'app' FastAPI instance" 错误。

本文件只依赖 common.py（同目录），确保打包后一定可运行。

数据来源按优先级降级 (解决 Serverless 无持久磁盘的问题)：
  1. Vercel KV / Upstash Redis  —— 环境变量 KV_REST_API_URL + KV_REST_API_TOKEN
  2. 仓库内 data/snapshot.json —— GitHub Actions 每小时 commit，随部署一起分发（只读）
  3. 仓库内 announcements.db   —— 同上，只读 SQLite
  4. /tmp/announcements.db     —— 同一实例内的热缓存
  5. 实时回源抓取 cninfo       —— 兜底，前 5 页，结果写入 /tmp 缓存
"""

from __future__ import annotations

import json
from typing import Any, Dict

from flask import Flask, request, Response

from common import build_payload, DEFAULT_RANGE

app = Flask(__name__)


def _json(body: Dict[str, Any], status: int = 200) -> Response:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(raw, status=status,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Cache-Control": "public, s-maxage=300, stale-while-revalidate=600",
                        "Access-Control-Allow-Origin": "*",
                    })


@app.after_request
def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/", methods=["GET", "OPTIONS"])
@app.route("/api/announcements", methods=["GET", "OPTIONS"])
def announcements() -> Response:
    if request.method == "OPTIONS":
        return _json({"ok": True})
    try:
        range_key = request.args.get("date_range", DEFAULT_RANGE)
        return _json(build_payload(range_key))
    except Exception as exc:                         # noqa: BLE001
        return _json({"ok": False, "error": str(exc), "data": [], "count": 0}, status=500)
