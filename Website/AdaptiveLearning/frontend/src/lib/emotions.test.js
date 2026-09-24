import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { EMOTION_COLOURS, FER_LABELS, UNKNOWN_EMOTION_COLOUR, emotionEmoji } from './emotions'

const SRC = resolve(fileURLToPath(import.meta.url), '..', '..')

describe('the emotion labels', () => {
  it('are the ones the backend fuses, in the same order', () => {
    const fusion = readFileSync(resolve(SRC, '..', '..', 'backend', 'signal_fusion.py'), 'utf8')
    const block = fusion.match(/^FER_LABELS = \(([^)]*)\)/m)
    expect(block, 'FER_LABELS not found -- this check is inert').toBeTruthy()
    const backend = [...block[1].matchAll(/"([a-z]+)"/g)].map(m => m[1])
    expect(backend.length).toBeGreaterThan(0)
    expect(FER_LABELS).toEqual(backend)
  })

  it('each have their own colour and an emoji, and nothing else does', () => {
    expect(Object.keys(EMOTION_COLOURS).sort()).toEqual([...FER_LABELS].sort())
    const colours = Object.values(EMOTION_COLOURS)
    expect(new Set(colours).size).toBe(colours.length)
    expect(colours).not.toContain(UNKNOWN_EMOTION_COLOUR)
    expect(FER_LABELS.filter(l => !emotionEmoji(l))).toEqual([])
    const emoji = FER_LABELS.map(emotionEmoji)
    expect(new Set(emoji).size).toBe(emoji.length)
  })

  it('offer no emoji for a label FER+ never produces', () => {
    for (const label of ['surprised', 'confused', 'frustrated', 'anger', '', undefined, null]) {
      expect(emotionEmoji(label)).toBeNull()
    }
  })
})
