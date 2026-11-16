// The overview's live channel: GET /api/live (Server-Sent Events of every registered repo,
// aifactory/web/app.py live_all). One shared EventSource per tab while someone listens and the
// tab is visible. Listeners only learn that something changed (`files`, `trace`, `resync` or a
// reconnect), debounced like the repo screens' live events; they reload what they show.
import { LIVE_DEBOUNCE_MS } from './live'

type Listener = () => void

const listeners = new Set<Listener>()
let source: EventSource | null = null
let hellos = 0
let timer: ReturnType<typeof setTimeout> | null = null
let visibilityHooked = false

function hidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

function notify() {
  if (timer !== null) return
  timer = setTimeout(() => {
    timer = null
    for (const listener of [...listeners]) listener()
  }, LIVE_DEBOUNCE_MS)
}

function open() {
  if (source || typeof EventSource === 'undefined' || hidden()) return
  hellos = 0
  const es = new EventSource('/api/live')
  // a second hello means the browser reconnected: events may have been missed
  es.addEventListener('hello', () => {
    hellos += 1
    if (hellos > 1) notify()
  })
  for (const kind of ['files', 'trace', 'resync']) es.addEventListener(kind, notify)
  source = es
}

function close() {
  source?.close()
  source = null
}

function onVisibility() {
  if (hidden()) close()
  else if (listeners.size > 0 && !source) open()
}

/** Call `listener` after changes in any registered repo; returns the unsubscribe function. */
export function onOverviewLive(listener: Listener): () => void {
  listeners.add(listener)
  if (!visibilityHooked && typeof document !== 'undefined') {
    visibilityHooked = true
    document.addEventListener('visibilitychange', onVisibility)
  }
  open()
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      close()
      if (timer !== null) clearTimeout(timer)
      timer = null
    }
  }
}

/** Test helper: forget the shared EventSource and every listener. */
export function resetOverviewLiveForTests(): void {
  listeners.clear()
  close()
  if (timer !== null) clearTimeout(timer)
  timer = null
  hellos = 0
}
