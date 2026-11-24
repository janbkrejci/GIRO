import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { setLocale } from '../../lib/i18n'
import { fetchScreen, inputScreen, streamScreen, type ScreenInfo, type ScreenInput } from '../../lib/screen'
import ScreenOverlay from './ScreenOverlay.vue'
import { chooseOption, selectLabels } from '../../test/select'
vi.mock('../../lib/screen', () => ({ fetchScreen: vi.fn(), inputScreen: vi.fn(), streamScreen: vi.fn() }))
let wrapper: VueWrapper | undefined
let frames: (frame: Uint8Array<ArrayBuffer>) => void
let endStream: () => void
let signal: AbortSignal
let callbacks: Map<number, FrameRequestCallback>
let nextId: number
const drawImage = vi.fn()
const info: ScreenInfo = { enabled: true, supported: true, reason: null, width: 200, height: 100 }
const bitmap = () => ({ width: 200, height: 100, close: vi.fn() })
function tick() {
  const current = [...callbacks.values()]
  callbacks.clear()
  current.forEach(callback => callback(0))
}
async function open(value = info) {
  vi.mocked(fetchScreen).mockResolvedValue(value)
  wrapper = mount(ScreenOverlay, { attachTo: document.body })
  await flushPromises()
  return wrapper
}
async function showFrame() {
  frames(new Uint8Array([1]))
  await flushPromises()
  tick()
  await flushPromises()
}
function batches() { return vi.mocked(inputScreen).mock.calls.map(call => call[0]) }
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.resetAllMocks()
  setLocale('en')
  callbacks = new Map(); nextId = 0
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callbacks.set(++nextId, callback); return nextId })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => callbacks.delete(id))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('createImageBitmap', vi.fn(async () => bitmap()))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage, clearRect: vi.fn() } as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 10, top: 20, width: 400, height: 400 } as DOMRect)
  vi.mocked(inputScreen).mockResolvedValue()
  vi.mocked(streamScreen).mockImplementation((abortSignal, onFrame) => {
    signal = abortSignal; frames = onFrame
    return new Promise(resolve => { endStream = resolve })
  })
})
afterEach(() => {
  wrapper?.unmount(); wrapper = undefined
  vi.useRealTimers()
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})
describe('ScreenOverlay', () => {
  it('letterboxes frames and maps only points inside the image; batches mouse buttons and wheel', async () => {
    const view = await open()
    await showFrame()
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 100, 400, 200)
    const canvas = view.get('canvas')
    await canvas.trigger('pointerdown', { clientX: 210, clientY: 50, button: 0 })
    tick(); await flushPromises()
    expect(inputScreen).not.toHaveBeenCalled()
    for (const button of [0, 1, 2]) {
      await canvas.trigger('pointerdown', { clientX: 110, clientY: 170, button })
      await canvas.trigger('pointerup', { clientX: 310, clientY: 270, button })
    }
    await canvas.trigger('wheel', { clientX: 210, clientY: 220, deltaX: 100, deltaY: 200 })
    tick(); await flushPromises()
    expect(batches()[0]).toEqual([
      ...(['left', 'middle', 'right'] as const).flatMap(button => [
        { type: 'button', button, down: true, x: .25, y: .25 },
        { type: 'button', button, down: false, x: .75, y: .75 },
      ]), { type: 'move', x: .5, y: .5 }, { type: 'wheel', dx: 1, dy: -2 },
    ])
    const context = new MouseEvent('contextmenu', { cancelable: true })
    canvas.element.dispatchEvent(context)
    expect(context.defaultPrevented).toBe(true)
  })
  it('switches sessions, releases the old target and translates the session labels', async () => {
    const view = await open({ ...info, sessions: [
      { id: 1, kind: 'console', state: 'active' },
      { id: 2, kind: 'remote', state: 'disconnected' },
    ] })
    vi.mocked(streamScreen).mockImplementation((abortSignal, onFrame) => {
      signal = abortSignal; frames = onFrame
      return new Promise(resolve => { endStream = resolve; abortSignal.addEventListener('abort', () => resolve(), { once: true }) })
    })
    // The original test stream also needs to finish when the first switch aborts it.
    const oldSignal = signal
    oldSignal.addEventListener('abort', () => endStream(), { once: true })
    await showFrame()
    await chooseOption(view, '[data-test="desktop-session"]', '2')
    await flushPromises()
    expect(oldSignal.aborted).toBe(true)
    expect(inputScreen).toHaveBeenCalledWith([{ type: 'release_all' }], undefined)
    expect(streamScreen).toHaveBeenLastCalledWith(expect.any(AbortSignal), expect.any(Function), 2)
    await showFrame()
    await view.get('canvas').trigger('keydown', { code: 'KeyA' })
    tick(); await flushPromises()
    expect(inputScreen).toHaveBeenLastCalledWith([{ type: 'key', code: 'KeyA', down: true }], 2)
    expect((await selectLabels(view, '[data-test="desktop-session"]')).join(' ')).toContain('Disconnected')
    setLocale('cs'); await flushPromises()
    expect((await selectLabels(view, '[data-test="desktop-session"]')).join(' ')).toContain('Odpojen\u00e1')
    expect((await selectLabels(view, '[data-test="desktop-session"]')).join(' ')).toContain('Fyzick\u00e1 konzole')
    await chooseOption(view, '[data-test="desktop-session"]', '1'); await flushPromises()
    expect(inputScreen).toHaveBeenLastCalledWith([{ type: 'release_all' }], 2)
    expect(streamScreen).toHaveBeenLastCalledWith(expect.any(AbortSignal), expect.any(Function), 1)
    vi.mocked(fetchScreen).mockResolvedValue({ ...info, sessions: [], supported: false, reason: 'desktop_session_unavailable' })
    endStream(); await flushPromises()
    expect(view.get('[data-test="desktop-session"]').attributes('data-value')).toBe('1')
    expect((await selectLabels(view, '[data-test="desktop-session"]')).join(' ')).toContain('Nedostupná')
    expect(view.get('[data-test="screen-unsupported"]').text()).toContain('Vybraná relace')
    setLocale('en'); await flushPromises()
    expect((await selectLabels(view, '[data-test="desktop-session"]')).join(' ')).toContain('Unavailable')
    expect(view.get('[data-test="screen-unsupported"]').text()).toContain('selected session')
  })
  it('coalesces moves per animation frame and keeps only one request in flight', async () => {
    const view = await open(); await showFrame()
    let finish!: () => void
    vi.mocked(inputScreen).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const move = (clientX: number) => view.get('canvas').trigger('pointermove', { clientX, clientY: 220 })
    await move(110); await move(210)
    expect(inputScreen).not.toHaveBeenCalled()
    tick(); await flushPromises()
    expect(batches()).toEqual([[{ type: 'move', x: .5, y: .5 }]])
    await move(110); tick(); await move(210); tick(); await move(310); tick()
    expect(inputScreen).toHaveBeenCalledTimes(1)
    finish(); await flushPromises()
    expect(batches()).toEqual([[{ type: 'move', x: .5, y: .5 }], [{ type: 'move', x: .75, y: .5 }]])
  })
  it.each([false, true])('preserves wheel positions before later moves (request in flight: %s)', async inFlight => {
    const view = await open(); await showFrame()
    const canvas = view.get('canvas')
    let finish!: () => void
    if (inFlight) {
      vi.mocked(inputScreen).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      await canvas.trigger('keydown', { code: 'KeyA' })
      tick(); await flushPromises()
    }
    await canvas.trigger('wheel', { clientX: 110, clientY: 220, deltaX: 0, deltaY: 100 })
    await canvas.trigger('pointermove', { clientX: 210, clientY: 220 })
    if (inFlight) tick()
    await canvas.trigger('wheel', { clientX: 210, clientY: 170, deltaX: 100, deltaY: 0 })
    await canvas.trigger('pointermove', { clientX: 210, clientY: 220 })
    if (inFlight) tick()
    await canvas.trigger('pointermove', { clientX: 310, clientY: 220 })
    tick(); await flushPromises()
    if (inFlight) {
      expect(inputScreen).toHaveBeenCalledTimes(1)
      finish(); await flushPromises()
    }
    expect(batches()).toEqual([
      ...(inFlight ? [[{ type: 'key', code: 'KeyA', down: true }]] : []),
      [
        { type: 'move', x: .25, y: .5 }, { type: 'wheel', dx: 0, dy: -1 },
        { type: 'move', x: .5, y: .25 }, { type: 'wheel', dx: 1, dy: -0 },
        { type: 'move', x: .75, y: .5 },
      ],
    ])
  })
  it('splits more than 200 inputs into bounded serial batches without losing order', async () => {
    const view = await open()
    const finish: (() => void)[] = []
    let inFlight = 0
    let maxInFlight = 0
    vi.mocked(inputScreen).mockImplementation(() => {
      inFlight++
      maxInFlight = Math.max(maxInFlight, inFlight)
      return new Promise<void>(resolve => finish.push(resolve)).finally(() => { inFlight-- })
    })
    const expected: ScreenInput[] = []
    for (let index = 0; index < 450; index++) {
      const code = `Key${String.fromCharCode(65 + Math.floor(index / 2) % 26)}`
      const down = index % 2 === 0
      expected.push({ type: 'key', code, down })
      view.get('canvas').element.dispatchEvent(new KeyboardEvent(down ? 'keydown' : 'keyup', { code, bubbles: true }))
    }
    expect(inputScreen).not.toHaveBeenCalled()
    tick(); await flushPromises()
    expect(batches().map(batch => batch.length)).toEqual([200])
    tick(); await flushPromises()
    expect(inputScreen).toHaveBeenCalledTimes(1)
    finish[0]!(); await flushPromises()
    expect(batches().map(batch => batch.length)).toEqual([200, 200])
    finish[1]!(); await flushPromises()
    expect(batches().map(batch => batch.length)).toEqual([200, 200, 50])
    finish[2]!(); await flushPromises()
    expect(inFlight).toBe(0)
    expect(maxInFlight).toBe(1)
    expect(batches().flat()).toEqual(expected)
    vi.mocked(inputScreen).mockResolvedValue()
  })
  it('sends physical key codes, prevents browser defaults and releases on blur and close', async () => {
    const view = await open()
    for (const type of ['keydown', 'keyup']) {
      const event = new KeyboardEvent(type, { code: 'KeyW', key: 'w', ctrlKey: true, bubbles: true, cancelable: true })
      view.get('canvas').element.dispatchEvent(event)
      expect(event.defaultPrevented).toBe(true)
    }
    tick(); await flushPromises()
    expect(batches()[0]).toEqual([{ type: 'key', code: 'KeyW', down: true }, { type: 'key', code: 'KeyW', down: false }])
    window.dispatchEvent(new Event('blur')); await flushPromises()
    expect(batches().at(-1)).toEqual([{ type: 'release_all' }])
    await view.get('.close').trigger('click'); await flushPromises()
    expect(batches().filter(batch => batch[0]?.type === 'release_all')).toHaveLength(2)
    expect(signal.aborted).toBe(true)
    expect(view.emitted('close')).toHaveLength(1)
  })
  it('queues cleanup behind an in-flight input request', async () => {
    const view = await open()
    let finish!: () => void
    vi.mocked(inputScreen).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await view.get('canvas').trigger('keydown', { code: 'ShiftLeft' })
    tick(); await flushPromises()
    view.unmount(); wrapper = undefined
    expect(inputScreen).toHaveBeenCalledTimes(1)
    finish(); await flushPromises()
    expect(batches().at(-1)).toEqual([{ type: 'release_all' }])
  })
  it('discards older decoded and undrawn bitmaps, and frees images on page exit', async () => {
    await open()
    const resolvers: ((value: ImageBitmap) => void)[] = []
    vi.mocked(createImageBitmap).mockImplementation(() => new Promise(resolve => resolvers.push(resolve)))
    frames(new Uint8Array([1])); frames(new Uint8Array([2])); frames(new Uint8Array([3]))
    const first = bitmap(), second = bitmap(), third = bitmap()
    resolvers[1]!(second as unknown as ImageBitmap); await flushPromises()
    resolvers[0]!(first as unknown as ImageBitmap); await flushPromises()
    expect(first.close).toHaveBeenCalledOnce()
    resolvers[2]!(third as unknown as ImageBitmap); await flushPromises()
    expect(second.close).toHaveBeenCalledOnce()
    expect(drawImage).not.toHaveBeenCalled()
    tick(); expect(drawImage).toHaveBeenCalledWith(third, 0, 100, 400, 200)
    window.dispatchEvent(new Event('pagehide'))
    expect(third.close).toHaveBeenCalledOnce()
    expect(signal.aborted).toBe(true)
  })
  it('shows loopback status and updates it when the locale changes', async () => {
    const view = await open({ ...info, enabled: false })
    const english = view.get('[data-test="screen-disabled"]').text()
    setLocale('cs'); await flushPromises()
    expect(view.get('[data-test="screen-disabled"]').text()).not.toBe(english)
    expect(streamScreen).not.toHaveBeenCalled()
    expect(inputScreen).not.toHaveBeenCalled()
  })
  it.each(['permission_denied', 'wayland_unsupported', 'no_display', 'capture_failed', 'desktop_service_required', 'unexpected'])('translates unsupported reason %s', async reason => {
    const view = await open({ ...info, supported: false, reason })
    const english = view.get('[data-test="screen-unsupported"]').text()
    expect(english).not.toContain(reason)
    if (reason === 'permission_denied') expect(view.get('a').attributes('href')).toContain('support.apple.com')
    setLocale('cs'); await flushPromises()
    expect(view.get('[data-test="screen-unsupported"]').text()).not.toBe(english)
    expect(streamScreen).not.toHaveBeenCalled()
  })
  it('automatically reconnects only after stream end and translates the status', async () => {
    const view = await open()
    const firstSignal = signal
    await vi.advanceTimersByTimeAsync(60000)
    expect(streamScreen).toHaveBeenCalledTimes(1)
    endStream(); await flushPromises()
    expect(view.get('[role="status"]').text()).toContain('Reconnecting')
    expect(view.find('.status button').exists()).toBe(false)
    setLocale('cs'); await flushPromises()
    expect(view.get('[role="status"]').text()).toContain('Automaticky')
    await view.get('canvas').trigger('keydown', { code: 'KeyA' })
    tick(); await flushPromises()
    expect(batches().flat().some(event => event.type === 'key')).toBe(false)
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(firstSignal.aborted).toBe(true)
    expect(streamScreen).toHaveBeenCalledTimes(2)
  })
  it('refreshes the reason after a lock and translates it while displayed', async () => {
    const view = await open()
    await showFrame()
    vi.mocked(fetchScreen).mockResolvedValue({ ...info, supported: false, reason: 'desktop_service_required' })
    endStream(); await flushPromises()
    expect(view.get('[data-test="screen-unsupported"]').text()).toContain('protected')
    setLocale('cs'); await flushPromises()
    expect(view.get('[data-test="screen-unsupported"]').text()).toContain('chráněnou')
    vi.mocked(fetchScreen).mockResolvedValue(info)
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(streamScreen).toHaveBeenCalledTimes(2)
  })
  it('does not disconnect for failed input or a bad frame, and clears input errors on success', async () => {
    const view = await open(); await showFrame()
    vi.mocked(inputScreen).mockRejectedValueOnce(new Error('temporary input failure'))
    await view.get('canvas').trigger('keydown', { code: 'KeyA' })
    tick(); await flushPromises()
    expect(view.get('[role="alert"]').text()).toContain('input')
    setLocale('cs'); await flushPromises()
    expect(view.get('[role="alert"]').text()).toContain('Ovládání')
    vi.mocked(createImageBitmap).mockRejectedValueOnce(new Error('bad JPEG'))
    await showFrame()
    await showFrame()
    await view.get('canvas').trigger('keyup', { code: 'KeyA' })
    tick(); await flushPromises()
    await vi.advanceTimersByTimeAsync(60000)
    expect(view.find('.status').exists()).toBe(false)
    expect(streamScreen).toHaveBeenCalledTimes(1)
    expect(signal.aborted).toBe(false)
  })
  it('backs off repeated failures, recovers and cancels retries on close', async () => {
    vi.mocked(streamScreen).mockRejectedValue(new Error('offline'))
    const view = await open()
    expect(streamScreen).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(999)
    expect(streamScreen).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1); await flushPromises()
    expect(streamScreen).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1999)
    expect(streamScreen).toHaveBeenCalledTimes(2)
    vi.mocked(streamScreen).mockImplementation((abortSignal, onFrame) => {
      signal = abortSignal; frames = onFrame
      return new Promise(resolve => { endStream = resolve })
    })
    await vi.advanceTimersByTimeAsync(1); await flushPromises()
    await showFrame()
    expect(view.find('[role="status"]').exists()).toBe(false)
    endStream(); await flushPromises()
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(streamScreen).toHaveBeenCalledTimes(4)
    endStream(); await flushPromises()
    await view.get('.close').trigger('click')
    await vi.advanceTimersByTimeAsync(60000)
    expect(streamScreen).toHaveBeenCalledTimes(4)
  })
  it('retries a failed status request but never retries a disabled desktop', async () => {
    vi.mocked(fetchScreen).mockRejectedValueOnce(new Error('offline'))
    const view = await open()
    expect(view.get('[role="status"]').text()).toContain('Reconnecting')
    vi.mocked(fetchScreen).mockResolvedValue({ ...info, enabled: false })
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchScreen).toHaveBeenCalledTimes(2)
    expect(streamScreen).not.toHaveBeenCalled()
    expect(view.find('[role="status"]').exists()).toBe(false)
  })

  it('caps retry delays at ten seconds', async () => {
    vi.mocked(streamScreen).mockRejectedValue(new Error('offline'))
    await open()
    let calls = 1
    for (const delay of [1000, 2000, 4000, 8000, 10000, 10000]) {
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(streamScreen).toHaveBeenCalledTimes(calls)
      await vi.advanceTimersByTimeAsync(1); await flushPromises()
      expect(streamScreen).toHaveBeenCalledTimes(++calls)
    }
  })
  it('ignores a late decoded frame after the stream has ended', async () => {
    const view = await open()
    let finish!: (value: ImageBitmap) => void
    vi.mocked(createImageBitmap).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    frames(new Uint8Array([1]))
    endStream(); await flushPromises()
    const late = bitmap()
    finish(late as unknown as ImageBitmap); await flushPromises()
    expect(late.close).toHaveBeenCalledOnce()
    expect(view.get('[role="status"]').text()).toContain('Reconnecting')
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(streamScreen).toHaveBeenCalledTimes(2)
  })

})
