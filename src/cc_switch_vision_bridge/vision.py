from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import time
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from .cache import VisionCache
from .config import VisionConfig

PROMPT_VERSION = "v3"
VISION_PROMPT = """You are a precise image describer. Analyze the supplied image.

1. Transcribe all visible text accurately, including code, errors, labels and timestamps.
2. Describe the interface, document, chart, diagram or scene and its layout.
3. Highlight errors, warnings, status indicators and actionable details.
4. Focus on details relevant to the user's request.

Describe only what is visible. Do not solve the user's task. Return concise plain text."""

SUPPORTED_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
REFUSAL_MARKERS = (
    "considered high risk",
    "cannot assist with",
    "unable to process this image due to safety",
    "request was rejected",
)

logger = logging.getLogger("ccsvb.vision")


class VisionError(RuntimeError):
    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status


def effective_prompt(user_text: str) -> str:
    # Keep vision preprocessing deterministic. The original user text remains
    # in the Anthropic message for the main model to answer after it receives
    # this comprehensive description. Some compatible vision gateways become
    # unstable when arbitrary user instructions are embedded in the vision
    # system prompt, and prompt-dependent descriptions also defeat safe reuse.
    _ = user_text
    return VISION_PROMPT


def validate_image(image_bytes: bytes, max_image_mb: int) -> str:
    if not image_bytes:
        raise VisionError("Image data is empty", status=422)
    if len(image_bytes) > max_image_mb * 1024 * 1024:
        raise VisionError(f"Decoded image exceeds {max_image_mb}MB", status=422)
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
            image_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisionError("Image data is corrupt or unsupported", status=422) from exc
    media_type = SUPPORTED_FORMATS.get(image_format)
    if not media_type:
        raise VisionError(f"Unsupported image format: {image_format or 'unknown'}", status=422)
    return media_type


class VisionClient:
    def __init__(
        self,
        config: VisionConfig,
        api_key: str,
        cache: VisionCache,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.cache = cache
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self.calls = 0
        self.failures = 0

    async def __aenter__(self) -> VisionClient:
        if self._client is None:
            # Some OpenAI-compatible vision gateways close keep-alive sockets
            # without a reusable shutdown signal. Reusing those stale sockets
            # makes every request after the first hang until timeout.
            limits = httpx.Limits(
                max_connections=self.config.max_concurrency,
                max_keepalive_connections=0,
            )
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                limits=limits,
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def describe(self, image_bytes: bytes, user_text: str = "") -> str:
        media_type = validate_image(image_bytes, self.config.max_image_mb)
        prompt = effective_prompt(user_text)
        key = self.cache.key(image_bytes, self.config.model, PROMPT_VERSION, prompt)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        started = time.monotonic()
        async with self._semaphore:
            self.calls += 1
            try:
                text = await self._request(image_bytes, media_type, prompt)
            except VisionError:
                self.failures += 1
                logger.warning("vision_failed image=%s bytes=%d", digest, len(image_bytes))
                raise

        elapsed_ms = round((time.monotonic() - started) * 1000)
        if not text.strip():
            self.failures += 1
            raise VisionError("Vision provider returned an empty response")
        lowered = text.casefold()
        if any(marker in lowered for marker in REFUSAL_MARKERS):
            self.failures += 1
            raise VisionError("Vision provider rejected the image", status=422)

        self.cache.set(
            key,
            text,
            {
                "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "model": self.config.model,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            },
        )
        logger.info(
            "vision_ok image=%s bytes=%d elapsed_ms=%d chars=%d",
            digest,
            len(image_bytes),
            elapsed_ms,
            len(text),
        )
        return text

    async def _request(self, image_bytes: bytes, media_type: str, prompt: str) -> str:
        if self._client is None:
            raise RuntimeError("VisionClient must be used as an async context manager")
        data_url = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        body = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": 4096,
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise VisionError("Vision provider timed out") from exc
        except httpx.RequestError as exc:
            raise VisionError(f"Vision provider connection failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            mapped = 422 if response.status_code in {400, 413, 415, 422} else 502
            raise VisionError(
                f"Vision provider returned HTTP {response.status_code}", status=mapped
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VisionError("Vision provider returned an unexpected response") from exc
        if not isinstance(text, str):
            raise VisionError("Vision provider response content is not text")
        return text
