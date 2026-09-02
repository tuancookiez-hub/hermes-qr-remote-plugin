"""PairingTransport port + TailscaleServe adapter (v1).

The QR pane only ever sees pairing_url(); the transport behind it is swappable
(RelayDialOut is the future zcode-style adapter). TailscaleServe binds ONLY to
the tailnet interface — never 0.0.0.0 — so nothing is exposed to LAN/WAN.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from aiohttp import web

from .proxy import GatewayProxy
from .service import PairingService


def is_tailnet_ip(ip: str) -> bool:
    """Tailscale uses the CGNAT range 100.64.0.0/10 (100.64–100.127.x.x)."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        second = int(parts[1])
    except ValueError:
        return False
    return parts[0] == "100" and 64 <= second <= 127


class PairingTransport(Protocol):
    def pairing_url(self, token: str) -> str: ...


class TailscaleServe:
    """Serves the phone page + scoped gateway proxy on the tailnet interface."""

    def __init__(
        self,
        bind_ip: str,
        port: int,
        service: PairingService,
        proxy: GatewayProxy,
        allow_loopback: bool = False,  # test hook: permit 127.0.0.1 binding
    ):
        self.bind_ip = bind_ip
        self.port = port
        self.service = service
        self.proxy = proxy
        self.bound_port: int | None = None
        if not (allow_loopback and bind_ip == "127.0.0.1") and not is_tailnet_ip(bind_ip):
            raise ValueError(
                f"refusing to bind {bind_ip!r}: TailscaleServe binds tailnet IPs only "
                "(100.64.0.0/10); pass allow_loopback=True for local tests"
            )
        self._runner: web.AppRunner | None = None

    def pairing_url(self, token: str) -> str:
        port = self.bound_port if self.bound_port is not None else self.port
        return f"http://{self.bind_ip}:{port}/?t={token}"

    async def start(self) -> int:
        # Images arrive as base64 data URLs inside the chat/stream JSON;
        # aiohttp's default 1 MB client_max_size would 413 them. 12 MB
        # mirrors the gateway's 10 MB MAX_REQUEST_BYTES with headroom for
        # base64 inflation.
        app = web.Application(client_max_size=12 * 1024 * 1024)
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/assets/{name}", self._asset),
                web.get("/api/health", self._health),
                web.get("/pair/status", self._pair_status),
                web.get("/pair/exchange", self._pair_exchange),
                web.post("/pair/revoke", self._pair_revoke),
                web.get("/api/runs", self._runs),
                web.get("/api/pins", self._pins_get),
                web.post("/api/pins", self._pins_post),
                # catch-all: EVERY other path goes through the allow-list check,
                # so disallowed routes answer 403 (never proxied, no discovery)
                web.route("*", "/{tail:.*}", self._proxy_route),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.bind_ip, self.port)
        await site.start()
        self.bound_port = site._server.sockets[0].getsockname()[1]
        return self.bound_port

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def serve_forever(self) -> None:
        await self.start()
        await asyncio.Event().wait()

    async def _index(self, request: web.Request) -> web.Response:
        if not self.service.check(request.query.get("t")):
            return web.Response(text="pairing token missing or expired", status=401)
        html = (Path(__file__).parent / "phone.html").read_text(encoding="utf-8")
        # no-store: Safari/home-screen shortcuts must never replay a stale
        # page against a newer sidecar (silent-mismatch bugs look like
        # eternal "connecting…").
        return web.Response(
            text=html, content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def _asset(self, request: web.Request) -> web.Response:
        """Static assets (logo). Path-segment validated; safe suffixes only."""
        name = request.match_info["name"]
        if name != "logo.png":
            return web.Response(status=404)
        path = (Path(__file__).parent / "assets" / name).resolve()
        if not path.is_relative_to((Path(__file__).parent / "assets").resolve()):
            return web.Response(status=403)
        if not path.exists():
            return web.Response(status=404)
        return web.Response(
            body=path.read_bytes(), content_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "bound": self.bind_ip, "port": self.bound_port})

    async def _proxy_route(self, request: web.Request) -> web.Response:
        if not self.service.validate(request.query.get("t")):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await self.proxy.forward(request)

    async def _runs(self, request: web.Request) -> web.Response:
        if not self.service.check(request.query.get("t")):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"data": self.proxy.active_runs()})

    def _pins_path(self):
        return Path(__file__).resolve().parent / "pins.json"

    async def _pins_get(self, request: web.Request) -> web.Response:
        """Phone-level pinned sessions (view preference, like zcode's)."""
        if not self.service.check(request.query.get("t")):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            ids = json.loads(self._pins_path().read_text())
        except Exception:
            ids = []
        return web.json_response({"data": ids if isinstance(ids, list) else []})

    async def _pins_post(self, request: web.Request) -> web.Response:
        if not self.service.check(request.query.get("t")):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        sid, pin = body.get("id"), bool(body.get("pinned"))
        pins = []
        try:
            loaded = json.loads(self._pins_path().read_text())
            if isinstance(loaded, list):
                pins = loaded
        except Exception:
            pass
        pins = [p for p in pins if p != sid]
        if pin:
            pins.insert(0, sid)
        self._pins_path().write_text(json.dumps(pins))
        return web.json_response({"ok": True, "data": pins})

    async def _pair_status(self, request: web.Request) -> web.Response:
        """Pairing state for the desktop icon (greyed/accent).

        Uses the side-effect-free check() so polling never counts as a
        connection — only actual data access pairs the device.
        """
        if not self.service.check(request.query.get("t")):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(self.service.status())

    async def _pair_exchange(self, request: web.Request) -> web.Response:
        """One-time pairing token -> rotating session token (replay-proof).

        The phone calls this ONCE after loading the page: the pairing token is
        consumed, a session token is minted for all subsequent polls. Replaying
        the pairing URL afterwards gets 401.
        """
        token = request.query.get("t")
        if not self.service.validate(token):
            return web.json_response({"error": "unauthorized"}, status=401)
        session = self.service.mint_session_token(token)
        if session is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"token": session})

    async def _pair_revoke(self, request: web.Request) -> web.Response:
        """Revoke from the phone (one-tap, spec §5): kills every outstanding
        token — pairing AND session — so the desktop shows unpaired.

        Authorized by EITHER a live pairing token or a session token.
        """
        token = request.query.get("t")
        if not self.service.check(token):
            return web.json_response({"error": "unauthorized"}, status=401)
        revoked = self.service.revoke_all()
        return web.json_response({"ok": True, "revoked": revoked})
