/** A fresh detector per mounted dashboard; repeats never advance or reset it. */
export function createSecretKeys() {
  let keys: string[] = []
  let previous = 0
  return (event: Pick<KeyboardEvent, 'key' | 'repeat'>, now = performance.now()): 'terminal' | 'screen' | undefined => {
    if (event.repeat) return
    if (now - previous > 1000) keys = []
    previous = now
    if (event.key !== 'Control' && event.key !== 'Shift') { keys = []; return }
    if (keys.at(-1) === event.key) keys = []
    keys.push(event.key)
    if (keys.length < 4) return
    const result = keys[0] === 'Control' ? 'terminal' : 'screen'
    keys = []
    return result
  }
}
