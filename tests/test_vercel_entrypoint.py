import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import FastAPI


class VercelEntrypointTests(unittest.TestCase):
    def test_declares_an_importable_fastapi_entrypoint(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(configuration["tool"]["vercel"]["entrypoint"], "cninfo_miner.main:app")

        from cninfo_miner.main import app

        self.assertIsInstance(app, FastAPI)
