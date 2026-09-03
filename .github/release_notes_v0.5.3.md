# v0.5.3 — fix "send but no reply" (dead stored model) + pin UX fixes

## Root cause found

Sessions carry a stored model from whichever model was picked when they
were created (grok-4.6, muse-spark, ling-3.0...). When that model's token
lost access, every turn died with HTTP 403 before any reply — the phone
showed "thinking…" then silence. The stream even completed "successfully"
with the error text as the whole reply.

## Fixed

- **403 self-heal**: when a turn fails with a model-access error, the phone
  locks the session to a working default model (glm-5.3-flash via your
  provider) and retries the same turn once. Sessions fix themselves.
- **Sidecar route**: POST /api/sessions/{id}/model added to the proxy
  allow-list so the self-heal can reach the gateway.
- Pin icon removed from rows (was double UI with the long-press menu);
  pinning is now exclusively long-press → sheet, which also fixes the
  buggy tap-vs-hold conflicts.

## Verified

End-to-end through the live sidecar: session with dead grok-4.6 model
locked to glm-5.3-flash → turn completes with real assistant reply.
