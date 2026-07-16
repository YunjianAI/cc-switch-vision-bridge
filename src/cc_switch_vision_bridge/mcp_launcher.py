from __future__ import annotations

import logging
import os

from .config import load_config
from .credentials import get_api_key


def main() -> int:
    config_path = os.environ.get("CCSVB_CONFIG")
    config = load_config(config_path)
    os.environ.update(
        {
            "MCP_OCR_PROVIDER": "custom",
            "MCP_OCR_BASE_URL": config.vision.base_url.rstrip("/"),
            "MCP_OCR_MODEL": config.vision.model,
            "MCP_OCR_API_KEY": get_api_key(),
        }
    )

    # mcp-vision 1.0.1 logs the complete OpenAI request body at DEBUG level,
    # which includes image base64. Start it in-process and suppress that logger
    # before the server module is imported.
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("mcp-ocr").setLevel(logging.WARNING)
    from mcp_ocr.server import main as mcp_main

    mcp_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
