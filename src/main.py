"""Vercel FastAPI entrypoint using the committed SQLite database in read-only mode."""

from src.cninfo_miner.main import create_vercel_app

app = create_vercel_app()
