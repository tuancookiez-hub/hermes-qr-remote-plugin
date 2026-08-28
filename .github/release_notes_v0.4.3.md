# v0.4.3 — composer always visible in detail view

## Fixed

- **Composer hidden below the fold in session detail**: iOS `100dvh` includes the
  area behind the bottom toolbar, so the detail view was taller than the
  visible screen and the input box was pushed off-screen. Pinned `#detail` to
  the visible viewport with `position: fixed; inset: 0`.
