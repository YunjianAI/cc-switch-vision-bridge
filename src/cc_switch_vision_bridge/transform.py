from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass
from typing import Any, Protocol

from .vision import VisionError

BRIDGE_SYSTEM_INSTRUCTION = """CC Switch Vision Bridge preprocessing is active.
Any [Vision Bridge Image Analysis] block in message content is the completed result of the
configured external vision service. The original image has already been analyzed and removed
before this request reached the text model. Use that analysis as visual evidence and answer the
user directly. Do not call another vision or OCR tool, search temporary folders, or request an
image path for such a block. Only report that vision is unavailable when an
[Image Analysis Failed] block is present."""


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
        text = _direct_message_text(message)
        if text:
            return text
    return ""


async def transform_images(
    body: dict[str, Any], describer: ImageDescriber
) -> TransformResult:
    transformed = copy.deepcopy(body)
    messages = transformed.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    image_message_indices = [
        index
        for index, message in enumerate(messages)
        if any(_is_base64_image(node) for node, _ in _walk(message))
    ]
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], dict)
            and messages[index].get("role") == "user"
        ),
        None,
    )
    focus_image_index: int | None = None
    current_prompt = ""
    if latest_user_index is not None:
        current_prompt = _direct_message_text(messages[latest_user_index])
        if not current_prompt:
            current_prompt = _stable_message_prompt(messages, latest_user_index)
        if latest_user_index in image_message_indices:
            focus_image_index = latest_user_index
        else:
            focus_image_index = next(
                (
                    index
                    for index in reversed(image_message_indices)
                    if index < latest_user_index
                ),
                None,
            )
    elif image_message_indices:
        focus_image_index = image_message_indices[-1]

    targets = []
    for message_index, message in enumerate(messages):
        prompt = (
            current_prompt
            if message_index == focus_image_index and current_prompt
            else _stable_message_prompt(messages, message_index)
        )
        targets.extend(
            (parent, key, node, inside_tool, prompt)
            for node, inside_tool, parent, key in _walk_mutable(message)
            if _is_base64_image(node)
        )
    if not targets:
        return TransformResult(transformed, 0, 0, 0, 0)

    async def process(
        node: dict[str, Any], inside_tool: bool, prompt: str
    ) -> dict[str, str]:
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
            description = await describer.describe(raw, prompt)
        except VisionError as exc:
            if inside_tool:
                return _failure_block(exc)
            raise DirectImageError(str(exc), status=exc.status) from exc
        return {
            "type": "text",
            "text": (
                f"[Vision Bridge Image Analysis]\n{description}\n"
                "[End Vision Bridge Image Analysis]"
            ),
        }

    replacements = await asyncio.gather(
        *(process(node, inside_tool, prompt) for _, _, node, inside_tool, prompt in targets)
    )
    tool_failures = 0
    for (parent, key, _, inside_tool, _), replacement in zip(
        targets, replacements, strict=True
    ):
        parent[key] = replacement
        if inside_tool and replacement["text"].startswith("[Image Analysis Failed]"):
            tool_failures += 1

    _append_bridge_system_instruction(transformed)

    direct = sum(not target[3] for target in targets)
    tools = len(targets) - direct
    return TransformResult(transformed, len(targets), direct, tools, tool_failures)


def _failure_block(error: VisionError) -> dict[str, str]:
    return {
        "type": "text",
        "text": f"[Image Analysis Failed]\n{error}\n[End Image Analysis Failure]",
    }


def _append_bridge_system_instruction(body: dict[str, Any]) -> None:
    system = body.get("system")
    if system is None:
        body["system"] = BRIDGE_SYSTEM_INSTRUCTION
    elif isinstance(system, str):
        body["system"] = f"{system}\n\n{BRIDGE_SYSTEM_INSTRUCTION}"
    elif isinstance(system, list):
        system.append({"type": "text", "text": BRIDGE_SYSTEM_INSTRUCTION})


def _direct_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", [])
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return " ".join(
        block.get("text", "").strip()
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block.get("text", "").strip()
    )


def _stable_message_prompt(messages: list[Any], message_index: int) -> str:
    own_text = _direct_message_text(messages[message_index])
    if own_text:
        return own_text
    for index in range(message_index - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _direct_message_text(message)
        if text:
            return text
    return ""


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
