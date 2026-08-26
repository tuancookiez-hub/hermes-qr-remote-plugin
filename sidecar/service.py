"""PairingService: persistent-link pairing (zcode model).

Model (user-directed 2026-08-24): scan ONCE; the saved link keeps working on
every open while the Hermes desktop app runs. Concretely:

* LINK token (what the QR encodes): no countdown — valid for the lifetime of
  the sidecar process AND across app restarts (sha256 hash persisted to disk;
  plaintext lives only in the QR/link on the phone — paseo: treat as password).
  Exchange does NOT consume it: reopening the saved link re-mints a session.
* SESSION token (data access): sliding idle timeout — every authorized request
  extends expiry, so active use never expires; abandoned devices age out.
* issue() (Start/Refresh QR) NEVER unpairs an existing device. Only an
  explicit revoke_all() (Revoke button) kills the link + sessions + disk hash.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

MAX_SESSIONS = 4  # bound session table (phone reloads mint fresh ones)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class _Record:
    expires_at: float | None  # None = no countdown (link tokens)
    revoked: bool = False


class PairingService:
    """Persistent-link pairing lifecycle: reusable link -> sliding sessions."""

    def __init__(
        self,
        ttl_seconds: int = 90,
        session_ttl_seconds: int = 1800,
        clock=time.monotonic,
        persist_path: Path | None = None,
    ):
        self._ttl = ttl_seconds  # legacy field; link tokens no longer count down
        self._session_ttl = session_ttl_seconds
        self._clock = clock
        self._tokens: dict[str, _Record] = {}  # legacy offers (pre-restart scans)
        self._session_tokens: dict[str, _Record] = {}
        self._link_hash: str | None = None
        self._link_plain: str | None = None
        self._persist_path = persist_path
        self._paired = False
        self._device_name: str | None = None
        self._connected_at: float | None = None
        self._last_active: float | None = None
        if persist_path is not None and persist_path.exists():
            try:
                data = json.loads(persist_path.read_text(encoding="utf-8"))
                tok = data.get("link")
                if isinstance(tok, str) and len(tok) >= 32:
                    # Plaintext on the user's own disk — same trust class as
                    # API_SERVER_KEY in .env beside it. Lets the pane re-show
                    # the SAME QR after restarts so the phone never rescans.
                    self._link_plain = tok
                    self._link_hash = _hash(tok)
            except (json.JSONDecodeError, OSError):
                self._link_hash = None
                self._link_plain = None

    @property
    def link_token(self) -> str | None:
        """Plaintext link when known this process (fresh or restored)."""
        return self._link_plain

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @property
    def session_ttl_seconds(self) -> int:
        """Sliding idle timeout: active phones never hit it."""
        return self._session_ttl

    @property
    def linked(self) -> bool:
        """A link exists (fresh or restored from disk)."""
        return self._link_hash is not None

    def issue(self) -> str:
        """Return the persistent link, minting only on first ever start.

        Idempotent: after a restart the restored link is returned unchanged,
        so the pane shows the same QR and the phone's saved link stays valid.
        Only revoke_all() mints a different link afterwards.
        """
        if self._link_plain is not None:
            return self._link_plain
        token = secrets.token_urlsafe(32)
        self._link_hash = _hash(token)
        self._link_plain = token
        self._tokens.clear()
        if self._persist_path is not None:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                self._persist_path.write_text(
                    json.dumps({"link": token, "created": time.time()}),
                    encoding="utf-8",
                )
            except OSError:
                pass  # headless/RO fs: link still works for this process life
        return token

    def check(self, token: str | None) -> bool:
        """Pure validity check — no side effects, no expiry bump."""
        if not token:
            return False
        if token in self._session_tokens:
            return self._session_ok(token)
        return self._link_ok(token)

    def _link_ok(self, token: str) -> bool:
        if self._link_hash is None:
            return False
        return secrets.compare_digest(_hash(token), self._link_hash)

    def _session_ok(self, token: str) -> bool:
        record = self._session_tokens.get(token)
        if record is None or record.revoked:
            return False
        if record.expires_at is not None and self._clock() > record.expires_at:
            self._session_tokens.pop(token, None)
            return False
        return True

    def validate(self, token: str | None) -> bool:
        """Authorize a data access; slides the session window on success."""
        if not token:
            return False
        if token in self._session_tokens:
            if not self._session_ok(token):
                return False
            self._session_tokens[token].expires_at = self._clock() + self._session_ttl
            self._last_active = self._clock()
            return True
        if not self._link_ok(token):
            return False
        # Link-token data access pairs the device (page pre-exchange loads).
        if not self._paired:
            self._paired = True
            self._connected_at = self._clock()
        self._last_active = self._clock()
        return True

    def mint_session_token(self, link_token: str | None) -> str | None:
        """Mint a fresh session from the persistent link. NOT consuming.

        Every open of the saved link gets a new session (old ones age out via
        the sliding idle window); the link itself stays valid.
        """
        if not link_token or not self._link_ok(link_token):
            return None
        session = secrets.token_urlsafe(32)
        self._session_tokens[session] = _Record(expires_at=self._clock() + self._session_ttl)
        # Bound the table: drop oldest when over capacity.
        if len(self._session_tokens) > MAX_SESSIONS:
            oldest = min(self._session_tokens, key=lambda k: self._session_tokens[k].expires_at or 0)
            self._session_tokens.pop(oldest, None)
        if not self._paired:
            self._paired = True
            self._connected_at = self._clock()
        self._last_active = self._clock()
        return session

    def revoke_all(self) -> int:
        """Explicit unpair: kills link + sessions + persisted hash."""
        count = len(self._tokens) + len(self._session_tokens) + (1 if self.linked else 0)
        self._tokens.clear()
        self._session_tokens.clear()
        self._link_hash = None
        self._link_plain = None
        if self._persist_path is not None:
            try:
                self._persist_path.unlink(missing_ok=True)
            except OSError:
                pass
        self._paired = False
        self._device_name = None
        self._connected_at = None
        self._last_active = None
        return count

    def status(self) -> dict:
        """Pairing state for the desktop icon: paired, device info, activity."""
        return {
            "paired": self._paired,
            "device": self._device_name,
            "connected_at": self._connected_at,
            "last_active": self._last_active,
            "token_ttl_seconds": self._ttl,
            "session_ttl_seconds": self._session_ttl,
        }
