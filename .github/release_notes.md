# v0.4.0 — QR Pairing over Tailscale

First public release. Install with:

```bash
hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin
```

Or paste the README's agent prompt into your Hermes chat and let it drive.

## Highlights

- **Pair once, use forever** — scan the QR a single time; the saved link keeps
  working whenever Hermes desktop is running. No rescans, no countdowns.
- **Full control surface on your phone** — session list, live streaming
  replies, tool activity chips, send + run-scoped stop.
- **Private by design** — the sidecar binds only your tailnet interface;
  nothing touches the internet. Revoke kills access instantly.
- **Guided setup** — new `/pair/prereqs` endpoint detects missing Tailscale
  (installed? signed in?) and the desktop pane shows a step-by-step checklist
  card instead of an error. `pair/start` returns actionable guidance (503)
  when prerequisites are missing.

## Also in this release

- Session toolbar: refresh, organize menu (sort by timeline/source, order by
  last-active/created), RUNNING/DONE status pills, pinnable sessions
- Splash screen with the official Hermes logo; clear failure card on
  invalid connections
- `AGENTS.md` so *your* agent can install and set everything up for you
- Authorship: plugin.yaml/README credit Tuan Dev (tuancookiez-hub)

## Fixes

- CI: root `conftest.py` puts the repo root on sys.path so bare `pytest`
  collects tests correctly (#1)

**Full Changelog**: initial public release (`7c17855` → `9916da3`)
