# v0.5.0 — mature chat UX

Borrows the proven mobile patterns from NextChat (75k+ stars) and the
self-hosted push pattern from ntfy.

## Added

- **Image attachments** — tap the 📷 button to attach up to 4 photos per
  message. Client-side downscale (1280px, JPEG) keeps payloads small; sent
  in OpenAI vision format which the gateway already supports. Thumbnails
  show in the composer and inline in your message bubble.
- **Message actions** — tap any message for copy / use-as-input actions;
  double-tap your own bubble to re-edit it in the composer.
- **Session long-press menu** — hold a session row (haptic tick) for
  Pin / Unpin + Open in a bottom sheet; right-click works on desktop.
  No more accidental pin taps from the always-visible icon.

## Fixed

- **Auto-scroll lock** — the transcript no longer yanks you to the bottom
  while you're reading history mid-stream. A "↓ latest" pill appears when
  new content arrives off-screen.
- **Header flicker** — the top-right session count only rewrites when the
  count actually changes, instead of every poll cycle.
- **Sidecar body cap** — raised to 12 MB so image payloads don't 413.
