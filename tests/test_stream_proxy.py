"""SSE passthrough proof: /chat/stream must stream THROUGH the sidecar live.

Spins a fake gateway upstream that emits three SSE frames 0.5s apart and
asserts the phone-side client receives frame deltas progressively (arrival
spread) rather than one buffered dump at the end. No model calls involved.
"""
from __future__ import annotations

import asyncio
import time

import aiohttp
import pytest
from aiohttp import web

from sidecar.proxy import GatewayProxy
from sidecar.service import PairingService
from sidecar.transport import TailscaleServe


def _sse(name: str, payload: str) -> bytes:
    import json

    return f"event: {name}\ndata: {json.dumps({'delta': payload})}\n\n".encode()


async def _start_upstream() -> tuple[web.AppRunner, int]:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for i in range(3):
            await resp.write(_sse("assistant.delta", f"chunk{i} "))
            await asyncio.sleep(0.5)
        await resp.write(b"event: done\ndata: {}\n\n")
        await resp.write_eof()
        return resp

    app = web.Application()
    app.add_routes([web.post("/api/sessions/s1/chat/stream", handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


pytestmark = pytest.mark.skipif(
    aiohttp.__version__ == "", reason="aiohttp missing"
)


def test_chat_stream_proxies_progressively():
    async def scenario():
        upstream, uport = await _start_upstream()
        service = PairingService(ttl_seconds=90)
        proxy = GatewayProxy(f"http://127.0.0.1:{uport}", "test-key")
        transport = TailscaleServe(
            bind_ip="127.0.0.1", port=0, service=service, proxy=proxy,
            allow_loopback=True,
        )
        port = await transport.start()
        token = service.issue()

        async def exchange():
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"http://127.0.0.1:{port}/pair/exchange", params={"t": token}
                ) as r:
                    return (await r.json())["token"]

        session = await exchange()
        arrivals: list[float] = []
        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{port}/api/sessions/s1/chat/stream",
                    params={"t": session},
                    json={"message": "hi"},
                ) as r:
                    assert r.status == 200
                    assert r.headers["Content-Type"].startswith("text/event-stream")
                    buf = b""
                    async for chunk in r.content.iter_any():
                        buf += chunk
                        while b"\n\n" in buf:
                            frame, buf = buf.split(b"\n\n", 1)
                            if frame:
                                arrivals.append(time.monotonic() - t0)
            # All 4 frames arrived, in order, spread over time (live stream).
            assert len(arrivals) == 4
            assert arrivals[-1] - arrivals[0] >= 0.9, (
                f"frames arrived together ({arrivals}) — proxy is buffering SSE"
            )
        finally:
            await transport.stop()
            await proxy.close()
            await upstream.cleanup()

    asyncio.run(scenario())


def test_stream_route_requires_token():
    async def scenario():
        upstream, uport = await _start_upstream()
        proxy = GatewayProxy(f"http://127.0.0.1:{uport}", "test-key")
        transport = TailscaleServe(
            bind_ip="127.0.0.1", port=0,
            service=PairingService(ttl_seconds=90), proxy=proxy,
            allow_loopback=True,
        )
        port = await transport.start()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{port}/api/sessions/s1/chat/stream",
                    json={"message": "hi"},
                ) as r:
                    assert r.status == 401
        finally:
            await transport.stop()
            await proxy.close()
            await upstream.cleanup()

    asyncio.run(scenario())
