/**
 * Hermes Desktop ↔ Phone Remote Control — Phase 1 pairing page.
 *
 * Layout follows ZCode's "Mobile remote control" dialog 1:1 (per @user's
 * request): header → "Scan from phone" section → status card (status + Stop,
 * "Can't scan?" + Refresh QR / Copy link) → large QR in a dashed frame.
 * Icons only, zero emoji, no countdown ring, no badge clutter.
 *
 * A desktop plugin that renders a full "Remote Control" page (sidebar nav row
 * under SESSIONS, below Artifacts — the `SIDEBAR_NAV_AREA` contribution).
 * Scan the QR with a phone already on the tailnet → the phone opens a
 * lightweight web page served by the sidecar and becomes a read-only control
 * surface over desktop sessions.
 *
 * Zero core changes: the QR + token come from the plugin's Python backend
 * (`plugin_api.py` / sidecar) via `ctx.rest('/pair/*')`; the gateway stays
 * localhost-only.
 *
 * Backend contract this page renders against (transport-agnostic — see the
 * PairingTransport port in the spec):
 *   POST /pair/start   -> { port, token, qrDataUrl, expiresIn, url? }
 *   POST /pair/revoke  -> ok
 *   GET  /pair/status  -> { paired, sessionCount?, device?, ... }
 *
 * Security invariants (QA-2 / sentinel):
 *   - The QR image is ALWAYS the backend's `qrDataUrl` (segno, generated
 *     locally — no network). There is NO third-party QR fallback: the pairing
 *     URL carries the live token and must never leave this machine. Without a
 *     `qrDataUrl` we show the copyable link instead.
 *   - Stop/Revoke always calls real `POST /pair/revoke` server-side.
 *
 * Ships OFF by default (`defaultEnabled: false`): it exposes a network pairing
 * surface, so enable it deliberately in Settings -> Plugins.
 *
 * Plain ESM, loaded uncompiled — UI is jsx() calls, not JSX syntax.
 */
