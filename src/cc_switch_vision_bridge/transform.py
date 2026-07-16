from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass
from typing import Any, Protocol

from .vision import VisionError


class ImageDescriber(Protocol):
    async def describe(self, image_bytes: bytes, user_text: str = "") -> str: ...


class DirectImageError(VisionError):
    pass


@dataclass(slots=True)
class TransformResult:
    body: dict[str, Any]
    image_count: int
    direct_image_count: int
    tool_image_count: int
    tool_failures: int


def has_supported_images(body: dict[str, Any]) -> bool:
    return any(_is_base64_image(node) for node, _ in _walk(body.get("messages", [])))


def extract_user_text(body: dict[str, Any]) -> str:
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    return ""


async def transform_images(
    body: dict[str, Any], describer: ImageDescriber
) -> TransformResult:
    transformed = copy.deepcopy(body)
    user_text = extract_user_text(transformed)
    targets = [
        (parent, key, node, inside_tool)
        for node, inside_tool, parent, key in _walk_mutable(transformed.get("messages", []))
        if _is_base64_image(node)
    ]
    if not targets:
        return TransformResult(transformed, 0, 0, 0, 0)

    async def process(node: dict[str, Any], inside_tool: bool) -> dict[str, str]:
        encoded = node["source"].get("data")
        if not isinstance(encoded, str) or not encoded:
            error = VisionError("Image base64 is empty", status=422)
            if inside_tool:
                return _failure_block(error)
            raise DirectImageError(str(error), status=error.status)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            error = VisionError("Image base64 is invalid", status=422)
            if inside_tool:
                return _failure_block(error)
            raise DirectImageError(str(error), status=error.status) from exc
        try:
            description = await describer.describe(raw, user_text)
        except VisionError as exc:
            if inside_tool:
                return _failure_block(exc)
            raise DirectImageError(str(exc), status=exc.status) from exc
        return {
            "type": "text",
            "text": f"[Image Description]\n{description}\n[End Image Description]",
        }

    replacements = await asyncio.gather(
        *(process(node, inside_tool) for _, _, node, inside_tool in targets)
    )
    tool_failures = 0
    for (parent, key, _, inside_tool), replacement in zip(targets, replacements, strict=True):
        parent[key] = replacement
        if inside_tool and replacement["text"].startswith("[Image Analysis Failed]"):
            tool_failures += 1

    direct = sum(not target[3] for target in targets)
    tools = len(targets) - direct
    return TransformResult(transformed, len(targets), direct, tools, tool_failures)


def _failure_block(error: VisionError) -> dict[str, str]:
    return {
        "type": "text",
        "text": f"[Image Analysis Failed]\n{error}\n[End Image Analysis Failure]",
    }


def _is_base64_image(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and node.get("type") == "image"
        and isinstance(node.get("source"), dict)
        and node["source"].get("type") == "base64"
    )


def _walk(node: Any, inside_tool: bool = False):
    if isinstance(node, dict):
        now_inside = inside_tool or node.get("type") == "tool_result"
        yield node, now_inside
        for value in node.values():
            yield from _walk(value, now_inside)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, inside_tool)


def _walk_mutable(node: Any, inside_tool: bool = False, parent=None, key=None):
    if isinstance(node, dict):
        now_inside = inside_tool or node.get("type") == "tool_result"
        yield node, now_inside, parent, key
        for child_key, value in list(node.items()):
            yield from _walk_mutable(value, now_inside, node, child_key)
    elif isinstance(node, list):
        for index, value in enumerate(list(node)):
            yield from _walk_mutable(value, inside_tool, node, index)
