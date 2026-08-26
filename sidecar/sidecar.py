"""Sidecar entrypoint (Phase 0 spike): PairingService + GatewayProxy + TailscaleServe.

Usage:
    python -m sidecar.sidecar --bind 100.100.x.y [--port 8643]
Prints the pairing URL — that URL is what the QR pane encodes (Phase 1).
"""
from __future__ import annotations

import argparse
import asyncio

from .config import Config
from .proxy import GatewayProxy
from .service import PairingService
from .transport import TailscaleServe


async def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes phone-pairing sidecar (Phase 0 spike)")
    parser.add_argument("--bind", default=None, help="tailnet IP to bind (default: from config)")
    parser.add_argument("--port", type=int, default=None, help="sidecar port (default: 8643)")
    parser.add_argument("--gateway", default=None, help="gateway base URL (default: http://127.0.0.1:8642)")
    parser.add_argument("--ttl", type=int, default=None, help="pairing token TTL in seconds (default: 90)")
    parser.add_argument("--allow-loopback", action="store_true", help="TEST ONLY: bind 127.0.0.1")
    args = parser.parse_args()

    cfg = Config()
    bind = args.bind or cfg.bind
    if bind is None:
        raise SystemExit("--bind <tailnet-ip> is required (or set Config.bind)")
    port = args.port or cfg.port
    gateway = args.gateway or cfg.gateway_base
    ttl = args.ttl or cfg.token_ttl_seconds

    service = PairingService(
        ttl_seconds=ttl,
        session_ttl_seconds=cfg.session_ttl_seconds,
    )
    proxy = GatewayProxy(gateway, cfg.api_server_key)
    transport = TailscaleServe(
        bind_ip=bind, port=port, service=service, proxy=proxy,
        allow_loopback=args.allow_loopback,
    )

    token = service.issue()
    try:
        bound = await transport.start()
        print(f"sidecar listening on {bind}:{bound}")
        print(f"pairing-url: {transport.pairing_url(token)}")
        print(f"token-ttl-seconds: {ttl}")
        await asyncio.Event().wait()
    finally:
        await transport.stop()
        await proxy.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
