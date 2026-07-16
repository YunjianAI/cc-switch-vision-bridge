from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "CCSwitchVisionBridge"


def default_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / ".cc-switch-vision-bridge"


@dataclass(slots=True)
class ProxyConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 15722
    upstream_base_url: str = "http://127.0.0.1:15721"
    max_request_mb: int = 64
    max_upstream_mb: int = 32


@dataclass(slots=True)
class VisionConfig:
    base_url: str = ""
    model: str = "mimo-v2.5"
    timeout_seconds: float = 60.0
    max_concurrency: int = 3
    max_image_mb: int = 20


@dataclass(slots=True)
class ProfileConfig:
    path: str = ""
    guard_enabled: bool = True
    proxy_base_url: str = "http://127.0.0.1:15722/claude-desktop"
    poll_seconds: float = 2.0


@dataclass(slots=True)
class CacheConfig:
    directory: str = ""
    ttl_hours: float = 24.0
    enabled: bool = True


@dataclass(slots=True)
class AppConfig:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    config_path: Path = field(default_factory=lambda: default_app_dir() / "config.toml")

    @property
    def app_dir(self) -> Path:
        return self.config_path.parent

    @property
    def cache_dir(self) -> Path:
        if self.cache.directory:
            return Path(os.path.expandvars(self.cache.directory)).expanduser()
        return self.app_dir / "vision-cache"

    @property
    def log_dir(self) -> Path:
        return self.app_dir / "logs"

    @property
    def state_path(self) -> Path:
        return self.app_dir / "state.json"

    @property
    def pid_path(self) -> Path:
        return self.app_dir / "bridge.pid"

    def validate(self) -> None:
        if self.proxy.listen_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("proxy.listen_host must be loopback-only")
        if not 1 <= self.proxy.listen_port <= 65535:
            raise ValueError("proxy.listen_port is invalid")
        if self.proxy.max_request_mb < self.proxy.max_upstream_mb:
            raise ValueError("max_request_mb must be >= max_upstream_mb")
        for label, value in (
            ("proxy.upstream_base_url", self.proxy.upstream_base_url),
            ("vision.base_url", self.vision.base_url),
        ):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{label} must be an http(s) URL")
        if self.vision.max_concurrency < 1:
            raise ValueError("vision.max_concurrency must be positive")
        if self.cache.ttl_hours < 0:
            raise ValueError("cache.ttl_hours cannot be negative")


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else default_app_dir() / "config.toml"
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    cfg = AppConfig(
        proxy=ProxyConfig(**_section(data, "proxy")),
        vision=VisionConfig(**_section(data, "vision")),
        profile=ProfileConfig(**_section(data, "profile")),
        cache=CacheConfig(**_section(data, "cache")),
        config_path=config_path.resolve(),
    )
    cfg.validate()
    return cfg