import {
  cn,
  haptic,
  host,
  atom,
  useValue,
  Button,
  CopyButton,
  Codicon,
  GlyphSpinner,
  queryClient,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  useMutation,
  usePluginI18n,
  useQuery
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-phone-pairing'
const PAGE_PATH = '/phone-remote'

// Should the page show a live QR offer? Module-level atom so the palette
// commands ("Remote: Pair new device") can flip it and navigate in one step;
// Stop flips it back off.
const $wantQr = atom(false)

// The sidebar nav row is a static SIDEBAR_NAV_AREA contribution — its payload
// is exactly `{codicon, label, path}` (routes.ts `SidebarNavContribution`),
// with no render hook, so it CANNOT carry live state. The paired/unpaired
// indicator lives on the statusbar chip (below) + the page's status dot.
const REFRESH_MS = 8000
// Fallback token window when `/pair/start` doesn't report `expiresIn`; the
// backend is the source of truth either way.
const TOKEN_TTL_SECONDS = 90

// Set in register(); shared by the page components and the palette commands.
let rest = null

async function call(path, opts) {
  if (!rest) throw new Error('plugin backend not ready')
  const r = await rest(path, opts)
  if (r && r.error) throw new Error(r.error)
  return r
}

// One token-issue = one pairing offer. `/pair/start` issues a FRESH token each
// call (QR refresh invalidates the previous one, zcode-style); `/pair/status`
// is the live pairing/device line.
const qKeyQr = ['pair', 'qr']
const qKeyStatus = ['pair', 'status']

// Shared by the page's Stop button and the `Remote: Revoke device` palette
// command. Always a real server-side revoke (QA-5).
async function revokeNow() {
  haptic('tap')
  $wantQr.set(false)
  try {
    await call('/pair/revoke', { method: 'POST' })
  } finally {
    queryClient.invalidateQueries({ queryKey: ['pair'] })
  }
}

// Shared by the page's Start button and `Remote: Pair new device` — open the
// page and start a fresh offer.
function pairNow() {
  haptic('tap')
  $wantQr.set(true)
  host.navigate(PAGE_PATH)
}

/** Set the browser-tab title while the page is mounted. */
function usePageTitle(title) {
  useEffect(() => {
    const prev = document.title
    if (title) document.title = title
    return () => {
      document.title = prev
    }
  }, [title])
}

/** Live pairing state — one shared query drives the chip AND the page. */
function usePairStatus() {
  return useQuery({
    queryKey: qKeyStatus,
    queryFn: () => call('/pair/status'),
    refetchInterval: REFRESH_MS,
    retry: false
  })
}

/** 1s countdown from `total` seconds; fires `onExpire` once it hits 0. */
function useCountdown(total, onExpire) {
  const [left, setLeft] = useState(total)
  const expireRef = useRef(onExpire)
  useEffect(() => {
    expireRef.current = onExpire
  })
  useEffect(() => {
    setLeft(total)
    if (!total || total <= 0) return
    const iv = setInterval(() => {
      setLeft(prev => {
        if (prev <= 1) {
          clearInterval(iv)
          expireRef.current?.()
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(iv)
  }, [total])
  return left
}

/** Rounded-square icon chip for the dialog header (codicon, no emoji). */
function IconChip({ name, className }) {
  return jsx('div', {
    className: cn(
      'flex size-9 shrink-0 items-center justify-center rounded-lg',
      'border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary)',
      className
    ),
    children: jsx(Codicon, { name, className: 'size-4 text-(--ui-text-secondary)' })
  })
}

/** Small colored dot: waiting (amber) / connected (accent) / idle (muted). */
function StatusDot({ tone }) {
  return jsx('span', {
    'aria-hidden': true,
    className: cn(
      'inline-block size-2 shrink-0 rounded-full',
      tone === 'waiting' && 'bg-amber-500',
      tone === 'connected' && 'bg-(--ui-accent)',
      tone === 'idle' && 'bg-(--ui-text-quaternary)',
      tone === 'error' && 'bg-(--ui-danger)'
    )
  })
}

/**
 * Statusbar chip — the live connection dot the sidebar nav row can't carry
 * (the contribution payload is static data). Greyed when idle, accent when a
 * phone is paired; click opens the page.
 */
function PairStatusChip() {
  const status = usePairStatus()
  const paired = Boolean(status.data?.paired)
  const label = paired ? 'phone paired' : 'phone remote'
  return jsx('button', {
    type: 'button',
    onClick: () => { haptic('tap'); host.navigate(PAGE_PATH) },
    className: cn(
      'flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px]',
      'text-(--ui-text-tertiary) transition-colors',
      'hover:bg-(--ui-control-hover-background) hover:text-foreground'
    ),
    title: paired ? 'A phone is paired — open Remote Control' : 'No phone paired — open Remote Control',
    children: jsxs('span', {
      className: 'flex items-center gap-1.5',
      children: [
        jsx('span', {
          className: cn(
            'inline-block size-1.5 rounded-full',
            paired ? 'bg-(--ui-accent)' : 'bg-(--ui-text-quaternary)'
          )
        }),
        label
      ]
    })
  })
}

/** Large centered QR in a white frame inside a dashed container (zcode). */
function QrFrame({ qr, t }) {
  const qrSrc = qr.qrDataUrl || null // NEVER a third-party QR service (QA-2)
  return jsx('div', {
    className: cn(
      'mt-4 flex items-center justify-center rounded-xl border border-dashed',
      'border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-6'
    ),
    children: qrSrc
      ? jsx('div', {
          className: 'rounded-2xl bg-white p-3 shadow-sm',
          children: jsx('img', {
            src: qrSrc,
            alt: t('qrAlt'),
            className: 'block size-[220px]',
            draggable: false
          })
        })
      : jsx('div', {
          className: 'max-w-[300px] text-center text-xs leading-relaxed text-(--ui-text-tertiary)',
          children: t('noQr')
        })
  })
}

function RemotePage() {
  const t = usePluginI18n(ID)
  usePageTitle(t('title'))

  const status = usePairStatus()
  const wantQr = useValue($wantQr)

  const qr = useQuery({
    queryKey: qKeyQr,
    queryFn: () => call('/pair/start', { method: 'POST' }),
    retry: 1,
    staleTime: TOKEN_TTL_SECONDS * 1000,
    enabled: wantQr
  })

  const revoke = useMutation({
    mutationFn: () => call('/pair/revoke', { method: 'POST' }),
    onSuccess: () => {
      $wantQr.set(false)
      queryClient.invalidateQueries({ queryKey: ['pair'] })
    }
  })

  // Persistent link (zcode pair-once): no countdown, no auto-refresh — the QR
  // is always the SAME link. `expiresIn: null` from the backend selects this.
  const persistent = qr.data?.persistent === true || qr.data?.expiresIn == null
  const left = useCountdown(
    wantQr && qr.data && !qr.isFetching && !persistent ? (qr.data.expiresIn ?? TOKEN_TTL_SECONDS) : 0,
    () => {
      if (!persistent && wantQr && !paired && !qr.isError && document.visibilityState === 'visible') {
        queryClient.invalidateQueries({ queryKey: qKeyQr })
      }
    }
  )
  void left

  const paired = Boolean(status.data?.paired)
  const sessionCount = status.data?.sessionCount
  const backendDown = status.isError || (qr.isError && wantQr)
  const tailnetHost = useMemo(() => {
    try {
      return qr.data?.url ? new URL(qr.data.url).host : null
    } catch {
      return null
    }
  }, [qr.data])

  // ── Status line: waiting / connected / stopped / backend down ─────────────
  const tone = backendDown ? 'error' : paired ? 'connected' : wantQr ? 'waiting' : 'idle'
  const statusLabel = backendDown
    ? t('statusError')
    : paired
      ? sessionCount != null
        ? t('statusConnected', sessionCount)
        : t('statusConnectedSimple')
      : wantQr
        ? t('statusWaiting')
        : t('statusStopped')

  const refresh = () => { haptic('tap'); queryClient.invalidateQueries({ queryKey: qKeyQr }) }
  const stop = () => { haptic('tap'); revoke.mutate() }

  // ── Idle (stopped) view — header + one Start button, nothing else ─────────
  if (!wantQr) {
    return jsxs('div', { className: 'mx-auto w-full max-w-[560px] px-4 py-8', children: [
      jsxs('div', { className: 'flex items-start gap-3', children: [
        jsx(IconChip, { name: 'device-mobile' }),
        jsxs('div', { children: [
          jsx('h1', { className: 'text-base font-semibold', children: t('title') }),
          jsx('p', { className: 'mt-1 text-xs leading-relaxed text-(--ui-text-tertiary)', children: t('subtitle') })
        ]})
      ]}),
      jsxs('div', {
        className: cn(
          'mt-6 flex flex-col items-center gap-3 rounded-xl border border-dashed',
          'border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-6 py-10 text-center'
        ),
        children: [
          jsx(StatusDot, { tone: backendDown ? 'error' : 'idle' }),
          jsx('div', { className: 'text-sm', children: backendDown ? t('statusError') : t('idleLine') }),
          backendDown
            ? jsx('div', { className: 'max-w-[340px] text-xs leading-relaxed text-(--ui-text-quaternary)', children: t('unreachableHint') })
            : null,
          jsx(Button, { size: 'sm', onClick: pairNow, children: t('start') })
        ]
      })
    ]})
  }

  // ── Active view: header → scan section → status card → QR frame ───────────
  return jsxs('div', { className: 'mx-auto w-full max-w-[560px] px-4 py-8', children: [
    // Header
    jsxs('div', { className: 'flex items-start gap-3', children: [
      jsx(IconChip, { name: 'device-mobile' }),
      jsxs('div', { children: [
        jsx('h1', { className: 'text-base font-semibold', children: t('title') }),
        jsx('p', { className: 'mt-1 text-xs leading-relaxed text-(--ui-text-tertiary)', children: t('subtitle') })
      ]})
    ]}),

    // Scan from phone
    jsxs('div', { className: 'mt-6', children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx(Codicon, { name: 'device-mobile', className: 'size-3.5 text-(--ui-text-secondary)' }),
        jsx('h2', { className: 'text-sm font-semibold', children: t('scanTitle') })
      ]}),
      jsx('p', { className: 'mt-1 text-xs leading-relaxed text-(--ui-text-tertiary)', children: t('scanBody') })
    ]}),

    // Status card: status + Stop, then Can't-scan? + Refresh / Copy link
    jsxs('div', { className: 'mt-4 rounded-xl border border-(--ui-stroke-secondary) p-4', children: [
      jsxs('div', { className: 'flex items-center justify-between gap-3', children: [
        jsxs('div', { className: 'flex min-w-0 items-center gap-2', children: [
          jsx(StatusDot, { tone }),
          jsx('span', { className: 'truncate text-sm', children: statusLabel })
        ]}),
        jsx(Button, {
          size: 'sm', variant: 'ghost', onClick: stop, disabled: revoke.isPending,
          children: jsxs('span', { className: 'flex items-center gap-1.5', children: [
            jsx(Codicon, { name: 'debug-stop', className: 'size-3.5' }),
            t('stop')
          ]})
        })
      ]}),
      jsxs('div', { className: 'mt-3 flex flex-wrap items-center justify-between gap-2', children: [
        jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: t('cantScan') }),
        jsxs('div', { className: 'flex items-center gap-2', children: [
          jsx(Button, {
            size: 'sm', variant: 'ghost', onClick: refresh, disabled: qr.isFetching,
            children: jsxs('span', { className: 'flex items-center gap-1.5', children: [
              jsx(Codicon, { name: 'sync', className: cn('size-3.5', qr.isFetching && 'animate-spin') }),
              t('refresh')
            ]})
          }),
          qr.data?.url
            ? jsx(CopyButton, { value: qr.data.url, label: t('copyLink') })
            : null
        ]})
      ]})
    ]}),

    // QR (or loading / error inside the dashed frame)
    !qr.data && (qr.isLoading || qr.isFetching)
      ? jsx('div', {
          className: cn(
            'mt-4 flex h-[280px] items-center justify-center rounded-xl border border-dashed',
            'border-(--ui-stroke-secondary) bg-(--ui-bg-secondary)'
          ),
          children: jsx(GlyphSpinner, {})
        })
      : qr.isError
        ? jsx('div', {
            className: cn(
              'mt-4 flex flex-col items-center gap-3 rounded-xl border border-dashed',
              'border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) px-6 py-10 text-center'
            ),
            children: [
              jsx(Codicon, { name: 'warning', className: 'size-5 text-(--ui-danger)' }),
              jsx('div', { className: 'text-sm', children: t('unreachable') }),
              jsx('div', { className: 'max-w-[340px] text-xs leading-relaxed text-(--ui-text-quaternary)', children: t('unreachableHint') }),
              jsx(Button, { size: 'sm', onClick: refresh, children: t('retry') })
            ]
          })
        : jsx(QrFrame, { qr: qr.data ?? {}, t }),

    // One quiet footnote line: tailnet host + phase note
    jsx('p', { className: 'mt-3 text-center text-[0.6875rem] text-(--ui-text-quaternary)', children: tailnetHost ? t('tailnet', tailnetHost) : t('phaseNote') })
  ]})
}

