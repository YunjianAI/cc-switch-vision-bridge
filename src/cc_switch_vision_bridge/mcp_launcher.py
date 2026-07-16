from __future__ import annotations

import os
import subprocess
import sys

from .config import load_config
from .credentials import get_api_key


def main() -> int:
    config_path = os.environ.get("CCSVB_CONFIG")
    config = load_config(config_path)
    env = os.environ.copy()
    env.update(
        {
            "MCP_OCR_PROVIDER": "custom",
            "MCP_OCR_BASE_URL": config.vision.base_url.rstrip("/"),
            "MCP_OCR_MODEL": config.vision.model,
            "MCP_OCR_API_KEY": get_api_key(),
        }
    )
    return subprocess.call([sys.executable, "-m", "mcp_ocr.server"], env=env)


if __name__ == "__main__":
    raise SystemExit(main())

