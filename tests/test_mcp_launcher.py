from __future__ import annotations

import logging
import sys
import types

from cc_switch_vision_bridge import mcp_launcher
from cc_switch_vision_bridge.config import AppConfig, VisionConfig


def test_launcher_suppresses_dependency_debug_logging(monkeypatch):
    called = []
    for name in (
        "MCP_OCR_PROVIDER",
        "MCP_OCR_BASE_URL",
        "MCP_OCR_MODEL",
        "MCP_OCR_API_KEY",
    ):
        monkeypatch.setenv(name, "placeholder")
    package = types.ModuleType("mcp_ocr")
    package.__path__ = []
    server = types.ModuleType("mcp_ocr.server")
    server.main = lambda: called.append(True)
    monkeypatch.setitem(sys.modules, "mcp_ocr", package)
    monkeypatch.setitem(sys.modules, "mcp_ocr.server", server)
    monkeypatch.setattr(
        mcp_launcher,
        "load_config",
        lambda _: AppConfig(
            vision=VisionConfig(
                base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.5"
            )
        ),
    )
    monkeypatch.setattr(mcp_launcher, "get_api_key", lambda: "test-key")

    assert mcp_launcher.main() == 0
    assert called == [True]
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("mcp-ocr").level == logging.WARNING
    assert mcp_launcher.os.environ["MCP_OCR_API_KEY"] == "test-key"
