"""OpenAI-compatible structured analysis adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str


class OpenAICompatibleClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self._http_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=90.0)
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def analyze(self, title: str, text: str, candidate_labels: tuple[str, ...]) -> dict[str, Any]:
        if not all((self.config.base_url, self.config.api_key, self.config.model)):
            raise ValueError("未配置模型 API")
        prompt = {
            "task": "仅依据公告正文做结构化分类，不得推测或补充公告未披露的信息。",
            "allowed_labels": candidate_labels,
            "rules": [
                "labels 只能使用 allowed_labels 中的字符串；不符合时返回空数组。",
                "summary 用一句简短中文概括已披露的事件或业绩变化。",
                "evidence 必须是支持结论的 PDF 原文连续精确片段；无法提供时返回空字符串。",
                "只返回符合 schema 的 JSON 对象。",
            ],
            "title": title,
            "text": text[:30000],
            "schema": {
                "labels": ["仅 allowed_labels 中的字符串"],
                "summary": "简短中文结论",
                "confidence": "high|medium|low",
                "evidence": "PDF 原文连续精确片段",
                "stage": "事件阶段",
                "metrics": {"任意已披露指标": "原始数值"},
            },
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        response = await self._client().post(
            url,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "model": self.config.model,
                "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
