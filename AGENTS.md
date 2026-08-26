# Agent Guide — hermes-phone-pairing (Phone Remote)

You are reading this because your user wants to set up **Phone Remote**: scanning
a QR once, then viewing/sending messages to their Hermes desktop sessions from
their phone. Your job is to install this plugin cleanly and walk them through
the one-time Tailscale setup. Be proactive: run the checks yourself where you
can, and only ask the user for things only they can do (signing in, using their
phone).

## What this plugin is

A unified Hermes plugin with two halves:

- `dashboard/` + sidecar (`dashboard/plugin_api.py`, `sidecar/`) — the desktop
  backend mounts the FastAPI router at startup; it spawns a small aiohttp
  "sidecar" that serves a web page to the phone.
- `desktop/` — a Remote Control pane in the Hermes desktop app (QR + status).

Security model (tell the user this up front): the sidecar binds ONLY the
machine's tailnet IP — never `0.0.0.0` — so nothing is exposed to the internet.
Pairing tokens are HMAC-verified; revoking kills all sessions instantly.

## Install

```bash
hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin
```

Then restart Hermes desktop once (the backend mounts plugin routes only at
startup). Verify:

```bash
hermes plugins list            # shows hermes-phone-pairing
```

## Prerequisites check — do this BEFORE telling the user "done"

1. **Tailscale installed on the desktop machine?**

   ```bash
   tailscale ip -4        # prints 100.x.y.z when installed AND logged in
   ```

   - Command not found → not installed. Offer:
     - Windows: `winget install tailscale.tailscale`
     - macOS: `brew install --cask tailscale`
     - Linux: install via the one-line script at tailscale.com/download (run it yourself after reviewing it)
     - Or download from https://tailscale.com/download

2. **Tailscale logged in / connected?** If `tailscale ip -4` errors or is empty,
   the user must open the Tailscale app and sign in (or run `tailscale up`).
   You cannot do this step for them — it needs their browser/auth.

3. **Phone has Tailscale?** Ask them to install the Tailscale app on their
   phone (App Store / Play Store) and sign in to the SAME account/tailnet,
   then toggle it ON. Without the phone on the tailnet, the QR page will not
   load on their phone.

4. **Firewall:** the first time the sidecar binds port 8643, Windows may show a
   firewall prompt — the user should click Allow (private networks).

## The pairing flow (what to tell the user)

1. Open Hermes Desktop → sidebar → **Mobile Remote**
2. Press **Start remote control**
3. Scan the QR with the phone camera (or press "Copy link" and send it to
   the phone over any channel)
4. On the phone: bookmark the page or Add to Home Screen — from then on,
   opening the link works whenever the desktop app is running. No rescan.

The plugin itself also detects missing prerequisites: the pane shows a
"One-time setup" checklist card (GET `/pair/prereqs`) when Tailscale is absent
or signed out, with copyable setup steps. Point users there if they skip ahead.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pane says "One-time setup needed" | Tailscale missing/not logged in | Follow the checklist in the pane |
| `pair/start` returns 503 | same as above, API-level | same fix |
| Phone page won't load | phone not on tailnet, or firewall block | connect Tailscale on phone; allow python:8643 |
| "Invalid connection" on phone | link revoked or desktop closed | reopen desktop, Start again (link persists across restarts unless revoked) |
| Sessions list empty but desktop has chats | backend restarted after plugin install was skipped | restart desktop once |

## Env overrides (rarely needed)

- `HERMES_PAIR_BIND` — pin the bind IP instead of auto-discovery
- `HERMES_PAIR_PORTS` — default `8643,8644`
- `HERMES_PAIR_ALLOW_LOOPBACK=1` — dev-only loopback binding (NOT for daily use)

## Uninstall

```bash
hermes plugins remove hermes-phone-pairing
```

(Also remove the saved link/home-screen shortcut on the phone.)
