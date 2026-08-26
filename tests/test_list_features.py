"""Runs/pins sidecar endpoints: auth, roundtrip, and live-run tracking.

Covers the zcode-style list features: /api/runs reflects streams in flight
(mark_run on stream start/end), /api/pins persists phone-level pins, both
require a valid session token.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from sidecar.proxy import GatewayProxy
from sidecar.service import PairingService
from sidecar.transport import TailscaleServe

try:
    import aiohttp
    from aiohttp import web
except ImportError:  # pragma: no cover
    aiohttp = None


pytestmark = pytest.mark.skipif(
    aiohttp is None or aiohttp.__version__ == "", reason="aiohttp missing"
)


def test_mark_run_roundtrip(tmp_path):
    proxy = GatewayProxy("http://127.0.0.1:1", "k")
    proxy._runs_path = lambda: tmp_path / "active_runs.json"

    assert proxy.active_runs() == {}
    proxy.mark_run("s1", True)
    assert set(proxy.active_runs().keys()) == {"s1"}
    proxy.mark_run("s2", True)
    assert set(proxy.active_runs().keys()) == {"s1", "s2"}
    proxy.mark_run("s1", False)
    assert set(proxy.active_runs().keys()) == {"s2"}


def test_mark_run_stale_ages_out(tmp_path):
    proxy = GatewayProxy("http://127.0.0.1:1", "k")
    p = tmp_path / "active_runs.json"
    proxy._runs_path = lambda: p
    p.write_text(json.dumps({"old": time.time() - 3600}))
    assert proxy.active_runs() == {}
    # aged-out mark must also be purged from disk
    assert json.loads(p.read_text()) == {}


async def _start_upstream():
    app = web.Application()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


def test_pins_and_runs_endpoints():
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
        base = f"http://127.0.0.1:{port}"
        try:
            async with aiohttp.ClientSession() as s:
                # exchange pairing token -> session token
                async with s.get(f"{base}/pair/exchange", params={"t": token}) as r:
                    sess = (await r.json())["token"]

                # pins start empty, roundtrip a pin, idempotent unpin
                async with s.get(f"{base}/api/pins", params={"t": sess}) as r:
                    assert r.status == 200
                    assert (await r.json())["data"] == []
                async with s.post(
                    f"{base}/api/pins", params={"t": sess},
                    json={"id": "sess_a", "pinned": True},
                ) as r:
                    assert r.status == 200
                    assert (await r.json())["data"] == ["sess_a"]
                async with s.get(f"{base}/api/pins", params={"t": sess}) as r:
                    assert (await r.json())["data"] == ["sess_a"]
                async with s.post(
                    f"{base}/api/pins", params={"t": sess},
                    json={"id": "sess_a", "pinned": False},
                ) as r:
                    assert (await r.json())["data"] == []

                # runs endpoint returns the proxy's live map
                proxy.mark_run("live_sess", True)
                async with s.get(f"{base}/api/runs", params={"t": sess}) as r:
                    assert r.status == 200
                    assert "live_sess" in (await r.json())["data"]

                # both endpoints reject bad tokens like everything else
                for path in ("/api/runs", "/api/pins"):
                    async with s.get(base + path, params={"t": "nope"}) as r:
                        assert r.status == 401
        finally:
            await transport.stop()
            await proxy.close()
            await upstream.cleanup()

    asyncio.run(scenario())
