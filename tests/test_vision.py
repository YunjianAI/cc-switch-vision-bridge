from __future__ import annotations

import httpx
import pytest

from cc_switch_vision_bridge.cache import VisionCache
from cc_switch_vision_bridge.config import VisionConfig
from cc_switch_vision_bridge.vision import VisionClient, VisionError, validate_image


@pytest.mark.asyncio
async def test_success_and_cache_hit(tmp_path, png_bytes):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "yellow box"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        vision = VisionClient(
            VisionConfig(base_url="https://vision.example/v1", model="vision-model"),
            "test-secret",
            VisionCache(tmp_path),
            client,
        )
        first = await vision.describe(png_bytes, "what is this")
        second = await vision.describe(png_bytes, "a different user question")
    assert first == second == "yellow box"
    assert calls == 1
    assert vision.cache.stats.hits == 1


@pytest.mark.asyncio
async def test_refusal_is_not_cached(tmp_path, png_bytes):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "The request was rejected because it was considered high risk"
                            )
                        }
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        vision = VisionClient(
            VisionConfig(base_url="https://vision.example/v1"),
            "secret",
            VisionCache(tmp_path),
            client,
        )
        with pytest.raises(VisionError, match="rejected"):
            await vision.describe(png_bytes)
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize("status,expected", [(400, 422), (401, 502), (429, 502), (500, 502)])
@pytest.mark.asyncio
async def test_provider_errors_are_mapped(tmp_path, png_bytes, status, expected):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="secret upstream body"))
    async with httpx.AsyncClient(transport=transport) as client:
        vision = VisionClient(
            VisionConfig(base_url="https://vision.example/v1"),
            "secret",
            VisionCache(tmp_path),
            client,
        )
        with pytest.raises(VisionError) as captured:
            await vision.describe(png_bytes)
    assert captured.value.status == expected
    assert "secret upstream body" not in str(captured.value)


def test_corrupt_image_is_422():
    with pytest.raises(VisionError) as captured:
        validate_image(b"not-an-image", 20)
    assert captured.value.status == 422
