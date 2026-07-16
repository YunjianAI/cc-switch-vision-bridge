from __future__ import annotations

import base64
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from cc_switch_vision_bridge.config import (
    AppConfig,
    CacheConfig,
    ProfileConfig,
    ProxyConfig,
    VisionConfig,
)
from cc_switch_vision_bridge.proxy import create_app


def image_block(data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


@pytest.fixture
async def provider_server():
    async def describe(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        data = await request.json()
        assert data["messages"][0]["content"][0]["type"] == "image_url"
        return web.json_response(
            {"choices": [{"message": {"content": "provider description"}}]}
        )

    app = web.Application(client_max_size=64 * 1024**2)
    app.router.add_post("/v1/chat/completions", describe)
    server = TestServer(app)
    await server.start_server()
    try:
        yield server
    finally:
        await server.close()


@pytest.fixture
async def upstream_server():
    async def echo(request: web.Request) -> web.Response:
        body = await request.read()
        if request.query.get("stream") == "1":
            return web.Response(
                body=b'data: {"type":"message_start"}\n\ndata: [DONE]\n\n',
                headers={"Content-Type": "text/event-stream"},
            )
        return web.Response(body=body, content_type=request.content_type)

    app = web.Application(client_max_size=64 * 1024**2)
    app.router.add_route("*", "/{path:.*}", echo)
    server = TestServer(app)
    await server.start_server()
    try:
        yield server
    finally:
        await server.close()


async def bridge_client(tmp_path, provider_server, upstream_server) -> TestClient:
    cfg = AppConfig(
        proxy=ProxyConfig(
            upstream_base_url=str(upstream_server.make_url("/")).rstrip("/"),
            max_request_mb=64,
            max_upstream_mb=32,
        ),
        vision=VisionConfig(
            base_url=str(provider_server.make_url("/v1")).rstrip("/"),
            model="test-vision",
        ),
        profile=ProfileConfig(guard_enabled=False),
        cache=CacheConfig(directory=str(tmp_path / "cache")),
        config_path=tmp_path / "config.toml",
    )
    app = await create_app(cfg, api_key="test-key")
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_plain_json_is_forwarded_byte_for_byte(
    tmp_path, provider_server, upstream_server
):
    client = await bridge_client(tmp_path, provider_server, upstream_server)
    raw = b'{"model":"text","messages":[{"role":"user","content":"hello"}]}'
    try:
        response = await client.post(
            "/claude-desktop/v1/messages",
            data=raw,
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 200
        assert await response.read() == raw
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_proxy_replaces_direct_and_tool_images(
    tmp_path, provider_server, upstream_server, png_bytes
):
    client = await bridge_client(tmp_path, provider_server, upstream_server)
    body = {
        "model": "text-model",
        "messages": [
            {"role": "user", "content": [image_block(png_bytes)]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": [image_block(png_bytes)]}
                ],
            },
        ],
    }
    try:
        response = await client.post("/claude-desktop/v1/messages", json=body)
        assert response.status == 200
        forwarded = json.loads(await response.read())
        assert forwarded["messages"][0]["content"][0]["type"] == "text"
        nested = forwarded["messages"][1]["content"][0]["content"][0]
        assert nested["type"] == "text"
        assert "provider description" in nested["text"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sse_response_is_forwarded(tmp_path, provider_server, upstream_server):
    client = await bridge_client(tmp_path, provider_server, upstream_server)
    try:
        response = await client.post(
            "/claude-desktop/v1/messages?stream=1",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "[DONE]" in await response.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_contains_no_secret(tmp_path, provider_server, upstream_server):
    client = await bridge_client(tmp_path, provider_server, upstream_server)
    try:
        response = await client.get("/health")
        text = await response.text()
        assert response.status == 200
        assert "test-key" not in text
        data = json.loads(text)
        assert data["version"] == "0.1.0-beta"
        assert data["vision"]["model"] == "test-vision"
    finally:
        await client.close()

