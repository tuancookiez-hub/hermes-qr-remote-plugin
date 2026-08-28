# v0.4.4 — diagnose silent send failures

## Fixed

- **Silent send failures**: If the stream opened but never delivered a frame
  (model error before any delta, or a tunnel that swallows SSE), the user
  stared at "thinking…" forever. Added a 25s timeout + empty-stream check
  so they get an honest answer instead.
- **Specific error messages**: Added distinct messages for 403 (Forbidden),
  404 (Session not found), and 503 (Remote control not active) status codes
  so the user knows exactly what to do.
