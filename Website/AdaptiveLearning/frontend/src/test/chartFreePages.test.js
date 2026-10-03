import { expect, it, vi } from 'vitest'

// These pages draw no chart; through SignalPanel's helpers they once carried recharts (~119 KiB gzip).
vi.mock('recharts', () => { throw new Error('recharts was loaded') })
vi.mock('../lib/supabase', async () => await import('./mocks/supabase'))
vi.mock('../lib/api', async () => await import('./mocks/apiFetch'))

it.each([
  ['the parent dashboard', () => import('../pages/parent/Dashboard')],
  ['the teacher students page', () => import('../pages/teacher/Students')],
])('%s loads without the chart library', async (_name, load) => {
  await expect(load()).resolves.toHaveProperty('default')
}, 60_000)
