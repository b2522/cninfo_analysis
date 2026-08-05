# -*- coding: utf-8 -*-
"""
Vercel Serverless Function —— POST /api/refresh （也可 GET）

在 Serverless 环境下触发一次实时回源抓取，并写入 /tmp 热缓存（配置 KV 时同步推送）。
使用前请确认 Vercel 项目 Framework Preset 设为 "Other"，
否则 Vercel 会把根目录 main.py 当作 Python 框架入口而报错。

格式要求：Vercel 当前 Python 运行时需要顶层 `app`（WSGI/ASGI）。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from flask import Flask, request, Response

try:
    from common import refresh_now
except ImportError:  # pragma: no cover - Vercel 可能把项目根而非 api/ 加入 path
    from api.common import refresh_now

app = Flask(__name__)


def _json(body: Dict[str, Any], status: int = 200) -> Response:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(raw, status=status,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Cache-Control": "no-store",
                        "Access-Control-Allow-Origin": "*",
                    })


@app.after_request
def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/", methods=["POST", "GET", "OPTIONS"])
@app.route("/api/refresh", methods=["POST", "GET", "OPTIONS"])
def refresh() -> Response:
    if request.method == "OPTIONS":
        return _json({"ok": True})
    try:
        # 手动按钮不传 pages -> 5 页；Vercel 每小时 cron 传 ?pages=10 -> 10 页
        pages = request.args.get("pages", type=int) or (request.get_json(silent=True) or {}).get("pages")
        return _json(refresh_now(pages=pages))
    except Exception as exc:                         # noqa: BLE001
        return _json({"ok": False, "error": str(exc)}, status=500)
