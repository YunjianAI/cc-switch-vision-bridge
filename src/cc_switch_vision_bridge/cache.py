from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0


class VisionCache:
    def __init__(self, directory: Path, ttl_hours: float = 24, enabled: bool = True):
        self.directory = directory
        self.ttl_seconds = ttl_hours * 3600
        self.enabled = enabled and ttl_hours > 0
        self.stats = CacheStats()

    @staticmethod
    def key(image: bytes, model: str, prompt_version: str, prompt: str) -> str:
        digest = hashlib.sha256()
        digest.update(image)
        for value in (model, prompt_version, prompt):
            digest.update(b"\0")
            digest.update(value.encode("utf-8"))
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> str | None:
        if not self.enabled:
            self.stats.misses += 1
            return None
        path = self._path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - float(data["timestamp"])
            if age < 0 or age > self.ttl_seconds:
                self.stats.misses += 1
                return None
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                self.stats.misses += 1
                return None
            self.stats.hits += 1
            return text
        except (OSError, ValueError, KeyError, TypeError):
            self.stats.misses += 1
            return None

    def set(self, key: str, text: str, metadata: dict[str, str]) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "timestamp": time.time(),
            "text": text,
            **metadata,
        }
        path = self._path(key)
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
        self.stats.writes += 1

