"""GatewayProxy: localhost-only proxy to the Hermes gateway.

The gateway stays bound to 127.0.0.1; the sidecar is the ONLY surface exposed
to the tailnet. The API key is injected server-side HERE and never leaves the
PC (spec: "phone page never sees it"). Scope is enforced as an allow-list of
(method, path-prefix) pairs — Phase 2 adds per-session verbs with claim checks.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiohttp
from aiohttp import web

# Read-only in Phase 1; Phase 2: send + stop.
# Patterns use {param} for a single path segment (deterministic match, no regex).
ALLOWED_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/{id}/messages"),
    ("POST", "/api/sessions/{id}/chat"),
    ("POST", "/api/sessions/{id}/chat/stream"),
    ("GET", "/v1/runs/{run_id}"),
    ("POST", "/v1/runs/{run_id}/stop"),
    ("POST", "/v1/runs/{run_id}/steer"),
)


def _path_allowed(method: str, path: str) -> bool:
    """Segment-wise allow-list match; ``{param}`` matches one non-empty segment."""
    for allowed_method, pattern in ALLOWED_ROUTES:
        if method != allowed_method:
            continue
        if pattern == path:
            return True
        pattern_segments = pattern.split("/")
        path_segments = path.split("/")
        if len(pattern_segments) != len(path_segments):
            continue
        if all(
            p == s or (p.startswith("{") and p.endswith("}") and s)
            for p, s in zip(pattern_segments, path_segments)
        ):
            return True
    return False


class GatewayProxy:
    def __init__(
        self,
        gateway_base: str,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
    ):
        self._gateway = gateway_base.rstrip("/")
        self._api_key = api_key
        # One session PER EVENT LOOP: the plugin serves routes on the
        # backend's main loop (pane status/count_sessions) while the sidecar
        # handles phone traffic on the autostart thread's loop. Sharing a
        # session across loops makes aiohttp raise "Timeout context manager
        # should be used inside a task" on every proxied request.
        self._sessions: dict[int, aiohttp.ClientSession] = {}
        if session is not None:
            self._sessions[id(session.loop if hasattr(session, "loop") else 0)] = session

    async def _http(self) -> aiohttp.ClientSession:
        lid = id(asyncio.get_running_loop())
        sess = self._sessions.get(lid)
        if sess is None or sess.closed:
            sess = aiohttp.ClientSession()
            self._sessions[lid] = sess
        return sess

    # ── Live run tracking ────────────────────────────────────────────
    # The sidecar sees every phone-originated stream, so "running" pills are
    # tracked here. Survives restarts via a small JSON file.

    def _runs_path(self) -> Path:
        return Path(__file__).resolve().parent / "active_runs.json"

    def _runs_load(self) -> dict:
        try:
            data = json.loads(self._runs_path().read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _runs_save(self, runs: dict) -> None:
        try:
            self._runs_path().write_text(json.dumps(runs))
        except Exception:
            pass  # cosmetic-only state; never break a stream over it

    def mark_run(self, sid: str, running: bool) -> None:
        runs = self._runs_load()
        if running:
            runs[sid] = time.time()
        else:
            runs.pop(sid, None)
        self._runs_save(runs)

    def active_runs(self) -> dict:
        """Sessions with a live/phone-started run; stale marks age out."""
        runs = self._runs_load()
        now = time.time()
        stale = [sid for sid, t in runs.items() if now - t > 1800]
        for sid in stale:
            runs.pop(sid, None)
        if stale:
            self._runs_save(runs)
        return runs

    async def close(self) -> None:
        for sess in self._sessions.values():
            if not sess.closed:
                await sess.close()
        self._sessions.clear()

    async def count_sessions(self) -> int | None:
        """Live session count through the SAME token-scoped allow-list the
        phone uses. Returns None when the gateway is unreachable — the caller
        then omits the field instead of lying with a 0."""
        http = await self._http()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with http.get(
                f"{self._gateway}/api/sessions", headers=headers
            ) as upstream:
                if upstream.status != 200:
                    return None
                body = await upstream.json(content_type=None)
        except (aiohttp.ClientError, ValueError):
            return None
        data = body.get("data") if isinstance(body, dict) else None
        return len(data) if isinstance(data, list) else None

    async def forward(self, request: web.Request) -> web.Response:
        path, method = request.path, request.method
        if not _path_allowed(method, path):
            return web.json_response({"error": "forbidden"}, status=403)
        if method == "POST" and path.endswith("/chat/stream"):
            # SSE must bypass the read()-then-return buffering below or deltas
            # arrive all-at-once at turn end instead of live.
            return await self._forward_stream(request)

        http = await self._http()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        # NEVER forward the pairing token upstream: it would land in gateway
        # logs. The proxy is scoped by the sidecar's allow-list + token check
        # already; the gateway gets a clean query.
        clean_query = {k: v for k, v in request.query.items() if k != "t"}
        # For POST/PUT/PATCH: forward the JSON body (chat, steer, etc.).
        body_bytes: bytes | None = None
        if method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.read()
            except Exception:
                body_bytes = None
            ctype = request.headers.get("Content-Type", "")
            if ctype:
                headers["Content-Type"] = ctype
        try:
            async with http.request(
                method, f"{self._gateway}{path}", params=clean_query, headers=headers,
                data=body_bytes,
            ) as upstream:
                body = await upstream.read()
                content_type = upstream.headers.get("Content-Type", "application/json")
                # aiohttp rejects 'charset=...' inside content_type; strip parameters
                content_type = content_type.split(";")[0].strip()
                return web.Response(
                    status=upstream.status,
                    body=body,
                    content_type=content_type,
                )
        except aiohttp.ClientError as exc:
            return web.json_response({"error": f"gateway unreachable: {exc}"}, status=502)

    async def _forward_stream(self, request: web.Request) -> web.StreamResponse:
        """Byte-level passthrough for the gateway's SSE chat stream."""
        http = await self._http()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
        }
        clean_query = {k: v for k, v in request.query.items() if k != "t"}
        try:
            body_bytes = await request.read()
        except Exception:
            body_bytes = b""
        ctype = request.headers.get("Content-Type", "")
        if ctype:
            headers["Content-Type"] = ctype
        # Live "running" pill: remember the session while this stream is in
        # flight, clear it when the turn ends.
        sid = request.path.split("/")[-2]
        self.mark_run(sid, True)
        try:
            upstream = await http.request(
                "POST", f"{self._gateway}{request.path}",
                params=clean_query, headers=headers, data=body_bytes,
            )
        except aiohttp.ClientError as exc:
            return web.json_response({"error": f"gateway unreachable: {exc}"}, status=502)
        resp = web.StreamResponse(
            status=upstream.status,
            headers={"Content-Type": upstream.headers.get("Content-Type", "text/event-stream"),
                     "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass  # phone walked away; gateway drains its own run
        finally:
            upstream.release()
            self.mark_run(sid, False)
        return resp
