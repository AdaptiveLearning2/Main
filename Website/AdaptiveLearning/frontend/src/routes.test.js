import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cwd } from 'node:process'
import { expect, it, vi } from 'vitest'

// The real page modules load; these stand in for what they import at module scope.
vi.mock('./lib/supabase', async () => await import('./test/mocks/supabase'))
vi.mock('./lib/api', async () => await import('./test/mocks/apiFetch'))

const { ROUTES, pages, prefetchRoute } = await import('./routes')

// Transforming three real pages takes ~10 s cold; past the default 5 s the pending imports hang the run.
it('loads the page a direct link renders, matching parameters exactly', async () => {
  expect(await prefetchRoute('/teacher/classes/c1')).toBe(await pages.classDetail())
  expect(await prefetchRoute('/teacher/classes')).toBe(await pages.classes())
  expect(await prefetchRoute('/teacher/students/s1/report')).toBe(await pages.studentReport())
}, 60_000)

it('loads nothing for a path no route renders', () => {
  expect(prefetchRoute('/login')).toBeNull()
  expect(prefetchRoute('/no/such/page')).toBeNull()
})

it('covers every page route App declares', () => {
  // A text scan of App's `path="…"` attributes: the route table is JSX, not data.
  const app = readFileSync(join(cwd(), 'src/App.jsx'), 'utf8')
  const declared = [...app.matchAll(/<Route\s+path="([^"]+)"/g)].map(m => m[1])
    .filter(p => !['/login', '/register', '/', '*'].includes(p))
  expect(declared.length).toBeGreaterThan(20)
  expect(new Set(ROUTES.map(([path]) => path))).toEqual(new Set(declared))
})
