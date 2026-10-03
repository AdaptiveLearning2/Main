import '@testing-library/jest-dom/vitest'
import { createElement } from 'react'
import { LazyMotion, domAnimation } from 'framer-motion'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeAll, afterAll, vi } from 'vitest'

// Every render sits in App.jsx's <LazyMotion strict>: `m` animates as it does live, and `motion` throws.
vi.mock('@testing-library/react', async importOriginal => {
  const rtl = await importOriginal()
  const inMotion = Inner => ({ children }) => createElement(LazyMotion, { features: domAnimation, strict: true },
    Inner ? createElement(Inner, null, children) : children)
  return { ...rtl, render: (ui, options = {}) => rtl.render(ui, { ...options, wrapper: inMotion(options.wrapper) }) }
})

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
