// Adds jest-dom's matchers (toBeInTheDocument, toHaveTextContent, ...) to
// vitest's expect.
import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeAll, afterAll } from 'vitest'

// Testing Library's default `findBy`/`waitFor` budget is 1000ms, which is a
// figure it picked for a suite of pure components and is simply too small
// here. Several pages in this app are driven by real-timer polls -- the
// pairing chain is 1-1.5s waits noticed by a 5s poll, and CLAUDE.md records
// why a fake clock does not survive those components -- so a query for
// something that legitimately arrives on the second poll is racing a budget
// unrelated to what it is waiting for.
//
// It only ever passed because the machine was idle. Under the load of the
// full 62-file run, vitest's worker threads compete and the same query misses
// by a few hundred milliseconds: measured as intermittent failures in
// `AdaptiveCameraLifecycle` on three separate full runs, always at a
// default-budget query, never at the one beside it that already passes
// `{ timeout: 9000 }` for the same element.
//
// Raising it costs nothing on a passing assertion -- `waitFor` returns as
// soon as the condition holds -- and only makes a genuinely failing query
// take longer to report. That is the right trade at 739 tests, where the
// failing case is rare and the flaky case was routine.
configure({ asyncUtilTimeout: 5000 })

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
