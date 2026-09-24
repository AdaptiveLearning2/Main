import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeAll, afterAll } from 'vitest'

// 5000, not 1000: real-timer polls (5 s) legitimately land on the second poll.
configure({ asyncUtilTimeout: 5000 })

afterEach(() => {
  cleanup()
})

// jsdom has no layout, so recharts warns -1x-1 every render. Filter only that
// warn message; console.error is untouched.
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
