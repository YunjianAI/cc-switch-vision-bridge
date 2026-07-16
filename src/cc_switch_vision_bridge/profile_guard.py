from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import AppConfig

logger = logging.getLogger("ccsvb.profile")


class ProfileGuard:
    def __init__(self, config: AppConfig):
        self.config = config
        self.path = Path(os.path.expandvars(config.profile.path)).expanduser()
        self.running = False
        self.repairs = 0
        self.last_error = ""
        self.last_check = 0.0

    @staticmethod
    def is_gateway_url(value: object) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and value.rstrip("/").endswith(
            "/claude-desktop"
        )

    def backup(self) -> Path:
        backup_dir = self.config.app_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"profile_{stamp}.json"
        shutil.copy2(self.path, target)
        return target

    def ensure_proxy_url(self) -> bool:
        self.last_check = time.time()
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
            current = data.get("inferenceGatewayBaseUrl")
            desired = self.config.profile.proxy_base_url
            if current == desired:
                self.last_error = ""
                return False
            if not self.is_gateway_url(current):
                raise ValueError("profile inferenceGatewayBaseUrl is not a recognized gateway URL")
            data["inferenceGatewayBaseUrl"] = desired
            self._atomic_write(data)
            self.repairs += 1
            self.last_error = ""
            logger.info("profile_repaired path=%s", self.path)
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("profile_guard_failed error=%s", type(exc).__name__)
            return False

    def restore_if_owned(self, original_url: str) -> bool:
        data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if data.get("inferenceGatewayBaseUrl") != self.config.profile.proxy_base_url:
            return False
        data["inferenceGatewayBaseUrl"] = original_url
        self._atomic_write(data)
        return True

    def _atomic_write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    async def run(self, stop: asyncio.Event) -> None:
        self.running = True
        try:
            while not stop.is_set():
                self.ensure_proxy_url()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.config.profile.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            self.running = False

    def status(self) -> dict:
        return {
            "enabled": self.config.profile.guard_enabled,
            "running": self.running,
            "profile_path": str(self.path),
            "repairs": self.repairs,
            "last_check": self.last_check,
            "last_error": self.last_error,
        }

