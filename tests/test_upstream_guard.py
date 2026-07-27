from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cc_switch_vision_bridge.config import (
    AppConfig,
    ProxyConfig,
    UpstreamRecoveryConfig,
)
from cc_switch_vision_bridge.upstream_guard import UpstreamGuard


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE proxy_config (
                app_type TEXT PRIMARY KEY,
                proxy_enabled INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO proxy_config VALUES ('claude', 0, 0, datetime('now'))"
        )


def _config(tmp_path: Path) -> AppConfig:
    database = tmp_path / "cc-switch.db"
    executable = tmp_path / "cc-switch.exe"
    _database(database)
    executable.write_bytes(b"test")
    return AppConfig(
        proxy=ProxyConfig(upstream_base_url="http://127.0.0.1:15721"),
        upstream_recovery=UpstreamRecoveryConfig(
            enabled=True,
            cc_switch_db=str(database),
            cc_switch_exe=str(executable),
        ),
        config_path=tmp_path / "config.toml",
    )


def test_repair_database_only_enables_claude_row(tmp_path):
    config = _config(tmp_path)
    guard = UpstreamGuard(config)

    guard._repair_database(Path(config.upstream_recovery.cc_switch_db))

    with sqlite3.connect(config.upstream_recovery.cc_switch_db) as connection:
        assert connection.execute(
            "SELECT proxy_enabled, enabled FROM proxy_config WHERE app_type='claude'"
        ).fetchone() == (1, 1)


@pytest.mark.asyncio
async def test_guard_requires_consecutive_failures_before_repair(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.upstream_recovery.failure_threshold = 2
    guard = UpstreamGuard(config)
    repairs = 0

    async def unreachable(*_args, **_kwargs):
        return False

    def repair():
        nonlocal repairs
        repairs += 1

    async def recovered():
        return True

    monkeypatch.setattr(
        "cc_switch_vision_bridge.upstream_guard.upstream_reachable", unreachable
    )
    monkeypatch.setattr(guard, "repair_and_restart", repair)
    monkeypatch.setattr(guard, "_wait_for_upstream", recovered)

    assert await guard.check_once() is False
    assert repairs == 0
    assert await guard.check_once() is True
    assert repairs == 1
    assert guard.repairs == 1


@pytest.mark.asyncio
async def test_guard_cooldown_prevents_restart_storm(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.upstream_recovery.failure_threshold = 1
    config.upstream_recovery.cooldown_seconds = 300
    guard = UpstreamGuard(config)
    repairs = 0

    async def unreachable(*_args, **_kwargs):
        return False

    def repair():
        nonlocal repairs
        repairs += 1
        raise RuntimeError("failed")

    monkeypatch.setattr(
        "cc_switch_vision_bridge.upstream_guard.upstream_reachable", unreachable
    )
    monkeypatch.setattr(guard, "repair_and_restart", repair)

    assert await guard.check_once() is False
    assert await guard.check_once() is False
    assert repairs == 1
    assert guard.repair_attempts == 1
