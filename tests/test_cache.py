from __future__ import annotations

import json
import time

from cc_switch_vision_bridge.cache import VisionCache


def test_cache_key_includes_prompt_and_model(tmp_path, png_bytes):
    cache = VisionCache(tmp_path)
    first = cache.key(png_bytes, "model-a", "v1", "question one")
    assert first != cache.key(png_bytes, "model-a", "v1", "question two")
    assert first != cache.key(png_bytes, "model-b", "v1", "question one")


def test_cache_does_not_contain_raw_image_or_secret(tmp_path, png_bytes):
    cache = VisionCache(tmp_path)
    key = cache.key(png_bytes, "model", "v1", "prompt")
    cache.set(
        key,
        "safe description",
        {"model": "model", "prompt_sha256": "abc", "image_sha256": "def"},
    )
    raw = (tmp_path / f"{key}.json").read_text(encoding="utf-8")
    assert "safe description" in raw
    assert "base64" not in raw
    assert "api-key" not in raw


def test_expired_cache_is_miss(tmp_path):
    cache = VisionCache(tmp_path, ttl_hours=1)
    key = "a" * 64
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"timestamp": time.time() - 7200, "text": "old"}), encoding="utf-8"
    )
    assert cache.get(key) is None

