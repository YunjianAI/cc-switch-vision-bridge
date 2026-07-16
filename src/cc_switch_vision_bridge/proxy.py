from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from . import __version__
from .cache import VisionCache
from .config import AppConfig, load_config
from .credentials import get_api_key
from .profile_guard import ProfileGuard
from .transform import DirectImageError, has_supported_images, transform_images
from .vision import VisionClient

logger = logging.getLogger("ccsvb.proxy")
CONFIG_KEY = web.AppKey("config", AppConfig)
VISION_KEY = web.AppKey("vision", VisionClient)
UPSTREAM_SESSION_KEY = web.AppKey("upstream_session", aiohttp.ClientSession)
STOP_EVENT_KEY = web.AppKey("stop_event", asyncio.Event)
PROFILE_GUARD_KEY = web.AppKey("profile_guard", ProfileGuard)
GUARD_TASK_KEY = web.AppKey("guard_task", asyncio.Task)
HOP_BY_HOP = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
}


def setup_logging(config: AppConfig, verbose: bool = False) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handler = logging.handlers.RotatingFileHandler(
        config.log_dir / "bridge.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    root.handlers.clear()
    root.addHandler(handler)
    if verbose:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)


async def _upstream_reachable(base_url: str) -> bool:
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, port), timeout=1.5
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


def _safe_headers(headers: aiohttp.typedefs.LooseHeaders) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP}


async def health_handler(request: web.Request) -> web.Response:
    config = request.app[CONFIG_KEY]
    vision = request.app[VISION_KEY]
    cache = vision.cache.stats
    guard = request.app.get(PROFILE_GUARD_KEY)
    return web.json_response(
        {
            "status": "ok",
            "version": __version__,
            "listen": f"{config.proxy.listen_host}:{config.proxy.listen_port}",
            "upstream": {
                "url": config.proxy.upstream_base_url,
                "reachable": await _upstream_reachable(config.proxy.upstream_base_url),
            },
            "vision": {
                "configured": bool(config.vision.base_url and config.vision.model),
                "model": config.vision.model,
                "calls": vision.calls,
                "failures": vision.failures,
            },
            "cache": {"hits": cache.hits, "misses": cache.misses, "writes": cache.writes},
            "profile_guard": guard.status() if guard else {"enabled": False},
        }
    )


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    config = request.app[CONFIG_KEY]
    request_id = uuid.uuid4().hex[:12]
    raw_body = await request.read()
    outbound_body = raw_body
    image_count = 0

    if raw_body and request.content_type.lower() == "application/json":
        try:
            parsed: dict[str, Any] = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = {}
        if parsed and has_supported_images(parsed):
            try:
                result = await transform_images(parsed, request.app[VISION_KEY])
            except DirectImageError as exc:
                logger.warning("request=%s direct_image_failed status=%d", request_id, exc.status)
                return web.json_response(
                    {
                        "error": {
                            "type": "vision_preprocessing_error",
                            "message": str(exc),
                            "request_id": request_id,
                        }
                    },
                    status=exc.status,
                )
            outbound_body = json.dumps(
                result.body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            image_count = result.image_count
            logger.info(
                "request=%s images=%d direct=%d tool=%d tool_failures=%d bytes_in=%d bytes_out=%d",
                request_id,
                result.image_count,
                result.direct_image_count,
                result.tool_image_count,
                result.tool_failures,
                len(raw_body),
                len(outbound_body),
            )

    if len(outbound_body) > config.proxy.max_upstream_mb * 1024 * 1024:
        return web.json_response(
            {
                "error": {
                    "type": "request_too_large_after_preprocessing",
                    "message": f"Request remains larger than {config.proxy.max_upstream_mb}MB",
                    "request_id": request_id,
                }
            },
            status=413,
        )

    upstream_url = f"{config.proxy.upstream_base_url.rstrip('/')}{request.path_qs}"
    headers = _safe_headers(request.headers)
    session = request.app[UPSTREAM_SESSION_KEY]
    try:
        async with session.request(
            request.method,
            upstream_url,
            headers=headers,
            data=outbound_body,
        ) as upstream:
            response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
            for name, value in _safe_headers(upstream.headers).items():
                response.headers[name] = value
            response.headers["x-ccsvb-request-id"] = request_id
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            logger.info(
                "request=%s upstream_status=%d images=%d", request_id, upstream.status, image_count
            )
            return response
    except aiohttp.ClientConnectorError:
        logger.error("request=%s upstream_unreachable", request_id)
        return web.json_response(
            {
                "error": {
                    "type": "upstream_unreachable",
                    "message": "Cannot connect to CC Switch upstream",
                    "request_id": request_id,
                }
            },
            status=502,
        )
    except TimeoutError:
        logger.error("request=%s upstream_timeout", request_id)
        return web.json_response(
            {
                "error": {
                    "type": "upstream_timeout",
                    "message": "CC Switch upstream timed out",
                    "request_id": request_id,
                }
            },
            status=504,
        )


async def create_app(config: AppConfig, *, api_key: str | None = None) -> web.Application:
    config.app_dir.mkdir(parents=True, exist_ok=True)
    cache = VisionCache(config.cache_dir, config.cache.ttl_hours, config.cache.enabled)
    vision = VisionClient(config.vision, api_key or get_api_key(), cache)
    await vision.__aenter__()
    timeout = aiohttp.ClientTimeout(total=600, connect=10)
    upstream_session = aiohttp.ClientSession(timeout=timeout, auto_decompress=False)

    app = web.Application(client_max_size=config.proxy.max_request_mb * 1024**2)
    app[CONFIG_KEY] = config
    app[VISION_KEY] = vision
    app[UPSTREAM_SESSION_KEY] = upstream_session
    app[STOP_EVENT_KEY] = asyncio.Event()

    guard = None
    if config.profile.guard_enabled and config.profile.path:
        guard = ProfileGuard(config)
        app[PROFILE_GUARD_KEY] = guard

    async def start_background(_: web.Application) -> None:
        config.pid_path.write_text(str(os.getpid()), encoding="ascii")
        if guard:
            app[GUARD_TASK_KEY] = asyncio.create_task(guard.run(app[STOP_EVENT_KEY]))

    async def cleanup(_: web.Application) -> None:
        app[STOP_EVENT_KEY].set()
        task = app.get(GUARD_TASK_KEY)
        if task:
            await task
        await upstream_session.close()
        await vision.__aexit__(None, None, None)
        try:
            config.pid_path.unlink()
        except FileNotFoundError:
            pass

    app.on_startup.append(start_background)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", health_handler)
    app.router.add_route("*", "/{path:.*}", proxy_handler)
    return app


def run(config_path: str | Path, verbose: bool = False) -> None:
    config = load_config(config_path)
    setup_logging(config, verbose=verbose)
    web.run_app(
        create_app(config),
        host=config.proxy.listen_host,
        port=config.proxy.listen_port,
        print=None,
        access_log=None,
        handle_signals=hasattr(signal, "SIGTERM"),
    )
