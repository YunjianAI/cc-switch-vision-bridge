from __future__ import annotations

import base64

import pytest

from cc_switch_vision_bridge.transform import (
    DirectImageError,
    has_supported_images,
    transform_images,
)
from cc_switch_vision_bridge.vision import VisionError


def image_block(data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


class FakeVision:
    def __init__(self, error: VisionError | None = None):
        self.error = error
        self.prompts: list[str] = []

    async def describe(self, image_bytes: bytes, user_text: str = "") -> str:
        self.prompts.append(user_text)
        if self.error:
            raise self.error
        return f"described {len(image_bytes)} bytes"


@pytest.mark.asyncio
async def test_top_level_image_is_replaced(png_bytes):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [image_block(png_bytes), {"type": "text", "text": "read this"}],
            }
        ]
    }
    result = await transform_images(body, FakeVision())
    assert result.image_count == 1
    assert result.direct_image_count == 1
    assert result.tool_image_count == 0
    assert result.body["messages"][0]["content"][0]["type"] == "text"
    assert "Image Description" in result.body["messages"][0]["content"][0]["text"]
    assert body["messages"][0]["content"][0]["type"] == "image"


@pytest.mark.asyncio
async def test_nested_tool_result_image_is_replaced(png_bytes):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": [
                            {"type": "text", "text": "screenshot"},
                            image_block(png_bytes),
                        ],
                    }
                ],
            }
        ]
    }
    assert has_supported_images(body)
    result = await transform_images(body, FakeVision())
    content = result.body["messages"][0]["content"][0]["content"]
    assert [item["type"] for item in content] == ["text", "text"]
    assert result.tool_image_count == 1
    assert result.direct_image_count == 0


@pytest.mark.asyncio
async def test_tool_failure_degrades_but_direct_failure_blocks(png_bytes):
    failure = VisionError("provider down")
    tool_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": [image_block(png_bytes)]}
                ],
            }
        ]
    }
    result = await transform_images(tool_body, FakeVision(failure))
    assert result.tool_failures == 1
    assert "Image Analysis Failed" in result.body["messages"][0]["content"][0]["content"][0]["text"]

    direct_body = {"messages": [{"role": "user", "content": [image_block(png_bytes)]}]}
    with pytest.raises(DirectImageError, match="provider down"):
        await transform_images(direct_body, FakeVision(failure))


@pytest.mark.asyncio
async def test_invalid_base64_follows_context_policy():
    bad = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "***"},
    }
    with pytest.raises(DirectImageError, match="base64"):
        await transform_images(
            {"messages": [{"role": "user", "content": [bad]}]}, FakeVision()
        )

    nested = {
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "content": [bad]}]}
        ]
    }
    result = await transform_images(nested, FakeVision())
    assert result.tool_failures == 1


@pytest.mark.asyncio
async def test_empty_base64_follows_context_policy():
    empty = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": ""},
    }
    body = {"messages": [{"role": "user", "content": [empty]}]}
    assert has_supported_images(body)
    with pytest.raises(DirectImageError, match="empty"):
        await transform_images(body, FakeVision())

    nested = {
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "content": [empty]}]}
        ]
    }
    result = await transform_images(nested, FakeVision())
    assert result.tool_failures == 1


@pytest.mark.asyncio
async def test_multiple_images_preserve_order_and_prompt(png_bytes):
    vision = FakeVision()
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "compare"},
                    image_block(png_bytes),
                    {"type": "text", "text": "with"},
                    image_block(png_bytes + b"x"),
                ],
            }
        ]
    }
    result = await transform_images(body, vision)
    assert result.image_count == 2
    assert [item["type"] for item in result.body["messages"][0]["content"]] == [
        "text",
        "text",
        "text",
        "text",
    ]
    assert vision.prompts == ["compare with", "compare with"]


@pytest.mark.asyncio
async def test_large_base64_is_removed_before_forwarding(png_bytes):
    padded = png_bytes + b"0" * (2 * 1024 * 1024)
    body = {"messages": [{"role": "user", "content": [image_block(padded)]}]}
    before = len(str(body))
    result = await transform_images(body, FakeVision())
    after = len(str(result.body))
    assert before > 2 * 1024 * 1024
    assert after < 1000
