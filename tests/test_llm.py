import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.llm import LlmConfig, OpenAICompatibleClient


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"choices": [{"message": {"content": '{"labels": ["业绩增长"], "summary": "业绩预告显示净利润增长。", "confidence": "high", "evidence": "预计净利润同比增长80%", "stage": "业绩预告", "metrics": {}}'}}]}


class FakeAsyncClient:
    request: dict | None = None
    instances = 0

    def __init__(self, **_: object) -> None:
        type(self).instances += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        type(self).request = {"url": url, **kwargs}
        return FakeResponse()


class LlmTests(unittest.TestCase):
    def test_request_schema_requires_summary_without_sentiment(self) -> None:
        client = OpenAICompatibleClient(LlmConfig("http://model.example/v1", "test-key", "test-model"))

        with patch("cninfo_miner.llm.httpx.AsyncClient", FakeAsyncClient):
            asyncio.run(client.analyze("2026年半年度业绩预告", "预计净利润同比增长80%", ("业绩增长", "业绩大降")))

        payload = FakeAsyncClient.request["json"]
        prompt = json.loads(payload["messages"][0]["content"])
        self.assertEqual(prompt["schema"].get("summary"), "简短中文结论")
        self.assertNotIn("sentiment", prompt["schema"])

    def test_reuses_one_http_client_for_multiple_model_requests(self) -> None:
        FakeAsyncClient.instances = 0
        config = LlmConfig("http://model.example/v1", "test-key", "test-model")

        async def run() -> None:
            client = OpenAICompatibleClient(config)
            try:
                await client.analyze("业绩预告", "预计净利润同比增长80%", ("业绩增长",))
                await client.analyze("业绩预告", "预计净利润同比增长80%", ("业绩增长",))
            finally:
                await client.aclose()

        with patch("cninfo_miner.llm.httpx.AsyncClient", FakeAsyncClient):
            asyncio.run(run())

        self.assertEqual(FakeAsyncClient.instances, 1)


if __name__ == "__main__":
    unittest.main()
