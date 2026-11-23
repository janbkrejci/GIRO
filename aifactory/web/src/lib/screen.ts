import { ApiError, type Envelope } from './api'

export interface ScreenInfo { enabled: boolean; supported: boolean; reason: string | null; width: number | null; height: number | null }
export type ScreenInput =
  | { type: 'move'; x: number; y: number }
  | { type: 'button'; button: 'left' | 'middle' | 'right'; down: boolean; x: number; y: number }
  | { type: 'wheel'; dx: number; dy: number }
  | { type: 'key'; code: string; down: boolean }
  | { type: 'release_all' }

async function readEnvelope<T>(response: Response): Promise<T> {
  const body = await response.json() as Envelope<T>
  if (!response.ok || !body.ok || body.data === null) throw new ApiError(body.error?.code ?? 'screen_error', 'Screen request failed')
  return body.data
}
export async function fetchScreen(signal?: AbortSignal): Promise<ScreenInfo> {
  return readEnvelope(await fetch('/api/screen', { signal }))
}
export async function inputScreen(events: ScreenInput[]): Promise<void> {
  await readEnvelope(await fetch('/api/screen/input', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events }), keepalive: true,
  }))
}

/** Parse bounded length-prefixed JPEGs without assuming fetch chunk boundaries. */
export async function streamScreen(signal: AbortSignal, onFrame: (frame: Uint8Array<ArrayBuffer>) => void): Promise<void> {
  const response = await fetch('/api/screen/stream', { signal })
  if (!response.ok) { await readEnvelope(response); throw new Error('Screen stream failed') }
  if (!response.body) throw new Error('Missing screen stream')
  const reader = response.body.getReader()
  const cancel = () => { void reader.cancel().catch(() => {}) }
  signal.addEventListener('abort', cancel, { once: true })
  const header = new Uint8Array(4)
  let headerUsed = 0
  let frame: Uint8Array<ArrayBuffer> | undefined
  let used = 0
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done || signal.aborted) break
      let offset = 0
      while (offset < value.length) {
        if (!frame) {
          const count = Math.min(4 - headerUsed, value.length - offset)
          header.set(value.subarray(offset, offset + count), headerUsed)
          headerUsed += count; offset += count
          if (headerUsed < 4) continue
          const length = new DataView(header.buffer).getUint32(0)
          if (!length || length > 64 * 1024 * 1024) throw new Error('Invalid screen frame length')
          frame = new Uint8Array(length); used = 0; headerUsed = 0
        }
        const count = Math.min(frame.length - used, value.length - offset)
        frame.set(value.subarray(offset, offset + count), used)
        used += count; offset += count
        if (used === frame.length) { onFrame(frame); frame = undefined }
      }
    }
    if (!signal.aborted && (frame || headerUsed)) throw new Error('Truncated screen frame')
  } finally {
    signal.removeEventListener('abort', cancel)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
