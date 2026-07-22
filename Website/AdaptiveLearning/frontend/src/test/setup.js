// Adds jest-dom's matchers (toBeInTheDocument, toHaveTextContent, ...) to
// vitest's expect.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll, afterAll } from 'vitest'

// RTL auto-cleans when it can detect a global afterEach, but being explicit
// means a component left mounted by one test cannot leak into the next.
afterEach(() => {
  cleanup()
})

// recharts' ResponsiveContainer sizes itself from real layout, which jsdom does
// not implement, so it warns on every chart render that it measured -1x-1.
// The charts are fine in a browser; this is purely an artifact of the test DOM.
//
// Filtered rather than silenced wholesale: anything that is not this one known
// message still reaches the console, so a real warning cannot hide behind it.
// If ResponsiveContainer is ever given explicit dimensions, delete this.
const RECHARTS_SIZE_WARNING = 'width(-1) and height(-1)'
let realWarn
let realError

beforeAll(() => {
  realWarn = console.warn
  realError = console.error
  const passThrough = (original) => (...args) => {
    if (typeof args[0] === 'string' && args[0].includes(RECHARTS_SIZE_WARNING)) return
    original(...args)
  }
  console.warn = passThrough(realWarn)
  console.error = passThrough(realError)
})

afterAll(() => {
  console.warn = realWarn
  console.error = realError
})
