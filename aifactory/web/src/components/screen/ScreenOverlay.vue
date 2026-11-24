<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { locale, t } from '../../lib/i18n'
import { fetchScreen, inputScreen, streamScreen, type ScreenInfo, type ScreenInput } from '../../lib/screen'
import { messages } from './messages'
import SelectMenu from '../ui/SelectMenu.vue'

const emit = defineEmits<{ close: [] }>()
const text = computed(() => messages[locale.value])
const canvas = ref<HTMLCanvasElement>()
const info = ref<ScreenInfo>()
const loading = ref(true)
const ended = ref(false)
const failed = ref(false)
const reason = computed(() => {
  const key = info.value?.reason
  const catalog = text.value
  return key && Object.prototype.hasOwnProperty.call(catalog, key)
    ? catalog[key as keyof typeof catalog] : catalog.unsupported
})
const previousFocus = document.activeElement as HTMLElement | null
let controller: AbortController | undefined
let disposed = false
let active = false
let animation = 0
let observer: ResizeObserver | undefined
let pendingBitmap: ImageBitmap | undefined
let displayed: ImageBitmap | undefined
let frameNumber = 0
let decodedNumber = 0
let pending: ScreenInput[] = []
let pendingMove: Extract<ScreenInput, { type: 'move' }> | undefined
let sendTask: Promise<void> | undefined
let connectionSession: number | undefined
const selectedSession = ref('')
const sessionOptions = computed(() => {
  const options = [{ value: '', label: text.value.current }, ...(info.value?.sessions ?? []).map(session => ({
    value: String(session.id), label: `${text.value[session.kind]} ${session.id} · ${text.value[session.state]}`,
  }))]
  if (selectedSession.value && !options.some(option => option.value === selectedSession.value)) {
    options.push({ value: selectedSession.value, label: `${text.value.session} ${selectedSession.value} · ${text.value.unavailable}` })
  }
  return options
})
function selectSession(value: string) { selectedSession.value = value; void start() }
let streamTask: Promise<void> | undefined
let starting = false

function schedule() {
  if (!animation && !disposed) animation = requestAnimationFrame(paint)
}
function paint() {
  animation = 0
  if (pendingMove) { pending.push(pendingMove); pendingMove = undefined }
  if (pendingBitmap) { displayed?.close(); displayed = pendingBitmap; pendingBitmap = undefined }
  const el = canvas.value
  if (el && displayed) {
    const rect = el.getBoundingClientRect()
    el.width = Math.max(1, Math.round(rect.width))
    el.height = Math.max(1, Math.round(rect.height))
    const scale = Math.min(el.width / displayed.width, el.height / displayed.height)
    const width = displayed.width * scale, height = displayed.height * scale
    const ctx = el.getContext('2d')
    ctx?.clearRect(0, 0, el.width, el.height)
    ctx?.drawImage(displayed, (el.width - width) / 2, (el.height - height) / 2, width, height)
  }
  void sendPending()
}
async function decode(frame: Uint8Array<ArrayBuffer>, current: AbortController) {
  const number = ++frameNumber
  try {
    const bitmap = await createImageBitmap(new Blob([frame], { type: 'image/jpeg' }))
    if (disposed || current.signal.aborted || current !== controller || number < decodedNumber) { bitmap.close(); return }
    decodedNumber = number
    pendingBitmap?.close()
    pendingBitmap = bitmap
    schedule()
  } catch {
    if (!disposed && current === controller && !current.signal.aborted) failed.value = true
  }
}
function coordinates(event: MouseEvent) {
  const el = canvas.value
  if (!el || !displayed) return
  const rect = el.getBoundingClientRect()
  const scale = Math.min(rect.width / displayed.width, rect.height / displayed.height)
  const width = displayed.width * scale, height = displayed.height * scale
  if (!width || !height) return
  const x = (event.clientX - rect.left - (rect.width - width) / 2) / width
  const y = (event.clientY - rect.top - (rect.height - height) / 2) / height
  if (x < 0 || y < 0 || x > 1 || y > 1) return
  return { x, y }
}
function enqueue(event: ScreenInput) {
  if (!active || disposed) return
  if (event.type === 'move') {
    // A wheel has no coordinates in the API: its preceding move must survive
    // coalescing so scrolling still targets the point where it occurred.
    pending = pending.filter((item, index) => item.type !== 'move' || pending[index + 1]?.type === 'wheel')
    pendingMove = event
  } else {
    if (pendingMove) { pending.push(pendingMove); pendingMove = undefined }
    pending.push(event)
  }
  schedule()
}
function sendPending(): Promise<void> {
  if (sendTask) return sendTask
  const session = connectionSession
  sendTask = (async () => {
    while (pending.length) {
      const events = pending.splice(0, 200)
      try { await inputScreen(events, session) }
      catch { if (!disposed) failed.value = true }
    }
  })().finally(() => { sendTask = undefined })
  return sendTask
}