export default {
  id: ID, // must match the folder name
  name: 'Phone Remote Control',
  description: 'zcode-style QR pairing: scan from a phone on the tailnet to see and (later) steer desktop sessions.',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest.bind(ctx)

    ctx.i18n.register({
      en: {
        title: 'Mobile remote control',
        subtitle: 'Scan the QR code or open the link on your phone to view this desktop’s sessions.',
        scanTitle: 'Scan from phone',
        scanBody: 'Use your phone camera to open this desktop remotely. The phone must be on the same tailnet — no app needed.',
        statusWaiting: 'Waiting for phone',
        statusConnected: n => `Phone connected — ${n} session${n === 1 ? '' : 's'}`,
        statusConnectedSimple: 'Phone connected',
        statusStopped: 'Remote control stopped',
        statusError: 'Backend not reachable',
        idleLine: 'Remote control is not running.',
        start: 'Start remote control',
        stop: 'Stop',
        refresh: 'Refresh QR',
        retry: 'Retry',
        cantScan: 'Can’t scan? Open the link on your phone.',
        copyLink: 'Copy link',
        qrAlt: 'Pairing QR code',
        noQr: 'QR unavailable from the backend — copy the pairing link from the button above instead.',
        tailnet: host => `served on ${host} via Tailscale · read-only in Phase 1`,
        phaseNote: 'Read-only in Phase 1 — control verbs arrive in Phase 2.',
        unreachable: 'Backend not reachable',
        unreachableHint: 'The pairing sidecar didn’t answer. Restart the desktop app (the backend mounts at startup) and make sure the plugin is enabled.'
      }
    })

    // Page + its sidebar nav row. The row renders below the built-ins (New
    // session / Skills / Messaging / Artifacts) with the same chrome and
    // lights up while the app is at the route. NOTE: SIDEBAR_NAV_AREA data is
    // static ({codicon, label, path}) — no render hook — so the live
    // paired/unpaired signal lives on the statusbar chip below.
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: PAGE_PATH },
        render: () => jsx(RemotePage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { path: PAGE_PATH, label: 'Mobile Remote', codicon: 'device-mobile' }
      }
    ])

    // Statusbar chip carries the live connection state the nav row can't.
    ctx.register({
      id: 'status',
      area: STATUSBAR_AREAS.right,
      order: 70,
      render: () => jsx(PairStatusChip, {})
    })

    // Palette commands.
    ctx.registerMany([
      {
        id: 'pair',
        area: 'palette',
        data: {
          id: 'phoneRemote.pair',
          label: 'Remote: Pair new device',
          keywords: ['remote', 'phone', 'qr', 'pair', 'scan', 'pairing'],
          run: pairNow
        }
      },
      {
        id: 'revoke',
        area: 'palette',
        data: {
          id: 'phoneRemote.revoke',
          label: 'Remote: Revoke device',
          keywords: ['remote', 'phone', 'revoke', 'unpair', 'kill', 'pairing'],
          run: revokeNow
        }
      }
    ])
  }
}
