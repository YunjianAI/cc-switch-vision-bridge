from __future__ import annotations

import json

from cc_switch_vision_bridge.config import AppConfig, ProfileConfig
from cc_switch_vision_bridge.profile_guard import ProfileGuard


def test_guard_changes_only_base_url(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "inferenceProvider": "gateway",
                "inferenceGatewayBaseUrl": "http://127.0.0.1:15721/claude-desktop",
                "keep": {"nested": True},
            }
        ),
        encoding="utf-8",
    )
    cfg = AppConfig(
        profile=ProfileConfig(path=str(profile)), config_path=tmp_path / "config.toml"
    )
    guard = ProfileGuard(cfg)
    assert guard.ensure_proxy_url() is True
    updated = json.loads(profile.read_text(encoding="utf-8"))
    assert updated["inferenceGatewayBaseUrl"].startswith("http://127.0.0.1:15722")
    assert updated["keep"] == {"nested": True}
    assert guard.ensure_proxy_url() is False


def test_guard_does_not_rewrite_unrecognized_url(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"inferenceGatewayBaseUrl": "file:///bad"}), encoding="utf-8")
    cfg = AppConfig(
        profile=ProfileConfig(path=str(profile)), config_path=tmp_path / "config.toml"
    )
    guard = ProfileGuard(cfg)
    assert guard.ensure_proxy_url() is False
    assert "recognized" in guard.last_error


def test_restore_only_when_owned(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"inferenceGatewayBaseUrl": "http://127.0.0.1:15722/claude-desktop"}),
        encoding="utf-8",
    )
    cfg = AppConfig(
        profile=ProfileConfig(path=str(profile)), config_path=tmp_path / "config.toml"
    )
    guard = ProfileGuard(cfg)
    assert guard.restore_if_owned("http://127.0.0.1:15721/claude-desktop") is True
    assert guard.restore_if_owned("http://127.0.0.1:9999/claude-desktop") is False

