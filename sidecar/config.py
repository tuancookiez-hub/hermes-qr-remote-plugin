"""Sidecar configuration: gateway endpoint, API key, bind address, TTLs."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field


def _default_env_file() -> pathlib.Path:
    """Hermes .env location: %LOCALAPPDATA%/hermes/.env on Windows, ~/.hermes/.env elsewhere."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return pathlib.Path(local) / "hermes" / ".env"
    return pathlib.Path.home() / ".hermes" / ".env"


def load_env_file(path: pathlib.Path) -> dict[str, str]:
    """Minimal dotenv reader (no external dep): KEY=VALUE lines, quotes stripped."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@dataclass
class Config:
    gateway_base: str = "http://127.0.0.1:8642"
    bind: str | None = None  # tailnet IP; required unless a test passes 127.0.0.1 + allow_loopback
    port: int = 8643
    token_ttl_seconds: int = 90  # one-time pairing window (QR scan)
    session_ttl_seconds: int = 86400  # rotated session token (24h, spec §3.3)
    env_file: pathlib.Path = field(default_factory=_default_env_file)

    @property
    def api_server_key(self) -> str:
        """API_SERVER_KEY is read from env or the Hermes .env — injected server-side
        by GatewayProxy, never sent to the phone and never printed."""
        key = os.environ.get("API_SERVER_KEY")
        if not key:
            key = load_env_file(self.env_file).get("API_SERVER_KEY", "")
        if not key:
            raise RuntimeError(f"API_SERVER_KEY not found in env or {self.env_file}")
        return key
