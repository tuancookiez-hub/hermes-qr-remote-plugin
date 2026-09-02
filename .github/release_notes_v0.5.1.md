# v0.5.1 — header flicker root fix

## Fixed

- Background polls no longer narrate in the header. The sessions and
  transcript polls were writing "→ sessions" then the count twice per poll
  cycle, which read as a constant flicker. Only manual refresh narrates now.
- Session-count label is change-only on top of that (no rewrite when the
  number hasn't moved).
