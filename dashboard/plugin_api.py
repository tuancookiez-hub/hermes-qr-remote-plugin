"""Hermes Phone Pairing — plugin backend API.

Mounted by the dashboard plugin system at ``/api/plugins/hermes-phone-pairing/``
(manifest.json ``api`` field; FastAPI ``router`` contract). The desktop pane
calls these routes via ``ctx.rest('/pair/*')``.

Owns the pairing sidecar lifecycle for the desktop app:

* lazily starts ``TailscaleServe`` on the tailnet IP (auto-discovered via
  ``tailscale ip -4``; override with ``HERMES_PAIR_BIND``, e.g. for tests)
* ``POST /pair/start``  -> issues a fresh 90s one-time token, builds the
  pairing URL, returns it plus a segno PNG QR as a data-URL. QR refresh
  (new call) invalidates every previous token.
* ``POST /pair/revoke`` -> revokes all outstanding tokens (phone dies)
* ``GET  /pair/status`` -> ``{paired, connected_at, last_active, sessionCount?, ...}``
  for the sidebar icon (greyed/accent) + the pane's status line

The phone never talks to this API — it talks to the sidecar over the tailnet
with ``?t=<token>``; the gateway key is only ever injected by the sidecar's
GatewayProxy. Dashboard session-token auth protects these routes, same as
every other ``/api/plugins/...`` surface.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

# The dashboard loader imports this file by path; make our vendored sidecar
# package importable regardless of the ambient sys.path.
_DASHBOARD_DIR = Path(__file__).resolve().parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

from sidecar.config import Config  # noqa: E402
from sidecar.proxy import GatewayProxy  # noqa: E402
from sidecar.service import PairingService  # noqa: E402
from sidecar.transport import TailscaleServe  # noqa: E402

router = APIRouter()

# Default sidecar port + retry window (spec: ephemeral-ish, retry up to 3).
_PORTS = (8643, 8644, 8645)

# Module-level singleton: lives for the dashboard process lifetime.
_sidecar: tuple[PairingService, GatewayProxy, TailscaleServe] | None = None
_tailnet_ip: str | None = None


def _tailscale_ip() -> str | None:
    """Tailnet IPv4 via `tailscale ip -4`, or None when absent/not logged in."""
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    return out.splitlines()[0].strip()


def _tailscale_installed() -> bool:
    return shutil.which("tailscale") is not None


_SETUP_HINT = (
    "Tailscale is required: the sidecar binds your tailnet IP so ONLY your "
    "devices can reach it. One-time setup: "
    "(1) Install Tailscale on this PC — https://tailscale.com/download "
    "(Windows: winget install tailscale.tailscale). "
    "(2) Open the Tailscale app and sign in (or run `tailscale up`). "
    "(3) Install the Tailscale app on your phone (App Store / Play Store) and "
    "sign in to the SAME account/tailnet. "
    "(4) Come back and press Start again."
)


@router.get("/pair/prereqs")
async def pair_prereqs() -> dict:
    """Setup prerequisites for the pairing flow (Tailscale presence/login).

    Drives the desktop pane's setup card; never raises.
    """
    installed = await asyncio.to_thread(_tailscale_installed)
    ip = await asyncio.to_thread(_tailscale_ip) if installed else None
    ready = bool(installed and ip)
    return {
        "tailscale": {
            "installed": installed,
            "running": ip is not None,
            "ip": ip,
        },
        "ready": ready,
        "hint": None if ready else _SETUP_HINT,
    }


def _discover_tailnet_ip() -> str:
    """Tailnet IP for the sidecar bind: env override > `tailscale ip -4`."""
    global _tailnet_ip
    if _tailnet_ip:
        return _tailnet_ip
    override = os.environ.get("HERMES_PAIR_BIND")
    if override:
        _tailnet_ip = override
        return override
    if not _tailscale_installed():
        raise HTTPException(status_code=503, detail=_SETUP_HINT)
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=_SETUP_HINT) from exc
    if proc.returncode != 0:
        raise HTTPException(
            status_code=503,
            detail=_SETUP_HINT + f" (`tailscale ip -4` said: {proc.stderr.strip() or 'not logged in?'})",
        )
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(
            status_code=500,
            detail="`tailscale ip -4` returned no addresses; is the tailnet up?",
        )
    _tailnet_ip = lines[0]
    return _tailnet_ip


def _link_state_path() -> pathlib.Path:
    """Persistent link state location. Env override exists so tests never
    touch the real file (a stray revoke in a test once deleted the user's
    live pairing)."""
    env = os.environ.get("HERMES_PAIR_LINK_STATE")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parent.parent / "link_state.json"


_SIDECAR_LOCK = threading.Lock()


async def _ensure_sidecar() -> tuple[PairingService, GatewayProxy, TailscaleServe]:
    """Lazily start the sidecar singleton (idempotent).

    The pairing service persists its link to disk (hash + plaintext, same
    trust class as API_SERVER_KEY in .env beside it), so after an app restart
    ``pair_start`` returns the SAME URL and the phone never rescans.
    """
    global _sidecar
    if _sidecar is not None:
        return _sidecar
    # Lock acquisition in a worker thread: blocking acquire must not stall
    # the event loop; release happens in this coroutine's finally.
    await asyncio.to_thread(_SIDECAR_LOCK.acquire)
    try:
        if _sidecar is not None:
            return _sidecar

        bind = _discover_tailnet_ip()
        allow_loopback = os.environ.get("HERMES_PAIR_ALLOW_LOOPBACK") == "1"
        cfg = Config()
        service = PairingService(
            ttl_seconds=cfg.token_ttl_seconds,
            session_ttl_seconds=cfg.session_ttl_seconds,
            persist_path=_link_state_path(),
        )
        proxy = GatewayProxy(cfg.gateway_base, cfg.api_server_key)

        last_error: Exception | None = None
        for port in _PORTS:
            transport = TailscaleServe(
                bind_ip=bind, port=port, service=service, proxy=proxy,
                allow_loopback=allow_loopback,
            )
            try:
                await transport.start()
            except (OSError, ValueError) as exc:
                last_error = exc
                await transport.stop()
                continue
            _sidecar = (service, proxy, transport)
            log.info("phone-pairing sidecar bound to %s:%s", bind, transport.bound_port)
            return _sidecar

        raise HTTPException(
            status_code=500,
            detail=f"could not bind sidecar on {bind}:{_PORTS} ({last_error})",
        )
    finally:
        _SIDECAR_LOCK.release()


def _qr_data_url(payload: str) -> str:
    """segno QR as a PNG data-URL (pure python, no deps)."""
    import segno

    qr = segno.make(payload, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=4, border=2, dark="#000000", light="#ffffff")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


@router.post("/pair/start")
async def pair_start() -> dict:
    """Return the persistent pairing offer: token + URL + QR data-URL.

    Idempotent (pair-once model): first call mints the link, every later call —
    including after app restarts — returns the SAME link. Refreshing the QR
    never kicks a paired phone; only Revoke does.
    """
    service, _proxy, transport = await _ensure_sidecar()
    token = service.issue()
    url = transport.pairing_url(token)
    return {
        "port": transport.bound_port,
        "token": token,
        "qrDataUrl": _qr_data_url(url),
        "expiresIn": None,
        "url": url,
        "persistent": True,
    }


@router.post("/pair/revoke")
async def pair_revoke() -> dict:
    """Revoke every outstanding token. Safe to call with no active sidecar."""
    global _sidecar
    if _sidecar is None:
        return {"ok": True, "revoked": 0}
    service, _proxy, _transport = _sidecar
    revoked = service.revoke_all()
    return {"ok": True, "revoked": revoked}


@router.get("/pair/status")
async def pair_status() -> dict:
    """Pairing state for the desktop icon: greyed when idle, accent when paired.

    Adds ``sessionCount`` (live gateway count) while paired — the pane's
    status line renders "Phone connected — N sessions". Omitted (not 0) when
    the gateway is unreachable, so the pane falls back to the simple label.
    """
    if _sidecar is None:
        return {"paired": False, "device": None, "connected_at": None,
                "last_active": None, "token_ttl_seconds": Config().token_ttl_seconds}
    service, proxy, _transport = _sidecar
    status = service.status()
    if status["paired"]:
        count = await proxy.count_sessions()
        if count is not None:
            status["sessionCount"] = count
    return status


@router.get("/health")
async def health() -> dict:
    """Backend liveness for the pane's status line (no sidecar side effects)."""
    return {
        "ok": True,
        "sidecar_running": _sidecar is not None,
        "tailnet_ip": _tailnet_ip or _discover_tailnet_ip(),
    }


