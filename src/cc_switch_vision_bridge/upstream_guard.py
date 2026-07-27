from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import AppConfig

logger = logging.getLogger("ccsvb.upstream")


async def upstream_reachable(base_url: str, timeout: float = 1.5) -> bool:
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


class UpstreamGuard:
    def __init__(self, config: AppConfig):
        self.config = config
        self.settings = config.upstream_recovery
        self.running = False
        self.consecutive_failures = 0
        self.repair_attempts = 0
        self.repairs = 0
        self.last_check = 0.0
        self.last_repair = 0.0
        self.last_error = ""

    def _expanded_path(self, value: str) -> Path:
        return Path(os.path.expandvars(value)).expanduser()

    @staticmethod
    def _powershell_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _run_powershell(self, command: str) -> None:
        subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def _stop_cc_switch(self, executable: Path) -> None:
        literal = self._powershell_literal(str(executable))
        self._run_powershell(
            f"$target={literal}; "
            "Get-Process -Name 'cc-switch' -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Path -eq $target } | Stop-Process -Force"
        )

    def _start_cc_switch(self, executable: Path) -> None:
        literal = self._powershell_literal(str(executable))
        self._run_powershell(
            f"Start-Process -FilePath {literal} -WindowStyle Hidden"
        )

    def _repair_database(self, database: Path) -> None:
        with sqlite3.connect(database, timeout=5) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(proxy_config)")
            }
            required = {"app_type", "proxy_enabled", "enabled"}
            if not required.issubset(columns):
                raise RuntimeError("CC Switch proxy_config schema is unsupported")
            row = connection.execute(
                "SELECT proxy_enabled, enabled FROM proxy_config WHERE app_type = ?",
                (self.settings.app_type,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"CC Switch proxy_config has no {self.settings.app_type!r} row"
                )
            connection.execute(
                """
                UPDATE proxy_config
                SET proxy_enabled = 1, enabled = 1
                WHERE app_type = ?
                """,
                (self.settings.app_type,),
            )
            connection.commit()

    def repair_and_restart(self) -> None:
        database = self._expanded_path(self.settings.cc_switch_db)
        executable = self._expanded_path(self.settings.cc_switch_exe)
        if os.name != "nt":
            raise RuntimeError("CC Switch upstream recovery is Windows-only")
        if not database.is_file():
            raise FileNotFoundError(f"CC Switch database not found: {database}")
        if not executable.is_file():
            raise FileNotFoundError(f"CC Switch executable not found: {executable}")
        self._stop_cc_switch(executable)
        self._repair_database(database)
        self._start_cc_switch(executable)

    async def _wait_for_upstream(self) -> bool:
        deadline = asyncio.get_running_loop().time() + self.settings.startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await upstream_reachable(self.config.proxy.upstream_base_url):
                return True
            await asyncio.sleep(0.5)
        return False

    async def check_once(self) -> bool:
        self.last_check = time.time()
        if await upstream_reachable(self.config.proxy.upstream_base_url):
            self.consecutive_failures = 0
            self.last_error = ""
            return True

        self.consecutive_failures += 1
        now = time.time()
        if self.consecutive_failures < self.settings.failure_threshold:
            return False
        if now - self.last_repair < self.settings.cooldown_seconds:
            return False

        self.last_repair = now
        self.repair_attempts += 1
        try:
            await asyncio.to_thread(self.repair_and_restart)
            if not await self._wait_for_upstream():
                raise TimeoutError("CC Switch did not reopen the upstream port")
            self.repairs += 1
            self.consecutive_failures = 0
            self.last_error = ""
            logger.warning("upstream_recovered repairs=%d", self.repairs)
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("upstream_recovery_failed error=%s", type(exc).__name__)
            return False

    async def run(self, stop: asyncio.Event) -> None:
        self.running = True
        try:
            while not stop.is_set():
                await self.check_once()
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self.settings.check_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            self.running = False

    def status(self) -> dict:
        return {
            "enabled": self.settings.enabled,
            "running": self.running,
            "consecutive_failures": self.consecutive_failures,
            "repair_attempts": self.repair_attempts,
            "repairs": self.repairs,
            "last_check": self.last_check,
            "last_repair": self.last_repair,
            "last_error": self.last_error,
        }
