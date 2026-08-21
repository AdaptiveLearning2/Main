// Adds jest-dom's matchers (toBeInTheDocument, toHaveTextContent, ...) to
// vitest's expect.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll, afterAll } from 'vitest'

// RTL auto-cleans when it detects a global afterEach, but being explicit
// ensures a component left mounted by one test can't leak into the next.
afterEach(() => {
  cleanup()
})

// recharts' ResponsiveContainer measures real layout, which jsdom doesn't
// implement, so it warns every render that it measured -1x-1. Harmless in a
// browser; this filters only that one message so a real warning still gets
// through. Delete this if ResponsiveContainer is ever given explicit
// dimensions.
//
// console.warn only: recharts emits this on warn, never on error, and
// console.error is where React reports things worth seeing (act() warnings,
// missing keys, error boundaries).
const RECHARTS_SIZE_WARNING = 'width(-1) and height(-1)'
let realWarn

beforeAll(() => {
  realWarn = console.warn
  console.warn = (...args) => {
    if (typeof args[0] === 'string' && args[0].includes(RECHARTS_SIZE_WARNING)) return
    realWarn(...args)
  }
})

afterAll(() => {
  console.warn = realWarn
})