def _autostart_sidecar() -> None:
    """Start the sidecar at backend boot, not on first pane touch.

    Lazy start meant: app restarts with the Mobile Remote pane closed → no
    listener on 8643 → the phone spins forever while desktop says paired.
    Eager start makes the saved link work the moment the app is up. Runs in
    a daemon thread (its own event loop) so import never blocks or fails
    startup; pair/start later reuses this singleton via _ensure_sidecar.
    Skipped under HERMES_PAIR_ALLOW_LOOPBACK=1 (test fixture mode) and when
    tailnet discovery finds nothing — tests bind their own loopback server.
    """

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _boot():
                svc, _proxy, _transport = await _ensure_sidecar()
                # Self-heal: if the link file was lost (or this is the very
                # first boot), mint immediately so the phone works without
                # anyone opening the pane first.
                if not svc.linked:
                    svc.issue()
            loop.run_until_complete(_boot())
            # Keep the loop alive: closing it here left the socket listening
            # but permanently unaccepted (SYN timeouts on every scan).
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001 — boot must survive
            log.warning("phone-pairing autostart failed: %s", exc)

    if os.environ.get("HERMES_PAIR_ALLOW_LOOPBACK") == "1":
        return
    threading.Thread(target=_run, name="phone-pairing-autostart", daemon=True).start()


_autostart_sidecar()
