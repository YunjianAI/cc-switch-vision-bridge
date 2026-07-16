from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run_uninstall(repo: Path, local_app_data: Path) -> subprocess.CompletedProcess[str]:
    script = repo / "uninstall.ps1"
    command = (
        "function Get-ScheduledTask { param($TaskName) return $null }; "
        f"& '{script}' -KeepCredentials"
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_app_data)
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_uninstall(tmp_path: Path, *, user_changed_mcp: bool = False):
    app_dir = tmp_path / "CCSwitchVisionBridge"
    app_dir.mkdir()
    profile_path = tmp_path / "profile.json"
    mcp_path = tmp_path / ".mcp.json"
    original_entry = {"command": "legacy-python", "args": ["-m", "legacy.vision"]}
    installed_entry = {
        "command": str(app_dir / ".venv" / "Scripts" / "python.exe"),
        "args": ["-m", "cc_switch_vision_bridge.mcp_launcher"],
        "env": {"CCSVB_CONFIG": str(app_dir / "config.toml")},
    }
    current_entry = (
        {"command": "user-python", "args": ["-m", "user.changed"]}
        if user_changed_mcp
        else installed_entry
    )
    profile_path.write_text(
        json.dumps({"inferenceGatewayBaseUrl": "http://127.0.0.1:15722/claude-desktop"}),
        encoding="utf-8",
    )
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "vision": current_entry,
                    "playwright": {"command": "npx", "args": ["playwright"]},
                }
            }
        ),
        encoding="utf-8",
    )
    mcp_backup_path = tmp_path / ".mcp.json.ccsvb-backup"
    mcp_backup_path.write_text(
        json.dumps({"mcpServers": {"vision": original_entry}}), encoding="utf-8"
    )
    state = {
        "profile_path": str(profile_path),
        "original_profile_url": "http://127.0.0.1:15721/claude-desktop",
        "proxy_profile_url": "http://127.0.0.1:15722/claude-desktop",
        "mcp_config_path": str(mcp_path),
        "mcp_server_name": "vision",
        "mcp_entry_existed": True,
        "mcp_backup_path": str(mcp_backup_path),
        "mcp_installed_entry": installed_entry,
    }
    (app_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return app_dir, profile_path, mcp_path, original_entry, current_entry


def test_uninstall_restores_owned_profile_and_mcp(tmp_path):
    app_dir, profile_path, mcp_path, original_entry, _ = _prepare_uninstall(tmp_path)
    result = _run_uninstall(Path(__file__).parents[1], tmp_path)
    assert result.returncode == 0, result.stderr
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    assert profile["inferenceGatewayBaseUrl"] == "http://127.0.0.1:15721/claude-desktop"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8-sig"))
    assert mcp["mcpServers"]["vision"] == original_entry
    assert mcp["mcpServers"]["playwright"]["command"] == "npx"
    assert not app_dir.exists()
    assert list(tmp_path.glob("CCSwitchVisionBridge-uninstalled-*"))


def test_uninstall_does_not_overwrite_user_changed_mcp(tmp_path):
    _, _, mcp_path, _, current_entry = _prepare_uninstall(
        tmp_path, user_changed_mcp=True
    )
    result = _run_uninstall(Path(__file__).parents[1], tmp_path)
    assert result.returncode == 0, result.stderr
    mcp = json.loads(mcp_path.read_text(encoding="utf-8-sig"))
    assert mcp["mcpServers"]["vision"] == current_entry