function pointer(event: PointerEvent) {
  const point = coordinates(event)
  if (!point || !active) return
  event.preventDefault()
  canvas.value?.focus()
  if (event.type === 'pointermove') enqueue({ type: 'move', ...point })
  else {
    const button = (['left', 'middle', 'right'] as const)[event.button]
    if (button) enqueue({ type: 'button', button, down: event.type === 'pointerdown', ...point })
  }
}
function wheel(event: WheelEvent) {
  const point = coordinates(event)
  if (!point || !active) return
  event.preventDefault()
  enqueue({ type: 'move', ...point })
  const unit = event.deltaMode === 1 ? 1 : event.deltaMode === 2 ? 24 : 1 / 100
  enqueue({ type: 'wheel', dx: event.deltaX * unit, dy: -event.deltaY * unit })
}
function key(event: KeyboardEvent) {
  if (!active || disposed || (event.target instanceof Element && event.target.closest('.toolbar, .close, .status'))) return
  event.preventDefault()
  event.stopPropagation()
  enqueue({ type: 'key', code: event.code, down: event.type === 'keydown' })
}
function release() {
  if (!active) return
  pendingMove = undefined
  pending = [{ type: 'release_all' }]
  void sendPending()
}
function clearImages() {
  pendingBitmap?.close(); pendingBitmap = undefined
  displayed?.close(); displayed = undefined
  const el = canvas.value
  if (el) el.getContext('2d')?.clearRect(0, 0, el.width, el.height)
}
async function start() {
  if (disposed || starting) return
  starting = true
  release()
  active = false
  controller?.abort()
  await sendPending()
  await streamTask?.catch(() => {})
  if (disposed) { starting = false; return }
  connectionSession = selectedSession.value === '' ? undefined : Number(selectedSession.value)
  const current = new AbortController()
  controller = current
  clearImages()
  loading.value = true; ended.value = false; failed.value = false
  try {
    const result = await fetchScreen(current.signal, connectionSession)
    if (disposed || current !== controller) return
    info.value = result
    loading.value = false
    if (!result.enabled || !result.supported) return
    active = true
    canvas.value?.focus()
    starting = false
    streamTask = streamScreen(current.signal, frame => { void decode(frame, current) }, connectionSession)
    await streamTask
    if (current.signal.aborted) return
    // A lock or service outage may end a stream that was supported at startup.
    const latest = await fetchScreen(current.signal, connectionSession)
    if (!disposed && current === controller) {
      info.value = latest
      if (!latest.supported) { clearImages(); canvas.value?.getContext('2d')?.clearRect(0, 0, canvas.value.width, canvas.value.height) }
    }
  } catch {
    if (!disposed && !current.signal.aborted) failed.value = true
  } finally {
    if (!disposed && current === controller) {
      starting = false
      loading.value = false
      if (active) { release(); active = false; ended.value = true }
    }
  }
}
function dispose() {
  if (disposed) return
  release()
  active = false; disposed = true
  controller?.abort()
  cancelAnimationFrame(animation)
  observer?.disconnect()
  clearImages()
}
function close() { dispose(); emit('close') }
onMounted(() => {
  window.addEventListener('keydown', key)
  window.addEventListener('keyup', key)
  window.addEventListener('blur', release)
  window.addEventListener('pagehide', dispose)
  observer = new ResizeObserver(schedule)
  observer.observe(canvas.value!)
  canvas.value?.focus()
  void start()
})
onBeforeUnmount(() => {
  dispose()
  window.removeEventListener('keydown', key)
  window.removeEventListener('keyup', key)
  window.removeEventListener('blur', release)
  window.removeEventListener('pagehide', dispose)
  previousFocus?.focus()
})
</script>

<template>
  <section class="screen-overlay" role="dialog" aria-modal="true" :aria-label="text.title" data-test="screen-overlay">
    <canvas ref="canvas" tabindex="0" :aria-label="text.title" @pointermove="pointer" @pointerdown="pointer" @pointerup="pointer" @wheel="wheel" @contextmenu.prevent />
    <div class="toolbar" @keydown.stop @keyup.stop>
      <SelectMenu v-if="info?.sessions?.length || selectedSession" data-test="desktop-session" :model-value="selectedSession" :options="sessionOptions" :label="text.session" :disabled="loading" @update:model-value="selectSession" />
      <button type="button" :disabled="loading" @click="start">{{ text.refresh }}</button>
    </div>
    <button class="close" type="button" :aria-label="text.close" @click="close">×</button>
    <div v-if="loading || failed || ended || !info?.enabled || !info?.supported" class="status">
      <p v-if="loading">{{ text.loading }}</p>
      <p v-else-if="info && !info.enabled" data-test="screen-disabled">{{ t('helpers.terminalDisabled') }}</p>
      <p v-else-if="info && !info.supported" data-test="screen-unsupported">
        {{ reason }}
        <a v-if="info.reason === 'permission_denied'" href="https://support.apple.com/guide/mac-help/control-access-to-screen-and-system-audio-recording-mchld6aa7d23/mac" target="_blank" rel="noopener noreferrer">{{ text.permissions }}</a>
      </p>
      <p v-if="failed" role="alert">{{ text.error }}</p>
      <p v-if="ended" role="status">{{ text.ended }}</p>
      <button v-if="ended || failed || (info?.enabled && !info.supported)" type="button" @click="start">{{ text.restart }}</button>
    </div>
  </section>
</template>

<style scoped>
.screen-overlay { position: fixed; inset: 0; z-index: 1000; background: #000; color: #e7eaf3; }
.toolbar { position: absolute; top: 8px; left: 12px; right: 60px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
canvas { display: block; width: 100%; height: 100%; outline: none; touch-action: none; }
.close { position: absolute; top: 8px; right: 12px; opacity: .5; font-size: 24px; }
button { background: #151922; color: inherit; border: 1px solid #303849; border-radius: 5px; cursor: pointer; }
.close:hover, .close:focus-visible { opacity: 1; }
.status { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); text-align: center; }
a { color: #5ad2dd; }
</style>
