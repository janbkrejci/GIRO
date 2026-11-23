import { describe, expect, it } from 'vitest'
import { createSecretKeys } from './secretKeys'
const sequences = { terminal: ['Control', 'Shift', 'Control', 'Shift'], screen: ['Shift', 'Control', 'Shift', 'Control'] } as const

describe('secret keys', () => {
  it.each(['terminal', 'screen'] as const)('recognizes only %s and resets both sequences', name => {
    const detect = createSecretKeys()
    for (let cycle = 0; cycle < 2; cycle++) {
      expect(sequences[name].map(key => detect({ key, repeat: false }, 0))).toEqual([undefined, undefined, undefined, name])
    }
    const other = name === 'terminal' ? 'screen' : 'terminal'
    expect(sequences[other].map(key => detect({ key, repeat: false }, 0))).toEqual([undefined, undefined, undefined, other])
  })
  it.each(['terminal', 'screen'] as const)('resets %s on other keys and a gap over one second', name => {
    for (const interruption of ['x', 'Alt', 'Enter']) {
      const detect = createSecretKeys()
      detect({ key: sequences[name][0], repeat: false }, 0)
      detect({ key: interruption, repeat: false }, 10)
      expect(sequences[name].slice(1).map(key => detect({ key, repeat: false }, 20))).toEqual([undefined, undefined, undefined])
    }
    const detect = createSecretKeys()
    detect({ key: sequences[name][0], repeat: false }, 0)
    expect(sequences[name].slice(1).map(key => detect({ key, repeat: false }, 1001))).toEqual([undefined, undefined, undefined])
  })
  it.each(['terminal', 'screen'] as const)('ignores repeat without extending the %s timeout', name => {
    const detect = createSecretKeys()
    const keys = sequences[name]
    detect({ key: keys[0], repeat: false }, 0)
    expect(detect({ key: keys[1], repeat: true }, 100)).toBeUndefined()
    detect({ key: keys[1], repeat: false }, 1000)
    detect({ key: keys[2], repeat: false }, 2000)
    expect(detect({ key: keys[3], repeat: false }, 3000)).toBe(name)
    detect({ key: keys[0], repeat: false }, 4000)
    detect({ key: keys[0], repeat: true }, 4900)
    expect(keys.slice(1).map(key => detect({ key, repeat: false }, 5100))).toEqual([undefined, undefined, undefined])
  })
})
