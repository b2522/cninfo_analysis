"""Start the local CNINFO announcement-mining Web server."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("cninfo_miner.main:create_app", host="127.0.0.1", port=8000, factory=True, reload=False)
