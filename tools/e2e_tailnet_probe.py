"""Real-network E2E probe: bind the sidecar to the REAL tailnet IP (the
production bind path, not the loopback test hook), then walk the entire
phone lifecycle with raw HTTP, asserting the wire shapes the phone page
depends on (epoch-second timestamps, exchange, session token polls,
sessionCount, revoke).

Run from the spike dir: python tools/e2e_tailnet_probe.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import aiohttp

sys.path.insert(0, ".")

from sidecar.config import Config
from sidecar.proxy import GatewayProxy
from sidecar.service import PairingService
from sidecar.transport import TailscaleServe


async def main() -> int:
    ip = subprocess.run(
        ["tailscale", "ip", "-4"], capture_output=True, text=True, check=True
    ).stdout.split()[0]
    print(f"[1] tailnet ip: {ip}")

    cfg = Config()
    service = PairingService(ttl_seconds=cfg.token_ttl_seconds, session_ttl_seconds=cfg.session_ttl_seconds)
    proxy = GatewayProxy(cfg.gateway_base, cfg.api_server_key)
    transport = TailscaleServe(bind_ip=ip, port=8643, service=service, proxy=proxy)

    try:
        port = await transport.start()
        print(f"[2] sidecar bound: {ip}:{port} (real tailnet interface)")
    except OSError as e:
        print(f"[2] bind failed on 8643: {e} — is an old sidecar still holding the port?")
        await proxy.close()
        return 1

    ok = True
    try:
        async with aiohttp.ClientSession() as s:
            token = service.issue()
            url = transport.pairing_url(token)
            print(f"[3] pairing url: {url[:60]}...")

            # phone step 1: page load
            async with s.get(url) as r:
                html = await r.text()
                assert r.status == 200 and "Hermes Remote" in html, f"page load: {r.status}"
                print("[4] phone page loads: 200 OK")

            # phone step 2: exchange
            async with s.get(f"http://{ip}:{port}/pair/exchange?t={token}") as r:
                assert r.status == 200, f"exchange: {r.status}"
                session = (await r.json())["token"]
                print("[5] exchange: pairing token consumed, session token minted")

            # replay of the QR url must now die
            async with s.get(url) as r:
                assert r.status == 401, f"replay should 401, got {r.status}"
                print("[6] QR replay rejected: 401 (one-time holds)")

            # phone step 3: polls with session token
            async with s.get(f"http://{ip}:{port}/api/sessions?t={session}") as r:
                assert r.status == 200
                body = await r.json()
                sessions = body["data"]
                assert len(sessions) >= 1
                la = sessions[0]["last_active"]
                assert isinstance(la, float), f"last_active wire type: {type(la)}"
                print(f"[7] sessions over tailnet: {len(sessions)} sessions, last_active={la} (epoch seconds — phone.html ago() handles it)")

            sid = sessions[0]["id"]
            async with s.get(f"http://{ip}:{port}/api/sessions/{sid}/messages?t={session}") as r:
                assert r.status == 200
                msgs = (await r.json())["data"]
                nonempty = [m for m in msgs if (m.get("content") or "").strip()]
                print(f"[8] message stream: {len(msgs)} msgs, {len(nonempty)} non-empty (blank tool frames skipped by page)")

            # scope: anything off the allow-list stays out
            async with s.get(f"http://{ip}:{port}/api/config?t={session}") as r:
                assert r.status == 403, f"scope leak: {r.status}"
                print("[9] scope allow-list: /api/config -> 403 (never proxied)")

            # desktop pane contract: sessionCount while paired
            assert service.status()["paired"] is True
            count = await proxy.count_sessions()
            assert isinstance(count, int) and count >= 1
            print(f"[10] sessionCount contract: {count} (pane status line)")

            # revoke from the phone side (QA-5 path, session token)
            async with s.post(f"http://{ip}:{port}/pair/revoke?t={session}") as r:
                assert r.status == 200 and (await r.json())["ok"]
                print("[11] phone revoke: 200 OK")
            async with s.get(f"http://{ip}:{port}/api/sessions?t={session}") as r:
                assert r.status == 401
                print("[12] revoked session token dead: 401")

            # health endpoint (no token needed)
            async with s.get(f"http://{ip}:{port}/api/health") as r:
                assert r.status == 200
                print(f"[13] health: {await r.json()}")

        print("\nE2E PROBE: ALL STEPS PASSED — real tailnet interface, real gateway")
    except AssertionError as e:
        ok = False
        print(f"\nE2E PROBE FAILED: {e}")
    finally:
        await transport.stop()
        await proxy.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
