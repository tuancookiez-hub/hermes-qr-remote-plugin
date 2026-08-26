"""Hermes QR Remote — QR phone-pairing plugin.

Unified package:
- Agent half: ``dashboard/plugin_api.py`` is mounted by the dashboard/serve
  backend at startup; it starts the QR pairing sidecar (aiohttp) that binds
  the tailnet interface and serves the phone page.
- Desktop half: ``desktop/plugin.js`` renders the Remote Control pane in the
  desktop app and talks to the backend via ``POST /pair/start`` etc.

Install: ``hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin``
"""

from __future__ import annotations

__version__ = "0.3.0"


def register(ctx) -> None:
    """Agent-side registration hook (no hooks/tools contributed yet).

    The real integration points are the dashboard-mounted FastAPI router in
    ``dashboard/plugin_api.py`` and the desktop runtime door in
    ``desktop/plugin.js``. Kept as a no-op so the agent loader treats the
    package as a valid native plugin without doing anything at chat time.
    """
    return None
