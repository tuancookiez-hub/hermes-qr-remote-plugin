# Hermes QR Remote Plugin

**QR Pairing for Hermes**: scan once with your phone, then view and send
messages to your desktop's Hermes sessions from the phone browser — live
streaming replies, tool activity, stop — for as long as Hermes desktop is
running. No rescan, no cloud relay: the sidecar binds your Tailscale interface
and nothing else.

![author](https://img.shields.io/badge/author-Tuan%20Dev-blue) ![status](https://img.shields.io/badge/hermes-plugin-purple) ![version](https://img.shields.io/badge/version-0.4.1-green) ![security scan](https://img.shields.io/badge/plugin%20scan-safe-brightgreen) ![tests](https://github.com/tuancookiez-hub/hermes-qr-remote-plugin/actions/workflows/tests.yml/badge.svg)

**Author:** Tuan Dev · [github.com/tuancookiez-hub](https://github.com/tuancookiez-hub)

## ⚡ Easiest install — just tell your agent

Paste this into your Hermes chat (CLI, TUI, or desktop) and it will handle everything:

```text
Install the Phone Remote plugin for me: run
  hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin
Then check the setup prerequisites for me (is Tailscale installed and signed in?
run `tailscale ip -4` to verify). If anything is missing, walk me through fixing
it step by step. When ready: restart Hermes desktop once, then guide me through
scanning the QR from the Mobile Remote pane in the desktop app. The plugin
ships a full agent-installation guide — your agent can read it if it needs
details.
```

Your agent will:
1. Install the plugin and verify it loads (`hermes plugins list`)
2. Check Tailscale on this machine — install/sign-in guidance if needed
3. Remind you to put Tailscale on your **phone** too (same account)
4. Restart Hermes desktop so the backend mounts the plugin
5. Walk you through the QR scan in the **Mobile Remote** pane

> Prefer manual? Run `hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin`,
> then see [Requirements](#requirements).

## What it does

- **Pair once** — a QR in the desktop app; scanning mints a persistent link.
  Reopen that link any time while Hermes runs. Revoking kills all tokens.
- **Full session list** on the phone: titles, previews, ages, source badges,
  status pills (RUNNING/DONE), filter pills (All / Running / Pinned), pinning,
  refresh + organize menu (sort by timeline/source, order by last-active/created).
- **Send from the phone** — SSE-streamed replies token-by-token, tool chips,
  run-scoped Stop button, pill-input composer that morphs into a stop button.
- **Logo splash** + official Hermes home-screen icon when added to the iPhone/
  Android home screen (standalone display, no browser chrome).

## Install

```bash
hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin
```

Then restart Hermes desktop once so the backend mounts the plugin routes.

Requirements:

- Hermes Agent (desktop app recommended — the pane lives there)
- Python deps auto-installed on first run if missing: `aiohttp`, `fastapi`, `segno`
- [Tailscale](https://tailscale.com) on both machines (the sidecar binds ONLY
  the tailnet IP — never `0.0.0.0`)

## Use

1. Desktop → sidebar → **Remote Control**
2. Click Start → scan the QR with your phone (Tailscale connected)
3. Save/open the link on the phone — bookmark it or add to home screen
4. Any time Hermes desktop is open: open the link → splash → sessions

Security model:

| Layer | Guarantee |
|---|---|
| Binding | tailnet interface only; loopback is opt-in (`HERMES_PAIR_ALLOW_LOOPBACK=1`) |
| Auth | HMAC pairing token → rotating session tokens; replay-proof exchange |
| Scope | phone requests pass an allow-list only; pairing token never forwarded upstream |
| Revoke | one tap kills every outstanding token (pairing + sessions) |

## Architecture (one repo, both halves)

```
plugin.yaml            # agent manifest (native plugin, manifest_version 1)
__init__.py            # agent register() hook (no-op; integration is dashboard+desktop)
dashboard/
  manifest.json        # dashboard extension: mounts plugin_api.py router
  plugin_api.py        # FastAPI router: /pair/start|revoke|status + eager sidecar start
desktop/
  plugin.js            # desktop pane (SIDEBAR_NAV_AREA contribution, QR page)
sidecar/               # aiohttp server the backend spawns in-process
  service.py           #   persistent-link pairing state machine
  transport.py         #   tailnet-bound HTTP surface + /assets + pins/runs endpoints
  proxy.py             #   allow-listed gateway proxy w/ SSE passthrough + run tracking
  config.py            #   env-driven configuration
  phone.html           #   the whole phone UI (single file, no build step)
tests/                 # pytest suite (19 tests): pairing, roundtrip, SSE, list features
tools/e2e_tailnet_probe.py
```

The desktop runtime watches `<hermes>/plugins/<name>/desktop/plugin.js`, the
dashboard scans `<hermes>/plugins/<name>/dashboard/manifest.json` — one folder,
both surfaces, installed by one command.

## Configuration (env, all optional)

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_PAIR_BIND` | auto-discovered tailnet IP | override bind interface |
| `HERMES_PAIR_PORTS` | `8643,8644` | candidate ports |
| `HERMES_PAIR_ALLOW_LOOPBACK` | unset | dev-only loopback binding |
| `HERMES_PAIR_LINK_STATE` | `<plugin>/link_state.json` | persistent link storage |

## Development

```bash
pip install -e . || pip install aiohttp fastapi segno pytest httpx
pytest tests/
```

The sidecar reads `phone.html` from disk per request — page tweaks are live
after a refresh; backend changes need a Hermes restart.

## License

MIT
