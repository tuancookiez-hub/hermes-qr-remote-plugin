# v0.4.2 — mid-turn survival + UX polish

Phone turns now **finish even if you background the app**.

## Fixed

- **One-token replies on tab-away**: iOS kills backgrounded SSE fetches; the
  disconnect made the gateway interrupt the model run mid-generation (its
  SSE handler treats any client drop as "user walked away"). The sidecar
  now keeps draining the upstream stream until the turn genuinely
  completes, so the full reply persists to the transcript and is waiting
  when you come back.
- **RUNNING pill too small / flipped too early**: larger pill; liveness
  holds 20s instead of 8s so it no longer flips to DONE mid-turn.
- **Header flicker (green ↔ grey)**: one failed poll no longer shows
  offline; two consecutive failures required.
- **Missing back button / whole-page scroll in a session**: opening a
  session carried the list's scroll offset over, pushing the session
  header above the viewport. Detail view now locks body scroll.
- **"thinking…" feedback**: spinner appears immediately after sending,
  before the first streamed token.
- **Phantom "send failed (Load failed)"**: backgrounded-stream kills are
  reported as a pause, not a failure; transcript reconciles on return.

## Notes

`proxy.py` changed → restart Hermes desktop once after updating.
`phone.html` changes are live on page refresh.

**Install / update:**

```bash
hermes plugins install tuancookiez-hub/hermes-qr-remote-plugin
```
