import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchScreen, inputScreen, streamScreen } from './screen'

afterEach(() => vi.unstubAllGlobals())
function stream(chunks: number[][]) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({ start(controller) {
    for (const chunk of chunks) controller.enqueue(new Uint8Array(chunk))
    controller.close()
  } }))))
}
describe('screen client', () => {
  it('reassembles split headers and JPEG payloads and multiple frames per chunk', async () => {
    stream([[0], [0, 0], [3, 1], [2], [3, 0, 0, 0, 2, 4, 5, 0, 0, 0, 1, 6]])
    const frames: number[][] = []
    await streamScreen(new AbortController().signal, frame => frames.push([...frame]))
    expect(frames).toEqual([[1, 2, 3], [4, 5], [6]])
  })
  it.each([[0, 0, 0, 0], [255, 255, 255, 255], [0, 0], [0, 0, 0, 2, 1]])('rejects invalid or truncated frames %j', async (...chunk) => {
    stream([chunk])
    await expect(streamScreen(new AbortController().signal, () => {})).rejects.toThrow()
  })
  it('cancels a pending read on abort', async () => {
    const cancel = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({ cancel }))))
    const controller = new AbortController()
    const done = streamScreen(controller.signal, () => {})
    await new Promise(resolve => setTimeout(resolve, 0))
    controller.abort()
    await done
    expect(cancel).toHaveBeenCalledOnce()
  })
  it('reads availability and posts input batches', async () => {
    const info = { enabled: false, supported: true, reason: null, width: 800, height: 600 }
    const fetch = vi.fn(async () => new Response(JSON.stringify({ ok: true, data: info })))
    vi.stubGlobal('fetch', fetch)
    expect(await fetchScreen()).toEqual(info)
    await inputScreen([{ type: 'release_all' }])
    expect(fetch).toHaveBeenLastCalledWith('/api/screen/input', expect.objectContaining({ method: 'POST', body: JSON.stringify({ events: [{ type: 'release_all' }] }), keepalive: true }))
  })
})
