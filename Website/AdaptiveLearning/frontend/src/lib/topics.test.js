import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { TOPICS, TOPIC_ICONS, topicLabel } from './topics'

const SRC = resolve(fileURLToPath(import.meta.url), '..', '..')

describe('the topic list', () => {
  it('matches the backend, which is the only list that decides anything', () => {
    // This file duplicates `LLM_topic_decider.ALL_TOPICS` because a React
    // bundle cannot import Python. Parsing the backend is what turns the copy
    // from trusted into checked -- without it, a topic added on one side of
    // the stack goes missing on the other, which has now happened three times.
    //
    // Compared as sets: order here is display order and this file's own
    // business.
    const decider = readFileSync(
      resolve(SRC, '..', '..', 'backend', 'LLM_topic_decider.py'), 'utf8')
    const block = decider.match(/^ALL_TOPICS = \[([\s\S]*?)\]/m)
    expect(block, 'ALL_TOPICS not found -- this check is inert').toBeTruthy()
    const backend = [...block[1].matchAll(/"([a-z_]+)"/g)].map(m => m[1])
    expect(backend.length).toBeGreaterThan(0)
    expect([...TOPICS].sort()).toEqual([...backend].sort())
  })

  it('gives every topic an icon', () => {
    // A tile whose icon is `undefined` renders an empty slot rather than an
    // error, which is how two topics shipped iconless and nothing caught it.
    expect(TOPICS.filter(t => !TOPIC_ICONS[t])).toEqual([])
    expect(Object.keys(TOPIC_ICONS).filter(t => !TOPICS.includes(t))).toEqual([])
  })

  it('is the only place a topic list is written down', () => {
    // Six copies existed and they disagreed; the two teacher surfaces had
    // never been updated past the original ten. A seventh would go the same
    // way, so it fails here instead.
    const walk = (dir) => readdirSync(dir).flatMap(name => {
      const full = join(dir, name)
      if (statSync(full).isDirectory()) return walk(full)
      return /\.jsx?$/.test(full) && !full.includes('.test.') ? [full] : []
    })
    const offenders = walk(SRC)
      .filter(f => f !== resolve(SRC, 'lib', 'topics.js'))
      .filter(f => {
        const src = readFileSync(f, 'utf8')
        // Three or more known topic slugs quoted in one array literal is a
        // list, whatever it is called.
        return /\[[^\]]*'(?:ordering|rationals|algebra|geometry|probability)'[^\]]*'(?:ordering|rationals|algebra|geometry|probability)'[^\]]*'(?:ordering|rationals|algebra|geometry|probability)'/.test(src)
      })
      .map(f => f.slice(SRC.length + 1).split(sep).join('/'))
    expect(offenders).toEqual([])
  })

  it('spells out every underscore, not just the first', () => {
    // `String.replace` with a string pattern replaces one occurrence, which
    // was fine while every slug had at most one underscore.
    expect(topicLabel('angle_relationships')).toBe('angle relationships')
    expect(topicLabel('a_b_c')).toBe('a b c')
  })
})
